"""Static 90 m feature stack for the local (no-GEE) ML pipeline — PLAN Nível 3a.

Sources (NASA Earthdata, ``EARTHDATA_TOKEN`` bearer — no GEE):

* NASADEM_HGT.001 — 1 arc-sec DEM in 1°×1° zips via CMR + LP DAAC cloud.
  Terrain bands at 3 arc-sec (~90 m): elevation (3×3 mean), slope,
  aspect_sin/cos, curvature (Laplacian ×100, units 1/100 m), relief (5×5
  max−min). Neighbour rule identical to ``tp.terrain`` (gdaldem: NaN
  neighbour falls back to centre cell). Tile edges: a 2-pixel (90 m) halo
  is read from adjacent 1° cells when present; cells at the bbox boundary
  or over ocean get edge NaN.
* MCD12Q1.061 — LC_Type1 IGBP land cover, 500 m sinusoidal, via cloud
  OPeNDAP (HDF4 granule → netCDF4 response). Kept at ~500 m on EPSG:4326.
* VNP13C2.002 — VIIRS/NPP monthly 0.05° CMG NDVI (HDF5, global grid,
  geographic CRS — no reprojection needed). Annual mean of the 12 months
  of ``settings.NDVI_YEAR``; QA via pixel_reliability (<=1 kept) and
  _FillValue/valid_range → NaN. One global grid serves both
  ``sample_static`` and ``sample_static_points``. (Brief first asked for
  MOD13A3.061 — it has no OPeNDAP endpoint (verified: DMR 404) and its
  tiles are HDF4 which rasterio here cannot read; VNP13A3 tiles would
  work but the scope switched to the global CMG product.)
* hand_m — Now-domain HAND at 0.01°; optional band, NaN elsewhere.

Two modes:

* region mode (3a/export): ``build_static`` writes per-1°-tile GeoTIFFs
  under ``DATA_DIR/static90/terrain/`` plus landcover/ndvi rasters, an
  ``index.json`` and a ``static90.vrt``; ``sample_static`` /
  ``static_on_grid`` read them.
* point mode (3b, global GLC): ``sample_static_points`` computes the same
  columns for arbitrary lon/lat — NASADEM zip per 1° cell, terrain only
  for a small window around each point (zip+hgt deleted unless
  ``keep_dem``), MCD12Q1 via one OPeNDAP hyperslab request per MODIS
  tile, NDVI/hand from the shared rasters. Per-point results are cached
  in ``DATA_DIR/static_points.parquet`` keyed by lon/lat rounded to 5
  decimals.  NOTE: no parquet engine is installed (pyarrow/fastparquet
  absent, no new deps allowed) — the cache payload is pickle inside the
  ``.parquet`` path; swap to real parquet by adding an engine.

CLI: ``python -m ml.local.sources_static <nasadem|terrain|landcover|ndvi|all> --bbox W S E N``
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import os
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from xml.etree import ElementTree as ET

import h5py
import numpy as np
import pandas as pd
import rasterio
import requests
import scipy.ndimage as ndi
from affine import Affine
from rasterio.crs import CRS
from rasterio.merge import merge as rio_merge
from rasterio.transform import array_bounds
from rasterio.warp import Resampling, reproject, transform_bounds
from rasterio.windows import Window
from tp.terrain import _deg_len_m

from ml.local import settings

# --- constants ---------------------------------------------------------------
CMR = "https://cmr.earthdata.nasa.gov/search"
OPENDAP = "https://opendap.earthdata.nasa.gov/collections"

RES_SRC_DEG = 1.0 / 3600.0        # NASADEM 1 arc-second
SRC_PER_OUT = 3                   # 3x3 source px per 90 m output px
HALO_PX = 2                       # 90 m halo (covers 3x3 slope + 5x5 relief)
HGT_NODATA = -32768

SINUSOIDAL = CRS.from_proj4("+proj=sinu +R=6371007.181 +nadgrids=@null +wktext")
# MODIS sinusoidal tile grid (verified against MCD12Q1 h13v11 StructMetadata).
MODIS_X0, MODIS_Y0 = -20015109.354, 10007554.677
MODIS_TILE_M = 1111950.5196667    # 2400 px * 463.3128 m
LC_PIX_M = MODIS_TILE_M / 2400.0  # ~463.3 m
LC_RES_DEG = 1.0 / 240.0          # ~0.004167 deg — closest tidy grid to native
NDVI_RES_DEG = 0.05               # VNP13C2 CMG native
NDVI_SDS = ("HDFEOS/GRIDS/VIIRS_Grid_monthly_VI_CMG/Data Fields/"
            "CMG 0.05 Deg monthly NDVI")
NDVI_REL_SDS = ("HDFEOS/GRIDS/VIIRS_Grid_monthly_VI_CMG/Data Fields/"
                "CMG 0.05 Deg monthly pixel reliability")

BANDS_ALL = settings.TERRAIN + settings.LANDCOVER + ["hand_m"]
LC_WATER = {0, 17, 255}           # IGBP water=17; 0 and 255 = invalid/fill
POINT_CACHE_NAME = "static_points.parquet"
POINT_KEY_DECIMALS = 5
POINTS_VERSION = 1

_DAP4_TYPES = {
    "Byte", "Int8", "UInt8", "Int16", "UInt16", "Int32", "UInt32",
    "Int64", "UInt64", "Float32", "Float64", "String", "Url", "Opaque", "Enum",
}

# --- shared-checkout HAND (Now domain only) ----------------------------------
SHARED_HAND = (Path.home() / "code/r2l2pj/tipping-point-data/data/static/"
               "south_brazil/terrain/hand_m.tif")
LOCAL_HAND = (settings.REPO_ROOT / "data" / "static" / "south_brazil" /
              "terrain" / "hand_m.tif")


# --- tiny HTTP layer (offline tests monkeypatch these) ------------------------
def _token() -> str:
    tok = os.environ.get("EARTHDATA_TOKEN", "")
    if not tok:
        raise RuntimeError(
            "EARTHDATA_TOKEN not set — export it from "
            "~/code/r2l2pj/tipping-point-data/.env")
    return tok


_SESSION: requests.Session | None = None


def _session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        s = requests.Session()
        s.headers["Authorization"] = f"Bearer {_token()}"
        _SESSION = s
    return _SESSION


def _http_get(url: str, params: dict | None = None,
              timeout: int = 120, tries: int = 3) -> requests.Response:
    last: Exception | None = None
    for i in range(tries):
        try:
            r = _session().get(url, params=params, timeout=timeout)
            if r.status_code == 429 or r.status_code >= 500:
                last = RuntimeError(f"HTTP {r.status_code} {url}")
                time.sleep(2 ** i)
                continue
            return r
        except requests.RequestException as e:  # network-level: retry
            last = e
            time.sleep(2 ** i)
    raise RuntimeError(f"GET failed after {tries} tries: {url}") from last


def _download(url: str, dest: Path, tries: int = 4) -> Path:
    """Streamed download (redirects followed); skips an existing non-empty file."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    tmp = dest.with_suffix(dest.suffix + ".part")
    last: Exception | None = None
    for i in range(tries):
        try:
            with _session().get(url, stream=True, timeout=300) as r:
                r.raise_for_status()
                with open(tmp, "wb") as f:
                    f.writelines(r.iter_content(1 << 20))
            tmp.rename(dest)
            return dest
        except (requests.RequestException, OSError) as e:
            last = e
            time.sleep(2 ** i)
    raise RuntimeError(f"download failed after {tries} tries: {url}") from last


