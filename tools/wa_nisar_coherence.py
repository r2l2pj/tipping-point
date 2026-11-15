"""WA-2025 NISAR GUNW coherence-loss candidate layer (Beta products).

Pairs on track 172 D, frames 064 + 065 (Beta, CRID X05010):
  reference (no-event):        2025-11-22 -> 2025-12-04
  event (crosses Dec 8-12 AR): 2025-12-04 -> 2025-12-16

Reads wrappedInterferogram/HH/coherenceMagnitude plus the paired
water/subswath `mask` as HTTPS range reads: dataset paths verified with a
single h5py visit (earthaccess fsspec file -> h5py fileobj), then the
windows are streamed with GDAL 'NETCDF:"/vsicurl/<url>":<dataset>' (the
h5py path's pre-signed URL expired mid-read on the multi-minute hyperslab
fetches — GDAL re-signs per request). Each GUNW HDF5 is ~2.4 GB and is
never downloaded whole. Frame 065 is mosaicked in where 064 has no valid
samples (northern AOI).

delta = coh_ref - coh_event. Loss mask: coh_ref >= coherence_ref_min AND
delta >= coherence_drop (config.yaml thresholds, reused from
tp/landslide_coh.py), excluding water (GUNW mask water flag OR the NISAR
GCOV water COG) and elevation >= coh_max_elev_m (600 m — the crude
December snow line; NASADEM_HGT.001 via LP DAAC). Pixels also losing
> hv_drop_db of HV backscatter (GCOV pre/post, in dB) are flagged
`hv_vote` on the polygons / class 2 in the loss COG.

These are CANDIDATES, never confirmed scars: 12-day L-band winter
decorrelation also comes from rain, snowfall, soil moisture and forest
changes, and frame edges/forested slopes carry extra noise in the Beta
product.

Writes under data/wa_2025/:
  cog/wa_2025_nisar_coherence_{ref,event}_60m.tif   float32, EPSG:32610
  cog/wa_2025_nisar_coherence_loss_60m.tif          uint8 0/1/2
  geojson/nisar_coherence_candidates.geojson        polygons >= MMU (4326)
  png/nisar_coherence_loss_20251216.png             EPSG:3857 RGBA
  png/nisar_coherence_event_20251216.png            event coherence, gray
  nisar_coherence.json                            stats + caveats
Upserts png/_index.json (kind "nisar_extra") and metrics.json keys via
tp.metrics.merge_metrics.

    python tools/wa_nisar_coherence.py [--no-fetch] [--stats-only]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
for p in (str(REPO_ROOT), str(REPO_ROOT / "pipeline")):
    if p not in sys.path:
        sys.path.insert(0, p)

from tp.config import threshold
from tp.coherence_loss import baseline_hit_pct, hit_stats, loss_polygons
from tp.events import DATA_DIR, load_event
from tp.landslide_coh import compute_candidates
from tp.metrics import merge_metrics

CASE = "wa_2025"
EVENT_ID = "wa-2025-12"
COLLECTION = "NISAR_L2_GUNW_BETA_V1"
GRID_COG = "wa_2025_nisar_pre_20251204_60m.tif"   # target 60 m EPSG:32610 grid
GRID_CRS = "EPSG:32610"
CACHE_DIR = Path.home() / ".cache" / "lead-analysis" / "nisar-coh"

GUNW_BASE = "science/LSAR/GUNW/grids/frequencyA/wrappedInterferogram"
COH_REL = f"{GUNW_BASE}/HH/coherenceMagnitude"
MASK_REL = f"{GUNW_BASE}/mask"

GRANULES = {
    "ref": {
        "064": "NISAR_L2_PR_GUNW_005_172_D_064_006_4000_SH_20251122T031814_20251122T031849_20251204T031815_20251204T031850_X05010_N_F_J_001",
        "065": "NISAR_L2_PR_GUNW_005_172_D_065_006_4000_SH_20251122T031848_20251122T031923_20251204T031849_20251204T031924_X05010_N_F_J_001",
        "pair": "2025-11-22/2025-12-04",
    },
    "event": {
        "064": "NISAR_L2_PR_GUNW_006_172_D_064_007_4000_SH_20251204T031815_20251204T031850_20251216T031815_20251216T031850_X05010_N_F_J_001",
        "065": "NISAR_L2_PR_GUNW_006_172_D_065_007_4000_SH_20251204T031849_20251204T031924_20251216T031849_20251216T031924_X05010_N_F_J_001",
        "pair": "2025-12-04/2025-12-16",
    },
}

COH_REF_MIN = float(threshold("coherence_ref_min", 0.4))
COH_DROP = float(threshold("coherence_drop", 0.3))
MAX_ELEV_M = float(threshold("coh_max_elev_m", 600.0))
MMU_HA = float(threshold("mmu_ha", 3))
HV_DROP_DB = float(threshold("hv_drop_db", 2.0))
STEEP_DEG = float(threshold("slope_range_deg", [15, 30])[0])
CELL_M = 60.0
CELL_KM2 = CELL_M * CELL_M / 1e6
MMU_PX = int(math.ceil(MMU_HA * 1e4 / (CELL_M * CELL_M)))  # 3 ha -> 9 px @60 m
LAYER_ID = "nisar_coherence_loss"
EVENT_LAYER_ID = "nisar_coherence_event"
LAYER_DATE = "2025-12-16"                 # event pair secondary date
LOSS_COLOR = "#e6550d"
ATTRIBUTION = "NISAR (NASA/ISRO), ASF DAAC — Beta product"
NOTE = ("12-day L-band coherence loss Dec 4-16 vs Nov 22-Dec 4 (GUNW "
        "wrappedInterferogram HH, 20 m -> 60 m). Candidates, never "
        "confirmed: winter decorrelation also comes from rain, snow and "
        "forest change. Water and elevation >= 600 m excluded.")

NASADEM_DOI = "10.5067/MEaSUREs/NASADEM/NASADEM_HGT.001"


def _grid(case_dir: Path):
    """(crs, transform, height, width) of the target 60 m EPSG:32610 grid."""
    import rasterio

    with rasterio.open(case_dir / "cog" / GRID_COG) as src:
        return src.crs, src.transform, src.height, src.width


def _h5_url(gid: str) -> str:
    """Direct HTTPS URL of the granule HDF5 on ASF's TEA host (CMR-verified
    link pattern)."""
    return (f"https://nisar.asf.earthdatacloud.nasa.gov/NISAR/"
            f"{COLLECTION}/{gid}/{gid}.h5")


def verify_paths_once(gid: str) -> None:
    """One h5py visit (earthaccess fsspec file -> h5py fileobj) confirming
    the COH_REL/MASK_REL datasets exist — range reads only."""
    import earthaccess
    import h5py

    earthaccess.login(strategy="environment")
    res = earthaccess.search_data(
        short_name=COLLECTION, granule_name=f"{gid}*")
    if not res:
        raise RuntimeError(f"CMR has no granule {gid}")
    for f in earthaccess.open(res):
        name = getattr(f, "path", None) or getattr(f, "full_name", "") or ""
        if str(name).endswith(f"{gid}.h5"):
            with h5py.File(f, "r") as h5:
                missing = [p for p in (COH_REL, MASK_REL) if p not in h5]
                if missing:
                    raise RuntimeError(
                        f"{gid}: missing HDF5 datasets {missing}")
            return
    raise RuntimeError(f"earthaccess.open({gid}) returned no .h5 link")


def _gdal_env() -> dict:
    """GDAL /vsicurl/ env carrying the EDL creds (bearer on TEA only; the
    signed redirect URL authenticates itself)."""
    from fetch.edl import ensure_earthdata, gdal_auth_env

    env = gdal_auth_env(ensure_earthdata())
    # ASF TEA 303s to a signed URL on HEAD too — GDAL would read the empty
    # redirect body as the file. First request must be a byte-range GET.
    env["CPL_VSIL_CURL_USE_HEAD"] = "NO"
    return env


def _window(src, grid) -> tuple:
    """Slice covering the target grid (+pad) on the GUNW 20 m grid, and the
    window's affine transform."""
    from affine import Affine
    from rasterio.transform import array_bounds

    _, dtr, dh, dw = grid
    left, bottom, right, top = array_bounds(dh, dw, dtr)
    sx, sy = src.transform.a, src.transform.e
    pad = 16
    c0 = max(int(np.floor((left - src.transform.c) / sx)) - pad, 0)
    c1 = min(int(np.ceil((right - src.transform.c) / sx)) + pad, src.width)
    # y axis descends (sy < 0)
    r0 = max(int(np.floor((top - src.transform.f) / sy)) - pad, 0)
    r1 = min(int(np.ceil((bottom - src.transform.f) / sy)) + pad,
             src.height)
    win_tr = src.transform * Affine.translation(c0, r0)
    return (r0, r1, c0, c1), win_tr


