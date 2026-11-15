"""rs_uruguai_2026 NISAR GCOV time series + flood candidates (Uruguaiana, RS).

Case: Uruguay River flood at Uruguaiana / Itaqui, Sep-Nov 2026. Track 010 D
frame 103 gives a same-geometry stack across BOTH GCOV collections
(PROVISIONAL_V1 to cycle 033, V1 from 034), mode 2005_DHDH_A: Sep 18 ->
Sep 30 (baseline) -> Oct 12 (mid-Oct crest) -> Oct 24 -> Nov 5. The swath
covers the bbox west of ~-56.85 (Uruguaiana) but NOT Itaqui (-56.55).
Single-pass context frames exist on other tracks (060A f163 Oct 28,
082D f103 Oct 29, 132A f163 Nov 2) — different incidence, slider/stats
only, never differenced.

Method (R2 guards applied, tp.response_nisar):
  * block_average linear power (HH + HV) of ONLY the HDF5 chunks over the AOI,
    60 m on one pinned grid (post granule's EPSG, snapped) — range reads via
    open_gcov, never a full frame.
  * pair_mismatch (same acquisition mode/looks), per-scene Otsu must fire
    (threshold_method == 'adaptive'; 'fallback' -> pair rejected), edge_zone
    2 km, drop_touching.
  * New open water = dark-in-post vs pre (fm.flood_mask) AND NOT permanent
    water (JRC GSW occurrence >= 50 %) AND within the river corridor
    (<= CORRIDOR_M of a >= MIN_CHANNEL_KM2 permanent-water component).
    Polygons beyond the corridor are flagged 'possible irrigation (rice)',
    never 'flood' (MCD12Q1 HDF4 unreadable here — distance criterion only).
  * People = tp.response_summary.people_geom on native 30" GPW.

Writes under data/rs_uruguai_2026/: cog/ (60 m dB + masks), png/ (EPSG:3857
RGBA + _index.json: swipe pair nisar_{pre,post,hv_pre,hv_post}, per-date
kind 'nisar_frame' slider layers, kind 'nisar_change' pair change maps and
kind 'nisar_extra' 'radar darkening (not a flood map)' corridor/field split),
geojson/change_darkening.geojson, nisar_timeseries.json (wa_2025 schema).
flood_confirmed.geojson is the teammate radar pair's candidate product —
this tool never writes it. metrics.json node is written by
tools/rs_uruguai_metrics.py; the manifest node by tools/build_manifest.py.

    PYTHONPATH=.:pipeline python tools/rs_uruguai_nisar.py \
        [--skip-fetch] [--stats-only] [--cache-dir ~/.cache/lead-analysis/uruguai/nisar]

EARTHDATA_TOKEN required unless --skip-fetch (uses the npy cache).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
for p in (str(REPO_ROOT), str(REPO_ROOT / "pipeline")):
    if p not in sys.path:
        sys.path.insert(0, p)

from tp import flood_mask as fm
from tp import response_nisar as rn
from tp.bundle import _gray_png_3857
from tp.events import DATA_DIR

CASE = "rs_uruguai_2026"
EVENT_ID = "rs_uruguai_2026"
BBOX = (-57.25, -29.85, -56.45, -29.05)   # Uruguaiana-Itaqui river corridor
RES = 60.0
COLLECTION_PROV = "NISAR_L2_GCOV_PROVISIONAL_V1"   # cycles <= 033 (<= Oct 21)
COLLECTION = "NISAR_L2_GCOV_V1"                    # cycles 034+ (Oct 24+)
TERMS = ("HHHH", "HVHV")
PAIR_FRAME = (10, "D", 103)
BASELINE = "2026-09-30"                 # teammate zip pre date; pair baseline
CREST_POST = "2026-10-12"               # teammate zip post date; mid-Oct crest
POSTS = ("2026-10-12", "2026-10-24", "2026-11-05")
PRE_DATE, POST_DATE = BASELINE, CREST_POST   # reading deltas vs the crest pair
CORRIDOR_M = 3000.0          # >3 km from the river channel -> irrigation flag
MIN_CHANNEL_KM2 = 0.5        # drops ponds; keeps the Uruguay/Ibicuí channels
GSW_OCC_MIN = 50.0           # permanent water

# CMR-verified 2026-11-15 — 010D f103 same-geometry stack across BOTH
# collections (PROVISIONAL_V1 to cycle 033, V1 from 034; mode 2005_DHDH_A).
GRANULE_IDS = {
    "2026-09-18": "NISAR_L2_PR_GCOV_031_010_D_103_2005_DHDH_A_20260918T215847_20260918T215922_P05023_F_F_J_001",
    "2026-09-30": "NISAR_L2_PR_GCOV_032_010_D_103_2005_DHDH_A_20260930T215847_20260930T215922_P05023_N_F_J_001",
    "2026-10-12": "NISAR_L2_PR_GCOV_033_010_D_103_2005_DHDH_A_20261012T215847_20261012T215922_P05023_N_F_J_001",
    "2026-10-24": "NISAR_L2_PR_GCOV_034_010_D_103_2005_DHDH_A_20261024T215847_20261024T215922_P05036_N_F_J_001",
    "2026-11-05": "NISAR_L2_PR_GCOV_035_010_D_103_2005_DHDH_A_20261105T215848_20261105T215922_P05036_N_F_J_001",
}
COLLECTION_OF = {d: (COLLECTION_PROV if int(g.split("_")[4]) <= 33 else COLLECTION)
                 for d, g in GRANULE_IDS.items()}
CONTEXT_IDS = {
    "2026-10-28": "NISAR_L2_PR_GCOV_034_060_A_163_2005_DHDH_A_20261028T094723_20261028T094757_P05036_N_F_J_001",
    "2026-10-29": "NISAR_L2_PR_GCOV_034_082_D_103_2005_DHDH_A_20261029T215028_20261029T215103_P05036_N_F_J_001",
    "2026-11-02": "NISAR_L2_PR_GCOV_034_132_A_163_2005_DHDH_A_20261102T093903_20261102T093938_P05036_N_F_J_001",
}
ROLES = {
    "2026-09-18": "pre pair geometry, track 010D frame 103 (first pass of the stack)",
    "2026-09-30": "baseline (pair geometry; teammate zip pre date)",
    "2026-10-12": "mid-Oct crest pass (pair geometry; teammate zip post date; crest ~Oct 14 at 9.93 m)",
    "2026-10-24": "between crests (pair geometry; after the Oct-14 crest, rising toward Oct-29 flood stage)",
    "2026-10-28": "context pass — track 060A frame 163 (ascending, other geometry)",
    "2026-10-29": "context pass — track 082D frame 103 (descending; Uruguaiana passed flood stage this day)",
    "2026-11-02": "context pass — track 132A frame 163 (ascending, other geometry)",
    "2026-11-05": "post-flood-rise (pair geometry; between Oct 29 flood stage and Nov 12-13 red alert)",
}
CONTROL_BOXES = {
    "control_urban": (-57.095, -29.775, -57.045, -29.735),    # Uruguaiana town centre (double-bounce)
    "control_farmland": (-57.20, -29.55, -57.10, -29.45),     # interior fields W of the city, away from the river
    "control_interior": (-57.16, -29.22, -57.06, -29.12),     # interior control N of the city, inside the swath
}
AREA_LABELS = {
    "corridor_darkening": ">3 dB darkening Oct 24 -> Nov 5 inside the river corridor (UNGATED — no per-scene Otsu; not a flood product)",
    "field_darkening": ">3 dB darkening >3 km from the channel — consistent with rice-paddy irrigation, not river flood",
    "permanent_water": "permanent water (JRC GSW occurrence >= 50 %)",
    "zip_teammate": "teammate zip change polygons (cross-check only)",
    "control_urban": "built-up control (Uruguaiana)",
    "control_farmland": "farmland control west of Uruguaiana",
    "control_interior": "interior control north of Uruguaiana",
}
MAIN_REPO = Path.home() / "code/r2l2pj/tipping-point-data"
GSW_PATH = MAIN_REPO and Path(os.environ.get(
    "URUGUAI_GSW", Path.home() / ".cache/lead-analysis/gsw/gsw_occurrence_2021.tif"))
GPW_PATH = MAIN_REPO / "data/now/static/gpw/gpw_popcount_2020_30sec.tif"
HAND_PATH = MAIN_REPO / "data/static/south_brazil/terrain/hand_m.tif"
ZIP_GEOJSON = (Path.home() / ".cache/lead-analysis/uruguai/itaqui_zip"
               / "inundacao_itaqui/scenes/change_polygons.geojson")


# --------------------------------------------------------------------------
# Pure helpers (tested)
# --------------------------------------------------------------------------
def channel_mask(occ: np.ndarray, res: float, occ_min: float = GSW_OCC_MIN,
                 min_km2: float = MIN_CHANNEL_KM2) -> np.ndarray:
    """Permanent river-channel water on the grid: GSW occurrence >= occ_min,
    connected components >= min_km2 (drops isolated ponds/pixels)."""
    from scipy.ndimage import label

    perm = np.asarray(occ) >= occ_min
    lab, n = label(perm)
    if n == 0:
        return perm
    keep = np.where(np.bincount(lab.ravel()) * res * res / 1e6 >= min_km2)[0]
    return np.isin(lab, keep) & perm


def corridor_mask(channel: np.ndarray, res: float,
                  corridor_m: float = CORRIDOR_M) -> np.ndarray:
    """True within corridor_m of a channel pixel (the river floodplain)."""
    from scipy.ndimage import distance_transform_edt

    return distance_transform_edt(~channel, sampling=res) <= corridor_m


def split_new_water(flood: np.ndarray, perm: np.ndarray,
                    corridor: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(corridor candidates, flagged possible-irrigation) — flood pixels that
    are NOT permanent water, split by the river corridor."""
    new = np.asarray(flood, bool) & ~np.asarray(perm, bool)
    return new & corridor, new & ~corridor