def _cmr_granules(short_name: str, version: str | None = None,
                  bbox: tuple[float, ...] | None = None,
                  temporal: tuple[str, str] | None = None,
                  granule_ur: str | None = None,
                  page_size: int = 2000) -> list[dict]:
    """CMR granules.json -> list of feed entries (pages followed)."""
    params: dict = {"short_name": short_name, "page_size": page_size}
    if version:
        params["version"] = version
    if bbox:
        params["bounding_box"] = ",".join(f"{v}" for v in bbox)
    if temporal:
        params["temporal"] = ",".join(temporal)
    if granule_ur:
        params["granule_ur"] = granule_ur
    out: list[dict] = []
    page = 1
    while True:
        params["page_num"] = page
        entries = _http_get(f"{CMR}/granules.json",
                            params=params).json()["feed"]["entry"]
        out.extend(entries)
        if len(entries) < page_size:
            return out
        page += 1


def _data_link(entry: dict, suffix: str) -> str | None:
    for link in entry.get("links", []):
        if (link.get("rel", "").endswith("/data#")
                and link.get("href", "").startswith("http")
                and link["href"].endswith(suffix)):
            return link["href"]
    return None


def _opendap_base(entry: dict) -> tuple[str, str]:
    """(collection_concept_id, opendap base URL) for a granule entry."""
    coll = entry.get("collection_concept_id", "")
    for link in entry.get("links", []):
        if link.get("rel", "").endswith("/service#") and "opendap" in link.get(
                "href", ""):
            return coll, link["href"].rstrip("/")
    return coll, f"{OPENDAP}/{coll}/granules/{entry['title']}"


# --- OPeNDAP ------------------------------------------------------------------
def _dmr_paths(dmr_xml: str) -> dict[str, list[str]]:
    """leaf name -> fully-qualified DAP4 paths in a .dmr.xml document."""
    root = ET.fromstring(dmr_xml)
    paths: dict[str, list[str]] = {}

    def walk(el: ET.Element, prefix: str) -> None:
        for ch in el:
            tag = ch.tag.split("}")[-1]
            name = ch.get("name")
            if tag == "Group":
                walk(ch, f"{prefix}/{name}")
            elif tag in _DAP4_TYPES and name:
                paths.setdefault(name, []).append(f"{prefix}/{name}")

    walk(root, "")
    return paths


def _opendap_vars(base: str, wants: list[tuple[str, str | None]],
                  group_hint: str | None = None) -> dict[str, np.ndarray]:
    """Fetch variables from one granule via cloud OPeNDAP (single .dap.nc4
    request). ``wants``: (leaf_name, slab like "[r0:r1][c0:c1]" or None).
    Returns {leaf: ndarray}. ``group_hint``: prefer FQ paths that share the
    longest prefix with this path (e.g. the grid group of the data var)."""
    dmr = _http_get(f"{base}.dmr.xml").text
    paths = _dmr_paths(dmr)

    def resolve(leaf: str) -> str:
        cands = paths.get(leaf)
        if not cands:
            raise KeyError(f"{leaf} not in DMR of {base} (vars: {sorted(paths)})")
        if group_hint and len(cands) > 1:
            best = max(cands, key=lambda p: len(os.path.commonprefix(
                [p, group_hint])))
            return best
        return cands[0]

    fq = {leaf: resolve(leaf) for leaf, _ in wants}
    ce = ";".join(f"{p}{slab or ''}" for leaf, slab in wants
                  for p in [fq[leaf]])
    r = _http_get(f"{base}.dap.nc4", params={"dap4.ce": ce}, timeout=300)
    r.raise_for_status()
    out: dict[str, np.ndarray] = {}
    with h5py.File(io.BytesIO(r.content), "r") as f:
        for leaf, _ in wants:
            out[leaf] = np.asarray(f[fq[leaf].lstrip("/")])
    return out


def _grid_transform(x: np.ndarray, y: np.ndarray) -> Affine:
    """Geotransform from pixel-centre coordinate arrays (x east, y north)."""
    px_x = float(np.median(np.diff(x)))
    px_y = float(np.median(np.diff(y)))
    return Affine(px_x, 0.0, float(x[0]) - px_x / 2.0,
                  0.0, px_y, float(y[0]) - px_y / 2.0)


# --- NASADEM ------------------------------------------------------------------
def _cell_id(lon: float, lat: float) -> str:
    he = "n" if lat >= 0 else "s"
    ew = "e" if lon >= 0 else "w"
    return f"{he}{abs(int(lat)):02d}{ew}{abs(int(lon)):03d}"


def _cells_in_bbox(bbox: tuple[float, ...]) -> list[tuple[int, int]]:
    w, s, e, n = bbox
    return [(lo, la) for la in range(int(np.floor(s)), int(np.ceil(n)))
            for lo in range(int(np.floor(w)), int(np.ceil(e)))]


def _hgt_dir() -> Path:
    return settings.path("nasadem", "hgt")


def _hgt_path(cell: tuple[int, int]) -> Path:
    # zip member is named e.g. s30w052.hgt — SRTMHGT driver needs the bare id
    return _hgt_dir() / f"{_cell_id(*cell)}.hgt"


def _extract_hgt(zip_path: Path) -> Path | None:
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            if name.endswith(".hgt"):
                dest = _hgt_dir() / Path(name).name
                if not dest.exists() or dest.stat().st_size == 0:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(zf.read(name))
                return dest
    return None


def fetch_nasadem(bbox: tuple[float, ...] = settings.REGION_TRAIN,
                  workers: int = 4) -> list[Path]:
    """NASADEM_HGT.001 tiles covering ``bbox`` -> extracted .hgt paths.

    CMR short_name=NASADEM_HGT -> lp-prod-protected zip -> DATA_DIR/nasadem/.
    Idempotent cache: existing zips/hgts are not re-downloaded.
    """
    entries = _cmr_granules("NASADEM_HGT", version="001", bbox=bbox)
    zips = sorted({u for e in entries
                   if (u := _data_link(e, ".zip"))})
    if not zips:
        raise RuntimeError(f"no NASADEM granules for bbox {bbox}")
    zdir = settings.path("nasadem", "zip")
    with ThreadPoolExecutor(max_workers=workers) as ex:
        paths = list(ex.map(
            lambda u: _download(u, zdir / u.rsplit("/", 1)[-1]), zips))
    out = []
    for z in paths:
        h = _extract_hgt(z)
        if h is not None:
            out.append(h)
    return sorted(out)


def _ensure_hgt(cell: tuple[int, int], keep: bool = True,
                tmp: Path | None = None) -> tuple[Path, Path]:
    """(hgt_path, zip_path) for one 1 deg cell; downloads its zip on miss."""
    h = _hgt_path(cell)
    if h.exists() and h.stat().st_size > 0:
        return h, settings.path("nasadem", "zip") / (
            f"NASADEM_HGT_{_cell_id(*cell)}.zip")
    entries = _cmr_granules("NASADEM_HGT", version="001",
                            granule_ur=f"NASADEM_HGT_{_cell_id(*cell)}")
    urls = [u for e in entries if (u := _data_link(e, ".zip"))]
    if not urls:
        raise RuntimeError(f"no NASADEM granule for cell {cell}")
    zdir = tmp or settings.path("nasadem", "zip")
    z = _download(urls[0], zdir / urls[0].rsplit("/", 1)[-1])
    hgt = _extract_hgt(z)
    if hgt is None:
        raise RuntimeError(f"no .hgt member in {z}")
    return hgt, z


