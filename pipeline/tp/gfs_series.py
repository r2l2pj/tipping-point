"""GFS forecast rain fields for a case's daily chart series (L28b), shared by the
bundle writer (tp.bundle) and tools/gfs_series.py so a rebuild never drops them.

For calendar day X, from data/<case>/forecast_rain/<case>_gfs_<issue>.tif (band d<j>
= forecast precip over calendar day issue+j-1, UTC; tools/regen_gfs_replay.py):
  gfs_mm            AOI mean for day X from the run issued X-1 00Z (band d2)
  gfs_mm_lead0      same day from the run issued X 00Z (band d1)
  gfs_issued        X-1 (ISO) when gfs_mm is present
  gfs_rain3_mm      AOI mean of the 3-day total over X-2..X from the run issued X-2
                    (bands d1+d2+d3) — the like-for-like companion of imerg_mm
  gfs_rain3_issued  X-2 (ISO) when gfs_rain3_mm is present
Missing COG/band -> null.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np

GFS_SOURCE = ("NOAA GFS 0.25 deg 00Z runs via AWS noaa-gfs-bdp-pds; AOI mean of native cells; "
              "gfs_mm = 1-day forecast for the day (issued day-1); gfs_rain3_mm = 3-day forecast "
              "total ending the day (issued day-2), compare with imerg_mm (observed 3-day total)")


def _cog(fr_dir: Path, case: str, issue: dt.date) -> Path:
    return Path(fr_dir) / f"{case}_gfs_{issue:%Y%m%d}.tif"


def band_mean(fr_dir: Path, case: str, issue: dt.date, band: int) -> float | None:
    import rasterio

    path = _cog(fr_dir, case, issue)
    if not path.exists():
        return None
    with rasterio.open(path) as src:
        if band > src.count:
            return None
        arr = src.read(band).astype(np.float64)
    return float(np.nanmean(arr)) if np.isfinite(arr).any() else None


def rain3_mean(fr_dir: Path, case: str, issue: dt.date) -> float | None:
    """AOI mean of the 3-day GFS total (bands d1..d3) of the run issued `issue`."""
    import rasterio

    path = _cog(fr_dir, case, issue)
    if not path.exists():
        return None
    with rasterio.open(path) as src:
        if src.count < 3:
            return None
        arr = src.read([1, 2, 3]).astype(np.float64).sum(axis=0)
    return float(np.nanmean(arr)) if np.isfinite(arr).any() else None


def gfs_fields(fr_dir: Path, case: str, day: dt.date) -> dict:
    prev, prev2 = day - dt.timedelta(days=1), day - dt.timedelta(days=2)
    g1 = band_mean(fr_dir, case, prev, 2)
    g3 = rain3_mean(fr_dir, case, prev2)
    return {"gfs_mm": g1, "gfs_mm_lead0": band_mean(fr_dir, case, day, 1),
            "gfs_issued": prev.isoformat() if g1 is not None else None,
            "gfs_rain3_mm": g3, "gfs_rain3_issued": prev2.isoformat() if g3 is not None else None}
