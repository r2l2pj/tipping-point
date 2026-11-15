"""Radar flood candidates for the Now domain from NISAR L2 GCOV (unit R2, L27).

Pipeline per pre/post pair (same track + direction + frame, see
fetch.nisar.pair_latest_pass):

1. Read ONLY the co-pol covariance term (HHHH, else VVVV) of each granule,
   and only the HDF5 chunks that intersect the Now domain. Chunk byte ranges
   come from the HDF5 chunk index; adjacent ranges are coalesced and fetched
   with parallel HTTP range reads against the signed S3/CloudFront URL, then
   inflated + unshuffled here and block-averaged (linear power, i.e.
   multilooked) onto a snapped `res` metre grid in the granule's UTM CRS.
   Peak memory is one fetch batch plus the target grid — never a full frame.
2. Run the wa_2025 method unchanged: tp.flood_mask.flood_mask (pre->post
   change detection, Otsu/-15 dB water threshold, pre-water excluded, HAND
   gate, bright-flood layer, MMU). The same call with pre/post swapped gives
   the receded-water area.
3. Label candidates, sum GPW v4.11 people per polygon (pixel-centre sampling
   of the GPW count grid, area-weighted), polygonize to EPSG:4326.

Outputs are flood CANDIDATES (status "candidate"), never "confirmed".
"""
from __future__ import annotations

import json
import math
import tempfile
import zlib
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from tp import flood_mask as fm
from tp.config import REPO_ROOT

GCOV_GRIDS = "/science/LSAR/GCOV/grids/frequencyA"
CO_POL_TERMS = ("HHHH", "VVVV")
TARGET_RES_M = 100.0
MIN_VALID_FRAC = 0.5                 # of the block's native pixels
# Native samples below this are not backscatter: real GCOV granules carry a
# separate ~1e-6 (-60 dB) population at swath/burst edges, far below the
# L-band noise floor (~-25..-30 dB), which otherwise makes Otsu split noise
# from ocean (-38 dB "water" threshold seen on 024D_101, 2026-11-06).
MIN_POWER_DB = -40.0
FETCH_MAX_GAP = 1 << 20              # coalesce chunk ranges closer than 1 MiB
FETCH_MAX_SPAN = 16 << 20            # one HTTP range request <= 16 MiB
FETCH_BATCH_BYTES = 96 << 20         # bytes in flight per batch (RAM bound)
SOURCE = "nisar-gcov"
GPW_REL = Path("data/now/static/gpw/gpw_popcount_2020_30sec.tif")
NEON_BASE = (
    "https://br-lively-poetry-bdub0r34.storage.c-8.us-east-2.aws.neon.tech/"
    "tipping-point-data"
)
GPW_NEON_URL = f"{NEON_BASE}/{GPW_REL.as_posix()}"
HAND_REL = Path("data/static/south_brazil/terrain/hand_m.tif")          # 0.01 deg (~1 km) legacy
HAND90_REL = Path("data/static/south_brazil/terrain/hand_90m.tif")      # 3 arc-sec HAND proxy (preferred)


def hand_default(repo_root=None) -> Path | None:
    """Finest HAND raster present: 90 m proxy first, then the legacy ~1 km one."""
    from tp.config import REPO_ROOT as _R

    root = Path(repo_root or _R)
    for rel in (HAND90_REL, HAND_REL):
        if (root / rel).is_file():
            return root / rel
    return None


def hand_source(hand_path) -> dict | None:
    """Identity of the HAND raster used (repo-relative path, sha256 prefix, resolution)."""
    import hashlib

    import rasterio

    if not hand_path:
        return None
    p = Path(hand_path)
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    with rasterio.open(p) as src:
        res = abs(src.res[0])
    return {"file": (_hand_label(p) or "").replace("HAND (m), ", ""), "sha256_12": h.hexdigest()[:12],
            "res_deg": res, "res_m_approx": round(res * 111320)}


