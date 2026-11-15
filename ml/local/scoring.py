"""Scores shared by train (VAL) and evaluate (TEST): metrics, rules, horizons.

Horizon P for an issue day D and horizon H (days D .. D+H-1)::

    p_k = model(features at target day t = D+k), k = 0..H-1
    P_H = 1 - prod_k (1 - p_k)          # independence across days (declared)

Rain at t uses the daily series r[D-7 .. D+H-1]: days < D are observed IMERG;
days >= D are the forecast — ``perfect`` (observed IMERG, retrospective upper
bound) or ``gfs`` (NOAA GFS 00Z run of D, the operational case). Soil is held
at the issue day (SMAP at D), terrain/texture/land cover are static.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, confusion_matrix, roc_auc_score

from . import settings as S

TPI_W = (1 / 3, 1 / 3, 1 / 3)
OFF = 7  # observed days before the issue day kept in the rain series


# ---------------------------------------------------------------------- metrics
def metrics(y, p, thr: float) -> dict:
    y = np.asarray(y, dtype=int)
    p = np.asarray(p, dtype=float)
    ok = np.isfinite(p)
    y, p = y[ok], p[ok]
    out = {"n": len(y), "n_pos": int(y.sum()), "n_neg": int(len(y) - y.sum()),
           "base_rate": float(y.mean()) if len(y) else None, "threshold": float(thr),
           "n_dropped_nan": int((~ok).sum())}
    if len(y) == 0:
        return out | {k: None for k in ("precision", "recall", "f1", "accuracy", "tn", "fp",
                                         "fn", "tp", "pr_auc", "roc_auc")}
    pred = (p >= thr).astype(int)
    tn, fp, fn, tp = confusion_matrix(y, pred, labels=[0, 1]).ravel()
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    two = len(np.unique(y)) == 2
    return out | {
        "precision": float(prec), "recall": float(rec),
        "f1": float(2 * prec * rec / (prec + rec)) if prec + rec else 0.0,
        "accuracy": float((tp + tn) / len(y)),
        "tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp),
        "pr_auc": float(average_precision_score(y, p)) if two else None,
        "roc_auc": float(roc_auc_score(y, p)) if two else None,
    }


# ------------------------------------------------------------------------ rules
def slope_rule(slope) -> np.ndarray:
    """Manual terrain score (TPI's T_slide): 0 at 15°, 1 at 30° (config.yaml)."""
    lo, hi = _slope_range()
    return np.clip((np.asarray(slope, dtype=float) - lo) / (hi - lo), 0, 1)


def _slope_range():
    from tp.config import threshold
    return tuple(threshold("slope_range_deg", (15, 30)))


def tpi_score(rain_72h, p90, p95, p99, sm_wetness, slope) -> np.ndarray:
    """TPI = (R·S·T)^(1/3), same math as live/tpi.py (imported, not copied)."""
    from live.tpi import rain_score, soil_score, terrain_slide
    r = rain_score(rain_72h, p90, p95, p99)
    s = soil_score(sm_wetness)
    t = terrain_slide(slope)
    wr, ws, wt = TPI_W
    return r ** wr * s ** ws * t ** wt


# --------------------------------------------------------------------- horizons
def rain_series(rain, lon, lat, issue, H: int, *, source: str = "perfect",
                gfs_daily=None) -> np.ndarray:
    """(n, OFF + H) daily rain for days D-7 .. D+H-1 (column OFF = day D)."""
    issue = np.asarray(issue).astype("datetime64[D]")
    cols = []
    for o in range(-OFF, H):
        cols.append(rain.daily(lon, lat, issue + np.timedelta64(o, "D")))
    r = np.column_stack(cols).astype(float)
    if source == "gfs":
        if gfs_daily is None:
            raise ValueError("source='gfs' needs gfs_daily(lon, lat, issue_dates, lead_days)")
        fc = gfs_daily(lon, lat, issue, H)
        r[:, OFF:] = fc[:, :H]
    elif source != "perfect":
        raise ValueError(source)
    return r


def rain_feats_at(r: np.ndarray, k: int, p99d, p99_3) -> pd.DataFrame:
    """Rain features for target day D+k from the series of ``rain_series``."""
    c = OFF + k
    r24 = r[:, c]
    r72 = r[:, c - 2:c + 1].sum(axis=1)
    r7 = r[:, c - 6:c + 1].sum(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        return pd.DataFrame({
            "rain_24h": r24, "rain_72h": r72, "rain_7d": r7,
            "rain_24h_rel": r24 / p99d, "rain_72h_rel": r72 / p99_3,
        })


def doy_at(issue, k: int) -> pd.DataFrame:
    t = pd.to_datetime(np.asarray(issue).astype("datetime64[D]") + np.timedelta64(k, "D"))
    ang = 2 * np.pi * t.day_of_year.to_numpy() / 365.25
    return pd.DataFrame({"doy_sin": np.sin(ang), "doy_cos": np.cos(ang)})


def horizon_scores(hz: pd.DataFrame, H: int, entries: dict, rain, *, source: str = "perfect",
                   gfs_daily=None) -> dict[str, np.ndarray]:
    """Raw P_H per entry for issue rows ``hz`` (all with the same H).

    ``entries``: name -> {"kind": "xgb"|"rf"|"tpi"|"nowcast", "model", "cols"}.
    TPI entry scores max_k TPI(D+k) (0..1). ``nowcast`` uses only observed
    rain up to D-1 (no forecast at all): one daily p at target D-1.
    """
    from .models import predict

    lon, lat = hz["lon"].to_numpy(), hz["lat"].to_numpy()
    issue = hz["issue_date"].to_numpy()
    r = rain_series(rain, lon, lat, issue, H, source=source, gfs_daily=gfs_daily)
    p99d, p99_3 = rain.p99_daily(lon, lat), rain.p99_3day(lon, lat)
    base = hz.reset_index(drop=True)
    out: dict[str, np.ndarray] = {}
    for name, e in entries.items():
        kind = e["kind"]
        if kind == "nowcast":
            feats = pd.concat([base.drop(columns=[c for c in S.RAIN if c in base]),
                               rain_feats_at(r, -1, p99d, p99_3), doy_at(issue, -1)], axis=1)
            feats = feats.loc[:, ~feats.columns.duplicated(keep="last")]
            out[name] = predict(e["model"], feats, e["cols"])
            continue
        surv = np.ones(len(base))
        best = np.zeros(len(base))
        for k in range(H):
            rf = rain_feats_at(r, k, p99d, p99_3)
            if kind == "tpi":
                doy = pd.to_datetime(issue.astype("datetime64[D]") + np.timedelta64(k, "D")).day_of_year
                p90, p95, p99 = rain.pctl_3day(lon, lat, doy.to_numpy())
                best = np.fmax(best, tpi_score(rf["rain_72h"].to_numpy(), p90, p95, p99,
                                               base["sm_rootzone_wetness"].to_numpy(),
                                               base["slope"].to_numpy()))
                continue
            feats = pd.concat([base.drop(columns=[c for c in S.RAIN if c in base]
                                         + [c for c in ("doy_sin", "doy_cos") if c in base]),
                               rf, doy_at(issue, k)], axis=1)
            pk = predict(e["model"], feats, e["cols"])
            surv *= 1.0 - np.nan_to_num(pk)
        out[name] = best if kind == "tpi" else 1.0 - surv
    return out