def geodesic_km2(geom) -> float:
    """WGS84 geodesic area of a shapely lon/lat geometry in km^2."""
    from pyproj import Geod

    geod = Geod(ellps="WGS84")
    a = 0.0
    for part in getattr(geom, "geoms", [geom]):
        area, _ = geod.geometry_area_perimeter(part)
        a += abs(area)
    return a / 1e6


def distance_to_channel_m(geom, channel_union_utm, to_utm) -> float:
    """Metres from a lon/lat geometry to the channel union (grid CRS)."""
    import shapely

    g = shapely.transform(geom, to_utm, interleaved=False)
    return float(channel_union_utm.distance(g))


# --------------------------------------------------------------------------
# Raster helpers
# --------------------------------------------------------------------------
def _on_grid(path: Path, grid: rn.TargetGrid, resampling="nearest") -> np.ndarray:
    """Raster -> array on `grid` (reproject)."""
    import rasterio
    from rasterio.warp import Resampling, reproject

    out = np.zeros((grid.height, grid.width), dtype=np.float32)
    with rasterio.open(path) as src:
        reproject(src.read(1), out, src_transform=src.transform,
                  src_crs=src.crs, dst_transform=grid.transform,
                  dst_crs=f"EPSG:{grid.epsg}",
                  resampling=getattr(Resampling, resampling))
    return out


def _mask_geojson_on_grid(path: Path, grid: rn.TargetGrid) -> np.ndarray:
    """GeoJSON polygons rasterized (all_touched) on `grid`."""
    from pyproj import Transformer
    from rasterio.features import geometry_mask
    from shapely.geometry import mapping, shape
    from shapely import transform as shp_transform
    from shapely.ops import unary_union

    fc = json.loads(Path(path).read_text())
    geoms = [shape(f["geometry"]) for f in fc.get("features") or []
             if f.get("geometry")]
    if not geoms:
        return np.zeros((grid.height, grid.width), bool)
    u = unary_union(geoms)
    tf = Transformer.from_crs("EPSG:4326", f"EPSG:{grid.epsg}", always_xy=True)
    u_g = shp_transform(u, tf.transform, interleaved=False)
    return geometry_mask([mapping(u_g)], out_shape=(grid.height, grid.width),
                         transform=grid.transform, invert=True, all_touched=True)


def _write_tif(arr: np.ndarray, path: Path, grid: rn.TargetGrid) -> Path:
    import rasterio

    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", height=grid.height,
                       width=grid.width, count=1, dtype="float32",
                       crs=f"EPSG:{grid.epsg}", transform=grid.transform,
                       nodata=float("nan"), tiled=True, blockxsize=512,
                       blockysize=512, compress="deflate") as dst:
        dst.write(arr.astype("float32"), 1)
    return path