# --- terrain math --------------------------------------------------------------
def _horn_grad(dem: np.ndarray, dx, dy) -> tuple[np.ndarray, np.ndarray]:
    """(dz/dx_east, dz/dy_south) via Horn 3x3 — same neighbour rule as
    tp.terrain._horn: NaN centre -> NaN result; NaN neighbour -> centre."""
    h, w = dem.shape
    c = dem.astype(np.float64)
    pad = np.pad(c, 1, constant_values=np.nan)

    def nb(r0: int, c0: int) -> np.ndarray:
        v = pad[r0:r0 + h, c0:c0 + w]
        return np.where(np.isfinite(v), v, c)

    z1, z2, z3 = nb(0, 0), nb(0, 1), nb(0, 2)
    z4, z6 = nb(1, 0), nb(1, 2)
    z7, z8, z9 = nb(2, 0), nb(2, 1), nb(2, 2)
    dx = np.asarray(dx, dtype=np.float64)
    dy = np.asarray(dy, dtype=np.float64)
    if dx.ndim:
        dx = dx[:, None]
        dy = dy[:, None]
    dzdx = ((z3 + 2 * z6 + z9) - (z1 + 2 * z4 + z7)) / (8 * dx)
    dzdy = ((z7 + 2 * z8 + z9) - (z1 + 2 * z2 + z3)) / (8 * dy)
    bad = ~np.isfinite(c)
    dzdx[bad] = np.nan
    dzdy[bad] = np.nan
    return dzdx, dzdy


def _terrain_bands(elev: np.ndarray, transform: Affine
                   ) -> dict[str, np.ndarray]:
    """The 6 settings.TERRAIN bands from a 90 m elevation grid (EPSG:4326).

    ``transform`` is the geotransform of ``elev``. Derivatives at edge rows/
    cols use the gdaldem neighbour fallback (nearest valid -> centre), so a
    finite centre always gets a value; NaN centre -> NaN everywhere.
    """
    nrows = elev.shape[0]
    lat = transform.f + (np.arange(nrows) + 0.5) * transform.e
    lat_m, lon_m = _deg_len_m(lat)
    dx = abs(transform.a) * lon_m       # (n,) metres/pixel per row
    dy = abs(transform.e) * lat_m

    dzdx, dzdy = _horn_grad(elev, dx, dy)
    slope = np.degrees(np.arctan(np.hypot(dzdx, dzdy))).astype(np.float32)
    # downhill direction, clockwise from north (GDAL-style aspect)
    aspect = np.arctan2(-dzdx, dzdy)          # atan2(east, north)
    flat = np.hypot(dzdx, dzdy) == 0
    aspect_sin = np.where(flat, 0.0, np.sin(aspect)).astype(np.float32)
    aspect_cos = np.where(flat, 0.0, np.cos(aspect)).astype(np.float32)

    c = np.where(np.isfinite(elev), elev, np.nan)
    pad = np.pad(c, 1, constant_values=np.nan)
    h, w = elev.shape

    def nb(r0: int, c0: int) -> np.ndarray:
        v = pad[r0:r0 + h, c0:c0 + w]
        return np.where(np.isfinite(v), v, c)

    zc = c
    zw, ze = nb(1, 0), nb(1, 2)
    zn, zs = nb(0, 1), nb(2, 1)
    # Laplacian of elevation, units 1/100 m (Zevenbergen-Thorne style:
    # second finite differences on metric dx/dy, stored x100).
    lap = ((zw - 2 * zc + ze) / dx[:, None]**2
           + (zn - 2 * zc + zs) / dy[:, None]**2)
    curvature = (lap * 100.0).astype(np.float32)

    valid = np.isfinite(elev)
    vmax = ndi.maximum_filter(np.where(valid, elev, -np.inf), size=5)
    vmin = ndi.minimum_filter(np.where(valid, elev, np.inf), size=5)
    relief = (vmax - vmin).astype(np.float32)
    relief[~valid | ~np.isfinite(relief)] = np.nan

    nan = ~np.isfinite(elev)
    for b in (slope, aspect_sin, aspect_cos, curvature, relief):
        b[nan] = np.nan
    return {"elevation": elev.astype(np.float32), "slope": slope,
            "aspect_sin": aspect_sin, "aspect_cos": aspect_cos,
            "curvature": curvature, "relief": relief}


def _mean_blocks(src: np.ndarray, k: int = SRC_PER_OUT) -> np.ndarray:
    """NaN-aware k x k block mean (all-NaN block -> NaN)."""
    h, w = src.shape
    hb, wb = h // k, w // k
    a = src[:hb * k, :wb * k].reshape(hb, k, wb, k)
    cnt = np.isfinite(a).sum(axis=(1, 3))
    tot = np.where(np.isfinite(a), a, 0.0).sum(axis=(1, 3))
    return np.where(cnt > 0, tot / np.maximum(cnt, 1), np.nan)


def _tile_transform(lon: int, lat: int, res: float, halo: int = 0) -> Affine:
    return Affine(res, 0.0, lon - halo * res,
                  0.0, -res, lat + 1 + halo * res)


def build_terrain(bbox: tuple[float, ...] = settings.REGION_TRAIN,
                  res: float = settings.RES_STATIC_DEG) -> list[Path]:
    """Per-1°-tile GeoTIFFs of the 6 TERRAIN bands at ``res`` degrees.

    Reads the 1" NASADEM tiles with a 2-px (90 m) halo from adjacent cells
    where they exist (downloaded via fetch_nasadem on bbox+halo), 3x3
    block-mean to 90 m, derivatives on the padded grid, halo cropped.
    Output: DATA_DIR/static90/terrain/st90_<cell>.tif — float32, EPSG:4326,
    deflate, tiled; NaN over ocean/void. Cells with no DEM still get an
    all-NaN tile so the index is regular.
    """
    halo_deg = HALO_PX * res
    fetch_nasadem((bbox[0] - halo_deg, bbox[1] - halo_deg,
                   bbox[2] + halo_deg, bbox[3] + halo_deg))
    out_dir = settings.path("static90", "terrain")
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for lon, lat in _cells_in_bbox(bbox):
        src_bounds = (lon - HALO_PX * res, lat - HALO_PX * res,
                      lon + 1 + HALO_PX * res, lat + 1 + HALO_PX * res)
        srcs = []
        for la in range(lat - 1, lat + 2):
            for lo in range(lon - 1, lon + 2):
                p = _hgt_path((lo, la))
                if p.exists() and p.stat().st_size > 0:
                    srcs.append(rasterio.open(str(p)))
        try:
            if srcs:
                arr, _ = rio_merge(srcs, bounds=src_bounds, res=RES_SRC_DEG,
                                   nodata=HGT_NODATA, dtype="float32")
                elev_src = arr[0]
                elev_src[elev_src == HGT_NODATA] = np.nan
            else:
                n = round((1 + 2 * HALO_PX * res) / RES_SRC_DEG)
                elev_src = np.full((n, n), np.nan, np.float32)
        finally:
            for s in srcs:
                s.close()
        elev = _mean_blocks(elev_src, SRC_PER_OUT)
        # merge() may round the halo extent by a source pixel — normalize
        # to exactly n_out + 2*HALO_PX rows/cols (pad NaN / crop).
        n_out = round(1.0 / res)
        want = n_out + 2 * HALO_PX
        if elev.shape[0] < want or elev.shape[1] < want:
            pad = np.full((want, want), np.nan)
            pad[:elev.shape[0], :elev.shape[1]] = elev
            elev = pad
        elev = elev[:want, :want]
        tr = _tile_transform(lon, lat, res, HALO_PX)
        bands = _terrain_bands(elev, tr)
        interior = slice(HALO_PX, elev.shape[0] - HALO_PX)
        tr_out = _tile_transform(lon, lat, res)
        dest = out_dir / f"st90_{_cell_id(lon, lat)}.tif"
        profile = {"driver": "GTiff", "height": n_out, "width": n_out,
                   "count": len(settings.TERRAIN), "dtype": "float32",
                   "crs": "EPSG:4326", "transform": tr_out, "nodata": np.nan,
                   "compress": "deflate", "tiled": True,
                   "blockxsize": 256, "blockysize": 256}
        with rasterio.open(dest, "w", **profile) as dst:
            for i, name in enumerate(settings.TERRAIN, start=1):
                dst.write(bands[name][interior, interior], i)
                dst.set_band_description(i, name)
        written.append(dest)
    return written