def _hand_label(hand_path) -> str | None:
    """'HAND (m), <repo-relative path>' (basename outside the repo) — never a
    local absolute path in published meta."""
    if not hand_path:
        return None
    from tp.config import REPO_ROOT

    p = Path(hand_path)
    try:
        rel = p.resolve().relative_to(Path(REPO_ROOT).resolve()).as_posix()
    except ValueError:
        rel = p.name
    return f"HAND (m), {rel}"

_H5Z_DEFLATE, _H5Z_SHUFFLE, _H5Z_FLETCHER32 = 1, 2, 3


# --------------------------------------------------------------------------
# Target grid
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class TargetGrid:
    """North-up grid snapped to multiples of `res` in a projected CRS."""

    epsg: int
    left: float
    top: float
    res: float
    width: int
    height: int

    @property
    def transform(self):
        from rasterio.transform import from_origin

        return from_origin(self.left, self.top, self.res, self.res)

    @property
    def px_km2(self) -> float:
        return self.res * self.res / 1e6

    def as_dict(self) -> dict:
        return {"epsg": self.epsg, "left": self.left, "top": self.top, "res": self.res,
                "width": self.width, "height": self.height}


def target_grid(x: np.ndarray, y: np.ndarray, epsg: int, bbox, res: float = TARGET_RES_M):
    """Grid covering (granule extent ∩ bbox), snapped outward to `res`.

    `x`/`y` are the granule's pixel-centre coordinates (y descending).
    None when the granule does not reach the bbox.
    """
    from rasterio.warp import transform_bounds

    dx, dy = float(x[1] - x[0]), float(y[0] - y[1])
    gx0, gx1 = float(x[0]) - dx / 2, float(x[-1]) + dx / 2
    gy1, gy0 = float(y[0]) + dy / 2, float(y[-1]) - dy / 2
    bx0, by0, bx1, by1 = transform_bounds("EPSG:4326", f"EPSG:{epsg}", *bbox, densify_pts=21)
    x0, x1 = max(gx0, bx0), min(gx1, bx1)
    y0, y1 = max(gy0, by0), min(gy1, by1)
    if x1 <= x0 or y1 <= y0:
        return None
    left = math.floor(x0 / res) * res
    top = math.ceil(y1 / res) * res
    width = math.ceil((x1 - left) / res)
    height = math.ceil((top - y0) / res)
    return TargetGrid(int(epsg), left, top, float(res), width, height)


# --------------------------------------------------------------------------
# HDF5 chunk decoding + block averaging
# --------------------------------------------------------------------------
def _filters(ds) -> list[tuple[int, tuple]]:
    plist = ds.id.get_create_plist()
    return [plist.get_filter(i)[::2] for i in range(plist.get_nfilters())]


def decode_chunk(raw: bytes, dtype, chunk_shape, filters, filter_mask: int = 0) -> np.ndarray:
    """Undo the HDF5 filter pipeline (deflate/shuffle/fletcher32) of one chunk."""
    dtype = np.dtype(dtype)
    buf = raw
    for i in reversed(range(len(filters))):
        if filter_mask & (1 << i):
            continue
        code = filters[i][0]
        if code == _H5Z_DEFLATE:
            buf = zlib.decompress(buf)
        elif code == _H5Z_SHUFFLE:
            n = len(buf) // dtype.itemsize
            buf = np.frombuffer(buf, np.uint8).reshape(dtype.itemsize, n).T.tobytes()
        elif code == _H5Z_FLETCHER32:
            buf = buf[:-4]
        else:
            raise ValueError(f"unsupported HDF5 filter id {code}")
    return np.frombuffer(buf, dtype=dtype).reshape(chunk_shape)


def _chunk_index(ds) -> list:
    """StoreInfo(chunk_offset, filter_mask, byte_offset, size) of allocated chunks."""
    out = []
    if hasattr(ds.id, "chunk_iter"):
        try:
            ds.id.chunk_iter(out.append)
            return out
        except Exception:  # noqa: BLE001 — older HDF5: fall back to the indexed API
            out = []
    return [ds.id.get_chunk_info(i) for i in range(ds.id.get_num_chunks())]


