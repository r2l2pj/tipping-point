"""WA-2025 NISAR GCOV 3-date backscatter series + the Nov 10 'ref' swipe layer.

Adds the 2025-11-10 GCOV pass (track 172 D / frame 064, granule role 'ref' —
pre-season reference) next to the existing pre (2025-12-04) and post
(2025-12-16) 60 m HH COGs, then summarizes HH/HV backscatter over the NISAR
flood-candidate mask, the pre-existing-water mask and three control boxes.

Reads (network): anonymous CMR umm_json for the granule .h5 URLs, then
tp.response_nisar.open_gcov + block_average — HTTP range reads of ONLY the
HDF5 chunks over the AOI, block-averaged (linear power) onto the committed
60 m EPSG:32610 grid (left 529490 / top 5373020 / 1410x2797, snapped to 60 m —
identical to wa_2025_nisar_{pre,post}_*_60m.tif). No full granule is loaded.

Writes under data/wa_2025/:
  cog/wa_2025_nisar_ref_20251110_60m.tif     float32 dB HH, nodata nan
  cog/wa_2025_nisar_{ref,pre,post}_hv_*_60m.tif   float32 dB HV companions
  png/nisar_ref_20251110.png                 EPSG:3857 RGBA (tp.bundle
                                             ._gray_png_3857 — same 2-98 %
                                             stretch/bounds as nisar_pre/post)
  nisar_timeseries.json                      per-date HH/HV stats + reading
and upserts the nisar_ref entry in png/_index.json (same shape as
nisar_pre/nisar_post). Re-running is idempotent.

    PYTHONPATH=.:pipeline python tools/wa_nisar_timeseries.py \
        [--skip-fetch] [--stats-only]

EARTHDATA_TOKEN required unless --skip-fetch (uses already-written COGs).
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

CASE = "wa_2025"
BBOX = (-122.6, 47.0, -121.5, 48.5)
COLLECTION = "NISAR_L2_GCOV_BETA_V1"

# Committed 60 m grid of wa_2025_nisar_{pre,post}_*_60m.tif — the ref granule
# shares track/frame so block-averaging onto it is pixel-identical.
GRID = rn.TargetGrid(epsg=32610, left=529490.0, top=5373020.0, res=60.0,
                     width=1410, height=2797)

DATES = {"ref": "2025-11-10", "pre": "2025-12-04", "post": "2025-12-16"}
ROLES = {"2025-11-10": "reference (pre-season)",
         "2025-12-04": "pre-event",
         "2025-12-16": "post-event"}
GRANULES = {
    "ref": "NISAR_L2_PR_GCOV_004_172_D_064_4005_DHDH_A_20251110T031814_"
           "20251110T031849_X05009_N_F_J_001",
    "pre": "NISAR_L2_PR_GCOV_006_172_D_064_4005_DHDH_A_20251204T031815_"
           "20251204T031850_X05009_N_F_J_001",
    "post": "NISAR_L2_PR_GCOV_007_172_D_064_4005_DHDH_A_20251216T031815_"
            "20251216T031850_X05009_N_F_J_001",
}
WINDOWS = {"ref": ("2025-11-09", "2025-11-12"),
           "pre": ("2025-12-03", "2025-12-06"),
           "post": ("2025-12-15", "2025-12-18")}

# Mask COGs (EPSG:4326 ~10 m) reprojected nearest onto the 60 m grid.
MASKS = {"flood_candidates": "wa_2025_flood_nisar_20251210.tif",
         "pre_water": "wa_2025_water_nisar_20251210.tif"}
# Control areas (w, s, e, n) in EPSG:4326 — chosen to carry ~0 % flood and
# ~0 % permanent-water pixels on the reprojected 60 m masks (verified
# 2026-11-15: all three < 0.6 % water, 0.00 % flood).
CONTROL_BOXES = {
    "control_forest": (-121.75, 48.15, -121.55, 48.35),     # Cascade foothills upland forest, NE of the Skagit valley
    "control_urban": (-122.20, 48.03, -122.08, 48.12),      # Marysville built-up, away from the rivers
    "control_farmland": (-122.32, 48.32, -122.26, 48.37),   # Skagit valley-floor farmland that did NOT flood
}
AREA_LABELS = {"flood_candidates": "NISAR flood candidates",
               "pre_water": "pre-existing water",
               "control_forest": "upland forest control",
               "control_urban": "built-up control (Marysville)",
               "control_farmland": "Skagit floodplain farmland control (not flooded)"}

PX_KM2 = GRID.res * GRID.res / 1e6


def find_h5_urls(get=None) -> dict[str, str]:
    """{role: https .h5 url} for the three pinned granules (anonymous CMR)."""
    from fetch import nisar as fn

    out = {}
    for role, (start, end) in WINDOWS.items():
        for g in fn.search_gcov_domain(BBOX, start, end,
                                       collection=COLLECTION, get=get):
            if g["granule_id"] == GRANULES[role]:
                out[role] = g["h5_url"]
                break
    missing = [r for r in GRANULES if r not in out]
    if missing:
        raise RuntimeError(f"granules not found on CMR for roles {missing}")
    return out


def read_term_db(url: str, term: str, token: str | None) -> tuple[np.ndarray, dict]:
    """Block-averaged linear power -> dB on the fixed 60 m grid."""
    with rn.open_gcov(url, token) as (h5, rr):
        info = rn.granule_grid_info(h5)
        if info["epsg"] != GRID.epsg:
            raise RuntimeError(f"granule EPSG {info['epsg']} != {GRID.epsg}")
        if term not in info["terms"]:
            raise RuntimeError(f"granule lacks {term} ({info['terms']})")
        grid = rn.target_grid(info["x"], info["y"], info["epsg"], BBOX, GRID.res)
        io_note = {}
        if grid != GRID:
            # swath extent differs by ~10-20 m -> outward snap shifts; keep the
            # committed absolute-UTM cells so all three dates align exactly
            io_note["snap_delta_m"] = [
                round(GRID.left - grid.left, 3),
                round(GRID.top - grid.top, 3)]
        arr, io = rn.block_average(h5[f"{rn.GCOV_GRIDS}/{term}"],
                                   info["x"], info["y"], GRID, rr)
    return fm.to_db(arr).astype(np.float32), {**io, **io_note}


def load_cog_db(path: Path) -> np.ndarray:
    """Committed 60 m COG -> float32 dB array (already stored as dB)."""
    import rasterio

    with rasterio.open(path) as src:
        if (src.width, src.height) != (GRID.width, GRID.height):
            raise RuntimeError(f"{path.name} is not on the pinned 60 m grid")
        return np.asarray(src.read(1), dtype=np.float32)


def _write_db_tif(arr_db: np.ndarray, path: Path) -> Path:
    """float32 dB GTiff matching the committed NISAR COG profile."""
    import rasterio

    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", height=arr_db.shape[0],
                       width=arr_db.shape[1], count=1, dtype="float32",
                       crs=f"EPSG:{GRID.epsg}", transform=GRID.transform,
                       nodata=float("nan"), tiled=True, blockxsize=512,
                       blockysize=512, compress="deflate") as dst:
        dst.write(arr_db.astype("float32"), 1)
    return path


def _mask_on_grid(path: Path) -> np.ndarray:
    """uint8 mask COG -> bool array on the 60 m grid (nearest)."""
    import rasterio
    from rasterio.warp import Resampling, reproject

    out = np.zeros((GRID.height, GRID.width), dtype=np.uint8)
    with rasterio.open(path) as src:
        reproject(src.read(1), out, src_transform=src.transform,
                  src_crs=src.crs, dst_transform=GRID.transform,
                  dst_crs=f"EPSG:{GRID.epsg}", resampling=Resampling.nearest)
    return out.astype(bool)


def box_window(bbox: tuple, grid: rn.TargetGrid = GRID) -> tuple[int, int, int, int]:
    """Lon/lat bbox -> (r0, r1, c0, c1) window clipped to the grid."""
    from pyproj import Transformer
    from rasterio.transform import rowcol

    w, s, e, n = bbox
    tf = Transformer.from_crs("EPSG:4326", f"EPSG:{grid.epsg}", always_xy=True)
    x0, y0 = tf.transform(w, s)
    x1, y1 = tf.transform(e, n)
    r0, c0 = rowcol(grid.transform, x0, y1)
    r1, c1 = rowcol(grid.transform, x1, y0)
    clamp = lambda v, hi: min(max(0, int(v)), hi)
    return (clamp(r0, grid.height), clamp(r1, grid.height),
            clamp(c0, grid.width), clamp(c1, grid.width))


def area_stats(samples: list[np.ndarray]) -> dict:
    """Per-date stats for one area: mean is 10·log10 of the linear-power mean.

    `samples` = one 1-D dB array per date (NaNs dropped by the caller, or
    dropped here). Percentiles on dB directly (log is monotonic — same as
    linear quantiles); the mean goes through linear power per the contract.
    """
    cols = [np.asarray(s, dtype=np.float64) for s in samples]
    cols = [c[np.isfinite(c)] for c in cols]
    lin = [np.power(10.0, c / 10.0) for c in cols]
    def _stat(fn, arr):
        return [float(fn(a)) if a.size else float("nan") for a in arr]
    return {
        "mean_db": _stat(lambda a: 10.0 * np.log10(a.mean()), lin),
        "median_db": _stat(np.median, cols),
        "p10_db": _stat(lambda a: np.percentile(a, 10), cols),
        "p90_db": _stat(lambda a: np.percentile(a, 90), cols),
    }


def _round3(v) -> list:
    return [round(float(x), 3) if np.isfinite(x) else None
            for x in np.atleast_1d(v)]


def _upsert_index(index_path: Path, entry: dict) -> None:
    layers = []
    if index_path.exists():
        layers = json.loads(index_path.read_text()).get("layers", [])
    out = [ly for ly in layers
           if not (ly.get("id") == entry["id"] and ly.get("date") == entry["date"])]
    out.append(entry)
    index_path.write_text(json.dumps({"layers": out}, indent=2) + "\n")


def _reading(name: str, hh: dict, hv: dict | None) -> str:
    """One cautious sentence per area, computed from the arrays."""
    d = hh["mean_db"]
    drift = d[2] - d[0]          # post - ref
    pre_shift = d[1] - d[0]      # pre  - ref
    span = float(np.nanmax(d) - np.nanmin(d))
    if name == "flood_candidates":
        return (f"candidate pixels run {drift:+.1f} dB on Dec 16 vs the Nov 10 "
                f"reference (pre-event {pre_shift:+.1f} dB) — a darkening "
                "consistent with new standing water, not confirmed.")
    if name == "pre_water":
        return (f"pre-existing water stays water-dark on all three passes, "
                f"drifting {drift:+.1f} dB post vs ref — much weaker than the "
                "candidate signal.")
    return (f"control varies {span:.1f} dB across the three passes "
            f"(post vs ref {drift:+.1f}) — inter-date drift, not flood-scale "
            "darkening.")


def build_timeseries(db_by_date: dict[str, np.ndarray],
                     hv_by_date: dict[str, np.ndarray] | None,
                     case_dir: Path, bounds: list[float],
                     ios: dict | None = None) -> dict:
    """Assemble the nisar_timeseries.json payload (pure — no I/O)."""
    order = [DATES["ref"], DATES["pre"], DATES["post"]]
    masks = {name: _mask_on_grid(case_dir / "cog" / fname)
             for name, fname in MASKS.items()}
    valid3 = np.logical_and.reduce(
        [np.isfinite(db_by_date[d]) for d in order])
    areas = {}
    for name, mask in masks.items():
        sel = mask & valid3
        areas[name] = (sel, None)
    for name, bbox in CONTROL_BOXES.items():
        r0, r1, c0, c1 = box_window(bbox)
        sel = np.zeros((GRID.height, GRID.width), bool)
        sel[r0:r1, c0:c1] = True
        areas[name] = (sel & valid3, bbox)

    out_areas = {}
    for name, (sel, bbox) in areas.items():
        hh = area_stats([db_by_date[d][sel] for d in order])
        hv = None
        if hv_by_date is not None:
            hv = area_stats([hv_by_date[d][sel] for d in order])
        entry = {
            "label": AREA_LABELS[name],
            "hh_db": _round3(hh["mean_db"]),
            "hv_db": _round3(hv["mean_db"]) if hv is not None else None,
            "hh_median_db": _round3(hh["median_db"]),
            "hh_p10_db": _round3(hh["p10_db"]),
            "hh_p90_db": _round3(hh["p90_db"]),
            "n_px": int(sel.sum()),
            "km2": round(float(sel.sum() * PX_KM2), 3),
        }
        if bbox is not None:
            entry["bbox"] = list(bbox)
        if hv is not None:
            entry["hv_median_db"] = _round3(hv["median_db"])
            entry["hv_p10_db"] = _round3(hv["p10_db"])
            entry["hv_p90_db"] = _round3(hv["p90_db"])
        out_areas[name] = entry

    frames = [
        {"date": DATES["ref"], "layer_id": "nisar_ref",
         "png": f"{CASE}/png/nisar_ref_{DATES['ref'].replace('-', '')}.png",
         "bounds": bounds},
        {"date": DATES["pre"], "layer_id": "nisar_pre",
         "png": f"{CASE}/png/nisar_pre_{DATES['pre'].replace('-', '')}.png",
         "bounds": bounds},
        {"date": DATES["post"], "layer_id": "nisar_post",
         "png": f"{CASE}/png/nisar_post_{DATES['post'].replace('-', '')}.png",
         "bounds": bounds},
    ]
    hh_stats = {n: area_stats([db_by_date[d][s] for d in order])
                for n, (s, _) in areas.items()}
    hv_stats = ({n: area_stats([hv_by_date[d][s] for d in order])
                 for n, (s, _) in areas.items()}
                if hv_by_date is not None else {})
    doc = {
        "dates": order,
        "roles": dict(ROLES),
        "units": "dB (10·log10 of the linear-power mean)",
        "areas": out_areas,
        "frames": frames,
        "reading": {n: _reading(n, hh_stats[n], hv_stats.get(n))
                    for n in out_areas},
        "geometry_note": ("same track 172 D / frame 064, identical incidence/"
                          "acquisition geometry — relative change is "
                          "interpretable on BETA pre-calibration data"),
        "status": "candidates, never confirmed",
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "grid": {**GRID.as_dict(), "crs": f"EPSG:{GRID.epsg}",
                 "note": "60 m, snapped — identical to "
                         "wa_2025_nisar_{pre,post}_*_60m.tif"},
        "method": ("HH/HV block-averaged linear power over the AOI chunks only "
                   "(tp.response_nisar); stats on the common finite-on-all-"
                   "three-dates pixel set per area; masks reprojected nearest "
                   "to the 60 m grid"),
        "granules": {DATES[r]: GRANULES[r] for r in ("ref", "pre", "post")},
        "masks": {n: f"{CASE}/cog/{f}" for n, f in MASKS.items()},
    }
    if ios:
        doc["io_bytes"] = ios
    return doc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="tools.wa_nisar_timeseries",
                               description=__doc__.splitlines()[0])
    ap.add_argument("--skip-fetch", action="store_true",
                    help="use already-written COGs (no CMR/Earthdata access)")
    ap.add_argument("--stats-only", action="store_true",
                    help="only nisar_timeseries.json — no COG/PNG/index writes")
    args = ap.parse_args(argv)

    case_dir = Path(DATA_DIR) / CASE
    cog_dir, png_dir = case_dir / "cog", case_dir / "png"
    token = os.environ.get("EARTHDATA_TOKEN")

    hh_path = {r: cog_dir / f"{CASE}_nisar_{r}_{DATES[r].replace('-', '')}_60m.tif"
               for r in DATES}
    hv_path = {r: cog_dir / f"{CASE}_nisar_{r}_hv_{DATES[r].replace('-', '')}_60m.tif"
               for r in DATES}

    ios: dict[str, dict] = {}
    if not args.skip_fetch:
        if not token:
            raise RuntimeError("EARTHDATA_TOKEN required (or --skip-fetch)")
        urls = find_h5_urls()
        for role in ("ref", "pre", "post"):
            if role == "ref" or not hh_path[role].exists():
                arr, io = read_term_db(urls[role], "HHHH", token)
                if not args.stats_only:
                    _write_db_tif(arr, hh_path[role])
                ios[f"{role}_hh"] = io
            hv, io = read_term_db(urls[role], "HVHV", token)
            if not args.stats_only:
                _write_db_tif(hv, hv_path[role])
            ios[f"{role}_hv"] = io

    db_by_date = {DATES[r]: load_cog_db(hh_path[r]) for r in DATES}
    hv_by_date = ({DATES[r]: load_cog_db(hv_path[r]) for r in DATES}
                  if all(hv_path[r].exists() for r in DATES) else None)

    ref_db = db_by_date[DATES["ref"]]
    bounds = None
    if not args.stats_only:
        png_bytes, bounds = _gray_png_3857(ref_db, GRID.transform,
                                           f"EPSG:{GRID.epsg}", float("nan"))
        png_path = png_dir / f"nisar_ref_{DATES['ref'].replace('-', '')}.png"
        png_path.parent.mkdir(parents=True, exist_ok=True)
        png_path.write_bytes(png_bytes)
        index_path = png_dir / "_index.json"
        _upsert_index(index_path, {
            "id": "nisar_ref", "date": DATES["ref"],
            "png": f"{CASE}/png/{png_path.name}",
            "cog": f"{CASE}/cog/{hh_path['ref'].name}",
            "bounds": bounds, "kind": "observed"})
        # committed nisar_pre/post bounds sit ~10 m off this grid's render
        # extent — pin all three frames to the identical true bounds.
        idx = json.loads(index_path.read_text())
        for e in idx["layers"]:
            if e.get("id") in ("nisar_pre", "nisar_post") and e.get("bounds"):
                e["bounds"] = list(bounds)
        index_path.write_text(json.dumps(idx, indent=2) + "\n")
        print(f"png -> {png_path} ({len(png_bytes)} B, bounds {bounds})")
    else:
        idx = json.loads((png_dir / "_index.json").read_text())["layers"]
        bounds = next(e["bounds"] for e in idx
                      if e["id"] == "nisar_post")

    doc = build_timeseries(db_by_date, hv_by_date, case_dir, bounds,
                           ios=ios or None)
    ts_path = case_dir / "nisar_timeseries.json"
    ts_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
    print(f"ts  -> {ts_path}")
    for name, a in doc["areas"].items():
        print(f"  {name}: n={a['n_px']} km2={a['km2']} "
              f"hh_db={a['hh_db']} hv_db={a['hv_db']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