# --- land cover ---------------------------------------------------------------
def _grid_common(bbox: tuple[float, ...], res: float
                 ) -> tuple[Affine, tuple[int, int]]:
    """EPSG:4326 grid snapped to ``res`` multiples covering bbox -> (tr,(h,w))."""
    w, s, e, n = bbox
    x0 = np.floor(w / res) * res
    y0 = np.ceil(n / res) * res
    h = int(np.ceil((y0 - np.floor(s / res) * res) / res))
    wd = int(np.ceil((np.ceil(e / res) * res - x0) / res))
    return Affine(res, 0, x0, 0, -res, y0), (h, wd)


def fetch_landcover(year: int = settings.LANDCOVER_YEAR,
                    bbox: tuple[float, ...] = settings.REGION_TRAIN) -> Path:
    """MCD12Q1.061 LC_Type1 (IGBP) reprojected nearest to EPSG:4326 at
    ~500 m covering bbox -> DATA_DIR/static90/landcover_mcd12q1_<year>.tif.

    Cloud OPeNDAP returns netCDF4 for the HDF4 granules; XDim/YDim are the
    sinusoidal pixel-centre coords used to build the source transform.
    """
    entries = _cmr_granules("MCD12Q1", version="061", bbox=bbox,
                            temporal=(f"{year}-01-01", f"{year}-12-31"))
    if not entries:
        raise RuntimeError(f"no MCD12Q1 granules for {year} bbox {bbox}")
    seen: dict[str, dict] = {}
    for e in entries:
        tile = e["title"].split(".")[2]          # h13v11
        seen.setdefault(tile, e)
    tr, (h, w) = _grid_common(bbox, LC_RES_DEG)
    dest = np.full((h, w), 255, np.uint8)
    for tile, e in sorted(seen.items()):
        _, base = _opendap_base(e)
        v = _opendap_vars(base, [("LC_Type1", None), ("XDim", None),
                                 ("YDim", None)])
        arr = v["LC_Type1"].astype(np.uint8)
        src_tr = _grid_transform(v["XDim"], v["YDim"])
        sb = transform_bounds(SINUSOIDAL, CRS.from_epsg(4326),
                              *array_bounds(arr.shape[0], arr.shape[1],
                                            src_tr))
        w0 = int(np.floor((sb[0] - tr.c) / tr.a))
        w1 = int(np.ceil((sb[2] - tr.c) / tr.a))
        r0 = int(np.floor((tr.f - sb[3]) / abs(tr.e)))
        r1 = int(np.ceil((tr.f - sb[1]) / abs(tr.e)))
        r0c, r1c = max(r0, 0), min(r1, h)
        w0c, w1c = max(w0, 0), min(w1, w)
        if r1c <= r0c or w1c <= w0c:
            continue
        view = dest[r0c:r1c, w0c:w1c]
        sub_tr = tr @ Affine.translation(w0c, r0c)
        reproject(arr, view, src_transform=src_tr, src_crs=SINUSOIDAL,
                  src_nodata=255, dst_transform=sub_tr,
                  dst_crs=CRS.from_epsg(4326), dst_nodata=255,
                  resampling=Resampling.nearest)
    out = settings.path("static90", f"landcover_mcd12q1_{year}.tif")
    profile = {"driver": "GTiff", "height": h, "width": w, "count": 1,
               "dtype": "uint8", "crs": "EPSG:4326", "transform": tr,
               "nodata": 255, "compress": "deflate", "tiled": True,
               "blockxsize": 256, "blockysize": 256}
    with rasterio.open(out, "w", **profile) as ds:
        ds.write(dest, 1)
        ds.set_band_description(1, "landcover")
    return out


# --- NDVI (VNP13C2 global CMG) --------------------------------------------------
def _ndvi_scaled(h5: Path) -> np.ndarray:
    """NDVI float [-1,1] from a VNP13C2 .h5: scale 1e-4, fill/reliability -> NaN."""
    with h5py.File(h5, "r") as f:
        raw = f[NDVI_SDS][()].astype(np.float64)
        rel = f[NDVI_REL_SDS][()]
    ndvi = raw * 1e-4
    bad = (raw < -10000) | (raw > 10000) | (rel < 0) | (rel > 1)
    ndvi[bad] = np.nan
    return ndvi


def fetch_ndvi(year: int = settings.NDVI_YEAR,
               bbox: tuple[float, ...] = settings.REGION_TRAIN) -> Path:
    """Annual mean NDVI -> DATA_DIR/static90/ndvi_vnp13c2_<year>.tif.

    VNP13C2.002 monthly global 0.05 deg CMG (geographic grid, 3600x7200);
    mean of the 12 months of ``year``. ONE global grid serves both the
    S+SE stack and global point mode — ``bbox`` is accepted for API
    symmetry but the output is always the global grid (crop happens at
    sampling time). MOD13A3.061 was the first choice; its collection has
    no OPeNDAP endpoint (checked once per call) and rasterio here has no
    HDF4 driver.
    """
    out = settings.path("static90", f"ndvi_vnp13c2_{year}.tif")
    if out.exists() and out.stat().st_size > 0:
        return out
    # brief: try MOD13A3 OPeNDAP anyway once — expected 404, then VNP13C2.
    try:
        e = _cmr_granules("MOD13A3", version="061", bbox=bbox,
                          temporal=(f"{year}-01-01", f"{year}-01-31"))[0]
        base = _opendap_base(e)[1]
        r = _http_get(f"{base}.dmr.xml", timeout=60, tries=1)
        if r.status_code == 200:
            print("[ndvi] MOD13A3 OPeNDAP unexpectedly alive — "
                  "still using VNP13C2 (global CMG grid)", file=sys.stderr)
        else:
            print(f"[ndvi] MOD13A3 OPeNDAP -> HTTP {r.status_code}; "
                  "using VNP13C2.002", file=sys.stderr)
    except (IndexError, KeyError, RuntimeError,
            requests.RequestException) as exc:
        print(f"[ndvi] MOD13A3 OPeNDAP probe failed ({exc}); "
              "using VNP13C2.002", file=sys.stderr)

    entries = _cmr_granules("VNP13C2", version="002",
                            temporal=(f"{year}-01-01", f"{year}-12-31"))
    urls = sorted({u for e in entries if (u := _data_link(e, ".h5"))})
    if not urls:
        raise RuntimeError(f"no VNP13C2 granules for {year}")
    h5dir = settings.path("vnp13c2")
    with ThreadPoolExecutor(max_workers=4) as ex:
        files = list(ex.map(
            lambda u: _download(u, h5dir / u.rsplit("/", 1)[-1]), urls))
    stack = np.stack([_ndvi_scaled(p) for p in files])      # (12,3600,7200)
    cnt = np.isfinite(stack).sum(axis=0)
    mean = np.where(cnt > 0, np.where(np.isfinite(stack), stack, 0).sum(0)
                  / np.maximum(cnt, 1), np.nan).astype(np.float32)
    tr = Affine(NDVI_RES_DEG, 0, -180.0, 0, -NDVI_RES_DEG, 90.0)
    profile = {"driver": "GTiff", "height": mean.shape[0],
               "width": mean.shape[1], "count": 1, "dtype": "float32",
               "crs": "EPSG:4326", "transform": tr, "nodata": np.nan,
               "compress": "deflate", "tiled": True,
               "blockxsize": 256, "blockysize": 256}
    with rasterio.open(out, "w", **profile) as ds:
        ds.write(mean, 1)
        ds.set_band_description(1, "ndvi")
    return out