def _coalesce(infos: list, max_gap: int = FETCH_MAX_GAP, max_span: int = FETCH_MAX_SPAN):
    """Group chunk infos (sorted by offset) into contiguous fetch spans."""
    spans: list[list] = []
    for info in sorted(infos, key=lambda i: i.byte_offset):
        if spans:
            s = spans[-1]
            start = s[0].byte_offset
            end = s[-1].byte_offset + s[-1].size
            if (info.byte_offset - end <= max_gap
                    and info.byte_offset + info.size - start <= max_span):
                s.append(info)
                continue
        spans.append([info])
    return spans


def block_average(ds, x: np.ndarray, y: np.ndarray, grid: TargetGrid, read_ranges,
                  batch_bytes: int = FETCH_BATCH_BYTES,
                  min_valid_frac: float = MIN_VALID_FRAC,
                  min_power_db: float = MIN_POWER_DB) -> tuple[np.ndarray, dict]:
    """Mean linear power of `ds` on `grid`; NaN where too few valid pixels.

    `read_ranges(starts, ends) -> list[bytes]` fetches byte ranges of the
    HDF5 file (parallel HTTP for remote granules). Native pixels below
    `min_power_db` (incl. the 0 fill) or non-finite are nodata. Requires `grid.res` to be an integer multiple of
    the native posting (checked).
    """
    dx = abs(float(x[1] - x[0]))
    k = grid.res / dx
    if abs(k - round(k)) > 1e-6:
        raise ValueError(f"target res {grid.res} m is not a multiple of the {dx} m posting")
    k = round(k)
    floor = 10 ** (min_power_db / 10)
    col_t = np.floor((x - grid.left) / grid.res).astype(np.int64)
    row_t = np.floor((grid.top - y) / grid.res).astype(np.int64)
    cr, cc = ds.chunks
    filters = _filters(ds)
    nrows, ncols = ds.shape

    def _touches(info) -> bool:
        r0, c0 = info.chunk_offset
        r1, c1 = min(r0 + cr, nrows) - 1, min(c0 + cc, ncols) - 1
        return (row_t[r1] >= 0 and row_t[r0] < grid.height
                and col_t[c1] >= 0 and col_t[c0] < grid.width)

    infos = [i for i in _chunk_index(ds) if i.size > 0 and _touches(i)]
    spans = _coalesce(infos)
    sums = np.zeros((grid.height, grid.width), np.float32)
    counts = np.zeros((grid.height, grid.width), np.uint16)
    fetched = 0

    def _accumulate(info, raw):
        r0, c0 = info.chunk_offset
        a = decode_chunk(raw, ds.dtype, ds.chunks, filters, info.filter_mask)
        a = a[: min(cr, nrows - r0), : min(cc, ncols - c0)]
        rt = row_t[r0 : r0 + a.shape[0]]
        ct = col_t[c0 : c0 + a.shape[1]]
        rin = (rt >= 0) & (rt < grid.height)
        cin = (ct >= 0) & (ct < grid.width)
        a = a[rin][:, cin]
        rt, ct = rt[rin], ct[cin]
        if a.size == 0:
            return
        ok = np.isfinite(a) & (a >= floor)
        if not ok.any():
            return
        tr0, tc0 = int(rt[0]), int(ct[0])
        lh, lw = int(rt[-1]) - tr0 + 1, int(ct[-1]) - tc0 + 1
        idx = ((rt - tr0)[:, None] * lw + (ct - tc0)[None, :])[ok]
        s = np.bincount(idx, weights=a[ok], minlength=lh * lw).reshape(lh, lw)
        n = np.bincount(idx, minlength=lh * lw).reshape(lh, lw)
        sums[tr0 : tr0 + lh, tc0 : tc0 + lw] += s.astype(np.float32)
        counts[tr0 : tr0 + lh, tc0 : tc0 + lw] += n.astype(np.uint16)

    batch: list[list] = []
    batch_size = 0

    def _flush():
        nonlocal fetched
        if not batch:
            return
        starts = [s[0].byte_offset for s in batch]
        ends = [s[-1].byte_offset + s[-1].size for s in batch]
        blobs = read_ranges(starts, ends)
        for span, start, blob in zip(batch, starts, blobs, strict=True):
            fetched += len(blob)
            for info in span:
                off = info.byte_offset - start
                _accumulate(info, bytes(blob[off : off + info.size]))
        batch.clear()

    for span in spans:
        size = span[-1].byte_offset + span[-1].size - span[0].byte_offset
        if batch and batch_size + size > batch_bytes:
            _flush()
            batch_size = 0
        batch.append(span)
        batch_size += size
    _flush()

    need = max(1, math.ceil(min_valid_frac * k * k))
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = np.where(counts >= need, sums / np.maximum(counts, 1), np.nan).astype(np.float32)
    return mean, {"chunks": len(infos), "requests": len(spans), "bytes": fetched, "looks": k * k}