def _reproject(arr, src_tr, grid, resampling, nodata=np.nan):
    """Reproject one 20 m window onto the target 60 m grid."""
    from rasterio.warp import reproject

    crs, dtr, dh, dw = grid
    dst = np.full((dh, dw), nodata, dtype="float32")
    reproject(
        np.asarray(arr, dtype="float32"), dst,
        src_transform=src_tr, src_crs=GRID_CRS, src_nodata=np.nan,
        dst_transform=dtr, dst_crs=crs, dst_nodata=nodata,
        resampling=resampling,
    )
    return dst


def read_pair(gid_by_frame: dict, grid, pair: str) -> dict:
    """(coh, water, valid) mosaicked onto the target grid for one pair.

    Range reads only: GDAL 'NETCDF:"/vsicurl/<h5_url>":<dataset>' windows —
    no whole-granule download (h5py fileobj worked for layout/coords but the
    pre-signed URL expired mid-read on multi-minute hyperslab fetches).
    """
    import rasterio
    from rasterio.warp import Resampling
    from rasterio.windows import Window

    env = _gdal_env()
    coh = water = valid = None
    used = []
    for frame, gid in gid_by_frame.items():
        url = _h5_url(gid)
        with rasterio.Env(**env):
            with rasterio.open(
                    f'NETCDF:"/vsicurl/{url}":/{COH_REL}') as src:
                (r0, r1, c0, c1), win_tr = _window(src, grid)
                if r1 <= r0 or c1 <= c0:
                    continue
                c = src.read(1, window=Window(c0, r0, c1 - c0, r1 - r0))
            with rasterio.open(
                    f'NETCDF:"/vsicurl/{url}":/{MASK_REL}') as src:
                m = src.read(1, window=Window(c0, r0, c1 - c0, r1 - r0))
        c60 = _reproject(c, win_tr, grid, Resampling.average)
        m = m.astype("int32")
        # GUNW mask: three-digit code — MSD water flag (1=water), then the
        # reference/secondary subswath digits (0 = invalid); 255 is fill.
        w60 = _reproject((m // 100 == 1).astype("float32"),
                         win_tr, grid, Resampling.nearest, nodata=0.0)
        v60 = _reproject(
            (((m // 10) % 10 > 0) & (m % 10 > 0) & (m < 200))
            .astype("float32"),
            win_tr, grid, Resampling.nearest, nodata=0.0)
        # nearest reproject leaks denormal bit patterns (~1e-45) for 0 —
        # threshold at 0.5, never "> 0".
        if coh is None:
            coh, water, valid = c60, w60 >= 0.5, v60 >= 0.5
        else:
            fill = ~np.isfinite(coh) & np.isfinite(c60)
            coh[fill] = c60[fill]
            water |= w60 >= 0.5
            valid |= v60 >= 0.5
        used.append((frame, gid))
    return {"coh": coh, "water": water, "valid": valid, "granules": used,
            "pair": pair}


def _nasadem(cache: Path, aoi_bbox: tuple) -> Path:
    """NASADEM_HGT.001 tiles covering the AOI -> merged mosaic .hgt path.

    Small tiles (~MBs, not granules) are downloaded to the cache dir via
    earthaccess and merged into one GTiff (EPSG:4326, 1 arc-second).
    """
    import earthaccess
    import rasterio
    from rasterio.merge import merge

    out = cache / "nasadem_wa.tif"
    if out.exists():
        return out
    earthaccess.login(strategy="environment")
    res = earthaccess.search_data(doi=NASADEM_DOI, bounding_box=aoi_bbox)
    if not res:
        raise RuntimeError("no NASADEM_HGT.001 tiles found for the AOI")
    zips = earthaccess.download(res, local_path=str(cache / "nasadem"))
    hgts = []
    for zp in zips:
        zp = Path(zp)
        if zp.suffix.lower() != ".zip":
            continue
        with zipfile.ZipFile(zp) as z:
            for name in z.namelist():
                if name.lower().endswith(".hgt"):
                    z.extract(name, cache / "nasadem")
                    hgts.append(cache / "nasadem" / name)
    if not hgts:
        raise RuntimeError("NASADEM zips carried no .hgt tiles")
    srcs = [rasterio.open(h) for h in hgts]
    mosaic, mtr = merge(srcs)
    for s in srcs:
        s.close()
    with rasterio.open(
        out, "w", driver="GTiff", height=mosaic.shape[1],
        width=mosaic.shape[2], count=1, dtype="int16",
        crs="EPSG:4326", transform=mtr, nodata=-32768,
        tiled=True, compress="deflate",
    ) as dst:
        dst.write(mosaic[0], 1)
    return out


def _dem_on_grid(dem_path: Path, grid) -> np.ndarray:
    """NASADEM mosaic -> bilinear elevation on the 60 m grid."""
    import rasterio
    from rasterio.vrt import WarpedVRT
    from rasterio.warp import Resampling

    crs, dtr, dh, dw = grid
    with rasterio.open(dem_path) as src, WarpedVRT(
            src, crs=crs, transform=dtr, width=dw, height=dh,
            resampling=Resampling.bilinear, nodata=np.nan) as vrt:
        return vrt.read(1).astype("float32")


def _slope_on_grid(dem: np.ndarray, grid) -> np.ndarray:
    """Horn slope (deg) from the gridded DEM via tp.terrain."""
    import rasterio
    from rasterio.io import MemoryFile
    from tp.terrain import slope_deg

    crs, dtr, dh, dw = grid
    profile = {"driver": "GTiff", "height": dh, "width": dw, "count": 1,
               "dtype": "float32", "crs": crs, "transform": dtr,
               "nodata": np.nan}
    with MemoryFile() as mf:
        with mf.open(**profile) as ds:
            ds.write(dem, 1)
        with mf.open() as ds:
            slope, _ = slope_deg(ds)
    return slope


def _cog(arr: np.ndarray, grid, path: Path, dtype="float32") -> Path:
    """Write one band of the target grid as a COG (GTiff fallback)."""
    import rasterio

    crs, dtr, dh, dw = grid
    nodata = np.nan if dtype == "float32" else 0
    profile = {"driver": "COG", "height": dh, "width": dw, "count": 1,
               "dtype": dtype, "crs": crs, "transform": dtr,
               "nodata": nodata, "compress": "deflate"}
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.Env():
        try:
            with rasterio.open(path, "w", **profile) as dst:
                dst.write(np.asarray(arr, dtype=dtype), 1)
        except rasterio.errors.DriverRegistrationError:
            profile.update(driver="GTiff", tiled=True)
            with rasterio.open(path, "w", **profile) as dst:
                dst.write(np.asarray(arr, dtype=dtype), 1)
    return path


def _reproject_match(arr_path: Path, grid, resampling):
    """Reproject an external raster onto the target grid."""
    import rasterio
    from rasterio.warp import reproject

    crs, dtr, dh, dw = grid
    with rasterio.open(arr_path) as src:
        dst = np.zeros((dh, dw), dtype="float32")
        reproject(src.read(1), dst, src_transform=src.transform,
                  src_crs=src.crs, dst_transform=dtr, dst_crs=crs,
                  resampling=resampling, dst_nodata=0.0)
        return dst


def _loss_png(poly_mask: np.ndarray, grid) -> tuple:
    """EPSG:3857 RGBA PNG of the loss polygons + lon/lat bounds."""
    from live.render import palette_png_bytes, warp_to_3857

    crs, dtr, _, _ = grid
    cls3857, _, bounds = warp_to_3857(
        np.asarray(poly_mask, dtype="uint8"), dtr, src_crs=str(crs))
    return (palette_png_bytes(cls3857, cls3857 == 1, {1: LOSS_COLOR}),
            bounds)


def _event_gray_png(coh: np.ndarray, grid) -> tuple:
    """Grayscale EPSG:3857 PNG of event coherence (0 black -> 1 white)."""
    import rasterio
    from live.render import warp_to_3857

    crs, dtr, _, _ = grid
    v = np.nan_to_num(np.asarray(coh, dtype="float32"), nan=0.0)
    a3857, _, bounds = warp_to_3857(v, dtr, src_crs=str(crs))
    val = warp_to_3857(np.isfinite(coh).astype("uint8"), dtr,
                       src_crs=str(crs))[0]
    g = (np.clip(a3857, 0, 1) * 255).astype("uint8")
    rgba = np.stack([g, g, g, np.where(val > 0, 255, 0).astype("uint8")])
    with rasterio.MemoryFile() as mf:
        with mf.open(driver="PNG", height=g.shape[0], width=g.shape[1],
                     count=4, dtype="uint8") as dst:
            dst.write(rgba)
        return mf.read(), bounds


def _index_entry(layer_id: str, png_rel: str, cog_rel: str | None,
                 bounds, legend, note: str) -> dict:
    return {"id": layer_id, "date": LAYER_DATE, "png": png_rel,
            "cog": cog_rel, "bounds": bounds, "forecast": False,
            "kind": "nisar_extra", "label": "NISAR coherence loss (candidates)"
            if layer_id == LAYER_ID else "NISAR coherence (event pair)",
            "legend": legend, "attribution": ATTRIBUTION, "note": note}


def _upsert(layers: list, entry: dict) -> list:
    return [e for e in layers
            if not (e.get("id") == entry["id"]
                    and e.get("date") == entry["date"])] + [entry]


def _catalog_points(case_dir: Path) -> tuple[list, list]:
    """The 15 WGS landslide points -> (lons, lats)."""
    fc = json.loads(
        (case_dir / "geojson" / "landslide_catalog.geojson").read_text())
    coords = []
    for f in fc.get("features", []):
        g = f.get("geometry") or {}
        if g.get("type") == "MultiPoint":
            coords.extend(g.get("coordinates", []))
        elif g.get("type") == "Point":
            coords.append(g.get("coordinates"))
    return [c[0] for c in coords], [c[1] for c in coords]


def _poly_features(mask, slope, flood_dilated, grid, min_px: int,
                   delta: np.ndarray, hv_vote: np.ndarray):
    """MMU-filtered polygons -> (EPSG:4326 GeoJSON features, label raster,
    kept label ids)."""
    import geopandas as gpd
    from shapely.geometry import shape

    geoms, labels, sizes = loss_polygons(mask, grid[1], min_px)
    feats = []
    for lid, geom in geoms:
        comp = labels == lid
        sl = slope[comp]
        mean_slope = float(np.nanmean(sl)) if np.isfinite(sl).any() else None
        feats.append({
            "type": "Feature",
            "geometry": shape(geom),
            "properties": {
                "status": "candidate",
                "source": "NISAR GUNW coherence loss",
                "area_km2": round(int(sizes[lid]) * CELL_KM2, 4),
                "px": int(sizes[lid]),
                "slope_class": f">={STEEP_DEG:g} deg"
                if (mean_slope or 0) >= STEEP_DEG else f"<{STEEP_DEG:g} deg",
                "mean_slope_deg": round(mean_slope, 1)
                if mean_slope is not None else None,
                "mean_coh_drop": round(float(np.nanmean(delta[comp])), 3),
                "hv_vote": bool((hv_vote[comp]).any()),
                "near_flood_candidate": bool((flood_dilated[comp]).any()),
            },
        })
    if not feats:
        return [], labels, []
    gdf = gpd.GeoDataFrame.from_features(feats, crs=GRID_CRS).to_crs(4326)
    return (json.loads(gdf.to_json())["features"], labels,
            [lid for lid, _ in geoms])


def run(no_fetch: bool = False) -> dict:
    import rasterio
    from rasterio.warp import Resampling
    from scipy.ndimage import binary_dilation

    case_dir = DATA_DIR / CASE
    grid = _grid(case_dir)
    event = load_event(EVENT_ID)
    aoi = tuple(event["aoi"]["bbox"])

    coh_dir = CACHE_DIR
    coh_dir.mkdir(parents=True, exist_ok=True)
    ref_npz, evt_npz = coh_dir / "coh_ref.npz", coh_dir / "coh_evt.npz"
    if no_fetch and ref_npz.exists() and evt_npz.exists():
        ref = dict(np.load(ref_npz))
        evt = dict(np.load(evt_npz))
        used = {"ref": GRANULES["ref"], "event": GRANULES["event"]}
    else:
        verify_paths_once(next(iter(
            {k: v for k, v in GRANULES["event"].items()
             if k != "pair"}.values())))
        for tag, npz in (("ref", ref_npz), ("event", evt_npz)):
            frames = {k: v for k, v in GRANULES[tag].items() if k != "pair"}
            if npz.exists():
                continue
            out = read_pair(frames, grid, GRANULES[tag]["pair"])
            np.savez(npz, coh=out["coh"], water=out["water"],
                     valid=out["valid"])
        ref = dict(np.load(ref_npz))
        evt = dict(np.load(evt_npz))
        used = {"ref": GRANULES["ref"], "event": GRANULES["event"]}
    coh_ref, coh_evt = ref["coh"], evt["coh"]
    water = ref["water"] | evt["water"]
    valid = ref["valid"] & evt["valid"]

    dem = _dem_on_grid(_nasadem(CACHE_DIR, aoi), grid)
    slope = _slope_on_grid(dem, grid)

    water_gcov = _reproject_match(
        case_dir / "cog" / "wa_2025_water_nisar_20251210.tif", grid,
        Resampling.nearest) >= 0.5
    water |= water_gcov
    flood = _reproject_match(
        case_dir / "cog" / "wa_2025_flood_nisar_20251210.tif", grid,
        Resampling.nearest) >= 0.5
    opera = np.rint(_reproject_match(
        case_dir / "cog" / "wa_2025_opera_validation_20251216.tif", grid,
        Resampling.nearest)).astype("uint8")
    hv_pre = _reproject_match(
        case_dir / "cog" / "wa_2025_nisar_hv_pre_20251204_60m.tif", grid,
        Resampling.bilinear)
    hv_post = _reproject_match(
        case_dir / "cog" / "wa_2025_nisar_hv_post_20251216_60m.tif", grid,
        Resampling.bilinear)
    hv_drop = hv_pre.astype("float32") - hv_post.astype("float32")  # dB COGs

    classes_raw = compute_candidates(
        np.where(valid, coh_ref, np.nan), np.where(valid, coh_evt, np.nan),
        slope, flood=water, elev=dem, hv_drop_db=hv_drop,
        min_drop=COH_DROP, min_slope_deg=0.0, min_ref_coh=COH_REF_MIN,
        max_elev_m=MAX_ELEV_M)
    # NISAR flood-candidate pixels are excluded from landslide candidates
    # (landslide_coh.py semantics); overlap is measured pre-exclusion.
    classes = np.where(flood, 0, classes_raw)
    loss_raw = classes_raw > 0
    loss = classes > 0
    hv_vote = classes == 2
    delta = coh_ref - coh_evt

    cog_dir = case_dir / "cog"
    _cog(coh_ref.astype("float32"), grid,
         cog_dir / "wa_2025_nisar_coherence_ref_60m.tif")
    _cog(coh_evt.astype("float32"), grid,
         cog_dir / "wa_2025_nisar_coherence_event_60m.tif")
    _cog(classes.astype("uint8"), grid,
         cog_dir / "wa_2025_nisar_coherence_loss_60m.tif", dtype="uint8")

    steep = np.isfinite(slope) & (slope >= STEEP_DEG)
    loss_km2 = float(loss.sum()) * CELL_KM2
    loss_steep_km2 = float((loss & steep).sum()) * CELL_KM2
    flood_ov = float((loss_raw & flood).sum()) * CELL_KM2
    opera_water = (opera == 1) | (opera == 3)   # OPERA says new water
    opera_ov = float((loss & opera_water).sum()) * CELL_KM2

    flood_dil = binary_dilation(flood, iterations=2)  # ~120 m adjacency
    feats, labels, keep_ids = _poly_features(
        loss, slope, flood_dil, grid, MMU_PX, delta, hv_vote)
    geojson_path = case_dir / "geojson" / "nisar_coherence_candidates.geojson"
    geojson_path.write_text(json.dumps(
        {"type": "FeatureCollection", "features": feats},
        ensure_ascii=False, indent=1) + "\n")

    lons, lats = _catalog_points(case_dir)
    hits = hit_stats(loss, grid[1], grid[0], lons, lats)
    base = baseline_hit_pct(steep & valid & ~water, int(loss.sum()),
                            grid[1], grid[0], lons, lats, n_seeds=200)

    poly_mask = np.isin(labels, keep_ids)
    png_bytes, bounds = _loss_png(poly_mask.astype("uint8"), grid)
    png_rel = f"{CASE}/png/{LAYER_ID}_20251216.png"
    (case_dir / "png" / Path(png_rel).name).write_bytes(png_bytes)
    gray_bytes, _ = _event_gray_png(coh_evt, grid)
    gray_rel = f"{CASE}/png/{EVENT_LAYER_ID}_20251216.png"
    (case_dir / "png" / Path(gray_rel).name).write_bytes(gray_bytes)

    cog_loss_rel = f"{CASE}/cog/{CASE}_nisar_coherence_loss_60m.tif"
    cog_evt_rel = f"{CASE}/cog/{CASE}_nisar_coherence_event_60m.tif"
    index_path = case_dir / "png" / "_index.json"
    layers = json.loads(index_path.read_text()).get("layers", [])
    layers = _upsert(layers, _index_entry(
        LAYER_ID, png_rel, cog_loss_rel, bounds,
        [{"label": "coherence loss (candidate)", "color": LOSS_COLOR}],
        NOTE))
    layers = _upsert(layers, _index_entry(
        EVENT_LAYER_ID, gray_rel, cog_evt_rel, bounds,
        [{"label": "coherence (event pair, 0-1)", "color": "#888888"}],
        "GUNW HH coherenceMagnitude of the event-crossing pair "
        "2025-12-04 -> 2025-12-16, grayscale. Beta product."))
    index_path.write_text(json.dumps({"layers": layers}, indent=2) + "\n")

    stats = {
        "case": CASE, "event_id": EVENT_ID,
        "generated_at": datetime.now(UTC).isoformat(),
        "layer": LAYER_ID,
        "collection": COLLECTION, "maturity": "Beta",
        "attribution": ATTRIBUTION,
        "pairs": {"ref": GRANULES["ref"]["pair"],
                  "event": GRANULES["event"]["pair"]},
        "granules": {"ref": [g for g in GRANULES["ref"].values()
                             if isinstance(g, str) and g.startswith("NISAR")],
                     "event": [g for g in GRANULES["event"].values()
                               if isinstance(g, str) and g.startswith("NISAR")]},
        "granules_used": used,
        "access": ("range reads only: dataset paths verified with one h5py "
                   "visit (earthaccess fsspec fileobj); data streamed via "
                   "GDAL NETCDF:/vsicurl/ windows; no whole-granule "
                   "download"),
        "grid": {"crs": GRID_CRS, "res_m": CELL_M,
                 "matches": f"cog/{GRID_COG}"},
        "thresholds": {"coherence_ref_min": COH_REF_MIN,
                       "coherence_drop": COH_DROP,
                       "max_elev_m": MAX_ELEV_M, "mmu_ha": MMU_HA,
                       "mmu_px_60m": MMU_PX, "hv_drop_db": HV_DROP_DB},
        "coverage_px": {"valid_both_pairs": int(valid.sum()),
                        "water_excluded": int((valid & water).sum()),
                        "elev_excluded": int(
                            (valid & ~water & np.isfinite(dem)
                             & (dem >= MAX_ELEV_M)).sum())},
        "loss": {"px": int(loss.sum()), "km2": round(loss_km2, 3),
                 "steep_km2": round(loss_steep_km2, 3),
                 "gentle_km2": round(loss_km2 - loss_steep_km2, 3),
                 "high_conf_px": int(hv_vote.sum()),
                 "polygons_n": len(feats)},
        "flood_overlap": {"km2": round(flood_ov, 3),
                          "pct_of_loss": round(100 * flood_ov / loss_km2, 2)
                          if loss_km2 else 0.0,
                          "mask": "cog/wa_2025_flood_nisar_20251210.tif",
                          "measured": ("pre-flood-exclusion loss mask; "
                                       "flood pixels are removed from "
                                       "candidates")},
        "opera_new_water_overlap": {
            "km2": round(opera_ov, 3),
            "pct_of_loss": round(100 * opera_ov / loss_km2, 2)
            if loss_km2 else 0.0,
            "mask": ("cog/wa_2025_opera_validation_20251216.tif classes "
                     "1 (TP) + 3 (FN) = OPERA DSWx-S1 new water")},
        "landslide_hits": {
            "catalog": "geojson/landslide_catalog.geojson",
            **hits, "baseline": base,
            "baseline_basis": ("same loss area as random cells on steep "
                               "(>=15 deg), valid, non-water terrain; "
                               "200 seeds")},
        "caveats": [
            "Beta product (pre-calibration); use relative change only.",
            "12-day L-band decorrelation in December also comes from rain, "
            "snow (below the 600 m cap during warm storms), soil moisture "
            "and forest movement — candidates, never confirmed.",
            "GUNW mask water flag covers the reference scene's water mask; "
            "union with the NISAR GCOV water layer used here.",
        ],
        "bounds": bounds,
    }
    stats_path = case_dir / "nisar_coherence.json"
    stats_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2)
                          + "\n")

    merge_metrics(EVENT_ID, flat={
        "nisar_coh_loss_km2": round(loss_km2, 3),
        "nisar_coh_loss_steep_km2": round(loss_steep_km2, 3),
        "nisar_coh_flood_overlap_pct": stats["flood_overlap"]["pct_of_loss"],
        "nisar_coh_landslide_hit_100m_pct": hits["hit_100m_pct"],
        "nisar_coh_landslide_hit_500m_pct": hits["hit_500m_pct"],
        "nisar_coh_landslide_hit_100m_baseline_pct":
            base["hit_100m_mean_pct"],
        "nisar_coh_landslide_hit_500m_baseline_pct":
            base["hit_500m_mean_pct"],
        "nisar_coh_threshold": {
            "coherence_ref_min": COH_REF_MIN, "coherence_drop": COH_DROP,
            "max_elev_m": MAX_ELEV_M, "mmu_ha": MMU_HA},
        "nisar_coherence": stats,
        "nisar_coh_method": (
            "GUNW wrappedInterferogram/HH coherenceMagnitude (20 m) "
            "ref 2025-11-22->12-04 vs event 2025-12-04->12-16; loss = "
            f"coh_ref>={COH_REF_MIN} AND delta>={COH_DROP}; water + "
            f"elev>={MAX_ELEV_M:g} m excluded; {CELL_M:g} m {GRID_CRS}"),
    }, collections={
        "nisar_gunw_coherence": {
            "collection": COLLECTION, "maturity": "Beta",
            "ref": GRANULES["ref"]["pair"], "event": GRANULES["event"]["pair"],
            "frames": ["064", "065"]}})
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="tools.wa_nisar_coherence",
                               description=__doc__.splitlines()[0])
    ap.add_argument("--no-fetch", action="store_true",
                    help="reuse cached coherence grids under "
                    "~/.cache/lead-analysis/nisar-coh/")
    args = ap.parse_args(argv)
    stats = run(no_fetch=args.no_fetch)
    loss = stats["loss"]
    print(f"loss {loss['km2']} km2 ({loss['polygons_n']} polygons >= MMU), "
          f"steep {loss['steep_km2']} km2; flood overlap "
          f"{stats['flood_overlap']['pct_of_loss']}%; hits "
          f"{stats['landslide_hits']['hit_100m_pct']}% @100 m / "
          f"{stats['landslide_hits']['hit_500m_pct']}% @500 m")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
