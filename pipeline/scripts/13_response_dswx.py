"""Now-domain flood candidates from OPERA DSWx-S1 (L27 response, R1).

Writes data/now/response/{flood_dswx.geojson, dswx_state.npz, dswx_meta.json}
(meta last). Incremental: only granules newer than the state's last
acquisition (minus --overlap-h, de-duplicated by granule id). First run
bootstraps the baseline from the previous --bootstrap-days of granules.

Auth: EARTHDATA_TOKEN (bearer) for PO.DAAC downloads; CMR search is anonymous.

Usage: uv run python pipeline/scripts/13_response_dswx.py
       uv run python pipeline/scripts/13_response_dswx.py --dry-run
"""
import argparse
import json
import sys
from pathlib import Path

import requests
from fetch import opera
from fetch.edl import edl_token
from tp.config import REPO_ROOT, now_bounds
from tp.response_dswx import Grid, run

# GPW v4.11 2020 30" population COUNT (persons/cell), lead-gathering's
# data/now/static/gpw/manifest.json. Gitignored: CI must fetch it first.
GPW = REPO_ROOT / "data/now/static/gpw/gpw_popcount_2020_30sec.tif"
GPW_LABEL = "GPW v4.11 Population Count 2020, 30 arc-sec (CIESIN/SEDAC, doi:10.7927/H4JW8BX5)"


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=REPO_ROOT / "data/now/response")
    ap.add_argument("--res", type=float, default=0.0025, help="grid resolution (deg)")
    ap.add_argument("--bootstrap-days", type=int, default=60)
    ap.add_argument("--overlap-h", type=int, default=24)
    ap.add_argument("--window-h", type=int, default=144,
                    help="latest obs must be within this many hours of the newest acquisition")
    ap.add_argument("--min-obs", type=int, default=3,
                    help="earlier obs required before a pixel can be a candidate")
    ap.add_argument("--base-max", type=float, default=0.2)
    ap.add_argument("--min-area-km2", type=float, default=0.25)
    ap.add_argument("--simplify-m", type=float, default=150.0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--gpw", type=Path, default=GPW, help="GPW v4.11 2020 30-arc-sec count raster")
    ap.add_argument("--landcover", type=Path, default=REPO_ROOT / "data/now/static/mcd12q1/lc_type1_2025.tif")
    ap.add_argument("--outline", type=Path, default=REPO_ROOT / "data/now/static/brazil_outline.geojson")
    ap.add_argument("--gsw-occurrence", type=Path,
                    default=REPO_ROOT / "data/now/static/jrc_gsw/gsw_occurrence_2021.tif")
    ap.add_argument("--imerg-dir", type=Path, default=REPO_ROOT / "data/now/cog/imerg_early",
                    help="daily imerg_early_YYYYMMDD.tif COGs (written by the now job)")
    ap.add_argument("--gsw-seasonality", type=Path,
                    default=REPO_ROOT / "data/now/static/jrc_gsw/gsw_seasonality_2021.tif")
    ap.add_argument("--dry-run", action="store_true", help="print the grid + CMR query only")
    args = ap.parse_args(argv)

    w, s, e, n = now_bounds()
    grid = Grid(w, s, e, n, args.res)
    if args.dry_run:
        print(json.dumps({"short_name": opera.DSWX_S1_SHORT_NAME, "bbox": [w, s, e, n],
                          "grid_shape": list(grid.shape), "out": str(args.out)}, indent=2))
        return
    if not edl_token():
        sys.exit("EARTHDATA_TOKEN is required for PO.DAAC DSWx-S1 downloads")

    session = requests.Session()
    meta = run(
        args.out, grid,
        search=lambda start, end: opera.search_dswx_s1((w, s, e, n), start, end, session=session),
        fetch=lambda g: opera.download_wtr(g["wtr_url"], session=session),
        gpw_sources=[str(args.gpw)], gpw_label=GPW_LABEL, landcover_path=args.landcover, outline_path=args.outline,
        gsw_paths=(args.gsw_occurrence, args.gsw_seasonality),
        imerg_dir=args.imerg_dir,
        bootstrap_days=args.bootstrap_days, overlap_h=args.overlap_h,
        window_h=args.window_h, min_obs=args.min_obs, base_max=args.base_max,
        min_area_km2=args.min_area_km2, simplify_m=args.simplify_m, workers=args.workers,
    )
    print(json.dumps({k: meta[k] for k in (
        "latest_acquired", "latency_h", "n_granules", "n_failed", "raw_candidate_km2", "flooded_km2",
        "new_km2", "receded_km2", "people_flooded", "lower_confidence_km2", "lower_confidence_people",
        "excluded_water_wetland_km2", "outside_brazil_km2", "seasonal_downgraded_km2",
        "seasonal_downgraded_people", "norain_downgraded_km2", "norain_downgraded_people", "all_candidates_km2",
        "all_candidates_people", "unbaselined_water_km2", "coverage_pct", "features", "_null_reasons")}, indent=2))


if __name__ == "__main__":
    main()