# --- HAND ---------------------------------------------------------------------
def _hand_path() -> Path | None:
    for p in (LOCAL_HAND, SHARED_HAND):
        if p.exists() and p.stat().st_size > 0:
            return p
    return None


# --- index / vrt ---------------------------------------------------------------
def _index_path() -> Path:
    return settings.path("static90", "index.json")


def _write_index(bbox, res, tiles: list[Path], landcover: Path | None,
                 ndvi: Path | None, hand: Path | None) -> Path:
    idx = {
        "version": 1, "created": dt.datetime.now(dt.UTC).isoformat(),
        "bbox": list(bbox), "res": res, "bands": BANDS_ALL,
        "terrain_bands": settings.TERRAIN,
        "terrain": {p.stem.replace("st90_", ""): f"terrain/{p.name}"
                    for p in tiles},
        "landcover": landcover.name if landcover else None,
        "ndvi": ndvi.name if ndvi else None,
        "hand": str(hand) if hand else None,
    }
    p = _index_path()
    p.write_text(json.dumps(idx, indent=2) + "\n")
    return p


def _write_vrt(idx: dict) -> Path | None:
    """static90.vrt — all bands on the degree-aligned terrain grid."""
    try:
        root = _index_path().parent
        tiles = idx["terrain"]
        if not tiles:
            return None
        lons = sorted({_cell_lonlat(k)[0] for k in tiles})
        lats = sorted({_cell_lonlat(k)[1] for k in tiles})
        res = idx["res"]
        x0, y1 = min(lons), max(lats) + 1
        n_w = round((max(lons) + 1 - x0) / res)
        n_h = round((y1 - min(lats)) / res)

        def esc(s: str) -> str:
            return (s.replace("&", "&amp;").replace("<", "&lt;")
                     .replace(">", "&gt;"))

        geo = (f"{x0!r}, {res!r}, 0.0, {y1!r}, 0.0, {-res!r}")
        lines = [f'<VRTDataset rasterXSize="{n_w}" rasterYSize="{n_h}">',
                 "  <SRS dataAxisToSRSAxisMapping=\"2,1\">EPSG:4326</SRS>",
                 f"  <GeoTransform>{geo}</GeoTransform>"]

        def simple_src(band: int, src: str, srcrect: str, dstrect: str,
                       nodata: str = "nan") -> str:
            return (f'    <SimpleSource><SourceFilename '
                    f'relativeToVRT="1">{esc(src)}</SourceFilename>'
                    f"<SourceBand>{band}</SourceBand>"
                    f"<SrcRect {srcrect}/><DstRect {dstrect}/>"
                    f"<NODATA>{nodata}</NODATA></SimpleSource>")

        for i in range(len(settings.TERRAIN)):
            lines.append(f'  <VRTRasterBand dataType="Float32" band="{i+1}">'
                         "<NoDataValue>nan</NoDataValue>")
            for cell, rel in sorted(tiles.items()):
                lo, la = _cell_lonlat(cell)
                xoff = round((lo - x0) / res)
                yoff = round((y1 - (la + 1)) / res)
                n = round(1.0 / res)
                lines.append(simple_src(
                    i + 1, rel,
                    f'xOff="0" yOff="0" xSize="{n}" ySize="{n}"',
                    f'xOff="{xoff}" yOff="{yoff}" xSize="{n}" ySize="{n}"'))
            lines.append("  </VRTRasterBand>")
        for name in ("landcover", "ndvi"):
            rel = idx.get(name)
            if not rel:
                continue
            with rasterio.open(root / rel) as ds:
                tr, hh, ww = ds.transform, ds.height, ds.width
                bx0 = (tr.c - x0) / res
                by0 = (y1 - tr.f) / res
                bx1 = bx0 + ww * tr.a / res
                by1 = by0 + ww * abs(tr.e) / res
            band = len(settings.TERRAIN) + (1 if name == "landcover" else 2)
            lines.append(f'  <VRTRasterBand dataType="Float32" band="{band}">'
                         "<NoDataValue>nan</NoDataValue>")
            nod = "255" if name == "landcover" else "nan"
            lines.append(simple_src(
                1, rel,
                f'xOff="0" yOff="0" xSize="{ww}" ySize="{hh}"',
                f'xOff="{bx0:.6f}" yOff="{by0:.6f}" xSize="{bx1 - bx0:.6f}" '
                f'ySize="{by1 - by0:.6f}"', nodata=nod))
            lines.append("  </VRTRasterBand>")
        hand = idx.get("hand")
        if hand:
            with rasterio.open(hand) as ds:
                tr, hh, ww = ds.transform, ds.height, ds.width
                bx0 = (tr.c - x0) / res
                by0 = (y1 - tr.f) / res
                bx1 = bx0 + ww * tr.a / res
                by1 = by0 + ww * abs(tr.e) / res
            src = os.path.relpath(hand, root)
            lines.append(f'  <VRTRasterBand dataType="Float32" '
                         f'band="{len(BANDS_ALL)}">'
                         "<NoDataValue>nan</NoDataValue>")
            lines.append(simple_src(
                1, src,
                f'xOff="0" yOff="0" xSize="{ww}" ySize="{hh}"',
                f'xOff="{bx0:.6f}" yOff="{by0:.6f}" xSize="{bx1 - bx0:.6f}" '
                f'ySize="{by1 - by0:.6f}"'))
            lines.append("  </VRTRasterBand>")
        lines.append("</VRTDataset>")
        out = _index_path().with_name("static90.vrt")
        out.write_text("\n".join(lines) + "\n")
        return out
    except Exception as exc:  # noqa: BLE001 — VRT is best-effort
        print(f"[vrt] skipped: {exc}", file=sys.stderr)
        return None


