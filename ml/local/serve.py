"""Serving: P(landslide within 24 h / 48 h / 72 h / 7 d / 14 d) on a grid.

For lead-live's 3-hourly Now job (publishes ``p_trigger_*`` frames). Pure
numpy + xgboost, no network, no GEE::

    from ml.local.serve import TriggerModel
    tm = TriggerModel.load()                      # ml/models/p_trigger_xgb.json
    out = tm.predict(issue_date, static=..., soil=..., texture=...,
                     rain_obs=obs7, rain_fc=gfs14, p99_daily=p99d, p99_3day=p99_3)
    out["p_trigger_24h"]  # 2-D float32, calibrated, NaN where inputs are missing

Inputs, all on the same (H, W) grid:
  static   {elevation, slope, aspect_sin, aspect_cos, curvature, relief, landcover, ndvi}
  soil     {sm_surface, sm_rootzone, sm_profile_wetness} — latest SMAP L4 (held)
  texture  {clay, sand, porosity} — SPL4SMLM
  rain_obs (7, H, W)  observed IMERG mm/day for days D-7 .. D-1
  rain_fc  (>=14, H, W) GFS mm/day for UTC days D .. D+13 (00Z run of D;
           ``live.fetch._gfs_day_label`` D+k == our day D+k, L28b)
  p99_daily, p99_3day  IMERG climatology (``sources_dynamic.RainClim``)
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd

from . import models as M
from . import settings as S


class TriggerModel:
    def __init__(self, model, meta: dict):
        self.model = model
        self.meta = meta
        self.cols = meta["features"]
        self.horizons = {k: int(v) for k, v in meta["horizons"].items()}
        self.cals = {h: M.Calibrator.from_dict(c)
                     for h, c in (meta.get("horizon_calibrators") or {}).items()}

    @classmethod
    def load(cls, path: Path | None = None) -> TriggerModel:
        m, meta = M.load_xgb(path or S.MODEL_DIR / "p_trigger_xgb.json")
        return cls(m, meta)

    def predict(self, issue_date: dt.date, *, static: dict, soil: dict, texture: dict,
                rain_obs: np.ndarray, rain_fc: np.ndarray, p99_daily: np.ndarray,
                p99_3day: np.ndarray) -> dict[str, np.ndarray]:
        hmax = max(self.horizons.values())
        rain_obs = np.asarray(rain_obs, dtype=np.float32)
        rain_fc = np.asarray(rain_fc, dtype=np.float32)
        if rain_obs.shape[0] < 7 or rain_fc.shape[0] < hmax:
            raise ValueError(f"need 7 observed days and {hmax} forecast days")
        series = np.concatenate([rain_obs[-7:], rain_fc[:hmax]], axis=0)  # D-7 .. D+hmax-1
        shape = series.shape[1:]
        const = {**static, **soil, **texture}
        flat = {k: np.asarray(v, dtype=np.float64).ravel() for k, v in const.items()}
        surv = np.ones(int(np.prod(shape)))
        out: dict[str, np.ndarray] = {}
        by_h = sorted(self.horizons.items(), key=lambda kv: kv[1])
        for k in range(hmax):
            c = 7 + k
            r24 = series[c]
            r72 = series[c - 2:c + 1].sum(axis=0)
            r7 = series[c - 6:c + 1].sum(axis=0)
            with np.errstate(divide="ignore", invalid="ignore"):
                feats = {**flat, "rain_24h": r24.ravel(), "rain_72h": r72.ravel(), "rain_7d": r7.ravel(),
                         "rain_24h_rel": (r24 / p99_daily).ravel(),
                         "rain_72h_rel": (r72 / p99_3day).ravel()}
            t = pd.Timestamp(issue_date) + pd.Timedelta(days=k)
            ang = 2 * np.pi * t.day_of_year / 365.25
            feats["doy_sin"] = np.full(surv.shape, np.sin(ang))
            feats["doy_cos"] = np.full(surv.shape, np.cos(ang))
            df = pd.DataFrame({col: feats.get(col, np.full(surv.shape, np.nan)) for col in self.cols})
            pk = M.predict(self.model, df, self.cols)
            surv *= 1.0 - np.nan_to_num(pk)
            for h, H in by_h:
                if H == k + 1:
                    p = 1.0 - surv
                    cal = self.cals.get(h)
                    p = cal(p) if cal else p
                    out[f"p_trigger_{h}"] = p.reshape(shape).astype(np.float32)
        # per-horizon isotonic calibrators can cross by ~1e-3; a longer window can never
        # be less likely than a shorter one -> running max over increasing H
        prev = None
        for h, _ in by_h:
            k = f"p_trigger_{h}"
            if k in out and prev is not None:
                out[k] = np.fmax(out[k], prev)
            prev = out.get(k, prev)
        missing = ~np.isfinite(np.asarray(static.get("elevation", np.zeros(shape)), dtype=float))
        for v in out.values():
            v[missing] = np.nan
        return out