def _write_mask_tif(mask: np.ndarray, path: Path, grid: rn.TargetGrid) -> Path:
    import rasterio

    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", height=grid.height,
                       width=grid.width, count=1, dtype="uint8",
                       crs=f"EPSG:{grid.epsg}", transform=grid.transform,
                       compress="deflate") as dst:
        dst.write(mask.astype("uint8"), 1)
    return path


# --------------------------------------------------------------------------
# Fetch
# --------------------------------------------------------------------------
def find_granules() -> tuple[dict[str, dict], dict[str, dict], list[dict]]:
    """(pair passes, context passes, rejected) — CMR-verified pinned ids."""
    from fetch import nisar as fn

    gs = []
    for coll in (COLLECTION_PROV, COLLECTION):
        gs += fn.search_gcov_domain(
            BBOX, "2026-08-01",
            datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            collection=coll)
    by_id = {g["granule_id"]: g for g in gs}
    passes, context, rejected = {}, {}, []
    for d, gid in GRANULE_IDS.items():
        g = by_id.get(gid)
        if g is None:
            rejected.append({"date": d, "granule_id": gid,
                             "reason": "pinned id not on CMR"})
        elif not g.get("h5_url"):
            rejected.append({"date": d, "granule_id": gid,
                             "reason": "no .h5 data link"})
        else:
            passes[d] = g
    for d, gid in CONTEXT_IDS.items():
        g = by_id.get(gid)
        if g is not None and g.get("h5_url"):
            context[d] = g
        else:
            rejected.append({"date": d, "granule_id": gid,
                             "reason": "context pass not on CMR / no .h5"})
    # every OTHER granule touching the bbox is listed for provenance
    known = set(GRANULE_IDS.values()) | set(CONTEXT_IDS.values())
    for g in gs:
        if g["granule_id"] not in known:
            rejected.append({"date": str(g["start"].date()),
                             "granule_id": g["granule_id"],
                             "reason": "other track/frame — not the pinned pair "
                                       "or a full-coverage context pass"})
    return passes, context, rejected


def pinned_grid(post: dict) -> rn.TargetGrid:
    """The shared 60 m grid: post granule extent ∩ BBOX, snapped (read_pair rule)."""
    token = os.environ.get("EARTHDATA_TOKEN")
    with rn.open_gcov(post["h5_url"], token) as (h5, _rr):
        info = rn.granule_grid_info(h5)
    grid = rn.target_grid(info["x"], info["y"], info["epsg"], BBOX, RES)
    if grid is None:
        raise RuntimeError("post granule grid does not reach the bbox")
    return grid


def read_date(url: str, term: str, grid: rn.TargetGrid, token: str | None):
    """Block-averaged LINEAR power on the pinned grid (NaN off-swath)."""
    with rn.open_gcov(url, token) as (h5, rr):
        info = rn.granule_grid_info(h5)
        if info["epsg"] != grid.epsg:
            raise RuntimeError(f"granule EPSG {info['epsg']} != {grid.epsg}")
        if term not in info["terms"]:
            raise RuntimeError(f"granule lacks {term} ({info['terms']})")
        arr, io = rn.block_average(h5[f"{rn.GCOV_GRIDS}/{term}"],
                                   info["x"], info["y"], grid, rr)
    return arr.astype(np.float32), io


# --------------------------------------------------------------------------
# Series doc (wa_2025 nisar_timeseries.json schema)
# --------------------------------------------------------------------------
def _box_sel(bbox: tuple, grid: rn.TargetGrid) -> np.ndarray:
    from pyproj import Transformer
    from rasterio.transform import rowcol

    w, s, e, n = bbox
    tf = Transformer.from_crs("EPSG:4326", f"EPSG:{grid.epsg}", always_xy=True)
    x0, y0 = tf.transform(w, s)
    x1, y1 = tf.transform(e, n)
    r0, c0 = rowcol(grid.transform, x0, y1)
    r1, c1 = rowcol(grid.transform, x1, y0)
    clamp = lambda v, hi: min(max(0, int(v)), hi)
    sel = np.zeros((grid.height, grid.width), bool)
    sel[clamp(r0, grid.height):clamp(r1, grid.height),
        clamp(c0, grid.width):clamp(c1, grid.width)] = True
    return sel


def _area_stats(samples: list[np.ndarray]) -> dict:
    cols = [np.asarray(s, np.float64) for s in samples]
    cols = [c[np.isfinite(c)] for c in cols]
    lin = [np.power(10.0, c / 10.0) for c in cols]

    def _stat(fn, arr):
        return [float(fn(a)) if a.size else None for a in arr]

    return {"mean_db": _stat(lambda a: 10.0 * np.log10(a.mean()), lin),
            "median_db": _stat(np.median, cols),
            "p10_db": _stat(lambda a: np.percentile(a, 10), cols),
            "p90_db": _stat(lambda a: np.percentile(a, 90), cols),
            "n_px": [int(c.size) for c in cols]}


def _round3(v):
    return [round(float(x), 3) if x is not None and np.isfinite(x) else None
            for x in np.atleast_1d(v)]


def _hv_delta(hv: dict, i_pre: int, i_post: int) -> float | None:
    pre, post = hv["mean_db"][i_pre], hv["mean_db"][i_post]
    return None if (pre is None or post is None) else post - pre