def _cell_lonlat(cell: str) -> tuple[int, int]:
    """'s30w052' -> (lon=-52, lat=-30)."""
    lat = int(cell[1:3]) * (1 if cell[0] == "n" else -1)
    lon = int(cell[4:7]) * (1 if cell[3] == "e" else -1)
    return lon, lat


def _load_index() -> dict:
    p = _index_path()
    if not p.exists():
        raise RuntimeError("static90 index missing — run build_static() "
                           "or `python -m ml.local.sources_static all` first")
    return json.loads(p.read_text())


def _terrain_tile_path(cell: tuple[int, int], idx: dict) -> Path | None:
    rel = idx["terrain"].get(_cell_id(*cell))
    return (_index_path().parent / rel) if rel else None


# --- sampling -------------------------------------------------------------------
def _as_xy(lon, lat) -> tuple[np.ndarray, np.ndarray]:
    lo = np.atleast_1d(np.asarray(lon, dtype=np.float64))
    la = np.atleast_1d(np.asarray(lat, dtype=np.float64))
    if lo.shape != la.shape:
        raise ValueError("lon/lat shapes differ")
    return lo, la


def _rc(ds: rasterio.DatasetReader, lon: np.ndarray, lat: np.ndarray
        ) -> tuple[np.ndarray, np.ndarray]:
    """Vectorized pixel row/col (floor) for point arrays."""
    inv = ~ds.transform
    cols_f = inv.a * lon + inv.b * lat + inv.c
    rows_f = inv.d * lon + inv.e * lat + inv.f
    return np.floor(rows_f).astype(np.int64), np.floor(cols_f).astype(np.int64)


def _sample_raster(ds: rasterio.DatasetReader, band: int, lon: np.ndarray,
                   lat: np.ndarray, scale_nan: tuple[float, ...] = ()
                   ) -> np.ndarray:
    """Nearest-pixel read of one band for point arrays (vectorized)."""
    rows, cols = _rc(ds, lon, lat)
    ok = (rows >= 0) & (rows < ds.height) & (cols >= 0) & (cols < ds.width)
    vals = np.full(lon.shape, np.nan, np.float64)
    if ok.any():
        arr = ds.read(band).astype(np.float64)
        vals[ok] = arr[rows[ok], cols[ok]]
        for fill in scale_nan:
            vals[vals == fill] = np.nan
    return vals


def sample_static(lon, lat) -> pd.DataFrame:
    """Feature columns at point(s) — settings.TERRAIN + LANDCOVER + hand_m.

    Nearest pixel; NaN outside coverage/void. Grouped per 1 deg tile with
    one rasterio.open per tile — fast for ~100k points.
    """
    idx = _load_index()
    root = _index_path().parent
    lo, la = _as_xy(lon, lat)
    n = lo.size
    out = {b: np.full(n, np.nan, np.float64) for b in BANDS_ALL}

    cells = np.floor(lo).astype(int), np.floor(la).astype(int)
    keys = cells[0] * 100000 + cells[1]
    for key in np.unique(keys):
        sel = keys == key
        clon, clat = cells[0][sel][0], cells[1][sel][0]
        p = _terrain_tile_path((clon, clat), idx)
        if p is None or not p.exists():
            continue
        with rasterio.open(p) as ds:
            arr = ds.read().astype(np.float64)      # (6,h,w) one open
            rows, cols = _rc(ds, lo[sel], la[sel])
            ok = ((rows >= 0) & (rows < ds.height)
                  & (cols >= 0) & (cols < ds.width))
            for i, b in enumerate(settings.TERRAIN):
                v = np.full(sel.sum(), np.nan)
                v[ok] = arr[i, rows[ok], cols[ok]]
                out[b][sel] = v

    for name, band in (("landcover", "landcover"), ("ndvi", "ndvi")):
        rel = idx.get(name)
        if not rel:
            continue
        with rasterio.open(root / rel) as ds:
            fills = (255.0,) if name == "landcover" else ()
            out[band] = _sample_raster(ds, 1, lo, la, scale_nan=fills)

    hand = idx.get("hand")
    if hand:
        with rasterio.open(hand) as ds:
            out["hand_m"] = _sample_raster(ds, 1, lo, la)

    df = pd.DataFrame(out)
    df.insert(0, "lon", lo)
    df.insert(1, "lat", la)
    return df


def static_on_grid(transform: Affine, shape: tuple[int, int],
                   bands: list[str] | None = None
                   ) -> dict[str, np.ndarray]:
    """Bands resampled onto an arbitrary EPSG:4326 grid window.

    ``transform``/``shape`` describe the DESTINATION window (degrees).
    Terrain/ndvi/hand use average resampling, landcover nearest.
    Reads only source windows overlapping the destination — no
    full-domain arrays (90 m tiles stay per-1 deg).
    """
    idx = _load_index()
    root = _index_path().parent
    bands = bands or BANDS_ALL
    h, w = shape
    west, north = transform.c, transform.f
    resx, resy = abs(transform.a), abs(transform.e)
    east, south = west + w * resx, north - h * resy
    out = {b: np.full(shape, np.nan, np.float32) for b in bands}

    def src_window(ds, bnds):
        """intersection window (src_win, dst_win) or None."""
        sb = rasterio.transform.array_bounds(ds.height, ds.width,
                                             ds.transform)
        iw = (max(bnds[0], sb[0]), max(bnds[1], sb[1]),
              min(bnds[2], sb[2]), min(bnds[3], sb[3]))
        if iw[2] <= iw[0] or iw[3] <= iw[1]:
            return None
        sw = ds.window(*iw).round_offsets().round_lengths()
        c0 = int(np.floor((iw[0] - west) / resx))
        c1 = int(np.ceil((iw[2] - west) / resx))
        r0 = int(np.floor((north - iw[3]) / resy))
        r1 = int(np.ceil((north - iw[1]) / resy))
        c0, r0 = max(c0, 0), max(r0, 0)
        c1, r1 = min(c1, w), min(r1, h)
        if c1 <= c0 or r1 <= r0:
            return None
        return sw, Window(c0, r0, c1 - c0, r1 - r0)

    def fill(src_path, dst_names: list[tuple[int, str]], resampling,
             src_nodata=np.nan):
        with rasterio.open(src_path) as ds:
            pair = src_window(ds, (west, south, east, north))
            if pair is None:
                return
            sw, dw = pair
            src = ds.read([i for i, _ in dst_names], window=sw
                          ).astype(np.float32)
            dst = np.stack([out[b][int(dw.row_off):int(dw.row_off + dw.height),
                                  int(dw.col_off):int(dw.col_off + dw.width)]
                            for _, b in dst_names])
            sub_tr = transform @ Affine.translation(dw.col_off, dw.row_off)
            reproject(src, dst,
                      src_transform=ds.window_transform(sw),
                      src_crs=ds.crs, src_nodata=src_nodata,
                      dst_transform=sub_tr, dst_crs=CRS.from_epsg(4326),
                      dst_nodata=np.nan, resampling=resampling)
            for j, (_, b) in enumerate(dst_names):
                out[b][int(dw.row_off):int(dw.row_off + dw.height),
                       int(dw.col_off):int(dw.col_off + dw.width)] = dst[j]

    tb = [b for b in bands if b in settings.TERRAIN]
    if tb:
        c0, c1 = int(np.floor(west)), int(np.ceil(east))
        r0, r1 = int(np.floor(south)), int(np.ceil(north))
        for la in range(r0, r1):
            for lo_ in range(c0, c1):
                p = _terrain_tile_path((lo_, la), idx)
                if p is None or not p.exists():
                    continue
                fill(p, [(settings.TERRAIN.index(b) + 1, b) for b in tb],
                     Resampling.average)
    for name in ("landcover", "ndvi"):
        if name not in bands:
            continue
        rel = idx.get(name)
        if not rel:
            continue
        rs = Resampling.nearest if name == "landcover" else Resampling.average
        fill(root / rel, [(1, name)], rs,
             src_nodata=255.0 if name == "landcover" else np.nan)
    if "hand_m" in bands and idx.get("hand"):
        fill(idx["hand"], [(1, "hand_m")], Resampling.average)
    if "landcover" in out:
        out["landcover"][out["landcover"] == 255] = np.nan
    return out


