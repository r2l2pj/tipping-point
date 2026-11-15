"""Add GFS forecast daily rain to the case rain/soil chart series (L28b).

For each calendar day X in data/<case>/series/<case>_rain_soil_<tag>.json and
data/<case>/series/imerg_smap_daily.json (the file the app charts):
  gfs_mm        AOI-mean GFS precip for day X from the run issued X-1 00Z
                (band d2 of data/<case>/forecast_rain/<case>_gfs_<X-1>.tif)
  gfs_mm_lead0  same day from the run issued X 00Z (band d1)
  gfs_issued    X-1 (ISO date) when gfs_mm is present
  gfs_rain3_mm  AOI-mean GFS 3-day total over days X-2..X from the run issued
                X-2 00Z (bands d1+d2+d3) — the like-for-like companion of
                imerg_mm / rain3_mm, which are OBSERVED 3-day totals ending X
  gfs_rain3_issued  X-2 (ISO date) when gfs_rain3_mm is present
Band d<j> of an issue-D COG = forecast precip over calendar day D+j-1 (UTC),
see tools/regen_gfs_replay.py. Missing COG/band -> null.

    PYTHONPATH=.:pipeline .venv/bin/python tools/gfs_series.py
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np
import rasterio

REPO = Path(__file__).resolve().parents[1]


def band_mean(case: str, issue: dt.date, band: int) -> float | None:
    path = REPO / "data" / case / "forecast_rain" / f"{case}_gfs_{issue:%Y%m%d}.tif"
    if not path.exists():
        return None
    with rasterio.open(path) as src:
        if band > src.count:
            return None
        arr = src.read(band).astype(np.float64)
    return float(np.nanmean(arr)) if np.isfinite(arr).any() else None


def rain3_mean(case: str, issue: dt.date) -> float | None:
    """AOI mean of the 3-day GFS total (bands d1..d3) of the run issued `issue`."""
    path = REPO / "data" / case / "forecast_rain" / f"{case}_gfs_{issue:%Y%m%d}.tif"
    if not path.exists():
        return None
    with rasterio.open(path) as src:
        if src.count < 3:
            return None
        arr = src.read([1, 2, 3]).astype(np.float64).sum(axis=0)
    return float(np.nanmean(arr)) if np.isfinite(arr).any() else None


def add_gfs(series_path: Path) -> int:
    doc = json.loads(series_path.read_text())
    case = series_path.parent.parent.name   # data/<case>/series/
    n = 0
    for row in doc["days"]:
        day = dt.date.fromisoformat(row["date"])
        prev = day - dt.timedelta(days=1)
        row["gfs_mm"] = band_mean(case, prev, 2)
        row["gfs_mm_lead0"] = band_mean(case, day, 1)
        row["gfs_issued"] = prev.isoformat() if row["gfs_mm"] is not None else None
        prev2 = day - dt.timedelta(days=2)
        row["gfs_rain3_mm"] = rain3_mean(case, prev2)
        row["gfs_rain3_issued"] = prev2.isoformat() if row["gfs_rain3_mm"] is not None else None
        n += row["gfs_mm"] is not None
    doc["gfs_source"] = ("NOAA GFS 0.25 deg 00Z runs via AWS noaa-gfs-bdp-pds; "
                         "AOI mean of native cells; gfs_mm = 1-day forecast for the day "
                         "(issued day-1); gfs_rain3_mm = 3-day forecast total ending the day "
                         "(issued day-2), compare with imerg_mm (observed 3-day total)")
    series_path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    return n


def main() -> int:
    for case in ("rs_2024", "wa_2025"):
        sdir = REPO / "data" / case / "series"
        for path in sorted([*sdir.glob(f"{case}_rain_soil_*.json"),
                            *sdir.glob("imerg_smap_daily.json")]):
            print(f"{path.relative_to(REPO)}: gfs_mm on {add_gfs(path)} days")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
