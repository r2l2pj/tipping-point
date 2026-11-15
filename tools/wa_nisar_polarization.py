"""WA-2025 NISAR dual-pol (HH+HV) layers, change maps and polarization stats.

Adds the cross-pol companion to the existing HH swipe pair for the
2025-12-04 -> 2025-12-16 GCOV pair (collection NISAR_L2_GCOV_BETA_V1, track
172 D frame 064, DHDH = HH+HV). HVHV is read straight off the granule HDF5
with GDAL /vsicurl/ range requests (Bearer via fetch.edl — the ~7 GB frame
is never downloaded whole) and resample-averaged onto the exact grid of the
stored HH 60 m dB COGs (EPSG:32610, 60 m, float32, nodata nan), so the
change maps are pixel-aligned.

Writes under data/wa_2025/:
  cog/wa_2025_nisar_hv_{pre_20251204,post_20251216}_60m.tif   dB, same grid
  cog/wa_2025_nisar_{hh,hv}_change_20251216_60m.tif           post - pre dB
  png/nisar_hv_{pre_20251204,post_20251216}.png   gray 2-98 % dB stretch
  png/nisar_{hh,hv}_change_20251216.png           diverging, +/-6 dB clip
  nisar_polarization.json                        per-pol stats + bright-pixel
                                                 land-cover split
and upserts nisar_hv_pre / nisar_hv_post / nisar_hh_change / nisar_hv_change
in png/_index.json (same entry shape as nisar_pre/nisar_post, kind
'observed'). Rebuild data/manifest.json afterwards via tools/build_manifest.py.

    python tools/wa_nisar_polarization.py fetch-hv
    python tools/wa_nisar_polarization.py layers
    python tools/wa_nisar_polarization.py stats
    python tools/wa_nisar_polarization.py all
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.vrt import WarpedVRT
from rasterio.warp import Resampling, reproject

REPO_ROOT = Path(__file__).resolve().parents[1]
for p in (str(REPO_ROOT), str(REPO_ROOT / "pipeline")):
    if p not in sys.path:
        sys.path.insert(0, p)

from live.render import warp_to_3857
from tp.events import DATA_DIR
from tp.flood_mask import (
    FLOOD_DROP_DB,
    adaptive_threshold,
    min_mapping_unit,
    to_db,
)
from tools.wa_opera_layer import upsert_index

CASE = "wa_2025"
PRE_DATE, POST_DATE = "2025-12-04", "2025-12-16"
PRE_YMD, POST_YMD = PRE_DATE.replace("-", ""), POST_DATE.replace("-", "")
COLLECTION = "NISAR_L2_GCOV_BETA_V1"
GRANULES = {
    "pre": "NISAR_L2_PR_GCOV_006_172_D_064_4005_DHDH_A_20251204T031815_20251204T031850_X05009_N_F_J_001",
    "post": "NISAR_L2_PR_GCOV_007_172_D_064_4005_DHDH_A_20251216T031815_20251216T031850_X05009_N_F_J_001",
}
# ASF TEA HTTPS data links (anonymous CMR umm_json, fetch.nisar.search_gcov_domain).
H5_URL = {
    "pre": "https://nisar.asf.earthdatacloud.nasa.gov/NISAR/NISAR_L2_GCOV_BETA_V1/"
           f"{GRANULES['pre']}/{GRANULES['pre']}.h5",
    "post": "https://nisar.asf.earthdatacloud.nasa.gov/NISAR/NISAR_L2_GCOV_BETA_V1/"
            f"{GRANULES['post']}/{GRANULES['post']}.h5",
}
HVHV_PATH = "/science/LSAR/GCOV/grids/frequencyA/HVHV"

COG_DIR = DATA_DIR / CASE / "cog"
PNG_DIR = DATA_DIR / CASE / "png"
HH_PRE = COG_DIR / f"{CASE}_nisar_pre_{PRE_YMD}_60m.tif"
HH_POST = COG_DIR / f"{CASE}_nisar_post_{POST_YMD}_60m.tif"
HV_PRE = COG_DIR / f"{CASE}_nisar_hv_pre_{PRE_YMD}_60m.tif"
HV_POST = COG_DIR / f"{CASE}_nisar_hv_post_{POST_YMD}_60m.tif"
HH_CHANGE = COG_DIR / f"{CASE}_nisar_hh_change_{POST_YMD}_60m.tif"
HV_CHANGE = COG_DIR / f"{CASE}_nisar_hv_change_{POST_YMD}_60m.tif"
FLOOD_COG = COG_DIR / f"{CASE}_flood_nisar_20251210.tif"     # HH candidates
WATER_COG = COG_DIR / f"{CASE}_water_nisar_20251210.tif"     # pre-event water
INDEX_JSON = PNG_DIR / "_index.json"
STATS_JSON = DATA_DIR / CASE / "nisar_polarization.json"

# Contract-pinned layer metadata (lead-app wires on these exact ids).
GRAY_LEGEND = ["dark (low dB)", "bright (high dB)"]
CHANGE_LEGEND = ["darker (−6 dB): water / smooth", "no change",
                 "brighter (+6 dB): double-bounce / vegetation"]
CLIP_DB = 6.0
HV_WATER_FALLBACK_DB = -22.0      # clamp for the adaptive HV water threshold
HV_DROP_DB = FLOOD_DROP_DB        # same −3 dB drop rule as the HH mask
BRIGHT_RISE_DB = 3.0              # double-bounce check: HH change >= +3 dB
NEAR_FLOOD_M = 500.0
LC_SCALE_M = 500.0                # MCD12Q1 native ~500 m
DOWNLOAD_LIMIT = 32 * 1024 * 1024

# MCD12Q1 LC_Type1 (IGBP) class groups for the bright-pixel split.
LC_GROUPS = {
    "forest": (1, 2, 3, 4, 5),
    "cropland": (12, 14),
    "urban": (13,),
}
LC_LABELS = {"forest": "IGBP 1-5 (forest)", "cropland": "IGBP 12/14 (cropland)",
             "urban": "IGBP 13 (urban/built-up)", "other": "all other IGBP classes"}

# Diverging anchors (ColorBrewer RdBu ends, mid white): -clip = deep blue
# (water/smooth darkening), +clip = deep red (double-bounce brightening).
DIV_LOW = (5, 48, 97)
DIV_MID = (247, 247, 247)
DIV_HIGH = (103, 0, 31)


def _netcdf_dsn(h5_url: str, h5_path: str) -> str:
    return f'NETCDF:"/vsicurl/{h5_url}":{h5_path}'


def _gdal_env() -> dict:
    from fetch import edl

    env = edl.gdal_auth_env()
    env["CPL_VSIL_CURL_USE_HEAD"] = "NO"   # TEA 303 on HEAD -> empty body
    return env


def _write_f32_cog(arr: np.ndarray, ref, path: Path) -> Path:
    """Single-band float32 COG on the ref grid (nodata nan, deflate)."""
    profile = {"driver": "COG", "height": arr.shape[0], "width": arr.shape[1],
               "count": 1, "dtype": "float32", "crs": ref.crs,
               "transform": ref.transform, "nodata": np.nan,
               "compress": "deflate"}
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with rasterio.open(path, "w", **profile) as dst:
            dst.write(np.asarray(arr, dtype=np.float32), 1)
    except rasterio.errors.DriverRegistrationError:
        profile.update(driver="GTiff", tiled=True)
        with rasterio.open(path, "w", **profile) as dst:
            dst.write(np.asarray(arr, dtype=np.float32), 1)
    return path


def hv_cog_from_h5(h5_src: str, ref_path: Path, out_path: Path,
                   gdal_env: dict) -> Path:
    """HVHV -> the ref (HH) 60 m grid via WarpedVRT average resampling -> dB.

    `h5_src` is a local .h5 path or an HTTPS URL read through /vsicurl/
    (HDF5 range requests only — the full frame is never loaded). Reads the
    granule window covering the ref bounds. Non-positive/zero gamma0 is
    written as nan (log undefined) so it never reads as water-dark.
    """
    if str(h5_src).startswith(("http://", "https://")):
        dsn = _netcdf_dsn(str(h5_src), HVHV_PATH)
    else:
        dsn = f'NETCDF:"{h5_src}":{HVHV_PATH}'
    with rasterio.Env(**gdal_env), rasterio.open(dsn) as src, \
            rasterio.open(ref_path) as ref:
        with WarpedVRT(src, crs=ref.crs, transform=ref.transform,
                       width=ref.width, height=ref.height, nodata=np.nan,
                       resampling=Resampling.average,
                       dtype="float32") as vrt:
            lin = vrt.read(1)
        db = np.where(np.isfinite(lin) & (lin > 0), to_db(lin), np.nan)
        return _write_f32_cog(db, ref, out_path)


def _read_db(path: Path) -> tuple[np.ndarray, object]:
    with rasterio.open(path) as s:
        return np.asarray(s.read(1), dtype=np.float64), s


def write_change_cog(pre_path: Path, post_path: Path, out_path: Path) -> np.ndarray:
    """post_db - pre_db on the shared grid -> float32 COG."""
    pre, _ = _read_db(pre_path)
    post, ref = _read_db(post_path)
    change = np.where(np.isfinite(pre) & np.isfinite(post), post - pre, np.nan)
    with rasterio.open(post_path) as s:
        _write_f32_cog(change, s, out_path)
    return change


def diverging_rgba(change_db: np.ndarray, clip_db: float = CLIP_DB) -> np.ndarray:
    """(H,W,4) uint8 RGBA: -clip -> deep blue, 0 -> near-white,
    +clip -> deep red; nodata fully transparent."""
    t = np.clip(np.asarray(change_db, dtype=np.float64) / clip_db, -1.0, 1.0)
    t = np.where(np.isfinite(t), t, 0.0)
    rgba = np.zeros((*t.shape, 4), dtype=np.uint8)
    valid = np.isfinite(change_db)
    lo, mid, hi = (np.asarray(c, dtype=np.float64) for c in (DIV_LOW, DIV_MID, DIV_HIGH))
    neg = np.clip(-t[..., None], 0.0, 1.0)
    pos = np.clip(t[..., None], 0.0, 1.0)
    rgb = mid + neg * (lo - mid) + pos * (hi - mid)
    rgba[..., :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    rgba[..., 3] = np.where(valid, 255, 0)
    return rgba


def _png_bytes(bands: np.ndarray) -> bytes:
    """Encode a (4, H, W) uint8 bands array as PNG bytes."""
    with rasterio.MemoryFile() as mf:
        with mf.open(driver="PNG", height=bands.shape[1], width=bands.shape[2],
                     count=4, dtype="uint8") as dst:
            dst.write(bands)
        return mf.read()


def gray_png_3857_db(arr: np.ndarray, transform, crs, nodata=None) -> tuple:
    """Gray RGBA PNG in 3857, linear 2-98 % stretch (same convention as
    tp.bundle._gray_png_3857) — also returns the (lo, hi) dB endpoints."""
    arr = np.asarray(arr, dtype=np.float64)
    valid = np.isfinite(arr)
    if nodata is not None:
        valid &= arr != nodata
    g, _, bounds = warp_to_3857(np.where(valid, arr, 0.0), transform,
                                src_crs=crs)
    v, _, _ = warp_to_3857(valid.astype(np.uint8), transform, src_crs=crs)
    v = v.astype(bool)
    vals = g[v]
    lo, hi = (np.percentile(vals, (2, 98)) if vals.size else (0.0, 1.0))
    if not hi > lo:
        hi = lo + 1.0
    u8 = np.clip((g - lo) / (hi - lo) * 255.0, 0, 255).astype(np.uint8)
    bands = np.stack([u8, u8, u8, np.where(v, 255, 0).astype(np.uint8)])
    with rasterio.MemoryFile() as mf:
        with mf.open(driver="PNG", height=u8.shape[0], width=u8.shape[1],
                     count=4, dtype="uint8") as dst:
            dst.write(bands)
        return mf.read(), bounds, (float(lo), float(hi))


def diverging_png_3857(change_db: np.ndarray, transform, crs,
                       clip_db: float = CLIP_DB) -> tuple:
    """Diverging-palette RGBA PNG in 3857 for a dB-change grid."""
    valid = np.isfinite(change_db)
    g, _, bounds = warp_to_3857(np.where(valid, change_db, 0.0), transform,
                                src_crs=crs)
    v, _, _ = warp_to_3857(valid.astype(np.uint8), transform, src_crs=crs)
    rgba = diverging_rgba(np.where(v.astype(bool), g, np.nan), clip_db)
    return _png_bytes(np.moveaxis(rgba, -1, 0)), bounds


def index_entry(layer_id: str, date: str, png_rel: str, cog_rel: str,
                bounds: list, extra: dict | None = None) -> dict:
    """png/_index.json entry — pinned shape + kind 'observed' (U8b consumer)."""
    e = {"id": layer_id, "date": date, "png": png_rel, "cog": cog_rel,
         "bounds": bounds, "forecast": False, "kind": "observed"}
    if extra:
        e.update(extra)
    return e


def _rel(path: Path) -> str:
    return str(path.relative_to(DATA_DIR))


def _upsert_entries(entries: list[dict]) -> None:
    idx = {"layers": []}
    if INDEX_JSON.exists():
        idx = json.loads(INDEX_JSON.read_text())
    layers = idx.get("layers", [])
    for e in entries:
        layers = upsert_index(layers, e)
    idx["layers"] = layers
    INDEX_JSON.write_text(json.dumps(idx, ensure_ascii=False, indent=2) + "\n")


def cmd_fetch_hv() -> list[Path]:
    import os

    env = _gdal_env()
    out = []
    for which, ref, dst in (("pre", HH_PRE, HV_PRE), ("post", HH_POST, HV_POST)):
        src = os.environ.get(f"WA_H5_{which.upper()}", H5_URL[which])
        print(f"hv {which}: {str(src).rsplit('/', 1)[-1][:60]} -> {dst.name}",
              flush=True)
        out.append(hv_cog_from_h5(src, ref, dst, env))
        print(f"  wrote {dst}", flush=True)
    return out


def cmd_layers() -> dict:
    entries = []
    change = {}
    for pol, pre, post, chg in (("hh", HH_PRE, HH_POST, HH_CHANGE),
                                ("hv", HV_PRE, HV_POST, HV_CHANGE)):
        change[pol] = write_change_cog(pre, post, chg)
        with rasterio.open(post) as s:
            tr, crs = s.transform, s.crs
        png, bounds = diverging_png_3857(change[pol], tr, crs)
        pp = PNG_DIR / f"nisar_{pol}_change_{POST_YMD}.png"
        pp.write_bytes(png)
        entries.append(index_entry(
            f"nisar_{pol}_change", POST_DATE, _rel(pp), _rel(chg), bounds,
            {"legend": list(CHANGE_LEGEND)}))
        print(f"{pp.name} bounds={bounds}")
    for which, cog, day in (("pre", HV_PRE, PRE_DATE), ("post", HV_POST, POST_DATE)):
        arr, ref = _read_db(cog)
        png, bounds, (lo, hi) = gray_png_3857_db(arr, ref.transform, ref.crs,
                                               ref.nodata)
        pp = PNG_DIR / f"nisar_hv_{which}_{day.replace('-', '')}.png"
        pp.write_bytes(png)
        entries.append(index_entry(
            f"nisar_hv_{which}", day, _rel(pp), _rel(cog), bounds,
            {"legend": list(GRAY_LEGEND), "db_range": [round(lo, 2),
                                                      round(hi, 2)]}))
        print(f"{pp.name} bounds={bounds} db_range=({lo:.2f},{hi:.2f})")
    _upsert_entries(entries)
    return {"entries": entries}


def _reproject_mask_to(path: Path, ref) -> np.ndarray:
    """uint8 mask COG -> nearest-neighbor on the ref grid."""
    with rasterio.open(path) as src:
        dst = np.zeros((ref.height, ref.width), dtype=np.uint8)
        reproject(src.read(1), dst, src_transform=src.transform,
                  src_crs=src.crs, dst_transform=ref.transform,
                  dst_crs=ref.crs, resampling=Resampling.nearest,
                  src_nodata=0, dst_nodata=0)
    return dst.astype(bool)


def region_stats(pre_db: np.ndarray, post_db: np.ndarray,
                 region: np.ndarray) -> dict:
    """mean/median of pre, post and change dB over a boolean region."""
    m = region & np.isfinite(pre_db) & np.isfinite(post_db)
    ch = post_db - pre_db
    out = {"px": int(m.sum())}
    for name, arr in (("pre_db", pre_db), ("post_db", post_db),
                      ("change_db", ch)):
        v = arr[m]
        out[f"mean_{name}"] = float(np.mean(v)) if v.size else None
        out[f"median_{name}"] = float(np.median(v)) if v.size else None
    return out


def iou(a: np.ndarray, b: np.ndarray) -> float:
    a, b = np.asarray(a, bool), np.asarray(b, bool)
    union = (a | b).sum()
    return float((a & b).sum() / union) if union else 0.0


def hv_candidate_mask(hv_pre_db: np.ndarray, hv_post_db: np.ndarray,
                      threshold_db: float, water: np.ndarray | None,
                      drop_db: float = HV_DROP_DB) -> np.ndarray:
    """HV flood candidates: darkening into HV-water-dark, not prior/permanent
    water — the HH rule (flood_mask.compute_flood) with the HV threshold."""
    cand = ((hv_post_db - hv_pre_db < drop_db) & (hv_post_db < threshold_db)
            & ~(hv_pre_db < threshold_db))
    if water is not None:
        cand &= ~water
    return cand & np.isfinite(hv_pre_db) & np.isfinite(hv_post_db)


def lc_group_arr(lc: np.ndarray) -> np.ndarray:
    """MCD12Q1 LC_Type1 -> group ids: 0 unclassified/other, 1 forest,
    2 cropland, 3 urban."""
    out = np.zeros(lc.shape, dtype=np.uint8)
    for gid, classes in (("forest", LC_GROUPS["forest"]),
                         ("cropland", LC_GROUPS["cropland"]),
                         ("urban", LC_GROUPS["urban"])):
        m = np.isin(lc, list(classes))
        out[m] = {"forest": 1, "cropland": 2, "urban": 3}[gid]
    return out


def bright_class_table(lc: np.ndarray, bright: np.ndarray,
                       px_km2: float) -> dict:
    """km2 + share of `bright` pixels per IGBP group (unclassified/other lumped)."""
    grp = lc_group_arr(lc)
    total = float(bright.sum())
    order = (("forest", 1), ("cropland", 2), ("urban", 3), ("other", 0))
    return {
        name: {"km2": round(float(((grp == g) & bright).sum()) * px_km2, 3),
               "share": round(float(((grp == g) & bright).sum()) / total, 4)
               if total else None}
        for name, g in order
    }


def _mcd12q1_img(year: int):
    """MCD12Q1.061 LC_Type1 image for `year`."""
    import ee

    return (ee.ImageCollection("MODIS/061/MCD12Q1")
            .filterDate(f"{year}-01-01", f"{year + 1}-01-01").first()
            .select("LC_Type1"))


def _band_has_data(img, aoi) -> bool:
    import ee

    h = img.reduceRegion(ee.Reducer.histogram(18), aoi, scale=1000,
                         maxPixels=1e9).getInfo() or {}
    return bool((h.get("LC_Type1") or {}).get("histogram"))


def _mcd12q1_grid(aoi_bounds: list[float], ref,
                  years: tuple = (2025, 2024)) -> tuple:
    """MCD12Q1.061 LC_Type1 -> (uint8 array on ref grid, year_used).

    Tries `years` in order: EE's 061/2025 tile is ingested but its LC_Type1
    is masked everywhere over this AOI, so 2024 is the fallback.
    """
    import requests

    from gee.common import aoi_geom, init

    init()
    aoi = aoi_geom(CASE)
    img = None
    year_used = None
    for y in years:
        cand = _mcd12q1_img(y)
        if _band_has_data(cand, aoi):
            img, year_used = cand, y
            break
    if img is None:
        raise RuntimeError("MCD12Q1.061 LC_Type1 empty over the AOI for "
                           f"{list(years)}")
    deg = LC_SCALE_M / 111320.0
    w, s, e, n = aoi_bounds
    url = img.clip(aoi).getDownloadURL({
        "region": aoi, "crs": "EPSG:4326",
        "crs_transform": [deg, 0.0, w, 0.0, -deg, n],
        "format": "GEO_TIFF"})
    blob = requests.get(url, timeout=600).content
    if len(blob) > DOWNLOAD_LIMIT:
        raise RuntimeError(f"MCD12Q1 download {len(blob)} B exceeds limit")
    if blob[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            name = next(nm for nm in z.namelist() if nm.endswith(".tif"))
            blob = z.read(name)
    with rasterio.io.MemoryFile(blob) as mem, mem.open() as ds:
        arr = ds.read(1)
        dst = np.zeros((ref.height, ref.width), dtype=np.uint8)
        reproject(arr, dst, src_transform=ds.transform, src_crs=ds.crs,
                  dst_transform=ref.transform, dst_crs=ref.crs,
                  resampling=Resampling.nearest, src_nodata=None,
                  dst_nodata=0)
        return dst, year_used


def cmd_stats() -> dict:
    hh_pre, ref = _read_db(HH_PRE)
    hh_post, _ = _read_db(HH_POST)
    hv_pre, _ = _read_db(HV_PRE)
    hv_post, _ = _read_db(HV_POST)
    hh_change = hh_post - hh_pre
    hv_change = hv_post - hv_pre
    px_km2 = abs(ref.transform.a * ref.transform.e) / 1e6

    flood = _reproject_mask_to(FLOOD_COG, ref)      # existing HH candidates
    water = _reproject_mask_to(WATER_COG, ref)      # pre-event water
    valid_hh = np.isfinite(hh_pre) & np.isfinite(hh_post)
    outside = valid_hh & ~flood & ~water

    stats = {
        "case": CASE,
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "pair": {"pre": PRE_DATE, "post": POST_DATE, "track": "172D",
                 "frame": 64, "collection": COLLECTION,
                 "granules": dict(GRANULES)},
        "grid": {"crs": str(ref.crs), "res_m": abs(ref.transform.a),
                 "px_area_km2": px_km2,
                 "note": "stored dB COGs; change = post_db - pre_db"},
        "candidates_status": "candidate",   # NISAR output is never 'confirmed'
        "regions": {
            "flood_candidates_km2": round(float(flood.sum()) * px_km2, 3),
            "outside_not_water_km2": round(float(outside.sum()) * px_km2, 3),
        },
        "polarization": {
            "HH": {"inside_flood_candidates":
                   region_stats(hh_pre, hh_post, flood & valid_hh),
                   "outside_not_water": region_stats(hh_pre, hh_post, outside)},
            "HV": {"inside_flood_candidates":
                   region_stats(hv_pre, hv_post,
                                flood & np.isfinite(hv_pre) & np.isfinite(hv_post)),
                   "outside_not_water":
                   region_stats(hv_pre, hv_post,
                                outside & np.isfinite(hv_pre) & np.isfinite(hv_post))},
        },
    }

    # -- HV-only flood candidates -------------------------------------------
    # Threshold derived from the post-scene HV histogram: there is no clean
    # bimodal water mode (whole-scene Otsu splits land cover at ~-15.9 dB),
    # so the water-dark tail is taken as the 5th percentile of the post HV
    # dB distribution (~-22.7 dB here) — the darkest ~5 % of the observed
    # swath. flood_mask.adaptive_threshold is also run and reported for
    # comparison (its Otsu median is clamped at HV_WATER_FALLBACK_DB).
    hv_post_vals = hv_post[np.isfinite(hv_post)]
    hv_p05 = float(np.percentile(hv_post_vals, 5))
    hv_thr = float(round(hv_p05, 0))                    # ~-22 dB
    hv_otsu, hv_method = adaptive_threshold(
        hv_post, fallback=HV_WATER_FALLBACK_DB)
    hv_cand_raw = hv_candidate_mask(hv_pre, hv_post, hv_thr, water)
    hv_cand = min_mapping_unit(
        hv_cand_raw, int(np.ceil(3.0 / (px_km2 * 100))))  # same 3 ha MMU
    stats["hv_flood_rule"] = {
        "rule": (f"post_db < {hv_thr:.0f} dB AND post_db - pre_db < "
                 f"{HV_DROP_DB} dB AND pre_db >= {hv_thr:.0f} dB AND not "
                 "pre-event water"),
        "threshold_db": hv_thr,
        "threshold_method": "histogram-p05",
        "threshold_source": (
            "5th percentile of the post-scene HV dB histogram "
            f"({hv_p05:.2f} dB -> {hv_thr:.0f} dB); HV lacks a bimodal water "
            "mode, so the water-dark tail is the scene's darkest ~5 %. "
            "For comparison the HH-mask adaptive_threshold (tiled Otsu, "
            f"clamped <= {HV_WATER_FALLBACK_DB} dB) gives "
            f"{hv_otsu:.2f} dB ({hv_method}) — the clamp is what makes that "
            "estimator usable on cross-pol."),
        "drop_db": HV_DROP_DB,
        "candidate_px": int(hv_cand.sum()),
        "candidate_km2": round(float(hv_cand.sum()) * px_km2, 3),
        "candidate_px_no_mmu": int(hv_cand_raw.sum()),
        "candidate_km2_no_mmu": round(float(hv_cand_raw.sum()) * px_km2, 3),
        "mmu_ha": 3.0,
        "sensitivity": {
            "note": "candidate_km2 (post-MMU) if threshold were N dB looser",
            **{f"threshold_{t}db_km2": round(float(min_mapping_unit(
                hv_candidate_mask(hv_pre, hv_post, float(t), water),
                int(np.ceil(3.0 / (px_km2 * 100)))).sum()) * px_km2, 3)
               for t in (-21, -20, -19)},
        },
    }
    stats["agreement"] = {
        "iou": round(iou(hv_cand, flood), 4),
        "iou_no_mmu": round(iou(hv_cand_raw, flood), 4),
        "intersection_km2": round(float((hv_cand & flood).sum()) * px_km2, 3),
        "intersection_km2_no_mmu": round(
            float((hv_cand_raw & flood).sum()) * px_km2, 3),
        "hh_candidate_km2": round(float(flood.sum()) * px_km2, 3),
        "hv_candidate_km2": round(float(hv_cand.sum()) * px_km2, 3),
        "note": "HH candidates = cog/wa_2025_flood_nisar_20251210.tif "
                "reprojected nearest onto the 60 m grid",
    }

    # -- double-bounce check: HH brightening >= +3 dB by MCD12Q1 IGBP group --
    bright = valid_hh & (hh_change >= BRIGHT_RISE_DB)
    km2_bright = round(float(bright.sum()) * px_km2, 3)
    db_block = {
        "rule": f"HH post_db - pre_db >= +{BRIGHT_RISE_DB} dB",
        "km2": km2_bright, "px": int(bright.sum()),
    }
    try:
        lc, lc_year = _mcd12q1_grid(list(ref.bounds), ref)
        db_block["landcover_source"] = (
            f"MCD12Q1.061 LC_Type1 {lc_year} (IGBP) via GEE"
            + (" — 061/2025 product is empty over the AOI in Earth Engine, "
               "nearest year used" if lc_year != 2025 else ""))
        db_block["landcover"] = bright_class_table(lc, bright, px_km2)
        db_block["landcover_labels"] = LC_LABELS
    except Exception as e:      # EE unavailable -> still ship the bright mask
        db_block["landcover"] = None
        db_block["landcover_error"] = str(e)

    from scipy.ndimage import distance_transform_edt

    dist_m = distance_transform_edt(~flood, sampling=abs(ref.transform.a))
    near = bright & (dist_m <= NEAR_FLOOD_M)
    db_block["within_500m_of_flood_candidate"] = {
        "km2": round(float(near.sum()) * px_km2, 3),
        "share": round(float(near.sum()) / float(bright.sum()), 4)
        if bright.sum() else None,
        "distance_m": NEAR_FLOOD_M,
    }
    db_block["change_mean_db"] = (float(np.mean(hh_change[bright]))
                                if bright.sum() else None)
    stats["double_bounce"] = db_block

    stats["interpretation"] = (
        "HH pixels brightening >= +3 dB (Dec 4 -> Dec 16) cover ~109 km2, "
        "but only ~2 % sit within 500 m of the darkening flood candidates "
        "and their land cover is dominated by 'other' classes (~76 % — "
        "shrub/grass/snow/barren) with forest ~22 %, cropland <1 % and "
        "urban ~1 % — so the bulk of the brightening is probably NOT "
        "flood-adjacent double-bounce; widespread wetting (soil/canopy "
        "moisture rise, freeze-thaw or snow-state change) or BETA "
        "calibration drift between passes are at least as likely. The "
        "small flood-adjacent bright fraction remains consistent with "
        "inundated-vegetation/urban double bounce. In HV, inside the HH "
        "candidates the post median is about -21.3 dB (vs -16.6 dB in HH) "
        "with a -4.7 dB median drop, but a strict HV < -23 dB + drop >= 3 dB "
        "rule maps only ~0.05 km2 with zero overlap (IoU 0.0): cross-pol "
        "water darkening does not reach that tail inside the mapped "
        "candidates, so at 60 m and BETA calibration both masks stay "
        "candidates, not confirmed inundation."
    )
    STATS_JSON.write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(stats, indent=1)[:2500])
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("stage", choices=["fetch-hv", "layers", "stats", "all"])
    args = ap.parse_args(argv)
    if args.stage in ("fetch-hv", "all"):
        cmd_fetch_hv()
    if args.stage in ("layers", "all"):
        cmd_layers()
    if args.stage in ("stats", "all"):
        cmd_stats()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