def is_land(lon, lat) -> np.ndarray:
    """True where elevation is finite and landcover is a valid non-water
    IGBP class (excludes 0/unclassified/255 fill and 17 water)."""
    lo, la = _as_xy(lon, lat)
    df = sample_static(lo, la)
    lc = df["landcover"].to_numpy()
    elev = df["elevation"].to_numpy()
    ok = np.isfinite(elev) & np.isfinite(lc)
    lc_i = np.where(np.isfinite(lc), lc, -1).astype(int)
    return ok & ~np.isin(lc_i, list(LC_WATER))


def random_land_points(n: int, bbox: tuple[float, ...] = settings.REGION_TRAIN,
                       rng: np.random.Generator | int | None = None
                       ) -> tuple[np.ndarray, np.ndarray]:
    """(lon, lat) uniform over land inside bbox (rejection via is_land)."""
    g = rng if isinstance(rng, np.random.Generator) else np.random.default_rng(rng)
    w, s, e, nn = bbox
    got_lo: list[np.ndarray] = []
    got_la: list[np.ndarray] = []
    total = 0
    for _ in range(200):
        need = n - total
        if need <= 0:
            break
        m = max(need * 3, 4096)
        lo = g.uniform(w, e, m)
        la = g.uniform(s, nn, m)
        ok = is_land(lo, la)
        got_lo.append(lo[ok])
        got_la.append(la[ok])
        total += int(ok.sum())
    if total < n:
        raise RuntimeError(f"only {total}/{n} land points found in {bbox} "
                           "after 200 batches — bbox may be mostly ocean")
    return np.concatenate(got_lo)[:n], np.concatenate(got_la)[:n]


# --- point mode (3b, global) -----------------------------------------------------
def _points_cache_path() -> Path:
    return settings.path(POINT_CACHE_NAME)


def _load_points() -> pd.DataFrame:
    p = _points_cache_path()
    if not p.exists():
        return pd.DataFrame(columns=["lon_r", "lat_r", "src"] + BANDS_ALL)
    try:
        return pd.read_parquet(p)
    except Exception:  # noqa: BLE001 — pickle payload when no parquet engine
        return pd.read_pickle(p)


def _save_points(df: pd.DataFrame) -> None:
    p = _points_cache_path()
    try:
        df.to_parquet(p, index=False)
    except ImportError:             # no parquet engine in this env
        df.to_pickle(p)


def _mcd12q1_tiles_year(year: int) -> dict[str, dict]:
    """{hvv: granule entry} for MCD12Q1.061 of ``year`` (global, one CMR call)."""
    entries = _cmr_granules("MCD12Q1", version="061",
                            temporal=(f"{year}-01-01", f"{year}-12-31"))
    out: dict[str, dict] = {}
    for e in entries:
        tile = e["title"].split(".")[2]
        out.setdefault(tile, e)
    return out