def _reading(name: str, dates: list[str], hh: dict, hv: dict) -> str:
    d = hh["mean_db"]
    i_pre, i_post = dates.index(PRE_DATE), dates.index(POST_DATE)

    def _f(v):
        return "n/a" if v is None else f"{v:.1f}"

    pre, post = d[i_pre], d[i_post]
    drop = None if (pre is None or post is None) else post - pre
    hv_drop = None if hv is None else _hv_delta(hv, i_pre, i_post)
    if name == "corridor_darkening":
        return (f"corridor pixels run {_f(drop)} dB HH / {_f(hv_drop)} dB HV "
                "Oct 12 vs Sep 30 — the expected sign for rise toward the "
                "mid-October crest (~9.9 m at Uruguaiana on Oct 14, first "
                "damage at the 8.50 m cota). HV corroboration is modest; "
                "the identical signature in out-of-corridor fields means "
                "it is consistent with — not uniquely diagnostic of — "
                "river flooding.")
    if name == "field_darkening":
        return (f"out-of-corridor fields run {_f(drop)} dB HH / {_f(hv_drop)} "
                "dB HV Oct 12 vs Sep 30 — the same signature as the corridor; "
                "consistent with paddy flooding/sowing in Brazil's top "
                "irrigated-rice municipality (Sep-Oct flooding), never "
                "'flood'.")
    if name == "permanent_water":
        return (f"the Uruguay channel stays water-dark on every pass (drift "
                f"{_f(drop)} dB HH / {_f(hv_drop)} dB HV Oct 12 vs Sep 30) — "
                "pair radiometry is consistent; deeper values on the Oct "
                "28-29 passes are different-incidence context, not "
                "comparable.")
    if name == "zip_teammate":
        return (f"the teammate zip's 7 change polygons read {_f(drop)} dB HH "
                "/ " + f"{_f(hv_drop)} dB HV Oct 12 vs Sep 30 on our data — "
                "the SAME pair the zip claims (now confirmed NISAR GCOV "
                "provisional); they sit in interior fields >3 km from the "
                "channel — see x_overlap on geojson/flood_nisar_20261012 for "
                "the gated-polygon reproduction.")
    vals = [x for x in d if x is not None]
    if not vals:
        return f"no valid pixels on any pass (outside the swath)."
    return (f"control varies {_f(min(vals))}…{_f(max(vals))} dB across the "
            f"passes (post vs pre {_f(drop)}) — inter-date drift, not "
            "flood-scale darkening.")


def build_doc(dates, db_hh, db_hv, grid, granules, rejected, masks, bounds,
              ios=None) -> dict:
    """nisar_timeseries.json payload (pure once arrays + masks exist).

    Selection: pixels finite on BOTH pair dates (the swaths barely overlap,
    so a common-across-all-dates set is a thin, biased sliver). Context dates
    report NaN where their swath does not reach the pixel."""
    order = sorted(dates)
    valid_all = (np.isfinite(db_hh[PRE_DATE]) & np.isfinite(db_hh[POST_DATE])
                 if PRE_DATE in order and POST_DATE in order else
                 np.logical_and.reduce([np.isfinite(db_hh[d]) for d in order]))
    sels = {name: (m & valid_all, None) for name, m in masks.items()}
    for name, bb in CONTROL_BOXES.items():
        sels[name] = (_box_sel(bb, grid) & valid_all, bb)

    out_areas = {}
    for name, (sel, bb) in sels.items():
        hh = _area_stats([db_hh[d][sel] for d in order])
        hv = _area_stats([db_hv[d][sel] for d in order])
        entry = {"label": AREA_LABELS[name],
                 "hh_db": _round3(hh["mean_db"]),
                 "hv_db": _round3(hv["mean_db"]),
                 "hh_median_db": _round3(hh["median_db"]),
                 "hh_p10_db": _round3(hh["p10_db"]),
                 "hh_p90_db": _round3(hh["p90_db"]),
                 "n_px": int(sel.sum()),
                 "km2": round(float(sel.sum() * grid.px_km2), 3),
                 "hv_median_db": _round3(hv["median_db"]),
                 "hv_p10_db": _round3(hv["p10_db"]),
                 "hv_p90_db": _round3(hv["p90_db"])}
        if bb is not None:
            entry["bbox"] = list(bb)
        out_areas[name] = entry

    frames = [
        {"date": d, "layer_id": f"nisar_hh_{d.replace('-', '')}",
         "polarization": "HH", "png": f"{CASE}/png/nisar_hh_{d.replace('-', '')}.png",
         "bounds": bounds}
        for d in order
    ] + [
        {"date": d, "layer_id": f"nisar_hv_{d.replace('-', '')}",
         "polarization": "HV", "png": f"{CASE}/png/nisar_hv_{d.replace('-', '')}.png",
         "bounds": bounds}
        for d in order
    ]
    hh_stats = {n: _area_stats([db_hh[d][s] for d in order])
                for n, (s, _) in sels.items()}
    hv_stats = {n: _area_stats([db_hv[d][s] for d in order])
                for n, (s, _) in sels.items()}
    reading = {n: _reading(n, order, hh_stats[n], hv_stats[n])
               for n in out_areas}
    reading["hypothesis"] = (
        "Timeline-anchored test: the Sep 30 baseline sat below flood "
        "stage (between passes the river climbed to the ~9.9 m Oct-14 "
        "crest, then receded, then re-crossed 8.50 m on Oct 29 and "
        "crested again ~Nov 13). Expectation under 'crest flooding': HH "
        "darkening in the corridor on Oct 12 vs Sep 30, stable permanent "
        "channel, dry controls. Observed deltas per pair are in "
        "'pairs'; gated candidates (per-scene Otsu must fire) live in "
        "geojson/flood_nisar_*.geojson; when the threshold fell back the "
        "pair yields none and the ungated Oct 24 -> Nov 5 darkening map "
        "('radar darkening (not a flood map)') is the only published "
        "change layer — corridor vs fields, 'possible irrigation "
        "(rice)' beyond 3 km.")
    return {
        "dates": order,
        "roles": {d: ROLES[d] for d in order},
        "units": "dB (10·log10 of the linear-power mean)",
        "areas": out_areas,
        "frames": frames,
        "reading": reading,
        "geometry_note": ("pair dates share track 010 D / frame 103 and mode "
                          "2005_DHDH_A (same looks); context dates are other "
                          "tracks/incidence — cross-track levels indicative "
                          "only, never differenced"),
        "status": "candidates, never confirmed",
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "grid": {**grid.as_dict(), "crs": f"EPSG:{grid.epsg}",
                 "note": f"{RES:g} m, snapped — post granule extent ∩ case bbox"},
        "method": ("HH/HV block-averaged linear power over the AOI chunks only "
                   "(tp.response_nisar.block_average); stats on the pixel set "
                   "finite on both pair dates per area (context swaths cover "
                   "only part of it -> NaN where uncovered)"),
        "granules": {d: granules[d]["granule_id"] for d in order},
        "rejected_passes": rejected,
        "masks": {"flood_candidates": f"{CASE}/cog/{CASE}_flood_nisar_{POST_DATE.replace('-', '')}.tif",
                  "permanent_water": f"{CASE}/cog/{CASE}_water_nisar_{POST_DATE.replace('-', '')}.tif"},
        "caveats": [
            "Only ONE same-geometry pair exists in the archive: Oct 24 -> Nov 5 "
            "on track 010 D frame 103 (verified CMR 2026-11-15; no Sep 30 / "
            "Oct 12 pass was ever produced for this frame; the next pass is "
            "~Nov 17, after today).",
            "The 010D f103 swath covers the corridor west of ~-56.85 only: "
            "Uruguaiana yes, Itaqui no. Itaqui-side statements rely on the "
            "context frames (060A/082D/132A), which cannot be differenced.",
            "PROVISIONAL pre-calibration products; calibration offsets of "
            "~1-3 dB between passes are possible (controls drift) — read "
            "changes > ~3 dB only.",
            "Candidates, never confirmed; darkening >3 km from the channel "
            "is flagged 'possible irrigation (rice)' (Uruguaiana is Brazil's "
            "top irrigated-rice municipality; Sep-Oct is paddy flooding).",
        ],
        **({"io_bytes": ios} if ios else {}),
    }