# --------------------------------------------------------------------------
# Granule access (remote via signed URL, or a local HDF5 path for tests)
# --------------------------------------------------------------------------
@contextmanager
def open_gcov(source: str, token: str | None = None):
    """Yield (h5py.File, read_ranges) for a local path or an ASF HTTPS URL.

    Remote: one bearer-authenticated GET (no redirect follow) resolves the
    ASF TEA 303 to the signed CloudFront URL; every later read is an
    unauthenticated byte-range GET against it. Neither the token nor the
    signed URL is ever logged.
    """
    import h5py

    if not str(source).startswith(("http://", "https://")):
        with open(source, "rb") as fh, h5py.File(source, "r") as h5:
            def _read_local(starts, ends):
                out = []
                for s, e in zip(starts, ends, strict=True):
                    fh.seek(s)
                    out.append(fh.read(e - s))
                return out

            yield h5, _read_local
        return

    import fsspec
    import requests

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = requests.get(source, headers={**headers, "Range": "bytes=0-0"},
                     allow_redirects=False, timeout=60)
    if r.status_code in (301, 302, 303, 307, 308):
        signed = r.headers.get("Location", "")
        if "urs.earthdata.nasa.gov" in signed or not signed:
            raise RuntimeError("Earthdata auth rejected (redirected to login): check EARTHDATA_TOKEN")
        url, fs_headers = signed, {}
    elif r.status_code in (200, 206):
        url, fs_headers = source, headers
    else:
        raise RuntimeError(f"granule HTTP {r.status_code} for {Path(source).name}")
    fs = fsspec.filesystem("https", client_kwargs={"headers": fs_headers}, skip_instance_cache=True)
    f = fs.open(url, mode="rb", block_size=2 << 20, cache_type="blockcache")
    try:
        with h5py.File(f, "r") as h5:
            def _read_remote(starts, ends):
                return fs.cat_ranges([url] * len(starts), list(starts), list(ends))

            yield h5, _read_remote
    finally:
        f.close()
        # Close the aiohttp session now, not at interpreter exit (where its
        # finalizer runs after the event loop is gone and spams excepthook).
        session = getattr(fs, "_session", None)
        if session is not None:
            fs.close_session(fs.loop, session)


def granule_grid_info(h5) -> dict:
    g = h5[GCOV_GRIDS]
    terms = [t.decode() if isinstance(t, bytes) else str(t) for t in g["listOfCovarianceTerms"][()]]
    return {
        "epsg": int(g["projection"].attrs["epsg_code"]),
        "x": g["xCoordinates"][()],
        "y": g["yCoordinates"][()],
        "terms": terms,
    }


def read_pair(pre_src: str, post_src: str, bbox, *, token: str | None = None,
              res: float = TARGET_RES_M, opener=open_gcov) -> dict:
    """Block-averaged co-pol power for pre and post on one shared grid.

    The grid comes from the POST granule ∩ bbox; pre pixels outside its own
    coverage stay NaN. Returns {"pre","post","grid","term","io"} or
    {"null_reason"} when the pair is not comparable.
    """
    with opener(post_src, token) as (h5, rr):
        info = granule_grid_info(h5)
        grid = target_grid(info["x"], info["y"], info["epsg"], bbox, res)
        if grid is None:
            return {"null_reason": "post granule grid does not reach the domain"}
        post_terms = info["terms"]
        term = next((t for t in CO_POL_TERMS if t in post_terms), None)
        if term is None:
            return {"null_reason": f"no co-pol term in post ({post_terms})"}
        post, io_post = block_average(h5[f"{GCOV_GRIDS}/{term}"], info["x"], info["y"], grid, rr)
    with opener(pre_src, token) as (h5, rr):
        pinfo = granule_grid_info(h5)
        if pinfo["epsg"] != grid.epsg:
            return {"null_reason": f"pre EPSG {pinfo['epsg']} != post EPSG {grid.epsg}"}
        if term not in pinfo["terms"]:
            return {"null_reason": f"pre lacks {term} ({pinfo['terms']})"}
        pre, io_pre = block_average(h5[f"{GCOV_GRIDS}/{term}"], pinfo["x"], pinfo["y"], grid, rr)
    return {"pre": pre, "post": post, "grid": grid, "term": term,
            "io": {"pre": io_pre, "post": io_post}}


