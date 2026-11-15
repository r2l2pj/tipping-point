"""L28b: as-issued 3-day rain for GFS forecast frames — no look-ahead, no
double counting.

At issue time D 00Z a forecaster has observed rain only for calendar days
<= D-1 and the GFS D 00Z run for calendar days D, D+1, D+2, D+3 (GFS COG
band d<j> = calendar day D+j-1, see tools/regen_gfs_replay.py). The frame
dated D+k (k = 1..3) is the 3-day accumulation over calendar days
D+k-2 .. D+k, each day taken exactly once: observed if the day is before D,
forecast otherwise.
"""
from __future__ import annotations

import datetime as dt
from collections.abc import Mapping

import numpy as np


def gfs_band_days(issue: dt.date, n_bands: int) -> dict[dt.date, int]:
    """Calendar day -> 1-based GFS COG band (band d<j> = day D+j-1)."""
    return {issue + dt.timedelta(days=j - 1): j for j in range(1, n_bands + 1)}


def window_days(issue: dt.date, k: int) -> list[dt.date]:
    """Calendar days of the 3-day window ending D+k."""
    if not 1 <= k <= 3:
        raise ValueError("k must be 1..3")
    target = issue + dt.timedelta(days=k)
    return [target - dt.timedelta(days=j) for j in (2, 1, 0)]


def issue_rain3(issue: dt.date, k: int,
                obs: Mapping[dt.date, np.ndarray],
                fc: Mapping[dt.date, np.ndarray]) -> tuple[np.ndarray, list[str]]:
    """3-day accumulation ending D+k as seen at issue D 00Z.

    Returns (mm array, per-day source labels like '2024-04-21:obs').
    Raises on look-ahead (any observed day >= D) or on a forecast day that is
    before D (that would be a hindcast, not a forecast).
    """
    late = sorted(d for d in obs if d >= issue)
    if late:
        raise ValueError(f"look-ahead: observed data for {late[0]} at issue {issue} 00Z")
    early = sorted(d for d in fc if d < issue)
    if early:
        raise ValueError(f"forecast day {early[0]} precedes issue {issue}")
    total, used = None, []
    for day in window_days(issue, k):
        if day < issue:
            src, arr = "obs", obs[day]
        else:
            src, arr = "fc", fc[day]
        arr = np.asarray(arr, dtype=np.float64)
        total = arr.copy() if total is None else total + arr
        used.append(f"{day.isoformat()}:{src}")
    return total, used
