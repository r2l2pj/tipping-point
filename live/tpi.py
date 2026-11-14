"""Pure-numpy TPI math — mirrors gee/01_tpi_index.js exactly.

R: piecewise score of 3-day rain against monthly p90/p95/p99 climatology.
S: SMAP L4 sm_rootzone_wetness rescaled to [0,1] over soil_wetness_range.
T_slide: slope rescaled over slope_range_deg.
T_flood: 1 - HAND rescaled over hand_range_m, zeroed where slope > 5 deg.
TPI = (R·S·T)^(1/3) — weighted geometric mean R^wR·S^wS·T^wT, uniform by
default (config.yaml thresholds.tpi_weights). Classes are counts of
thresholds exceeded (0..3).
"""
from __future__ import annotations

import numpy as np

from .config import (
    HAND_RANGE_M,
    LABELS,
    SLOPE_FLOOD_MAX_DEG,
    SLOPE_RANGE_DEG,
    SOIL_WETNESS_RANGE,
    TPI_CLASSES,
    TPI_WEIGHTS,
)


def unit_scale(x: np.ndarray, lo: float, hi: float) -> np.ndarray:
    """ee.Image.unitScale: linear ramp clamped to [0,1]. NaN propagates."""
    x = np.asarray(x, dtype=np.float64)
    return np.clip((x - lo) / (hi - lo), 0.0, 1.0)


def rain_score(rain3: np.ndarray, p90: np.ndarray, p95: np.ndarray, p99: np.ndarray) -> np.ndarray:
    """R in [0,1] — same .where() chain as gee/01 (later conditions win)."""
    rain3 = np.asarray(rain3, dtype=np.float64)
    with np.errstate(divide="ignore", invalid="ignore"):
        mid = (rain3 - p90) / (p95 - p90) * 0.5
        high = (rain3 - p95) / (p99 - p95) * 0.5 + 0.5
    r = np.zeros_like(rain3)
    r = np.where(rain3 >= p90, mid, r)
    r = np.where(rain3 >= p95, high, r)
    r = np.where(rain3 >= p99, 1.0, r)
    return np.clip(r, 0.0, 1.0)


def soil_score(sm_rootzone_wetness: np.ndarray) -> np.ndarray:
    """S in [0,1] — SMAP L4 wetness over soil_wetness_range, clamped."""
    return unit_scale(np.asarray(sm_rootzone_wetness, dtype=np.float64), *SOIL_WETNESS_RANGE)


def terrain_slide(slope_deg: np.ndarray) -> np.ndarray:
    """T_slide — 0 below slope_range_deg[0], 1 above slope_range_deg[1]."""
    return unit_scale(np.asarray(slope_deg, dtype=np.float64), *SLOPE_RANGE_DEG)


def terrain_flood(hand_m: np.ndarray, slope_deg: np.ndarray) -> np.ndarray:
    """T_flood — 1 where HAND <= hand_range_m[0]; 0 where slope > 5 deg."""
    t = np.clip(1.0 - unit_scale(np.asarray(hand_m, dtype=np.float64), *HAND_RANGE_M), 0.0, 1.0)
    return np.where(np.asarray(slope_deg, dtype=np.float64) > SLOPE_FLOOD_MAX_DEG, 0.0, t)


def tpi(rain3: np.ndarray, sm: np.ndarray, clim: dict[str, np.ndarray],
        t_slide: np.ndarray, t_flood: np.ndarray,
        weights: tuple[float, float, float] = TPI_WEIGHTS) -> dict[str, np.ndarray]:
    """Full TPI — weighted geometric mean R^wR·S^wS·T^wT (uniform 1/3 default).

    `clim` holds p90/p95/p99 for the target month's climatology. `weights`
    comes from config.yaml thresholds.tpi_weights.
    """
    r = rain_score(rain3, clim["p90"], clim["p95"], clim["p99"])
    s = soil_score(sm)
    wr, ws, wt = weights
    return {
        "r": r,
        "s": s,
        "tpi_slide": r ** wr * s ** ws * t_slide ** wt,
        "tpi_flood": r ** wr * s ** ws * t_flood ** wt,
    }


def classify(tpi_arr: np.ndarray, cuts: tuple[float, ...] = TPI_CLASSES) -> np.ndarray:
    """gee classes(img): count of thresholds strictly exceeded -> uint8 0..3."""
    v = np.asarray(tpi_arr, dtype=np.float64)
    cls = np.zeros(v.shape, dtype=np.uint8)
    for cut in cuts:
        cls += (v > cut).astype(np.uint8)
    cls[~np.isfinite(v)] = 0
    return cls


def class_label(cls: int) -> str:
    return LABELS[int(np.clip(cls, 0, len(LABELS) - 1))]


def projected_rain3(obs_recent: np.ndarray, fc_daily: np.ndarray, k: int) -> np.ndarray:
    """3-day accumulation for forecast day D+k.

    obs_recent: (m, H, W) most recent observed daily accumulations, latest last.
    fc_daily:   (>=k, H, W) forecast daily accumulations starting at D+1.
    For k < 3 the window still contains observed days; for k == 3 it is all forecast.
    """
    if not 1 <= k <= 3:
        raise ValueError("k must be 1..3 (D+1..D+3)")
    obs_recent = np.asarray(obs_recent, dtype=np.float64)
    fc_daily = np.asarray(fc_daily, dtype=np.float64)
    total = fc_daily[:k].sum(axis=0)
    obs_need = 3 - k
    if obs_need:
        total = total + obs_recent[-obs_need:].sum(axis=0)
    return total


def accumulate3(daily: np.ndarray) -> np.ndarray:
    """Rolling 3-day sums of a (n_days, H, W) daily accumulation stack."""
    daily = np.asarray(daily, dtype=np.float64)
    c = np.cumsum(np.nan_to_num(daily), axis=0)
    out = np.empty_like(daily)
    out[:2] = c[:2]
    out[2:] = c[2:] - np.concatenate([np.zeros_like(c[:1]), c[:-3]], axis=0)
    return out
