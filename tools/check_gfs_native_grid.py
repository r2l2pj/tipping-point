"""Real-GEE check: a fetched GFS COG's pixels must match day_sum sampled
on the native grid in Earth Engine.

For each check point we reduceRegion (Reducer.first) the d1 day_sum
image at the point, with crs = the source projection (native GFS grid),
and compare against rasterio's indexed cell value in the written COG.
Exits nonzero if any point differs by > 0.01 mm — this is the check
that caught the WKT-download one-row-north georeference bug.

Usage:
    uv run python tools/check_gfs_native_grid.py \
        --case rs_2024 --issue 2024-04-29
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

TOLERANCE_MM = 0.01


def check(case: str, issue: dt.date) -> list[tuple]:
    import ee
    import rasterio
    from rasterio.transform import rowcol

    from pipeline.fetch import gfs

    gfs._init_ee()
    case_id, bbox = gfs._case_cfg(case)
    col = ee.ImageCollection(gfs.COLLECTION)
    run_ms = gfs.select_run(col, ee, issue)
    aoi = ee.Geometry.Rectangle(bbox)
    image = gfs.forecast_image(col, ee, aoi, run_ms, issue)
    src = (col.filter(ee.Filter.eq("creation_time", run_ms))
              .filter(ee.Filter.gt("forecast_hours", 0)).first())
    proj = src.projection()

    # GEE-native day_sum at each point (d1 band), native projection.
    w, s, e, n = bbox
    points = [(w + (e - w) * fx, s + (n - s) * fy)
              for fx, fy in ((0.5, 0.5), (0.25, 0.2), (0.8, 0.85),
                             (0.6, 0.35))]
    gee_vals = []
    for lon, lat in points:
        v = (image.select("d1")
             .reduceRegion(reducer=ee.Reducer.first(),
                           geometry=ee.Geometry.Point([lon, lat]),
                           crs=proj)
             .getInfo().get("d1"))
        gee_vals.append(float(v) if v is not None else float("nan"))

    path = gfs.cog_path(case_id, issue)
    rows = []
    with rasterio.open(path) as ds:
        arr = ds.read(1)
        for (lon, lat), gee in zip(points, gee_vals):
            r, c = rowcol(ds.transform, lon, lat)
            file_val = float(arr[r, c])
            rows.append((lon, lat, file_val, gee,
                         abs(file_val - gee) <= TOLERANCE_MM))
    return rows


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--case", required=True)
    p.add_argument("--issue", type=dt.date.fromisoformat, required=True)
    args = p.parse_args()

    rows = check(args.case, args.issue)
    ok = True
    for lon, lat, fv, gv, good in rows:
        ok &= good
        print(f"({lon:+.3f},{lat:+.3f}) file={fv:.3f} "
              f"gee={gv:.3f} {'OK' if good else 'MISMATCH'}")
    print("PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