# --------------------------------------------------------------------------
# Detection (wa_2025 method, unforked) + people + polygons
# --------------------------------------------------------------------------
def _write_tif(path: Path, arr: np.ndarray, grid: TargetGrid) -> Path:
    import rasterio

    with rasterio.open(path, "w", driver="GTiff", height=grid.height, width=grid.width,
                       count=1, dtype="float32", crs=f"EPSG:{grid.epsg}",
                       transform=grid.transform, nodata=float("nan")) as dst:
        dst.write(arr.astype("float32"), 1)
    return path


def _read_mask(path: Path) -> np.ndarray:
    import rasterio

    with rasterio.open(path) as src:
        return src.read(1).astype(bool)


def detect(pre: np.ndarray, post: np.ndarray, grid: TargetGrid,
           hand_path: Path | None = None) -> dict:
    """tp.flood_mask.flood_mask on the pair (+ swapped, for receded water)."""
    with tempfile.TemporaryDirectory(prefix="nisar_resp_") as tmp:
        tmp = Path(tmp)
        pre_p = _write_tif(tmp / "pre.tif", pre, grid)
        post_p = _write_tif(tmp / "post.tif", post, grid)
        stats = fm.flood_mask(pre_p, post_p, tmp / "fwd", hand_path=hand_path, bright=True)
        flood = _read_mask(tmp / "fwd" / "flood_nisar.tif")
        bright = _read_mask(tmp / "fwd" / "flood_bright_nisar.tif")
        back = fm.flood_mask(post_p, pre_p, tmp / "rev", hand_path=hand_path, bright=False)
    return {"flood": flood, "bright": bright, "stats": stats, "receded_km2": back["flood_km2"],
            "valid_km2": float((np.isfinite(pre) & np.isfinite(post)).sum() * grid.px_km2)}


def gpw_source(repo_root: Path = REPO_ROOT) -> str:
    local = Path(repo_root) / GPW_REL
    return str(local) if local.is_file() else f"/vsicurl/{GPW_NEON_URL}"


def load_gpw(src: str):
    """(array float32 persons/cell with NaN->0, rasterio transform)."""
    import rasterio

    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"), rasterio.open(src) as ds:
        if ds.crs is None or not ds.crs.is_geographic:
            raise ValueError("GPW raster must be geographic (EPSG:4326)")
        arr = ds.read(1, masked=True).filled(0).astype(np.float32)
        return np.nan_to_num(arr, nan=0.0), ds.transform


def people_at(lon: np.ndarray, lat: np.ndarray, px_km2: float, gpw) -> np.ndarray:
    """Persons inside each target pixel: GPW cell count × pixel/cell area."""
    arr, tr = gpw
    col = np.floor((lon - tr.c) / tr.a).astype(np.int64)
    row = np.floor((lat - tr.f) / tr.e).astype(np.int64)
    ok = (row >= 0) & (row < arr.shape[0]) & (col >= 0) & (col < arr.shape[1])
    out = np.zeros(lon.shape, np.float64)
    cell_km2 = (abs(tr.a) * 111.32 * np.cos(np.deg2rad(lat))) * (abs(tr.e) * 110.574)
    out[ok] = arr[row[ok], col[ok]] * px_km2 / cell_km2[ok]
    return out