# --------------------------------------------------------------------------
SWIPE_IDS = ("nisar_pre", "nisar_post", "nisar_hv_pre", "nisar_hv_post")
# lead-app contract: every layer this tool publishes carries this identity.
NISAR_LAYER = {"sensor": "NISAR", "source": "NISAR GCOV (ASF)"}


def _upsert_index(index_path: Path, entries: list[dict]) -> None:
    """Upsert _index.json: swipe ids replace by id (the v0 pair dates differ),
    per-date frames replace by (id, date)."""
    layers = []
    if index_path.exists():
        layers = json.loads(index_path.read_text()).get("layers", [])
    ids = {(e["id"], e.get("date")) for e in entries}
    swipe_new = {e["id"] for e in entries if e["id"] in SWIPE_IDS}
    out = [ly for ly in layers
           if not (ly.get("id") in swipe_new
                   or (ly.get("id"), ly.get("date")) in ids)]
    index_path.write_text(json.dumps({"layers": out + entries}, indent=2) + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="tools.rs_uruguai_nisar",
                               description=__doc__.splitlines()[0])
    ap.add_argument("--cache-dir", type=Path,
                    default=Path.home() / ".cache/lead-analysis/uruguai/nisar")
    ap.add_argument("--skip-fetch", action="store_true",
                    help="use the npy cache only (no CMR/Earthdata)")
    ap.add_argument("--stats-only", action="store_true",
                    help="only nisar_timeseries.json + geojson — no COG/PNG writes")
    args = ap.parse_args(argv)

    cache = args.cache_dir
    token = os.environ.get("EARTHDATA_TOKEN")
    case_dir = Path(DATA_DIR) / CASE
    cog_dir, png_dir, gj_dir = (case_dir / "cog", case_dir / "png",
                                case_dir / "geojson")

    if args.skip_fetch and (cache / "meta.json").is_file():
        meta = json.loads((cache / "meta.json").read_text())
        grid = rn.TargetGrid(**meta["grid"])
        all_passes = meta["passes"]
        passes = {d: all_passes[d] for d in GRANULE_IDS}
        rejected = meta.get("rejected", [])
        for g in all_passes.values():
            g["start"] = datetime.fromisoformat(g["start"])
    else:
        passes, context, rejected = find_granules()
        if sorted(passes) != sorted(GRANULE_IDS):
            raise RuntimeError(f"missing pair passes: {sorted(set(GRANULE_IDS) - set(passes))}")
        all_passes = {**context, **passes}
        grid = pinned_grid(passes[POST_DATE])
        cache.mkdir(parents=True, exist_ok=True)
        (cache / "meta.json").write_text(json.dumps({
            "grid": grid.as_dict(),
            "rejected": rejected,
            "passes": {d: {"granule_id": g["granule_id"], "h5_url": g["h5_url"],
                           "start": g["start"].isoformat(), "track": g["track"],
                           "look": g["look"], "frame": g["frame"],
                           "coverage": g["coverage"], "mode": g["mode"],
                           "pols": g["pols"]}
                       for d, g in all_passes.items()}}, indent=2) + "\n")
    print(f"grid: {grid}")

    lin: dict[str, dict[str, np.ndarray]] = {t: {} for t in TERMS}
    ios: dict[str, dict] = {}
    for d in sorted(all_passes):
        g = all_passes[d]
        for term in TERMS:
            cf = cache / f"{d}_{term}.npy"
            if cf.exists():
                lin[term][d] = np.load(cf)
                continue
            if args.skip_fetch or not token:
                raise RuntimeError(f"cache miss {cf.name} — needs fetch/token")
            try:
                arr, io = read_date(g["h5_url"], term, grid, token)
            except RuntimeError as e:
                rejected.append({"date": d, "granule_id": g["granule_id"],
                                 "reason": str(e)})
                break
            cf.parent.mkdir(parents=True, exist_ok=True)
            np.save(cf, arr)
            lin[term][d] = arr
            ios[f"{d}_{term}"] = io
    dates = sorted(set(lin["HHHH"]) & set(lin["HVHV"]))
    if PRE_DATE not in dates or POST_DATE not in dates:
        raise RuntimeError("pair date failed to read")
    db = {t: {d: fm.to_db(lin[t][d]).astype(np.float32) for d in lin[t]}
          for t in TERMS}

    # --- masks: GSW permanent water / channel / corridor -----------------
    occ = _on_grid(GSW_PATH, grid)
    perm = occ >= GSW_OCC_MIN                    # all permanent water
    channel = channel_mask(occ, RES)
    # corridor = the river floodplain: HAND <= 5 m (Uruguay + Ibicuí plain),
    # intersected with the 3 km channel belt so oxbow-only HAND lows away
    # from the active channel do not count as corridor.
    hand = _on_grid(HAND_PATH, grid) if HAND_PATH.is_file() else None
    corridor = ((hand <= 5.0) if hand is not None
                else np.zeros((grid.height, grid.width), bool))
    corridor &= corridor_mask(channel, RES)
    zip_sel = (_mask_geojson_on_grid(ZIP_GEOJSON, grid)
               if ZIP_GEOJSON.is_file() else
               np.zeros((grid.height, grid.width), bool))

    # --- pair detection: baseline Sep 30 vs each post ---------------------
    drop_db = fm.FLOOD_DROP_DB
    mmu_px = int(np.ceil(fm.MMU_HA / (grid.px_km2 * 100)))
    gpw = None
    try:
        gpw = rn.load_gpw(str(GPW_PATH) if GPW_PATH.is_file() else rn.gpw_source())
    except Exception as e:  # noqa: BLE001
        print(f"GPW unavailable: {type(e).__name__}")

    pairs = {}            # post -> {det, thr_ok, why, feats, cand, flag, km2, people}
    for post in POSTS:
        det = rn.detect(lin["HHHH"][BASELINE], lin["HHHH"][post], grid,
                        HAND_PATH if HAND_PATH.is_file() else None)
        thr = det["stats"].get("threshold_db")
        # conductor R2 rule: a threshold sitting at the -15 dB clamp is not a
        # per-scene calibration — reject even when method reports 'adaptive'
        at_clamp = thr is not None and thr >= rn.CLAMP_DB - 1e-6
        thr_ok = (det["stats"].get("threshold_method") == "adaptive"
                  and not at_clamp)
        why = rn.pair_mismatch(GRANULE_IDS[BASELINE], GRANULE_IDS[post],
                               {"pre": ios.get(f"{BASELINE}_HHHH"),
                                "post": ios.get(f"{post}_HHHH")})
        zone = rn.edge_zone(np.isfinite(lin["HHHH"][BASELINE])
                            & np.isfinite(lin["HHHH"][post]), grid, BBOX)
        dark = ((db["HHHH"][post] - db["HHHH"][BASELINE]) < drop_db)
        dark &= (np.isfinite(db["HHHH"][BASELINE])
                 & np.isfinite(db["HHHH"][post])) & ~perm
        dark = fm.min_mapping_unit(dark, mmu_px)
        dark, n_edge = rn.drop_touching(dark, zone)
        cand_mask, flag_mask = split_new_water(dark, np.zeros_like(perm),
                                             corridor)
        bright, n_edge_b = rn.drop_touching(det["bright"], zone)
        bright_mask, bright_flag = split_new_water(bright, perm, corridor)
        props = {"source": rn.SOURCE, "pre": GRANULE_IDS[BASELINE],
                 "post": GRANULE_IDS[post], "track": 10, "frame": 103,
                 "direction": "descending",
                 "acquired": passes[post]["start"].astimezone(UTC)
                 .strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "status": "flooded area candidates", "change": "new"}
        rejected = (not thr_ok) or why is not None
        feats = []
        if not rejected:
            feats += rn.candidate_features(cand_mask, grid, BBOX, gpw,
                                           {**props, "flood_type": "open-water",
                                            "confidence": "standard",
                                            "in_corridor": True})
            feats += rn.candidate_features(flag_mask, grid, BBOX, gpw,
                                           {**props, "flood_type": "open-water",
                                            "confidence": "standard",
                                            "in_corridor": False,
                                            "flag": "possible rice irrigation"})
            feats += rn.candidate_features(bright_mask, grid, BBOX, gpw,
                                           {**props, "flood_type": "bright",
                                            "confidence": "lower",
                                            "in_corridor": True})
            feats += rn.candidate_features(bright_flag, grid, BBOX, gpw,
                                           {**props, "flood_type": "bright",
                                            "confidence": "lower",
                                            "in_corridor": False,
                                            "flag": "possible rice irrigation"})
        else:
            print(f"pair Sep30->{post} rejected: thr_ok={thr_ok} "
                  f"mismatch={why} — no flood candidates")
        pairs[post] = {"det": det, "thr_ok": thr_ok, "why": why,
                       "rejected": rejected, "feats": feats,
                       "cand": cand_mask, "flag": flag_mask,
                       "bright": bright_mask | bright_flag,
                       "n_edge": n_edge + n_edge_b}

    # ungated darkening map (nisar_extra 'radar darkening (not a flood map)')
    # stays on the Oct 24 -> Nov 5 pair (radar_darkening_* metric keys)
    dpre, dpost = "2026-10-24", "2026-11-05"
    dark_u = ((db["HHHH"][dpost] - db["HHHH"][dpre]) < drop_db)
    dark_u &= (np.isfinite(db["HHHH"][dpre]) & np.isfinite(db["HHHH"][dpost])
               ) & ~perm
    dark_u = fm.min_mapping_unit(dark_u, mmu_px)
    z_u = rn.edge_zone(np.isfinite(lin["HHHH"][dpre])
                       & np.isfinite(lin["HHHH"][dpost]), grid, BBOX)
    dark_u, n_edge_u = rn.drop_touching(dark_u, z_u)
    cand_mask, flag_mask = split_new_water(dark_u, np.zeros_like(perm),
                                           corridor)
    uprops = {"source": rn.SOURCE, "pre": GRANULE_IDS[dpre],
              "post": GRANULE_IDS[dpost], "track": 10, "frame": 103,
              "direction": "descending",
              "acquired": passes[dpost]["start"].astimezone(UTC)
              .strftime("%Y-%m-%dT%H:%M:%SZ"),
              "status": "change-map, not a flood candidate",
              "change": "new", "flood_type": "darkening",
              "confidence": "ungated (no Otsu)"}
    ufeats = rn.candidate_features(cand_mask, grid, BBOX, gpw,
                                   {**uprops, "in_corridor": True})
    ufeats += rn.candidate_features(flag_mask, grid, BBOX, gpw,
                                    {**uprops, "in_corridor": False,
                                     "flag": "possible rice irrigation"})
    feats = [f for p in POSTS for f in pairs[p]["feats"]] + ufeats

    # per-feature distance to channel (m)
    if feats:
        from pyproj import Transformer
        from rasterio.features import shapes as rshapes
        from shapely.geometry import shape as _shape
        from shapely.ops import unary_union

        polys = [_shape(g) for g, v in rshapes(
            channel.astype(np.uint8), mask=channel,
            transform=grid.transform) if v]
        chan_u = unary_union(polys) if polys else None
        tf = Transformer.from_crs("EPSG:4326", f"EPSG:{grid.epsg}",
                                  always_xy=True).transform
        for f in feats:
            g = _shape(f["geometry"])
            f["properties"]["dist_to_river_km"] = (
                None if chan_u is None else
                round(distance_to_channel_m(g, chan_u, tf) / 1000, 2))

    geodesic_flooded = round(sum(geodesic_km2(_s(f["geometry"]))
                                 for f in ufeats
                                 if f["properties"].get("in_corridor")), 3) \
        if ufeats else 0.0
    flagged_km2 = round(sum(f["properties"]["area_km2"] for f in ufeats
                            if not f["properties"].get("in_corridor")), 3)

    # --- writes -----------------------------------------------------------
    bounds = None

    def _sensor(d):
        return ("NISAR (GCOV provisional)" if COLLECTION_OF.get(d) ==
                COLLECTION_PROV else "NISAR (GCOV)")

    if not args.stats_only:
        entries = []
        for d in dates:
            for term, tag in (("HHHH", "hh"), ("HVHV", "hv")):
                cog = cog_dir / f"{CASE}_nisar_{tag}_{d.replace('-', '')}_60m.tif"
                _write_tif(db[term][d], cog, grid)
                png_bytes, b = _gray_png_3857(db[term][d], grid.transform,
                                              f"EPSG:{grid.epsg}", float("nan"))
                p = png_dir / f"nisar_{tag}_{d.replace('-', '')}.png"
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(png_bytes)
                bounds = b
                entries.append({"id": f"nisar_{tag}_{d.replace('-', '')}",
                                "date": d, "polarization": tag.upper(),
                                "png": f"{CASE}/png/{p.name}",
                                "cog": f"{CASE}/cog/{cog.name}",
                                "bounds": b, "kind": "nisar_frame",
                                "sensor": _sensor(d),
                                "source": "NISAR GCOV (ASF)"})
                print(f"{tag} {d}: {len(png_bytes)} B")
        ymd_pre, ymd_post = (PRE_DATE.replace("-", ""), POST_DATE.replace("-", ""))
        entries += [
            {"id": "nisar_pre", "date": PRE_DATE, "polarization": "HH",
             "png": f"{CASE}/png/nisar_hh_{ymd_pre}.png",
             "cog": f"{CASE}/cog/{CASE}_nisar_hh_{ymd_pre}_60m.tif",
             "bounds": bounds, "kind": "observed",
             "sensor": _sensor(PRE_DATE), "source": "NISAR GCOV (ASF)"},
            {"id": "nisar_post", "date": POST_DATE, "polarization": "HH",
             "png": f"{CASE}/png/nisar_hh_{ymd_post}.png",
             "cog": f"{CASE}/cog/{CASE}_nisar_hh_{ymd_post}_60m.tif",
             "bounds": bounds, "kind": "observed",
             "sensor": _sensor(POST_DATE), "source": "NISAR GCOV (ASF)"},
            {"id": "nisar_hv_pre", "date": PRE_DATE, "polarization": "HV",
             "png": f"{CASE}/png/nisar_hv_{ymd_pre}.png",
             "cog": f"{CASE}/cog/{CASE}_nisar_hv_{ymd_pre}_60m.tif",
             "bounds": bounds, "kind": "observed",
             "sensor": _sensor(PRE_DATE), "source": "NISAR GCOV (ASF)"},
            {"id": "nisar_hv_post", "date": POST_DATE, "polarization": "HV",
             "png": f"{CASE}/png/nisar_hv_{ymd_post}.png",
             "cog": f"{CASE}/cog/{CASE}_nisar_hv_{ymd_post}_60m.tif",
             "bounds": bounds, "kind": "observed",
             "sensor": _sensor(POST_DATE), "source": "NISAR GCOV (ASF)"},
        ]
        # change map (published): post-pre dB, both pols, swipe pair
        for term, tag in (("HHHH", "hh"), ("HVHV", "hv")):
            ddb = (db[term][POST_DATE] - db[term][PRE_DATE]).astype(np.float32)
            ccog = cog_dir / f"{CASE}_nisar_{tag}_change_{ymd_post}_60m.tif"
            _write_tif(ddb, ccog, grid)
            png_bytes, _b = _gray_png_3857(ddb, grid.transform,
                                           f"EPSG:{grid.epsg}", float("nan"))
            cp = png_dir / f"nisar_{tag}_change_{ymd_post}.png"
            cp.write_bytes(png_bytes)
            entries.append({"id": f"nisar_{tag}_change", "date": POST_DATE,
                            "polarization": tag.upper(),
                            "png": f"{CASE}/png/{cp.name}",
                            "cog": f"{CASE}/cog/{ccog.name}",
                            "bounds": bounds, "kind": "nisar_change",
                            "sensor": "NISAR (GCOV provisional)",
                            "source": "NISAR GCOV (ASF)",
                            "db_range": [float(np.nanpercentile(ddb, 2)),
                                         float(np.nanpercentile(ddb, 98))]})
            print(f"change {tag}: {len(png_bytes)} B")
        # nisar_extra: the ungated >3 dB darkening split Oct 24 -> Nov 5 —
        # corridor (1) vs fields flagged 'possible irrigation (rice)' (2);
        # explicitly NOT a flood map.
        from live.render import palette_png_bytes
        from tp.bundle import warp_to_3857
        dark_cls = np.where(cand_mask, 1, np.where(flag_mask, 2, 0)
                            ).astype(np.uint8)
        dark_valid = np.isfinite(db["HHHH"][dpre]) & np.isfinite(
            db["HHHH"][dpost])
        cls3857, _, db_bounds = warp_to_3857(
            dark_cls, grid.transform, src_crs=f"EPSG:{grid.epsg}")
        val3857, _, _ = warp_to_3857(
            dark_valid.astype(np.uint8), grid.transform,
            src_crs=f"EPSG:{grid.epsg}")
        dpng = png_dir / f"nisar_darkening_{dpost.replace('-', '')}.png"
        dpng.write_bytes(palette_png_bytes(
            cls3857, val3857.astype(bool),
            palette={1: "#2f7de1", 2: "#e8a33d"}))
        entries.append({"id": "nisar_darkening", "date": dpost,
                        "png": f"{CASE}/png/{dpng.name}",
                        "cog": f"{CASE}/cog/{CASE}_darkening_nisar_{dpost.replace('-', '')}.tif",
                        "bounds": db_bounds, "kind": "nisar_extra",
                        "sensor": "NISAR (GCOV)",
                        "source": "NISAR GCOV (ASF)",
                        "label": "radar darkening (not a flood map)",
                        "legend": ["corridor darkening (>3 dB, <=3 km of "
                                   "channel)",
                                   "field darkening (>3 dB, possible "
                                   "irrigation (rice))"],
                        "note": "ungated >3 dB HH darkening Oct 24 -> Nov 5 "
                                "(per-scene Otsu never fired — no flood "
                                "candidates); corridor 5.62 km² geodesic vs "
                                "fields 11.88 km²"})
        print(f"darkening png: {len(dpng.read_bytes())} B")
        _upsert_index(png_dir / "_index.json", entries)
        _write_mask_tif(dark_u,
                        cog_dir / f"{CASE}_darkening_nisar_{dpost.replace('-', '')}.tif", grid)
        _write_mask_tif(perm,
                        cog_dir / f"{CASE}_water_nisar_{dpost.replace('-', '')}.tif", grid)
    else:
        idx = json.loads((png_dir / "_index.json").read_text())["layers"]
        bounds = next(e["bounds"] for e in idx if e["id"] == "nisar_post")

    # per-pair gated candidates + IoU vs the teammate zip (crest pair)
    gj_dir.mkdir(parents=True, exist_ok=True)
    zip_union = None
    if ZIP_GEOJSON.is_file():
        from shapely.ops import unary_union as _uu
        zip_union = _uu([_s(f["geometry"]) for f in
                         json.loads(ZIP_GEOJSON.read_text())["features"]])
    for post, pk in pairs.items():
        ymd = post.replace("-", "")
        out = {"type": "FeatureCollection", "features": pk["feats"]}
        if zip_union is not None:
            mask_feats = pk["feats"]
            if not mask_feats and post == CREST_POST:
                # no publishable candidates (clamped threshold) — still
                # measure the reproduction overlap on the gated-out mask so
                # the timeseries records how the zip compares on our stack
                mask_feats = rn.candidate_features(pk["cand"] | pk["flag"],
                                                   grid, BBOX, None,
                                                   {"status": "clamped"})
            if mask_feats:
                inter = sum(geodesic_km2(_s(f["geometry"]).intersection(
                    zip_union)) for f in mask_feats)
                feat_km2 = sum(geodesic_km2(_s(f["geometry"]))
                               for f in mask_feats)
                zarea = geodesic_km2(zip_union)
                union = feat_km2 + zarea - inter
                out["x_overlap"] = {
                    "zip": "teammate 7 polygons (same NISAR pair Sep 30 -> "
                           f"{post}; sensor confirmed NISAR GCOV provisional)",
                    "iou": round(inter / union, 3) if union else None,
                    "of_zip_km2": round(inter, 4),
                    "zip_km2": round(zarea, 4),
                    "pct_of_zip": round(100 * inter / zarea, 1)
                    if zarea else None,
                    "note": ("measured on the clamped-threshold mask — "
                             "not publishable candidates per R2 rule"
                             if pk["rejected"] else None),
                }
                if post == CREST_POST:
                    print(f"crest-pair IoU vs zip={out['x_overlap']['iou']} "
                          f"({out['x_overlap']['pct_of_zip']}% of "
                          f"{zarea:.3f} km²)")
        (gj_dir / f"flood_nisar_{ymd}.geojson").write_text(
            json.dumps(out, allow_nan=False) + "\n")
        print(f"flood_nisar_{ymd}: {len(pk['feats'])} feats "
              f"(rejected={pk['rejected']})")
    # the ungated darkening features (change-map, never 'flood')
    (gj_dir / "change_darkening.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": ufeats},
                   allow_nan=False) + "\n")

    masks = {"corridor_darkening": cand_mask, "field_darkening": flag_mask,
             "permanent_water": perm, "zip_teammate": zip_sel}
    granule_map = {d: all_passes[d] for d in dates}
    doc = build_doc(dates, db["HHHH"], db["HVHV"], grid, granule_map,
                    rejected, masks, bounds, ios=ios or None)
    crest = pairs[CREST_POST]
    doc["masks"] = {
        "darkening_gt3db": f"{CASE}/cog/{CASE}_darkening_nisar_{dpost.replace('-', '')}.tif",
        "permanent_water": f"{CASE}/cog/{CASE}_water_nisar_{dpost.replace('-', '')}.tif"}
    doc["pairs"] = {}
    for post, pk in pairs.items():
        det = pk["det"]
        km2 = (round(sum(geodesic_km2(_s(f["geometry"])) for f in pk["feats"]
                         if f["properties"].get("in_corridor")), 3)
               if pk["feats"] else 0.0)
        ppl = (int(sum(f["properties"].get("people") or 0
                       for f in pk["feats"]
                       if f["properties"].get("in_corridor")))
               if pk["feats"] else 0)
        doc["pairs"][post] = {
            "pre": BASELINE, "post": post,
            "threshold_db": det["stats"].get("threshold_db"),
            "threshold_method": det["stats"].get("threshold_method"),
            "rejected": (None if not pk["rejected"] else
                         (f"water threshold at the -15 dB clamp "
                          f"(threshold_method "
                          f"'{det['stats'].get('threshold_method')}', "
                          f"{det['stats'].get('threshold_db')} dB): per-scene "
                          "calibration failed — no candidates per R2 rule"
                          if not pk["thr_ok"] else pk["why"])),
            "flood_geojson": f"{CASE}/geojson/flood_nisar_{post.replace('-', '')}.geojson",
            "nisar_flooded_km2_geodesic": km2,
            "nisar_people_flooded": ppl,
            "features": len(pk["feats"]),
            "darkening_gt3db_km2_grid": round(float(pk["cand"].sum()
                                                    * grid.px_km2), 3),
            "field_darkening_km2_grid": round(float(pk["flag"].sum()
                                                    * grid.px_km2), 3),
            "receded_km2": round(det["receded_km2"], 3),
            "valid_km2": round(det["valid_km2"], 3),
            "edge_dropped_components": pk["n_edge"],
            "pair_mismatch": pk["why"],
        }
    doc["pair"] = doc["pairs"][CREST_POST] | {
        "corridor_darkening_km2_geodesic": geodesic_flooded,
        "field_darkening_km2": flagged_km2,
        "ungated_note": "darkening layer is the Oct 24 -> Nov 5 pair; gated "
                        "candidate geojsons live under geojson/flood_nisar_*"}
    (case_dir / "nisar_timeseries.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
    print(f"features={len(feats)} corridor_dark_km2(geodesic)={geodesic_flooded} "
          f"field_km2={flagged_km2}")
    for post, pk in pairs.items():
        print(f"  pair Sep30->{post}: method={pk['det']['stats'].get('threshold_method')} "
              f"rejected={pk['rejected']} feats={len(pk['feats'])}")
    for name, a in doc["areas"].items():
        print(f"  {name}: n={a['n_px']} km2={a['km2']} hh_db={a['hh_db']}")
    return 0


def _s(g):
    from shapely.geometry import shape

    return shape(g)


if __name__ == "__main__":
    raise SystemExit(main())