def _modis_cell(lon: np.ndarray, lat: np.ndarray
                ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """lon/lat -> MODIS sinusoidal (tile h, tile v, pixel row, pixel col)
    on the MCD12Q1 2400x2400 grid."""
    xs, ys = rasterio.warp.transform(CRS.from_epsg(4326), SINUSOIDAL,
                                     list(lon), list(lat))
    x, y = np.asarray(xs), np.asarray(ys)
    h = np.floor((x - MODIS_X0) / MODIS_TILE_M).astype(int)
    v = np.floor((MODIS_Y0 - y) / MODIS_TILE_M).astype(int)
    col = np.floor((x - (MODIS_X0 + h * MODIS_TILE_M)) / LC_PIX_M).astype(int)
    row = np.floor(((MODIS_Y0 - v * MODIS_TILE_M) - y) / LC_PIX_M).astype(int)
    ok = (h >= 0) & (h <= 35) & (v >= 0) & (v <= 17) & np.isfinite(x)
    return np.where(ok, h, -1), np.where(ok, v, -1), np.clip(
        row, 0, 2399), np.clip(col, 0, 2399)


def _terrain_at_point(dem: np.ndarray, lon0: int, lat0: int, lon: float,
                      lat: float, res: float = settings.RES_STATIC_DEG
                      ) -> dict[str, float]:
    """The 6 terrain bands at one point, from a 1-deg NASADEM .hgt array.

    Only a small window (the point's 90 m cell +/- HALO_PX) is reduced —
    identical math to build_terrain, same 90 m grid.
    """
    c = int(np.floor((lon - lon0) / res))
    r = int(np.floor((lat0 + 1 - lat) / res))
    c0 = max(3 * (c - HALO_PX), 0)
    c1 = min(3 * (c + 1 + HALO_PX), dem.shape[1])
    r0 = max(3 * (r - HALO_PX), 0)
    r1 = min(3 * (r + 1 + HALO_PX), dem.shape[0])
    sub = dem[r0:r1, c0:c1].astype(np.float64)
    sub[sub == HGT_NODATA] = np.nan
    elev = _mean_blocks(sub, SRC_PER_OUT)
    tr = Affine(res, 0, lon0 + (c0 // 3) * res, 0, -res,
                lat0 + 1 - (r0 // 3) * res)
    bands = _terrain_bands(elev, tr)
    ri = int(r - r0 // 3)
    ci = int(c - c0 // 3)
    return {b: float(bands[b][ri, ci]) for b in settings.TERRAIN}


def _points_terrain(lon: np.ndarray, lat: np.ndarray, keep_dem: bool
                    ) -> dict[str, np.ndarray]:
    """Terrain bands for point arrays, grouped per 1 deg NASADEM cell."""
    out = {b: np.full(lon.shape, np.nan) for b in settings.TERRAIN}
    cells = np.floor(lon).astype(int), np.floor(lat).astype(int)
    keys = cells[0] * 100000 + cells[1]
    for key in np.unique(keys):
        sel = np.where(keys == key)[0]
        clon, clat = int(cells[0][sel[0]]), int(cells[1][sel[0]])
        try:
            hgt, zip_path = _ensure_hgt((clon, clat))
        except RuntimeError:
            continue                        # e.g. ocean cell, no granule
        try:
            with rasterio.open(hgt) as ds:
                dem = ds.read(1)
            for i in sel:
                b = _terrain_at_point(dem, clon, clat, lon[i], lat[i])
                for name, v in b.items():
                    out[name][i] = v
        finally:
            if not keep_dem:
                hgt.unlink(missing_ok=True)
                zip_path.unlink(missing_ok=True)
    return out


def _points_landcover(lon: np.ndarray, lat: np.ndarray,
                      year: int = settings.LANDCOVER_YEAR) -> np.ndarray:
    """LC_Type1 at points — one OPeNDAP hyperslab per MODIS tile."""
    out = np.full(lon.shape, np.nan)
    h, v, row, col = _modis_cell(lon, lat)
    ok = h >= 0
    if not ok.any():
        return out
    tiles = _mcd12q1_tiles_year(year)
    keys = h * 100 + v
    for key in np.unique(keys[ok]):
        sel = np.where((keys == key) & ok)[0]
        tile = f"h{h[sel][0]:02d}v{v[sel][0]:02d}"
        e = tiles.get(tile)
        if e is None:
            continue
        _, base = _opendap_base(e)
        r0, r1 = int(row[sel].min()), int(row[sel].max())
        c0, c1 = int(col[sel].min()), int(col[sel].max())
        slab = f"[{r0}:{r1}][{c0}:{c1}]"
        try:
            d = _opendap_vars(base, [("LC_Type1", slab)])
        except (KeyError, RuntimeError, requests.RequestException,
                ET.ParseError):
            d = _opendap_vars(base, [("LC_Type1", None)])
        arr = d["LC_Type1"]
        rr = row[sel] - r0
        cc = col[sel] - c0
        inb = (rr >= 0) & (rr < arr.shape[0]) & (cc >= 0) & (cc < arr.shape[1])
        vals = np.full(sel.size, np.nan)
        vals[inb] = arr[rr[inb], cc[inb]].astype(float)
        vals[~np.isin(vals, np.arange(1, 18))] = np.nan
        out[sel] = vals
    return out


def _points_ndvi_hand(lon: np.ndarray, lat: np.ndarray,
                      year: int = settings.NDVI_YEAR
                      ) -> tuple[np.ndarray, np.ndarray]:
    ndvi = np.full(lon.shape, np.nan)
    ndvi_path = settings.path("static90", f"ndvi_vnp13c2_{year}.tif")
    if not ndvi_path.exists():
        fetch_ndvi(year)
    with rasterio.open(ndvi_path) as ds:
        ndvi = _sample_raster(ds, 1, lon, lat)
    hand = np.full(lon.shape, np.nan)
    hp = _hand_path()
    if hp is not None:
        with rasterio.open(hp) as ds:
            hand = _sample_raster(ds, 1, lon, lat)
    return ndvi, hand


def sample_static_points(lon, lat, keep_dem: bool = False) -> pd.DataFrame:
    """sample_static columns for arbitrary global points (3b GLC mode).

    Per-1 deg NASADEM cell: zip downloaded, terrain computed only for a
    small window around each point, zip+hgt deleted unless ``keep_dem``.
    Land cover via MCD12Q1 OPeNDAP hyperslabs (one request per MODIS tile
    covering just the points' index window). NDVI = VNP13C2 annual mean
    global grid (same file as region mode). HAND NaN outside Now domain.
    Results cached in DATA_DIR/static_points.parquet keyed by lon/lat
    rounded to 5 decimals (pickle payload — no parquet engine installed).
    """
    lo, la = _as_xy(lon, lat)
    n = lo.size
    keys = np.round(lo, POINT_KEY_DECIMALS), np.round(la, POINT_KEY_DECIMALS)
    cache = _load_points()
    cached: dict[tuple[float, float], pd.Series] = {}
    if len(cache):
        for _, r in cache.iterrows():
            cached[(r["lon_r"], r["lat_r"])] = r
    out = {b: np.full(n, np.nan) for b in BANDS_ALL}
    miss = np.array([(keys[0][i], keys[1][i]) not in cached for i in range(n)])
    for i in np.where(~miss)[0]:
        r = cached[(keys[0][i], keys[1][i])]
        for b in BANDS_ALL:
            out[b][i] = r[b]
    if miss.any():
        m_lo, m_la = lo[miss], la[miss]
        terr = _points_terrain(m_lo, m_la, keep_dem)
        for b in settings.TERRAIN:
            out[b][miss] = terr[b]
        out["landcover"][miss] = _points_landcover(m_lo, m_la)
        ndvi, hand = _points_ndvi_hand(m_lo, m_la)
        out["ndvi"][miss] = ndvi
        out["hand_m"][miss] = hand
        new = pd.DataFrame({
            "lon_r": keys[0][miss], "lat_r": keys[1][miss],
            "src": POINTS_VERSION,
            **{b: out[b][miss] for b in BANDS_ALL}})
        cache = pd.concat([cache, new], ignore_index=True)
        cache = cache.drop_duplicates(["lon_r", "lat_r"], keep="last")
        _save_points(cache)
    df = pd.DataFrame(out)
    df.insert(0, "lon", lo)
    df.insert(1, "lat", la)
    return df


# --- build ---------------------------------------------------------------------
def build_static(bbox: tuple[float, ...] = settings.REGION_TRAIN,
                 res: float = settings.RES_STATIC_DEG) -> Path:
    """Full stack over bbox -> DATA_DIR/static90/index.json (+ static90.vrt).

    Runs fetch_nasadem -> build_terrain -> fetch_landcover -> fetch_ndvi,
    links hand_m if present, writes index.json and a VRT over the terrain
    tiles + coarse rasters.
    """
    tiles = build_terrain(bbox, res)
    landcover = fetch_landcover(settings.LANDCOVER_YEAR, bbox)
    ndvi = fetch_ndvi(settings.NDVI_YEAR, bbox)
    hand = _hand_path()
    idx_path = _write_index(bbox, res, tiles, landcover, ndvi, hand)
    idx = json.loads(idx_path.read_text())
    _write_vrt(idx)
    return idx_path


# --- CLI -------------------------------------------------------------------------
def _parse_bbox(s: list[str]) -> tuple[float, float, float, float]:
    w, s_, e, n = (float(v) for v in s)
    return w, s_, e, n


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ml.local.sources_static")
    ap.add_argument("stage",
                    choices=["nasadem", "terrain", "landcover", "ndvi",
                             "all", "points"])
    ap.add_argument("--bbox", nargs=4, metavar=("W", "S", "E", "N"),
                    default=[str(v) for v in settings.REGION_TRAIN])
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--year", type=int, default=None)
    ap.add_argument("--keep-dem", action="store_true")
    ap.add_argument("--lon", nargs="*", type=float, default=[])
    ap.add_argument("--lat", nargs="*", type=float, default=[])
    args = ap.parse_args(argv)
    bbox = _parse_bbox(args.bbox)
    t0 = time.time()
    if args.stage == "nasadem":
        out = fetch_nasadem(bbox, args.workers)
    elif args.stage == "terrain":
        out = build_terrain(bbox)
    elif args.stage == "landcover":
        out = fetch_landcover(args.year or settings.LANDCOVER_YEAR, bbox)
    elif args.stage == "ndvi":
        out = fetch_ndvi(args.year or settings.NDVI_YEAR, bbox)
    elif args.stage == "points":
        out = sample_static_points(args.lon, args.lat, args.keep_dem)
        print(out.to_string())
    else:
        out = build_static(bbox)
    if args.stage != "points":
        paths = out if isinstance(out, list) else [out]
        for p in paths:
            print(p, p.stat().st_size if Path(p).exists() else "")
    print(f"[{args.stage}] {time.time() - t0:.1f}s", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