def _pixel_lonlat(rows, cols, grid: TargetGrid):
    from pyproj import Transformer

    xs = grid.left + (cols + 0.5) * grid.res
    ys = grid.top - (rows + 0.5) * grid.res
    tr = Transformer.from_crs(f"EPSG:{grid.epsg}", "EPSG:4326", always_xy=True)
    return tr.transform(xs, ys)


def candidate_features(mask: np.ndarray, grid: TargetGrid, bbox, gpw, props: dict,
                       simplify_px: float = 0.5) -> list[dict]:
    """Label `mask` (pixel centres inside bbox only) into GeoJSON features."""
    import shapely
    from pyproj import Transformer
    from rasterio.features import shapes
    from scipy.ndimage import label
    from shapely.geometry import mapping, shape
    from shapely.ops import unary_union

    from tp.polygonize import polygon_parts
    from tp.response_summary import people_geom

    rows, cols = np.nonzero(mask)
    if rows.size == 0:
        return []
    lon, lat = _pixel_lonlat(rows, cols, grid)
    w, s, e, n = bbox
    inside = (lon >= w) & (lon <= e) & (lat >= s) & (lat <= n)
    rows, cols, lon, lat = rows[inside], cols[inside], lon[inside], lat[inside]
    if rows.size == 0:
        return []
    m = np.zeros(mask.shape, bool)
    m[rows, cols] = True
    labels, nlab = label(m)
    lab_px = labels[rows, cols]
    n_px = np.bincount(lab_px, minlength=nlab + 1)

    geoms: dict[int, list] = {}
    for geom, val in shapes(labels.astype(np.int32), mask=labels > 0, transform=grid.transform,
                            connectivity=4):
        geoms.setdefault(int(val), []).append(shape(geom))
    to_ll = Transformer.from_crs(f"EPSG:{grid.epsg}", "EPSG:4326", always_xy=True).transform
    feats = []
    for lab_id in sorted(geoms):
        poly = unary_union(geoms[lab_id]).simplify(grid.res * simplify_px, preserve_topology=True)
        ll = shapely.set_precision(shapely.transform(poly, to_ll, interleaved=False), 1e-5)
        parts = list(polygon_parts(ll))
        if not parts:
            continue
        geom = parts[0] if len(parts) == 1 else shapely.MultiPolygon(parts)
        feats.append({
            "type": "Feature",
            "geometry": mapping(geom),
            "properties": {
                **props,
                "area_km2": round(float(n_px[lab_id] * grid.px_km2), 4),
                # S03a: one method everywhere — GPW count on its native 30"
                # grid, area-weighted by the PUBLISHED polygon (tp.response_summary)
                "people": None if gpw is None else round(people_geom(geom, gpw)),
            },
        })
    return feats


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------
def _iso(dt: datetime | None) -> str | None:
    return None if dt is None else dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def run_response(bbox, out_dir: Path, *, search, pair, token: str | None = None,
                 hand_path: Path | None = None, gpw_src: str | None = None,
                 now: datetime | None = None, lookback_days: int = 60,
                 res: float = TARGET_RES_M, opener=open_gcov, log=print) -> dict:
    """Search → pair → read → detect → write flood_nisar.geojson + nisar_meta.json.

    `search(bbox, start, end)` returns fetch.nisar granule dicts and
    `pair(granules, bbox)` is fetch.nisar.pair_latest_pass (both injected so
    tests stay offline). Writes the GeoJSON first and the meta last; when no
    pair can be processed, only the meta is written (nulls + _null_reasons)
    and any stale flood_nisar.geojson is removed — never synthetic output.
    """
    from fetch.nisar import PROD_GCOV_COLLECTION

    now = now or datetime.now(UTC)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    geo_path, meta_path = out_dir / "flood_nisar.geojson", out_dir / "nisar_meta.json"
    reasons: dict[str, str] = {}
    meta = {
        "source": SOURCE,
        "collection": PROD_GCOV_COLLECTION,
        "generated_at": _iso(now),
        "bbox": list(bbox),
        "latest_acquired": None,
        "latency_h": None,
        "n_granules": 0,
        "pairs": [],
        "flooded_km2": None,
        "open_water_km2": None,
        "bright_km2": None,
        "new_km2": None,
        "receded_km2": None,
        "people_flooded": None,
        "method": {
            "algorithm": "tp.flood_mask.flood_mask (wa_2025): pre->post dB drop < "
                         f"{fm.FLOOD_DROP_DB} dB into water (Otsu, clamp {fm.WATER_DB} dB), "
                         "pre-water excluded, HAND gate, bright-flood layer, MMU "
                         f"{fm.MMU_HA} ha",
            "res_m": res,
            "multilook": "mean linear power per block",
            # label + repo-relative path, never a local absolute path
            "hand": _hand_label(hand_path),
            "people": "GPW v4.11 2020 count, pixel-centre sampled, area-weighted",
        },
        "_null_reasons": reasons,
    }

    def _finish(features: list[dict] | None):
        if features is None:
            if geo_path.exists():
                geo_path.unlink()
            meta["geojson"] = None
        else:
            geo_path.write_text(json.dumps({"type": "FeatureCollection", "features": features},
                                           allow_nan=False) + "\n")
            meta["geojson"] = geo_path.name
        meta_path.write_text(json.dumps(meta, indent=2, allow_nan=False) + "\n")
        return meta

    start = now - timedelta(days=lookback_days)
    try:
        granules = search(bbox, start, now)
    except Exception as e:  # noqa: BLE001 — CMR failure -> nulls, never a crash
        reasons["search"] = f"CMR search failed: {type(e).__name__}"
        return _finish(None)
    meta["n_granules"] = len(granules)
    meta["search_window"] = [_iso(start), _iso(now)]
    if granules:
        meta["newest_available_acquired"] = _iso(max(g["start"] for g in granules))
    plan = pair(granules, bbox)
    meta["newer_unpaired"] = [
        {**{k: v for k, v in s.items() if k != "acquired"}, "acquired": _iso(s["acquired"])}
        for s in plan["skipped"]
    ]
    if not plan["pairs"]:
        reasons["pairs"] = (f"no {PROD_GCOV_COLLECTION} pass over the domain could be paired "
                            f"(granules found: {len(granules)})")
        for k in ("latest_acquired", "latency_h", "flooded_km2", "new_km2", "receded_km2",
                  "people_flooded", "open_water_km2", "bright_km2"):
            reasons.setdefault(k, reasons["pairs"])
        return _finish(None)

    gpw = None
    try:
        gpw = load_gpw(gpw_src or gpw_source())
    except Exception as e:  # noqa: BLE001 — GPW optional: people -> null + reason
        reasons["people_flooded"] = f"GPW unavailable ({type(e).__name__}); people null"
    if hand_path is None:
        reasons["hand"] = "HAND raster absent: flood not HAND-gated, bright layer HAND-gated off"

    features: list[dict] = []
    tot = {"open": 0.0, "bright": 0.0, "receded": 0.0, "pre_open": 0.0, "pre_people": 0.0}
    for p in plan["pairs"]:
        pre, post = p["pre"], p["post"]
        label = f"{post['track']:03d}{post['look']}_{post['frame']:03d}"
        log(f"[nisar] {label}: pre {pre['granule_id']} -> post {post['granule_id']}")
        try:
            data = read_pair(pre["h5_url"], post["h5_url"], bbox, token=token, res=res,
                             opener=opener)
        except Exception as e:  # noqa: BLE001 — one bad granule must not sink the job
            reasons[f"pair {label}"] = f"read failed: {type(e).__name__}: {str(e)[:160]}"
            continue
        if "null_reason" in data:
            reasons[f"pair {label}"] = data["null_reason"]
            continue
        grid = data["grid"]
        det = detect(data["pre"], data["post"], grid, hand_path)
        det0 = detect(data["pre"], data["post"], grid, None) if hand_path else None   # pre-gate
        del data["pre"], data["post"]
        props = {
            "source": SOURCE,
            "pre": pre["granule_id"],
            "post": post["granule_id"],
            "track": post["track"],
            "frame": post["frame"],
            "direction": "descending" if post["look"] == "D" else "ascending",
            "acquired": _iso(post["start"]),
            "status": "candidate",
            "change": "new",
        }
        f_open = candidate_features(det["flood"], grid, bbox, gpw,
                                    {**props, "flood_type": "open-water", "confidence": "standard"})
        f_bright = candidate_features(det["bright"], grid, bbox, gpw,
                                      {**props, "flood_type": "bright", "confidence": "lower"})
        open_km2 = sum(f["properties"]["area_km2"] for f in f_open)
        if det0 is not None:
            f0 = candidate_features(det0["flood"], grid, bbox, gpw, dict(props))
            tot["pre_open"] += sum(f["properties"]["area_km2"] for f in f0)
            tot["pre_people"] += sum(f["properties"]["people"] or 0 for f in f0)
            del f0, det0
        else:
            tot["pre_open"] += open_km2
            tot["pre_people"] += sum(f["properties"]["people"] or 0 for f in f_open)
        bright_km2 = sum(f["properties"]["area_km2"] for f in f_bright)
        features += f_open + f_bright
        tot["open"] += open_km2
        tot["bright"] += bright_km2
        tot["receded"] += det["receded_km2"]
        meta["pairs"].append({
            "pre": pre["granule_id"], "post": post["granule_id"],
            "track": post["track"], "look": post["look"], "frame": post["frame"],
            "pre_acquired": _iso(pre["start"]), "post_acquired": _iso(post["start"]),
            "post_available": _iso(post["available"]),
            "term": data["term"], "grid": grid.as_dict(), "io": data["io"],
            "valid_km2": round(det["valid_km2"], 2),
            "threshold_db": det["stats"]["threshold_db"],
            "threshold_method": det["stats"]["threshold_method"],
            "open_water_km2": round(open_km2, 3), "bright_km2": round(bright_km2, 3),
            "receded_km2": round(det["receded_km2"], 3),
            "polygons": len(f_open) + len(f_bright),
        })
        del det

    if not meta["pairs"]:
        reasons.setdefault("pairs", "every paired granule failed to read (see pair reasons)")
        return _finish(None)
    done = [p for p in plan["pairs"]
            if any(m["post"] == p["post"]["granule_id"] for m in meta["pairs"])]
    newest = max(done, key=lambda p: p["post"]["start"])["post"]
    meta["latest_acquired"] = _iso(newest["start"])
    if newest.get("available"):
        meta["latency_h"] = round((newest["available"] - newest["end"]).total_seconds() / 3600, 2)
    else:
        reasons["latency_h"] = "CMR insert date missing for the post granule"
    meta["open_water_km2"] = round(tot["open"], 3)
    meta["bright_km2"] = round(tot["bright"], 3)
    meta["flooded_km2"] = round(tot["open"] + tot["bright"], 3)
    meta["new_km2"] = meta["flooded_km2"]
    meta["receded_km2"] = round(tot["receded"], 3)
    if gpw is not None:
        from tp.response_summary import PEOPLE_METHOD, people_geom
        from shapely.geometry import shape as _shape
        from shapely.ops import unary_union as _uu

        u = _uu([_shape(f["geometry"]) for f in features]) if features else None
        meta["people_flooded"] = round(people_geom(u, gpw) or 0.0)
        meta["people_method"] = PEOPLE_METHOD
    meta["n_polygons"] = len(features)
    # what the HAND <= 15 m gate removed (open water; the bright layer exists
    # only with HAND, so it is not part of the comparison)
    meta["pre_hand_flooded_km2"] = round(tot["pre_open"], 3)
    meta["hand_excluded_km2"] = round(max(0.0, tot["pre_open"] - tot["open"]), 3) if hand_path else None
    if gpw is not None:
        meta["pre_hand_people"] = int(round(tot["pre_people"]))
        meta["hand_excluded_people"] = (max(0, int(round(tot["pre_people"])) - int(sum(
            f["properties"]["people"] or 0 for f in features if f["properties"].get("flood_type") == "open-water")))
            if hand_path else None)
    if not hand_path:
        reasons["hand_excluded_km2"] = "no HAND raster: flood not HAND-gated"
    meta["hand_source"] = hand_source(hand_path)
    return _finish(features)
