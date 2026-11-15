"""Exports: t_ml.tif (3a), metrics into data/metrics.json, chart data for the app.

* ``data/cog/t_ml.tif`` — calibrated T_ML (0..1 float32, EPSG:4326 COG) on the
  Now-domain 0.01° grid (the live TPI grid), computed at 90 m and block-averaged
  (12×12) so slopes are not flattened by coarse resampling. ``--t-ml-90m`` also
  writes ``data/cog/t_ml_90m.tif`` (uint8 0-100, nodata 255).
* ``data/metrics.json`` cases.rs_2024: rf_auc (RandomForest), xgb_auc,
  rule_auc (slope rule), feature_importance, plus ``landslide_ml`` with 3b per
  horizon — merged with tp.metrics.merge_metrics (never write_text the root).
* ``data/ml/feature_importance.json`` and ``data/ml/model_metrics.json`` —
  small chart-ready files for the app.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import rasterio
from rasterio.transform import from_origin

from . import models as M
from . import settings as S

log = logging.getLogger(__name__)
DATA_OUT = S.REPO_ROOT / "data"
BLOCK = 12  # 0.01° / (1/1200°)


def _windows(bbox, step_deg: float = 1.0):
    w, s, e, n = bbox
    y = n
    while y > s + 1e-9:
        x = w
        y0 = max(s, y - step_deg)
        while x < e - 1e-9:
            x1 = min(e, x + step_deg)
            yield (x, y0, x1, y)
            x = x1
        y = y0


def export_t_ml(*, bbox=S.REGION_SERVE, model: str = "xgb_all", write_90m: bool = False,
                out: Path | None = None) -> Path:
    from . import sources_dynamic as D
    from . import sources_static as ST

    b = joblib.load(S.path("models", "bundle_3a.joblib"))
    e = b["entries"][model]
    res = S.RES_STATIC_DEG
    w, s, ee, n = bbox
    W1 = round((ee - w) / S.RES_EXPORT_1KM_DEG)
    H1 = round((n - s) / S.RES_EXPORT_1KM_DEG)
    km = np.full((H1, W1), np.nan, dtype=np.float32)
    out = out or DATA_OUT / "cog" / "t_ml.tif"
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp90 = None
    if write_90m:
        tmp90 = rasterio.open(out.with_name("t_ml_90m.tmp.tif"), "w", driver="GTiff",
                              width=round((ee - w) / res), height=round((n - s) / res), count=1,
                              dtype="uint8", nodata=255, crs="EPSG:4326",
                              transform=from_origin(w, n, res, res), tiled=True,
                              compress="deflate", BIGTIFF="IF_SAFER")
    for (x0, y0, x1, y1) in _windows(bbox):
        shape = (round((y1 - y0) / res), round((x1 - x0) / res))
        tr = from_origin(x0, y1, res, res)
        feats = {**ST.static_on_grid(tr, shape), **D.climate_on_grid(tr, shape)}
        df = pd.DataFrame({c: np.asarray(feats[c]).ravel() for c in e["cols"]})
        valid = np.isfinite(df["elevation"].to_numpy()) if "elevation" in df else np.ones(len(df), bool)
        p = np.full(len(df), np.nan, dtype=np.float32)
        if valid.any():
            p[valid] = e["cal"](M.predict(e["model"], df[valid], e["cols"]))
        grid = p.reshape(shape)
        bh, bw = shape[0] // BLOCK, shape[1] // BLOCK
        with np.errstate(invalid="ignore"):
            blk = np.nanmean(grid[:bh * BLOCK, :bw * BLOCK].reshape(bh, BLOCK, bw, BLOCK), axis=(1, 3))
        r0 = round((n - y1) / S.RES_EXPORT_1KM_DEG)
        c0 = round((x0 - w) / S.RES_EXPORT_1KM_DEG)
        km[r0:r0 + bh, c0:c0 + bw] = blk
        if tmp90 is not None:
            q = np.where(np.isfinite(grid), np.clip(np.round(grid * 100), 0, 100), 255).astype("uint8")
            tmp90.write(q, 1, window=rasterio.windows.Window(
                round((x0 - w) / res), round((n - y1) / res), shape[1], shape[0]))
        log.info("t_ml window %.0f,%.0f done", x0, y0)
    tr_km = from_origin(w, n, S.RES_EXPORT_1KM_DEG, S.RES_EXPORT_1KM_DEG)
    _write_cog(out, km, tr_km, desc="t_ml", nodata=np.nan)
    write_t_ml_png(km, tr_km, bbox)
    if tmp90 is not None:
        tmp90.close()
        with rasterio.open(out.with_name("t_ml_90m.tmp.tif")) as src:
            prof = src.profile | {"driver": "COG", "compress": "deflate"}
            with rasterio.open(out.with_name("t_ml_90m.tif"), "w", **prof) as dst:
                dst.write(src.read())
        out.with_name("t_ml_90m.tmp.tif").unlink()
    return out


T_ML_BREAKS = (0.2, 0.4, 0.6)   # same class breaks + palette as TPI (Baixo..Crítico)


def write_t_ml_png(km: np.ndarray, transform, bbox) -> Path:
    """data/ml/t_ml.png (EPSG:3857 RGBA, TPI palette) + data/ml/t_ml.json (bounds, legend)."""
    from live.config import LABELS, PALETTE_HEX
    from live.render import layer_png_3857

    valid = np.isfinite(km)
    cls = np.where(valid, np.digitize(np.nan_to_num(km), T_ML_BREAKS), 0).astype(np.uint8)
    png, bounds = layer_png_3857(cls, transform, valid)
    mldir = DATA_OUT / "ml"
    mldir.mkdir(parents=True, exist_ok=True)
    (mldir / "t_ml.png").write_bytes(png)
    (mldir / "t_ml.json").write_text(json.dumps({
        "layer": "t_ml", "png": "ml/t_ml.png", "cog": "cog/t_ml.tif",
        "png_crs": "EPSG:3857", "bounds_lonlat": bounds, "cog_crs": "EPSG:4326",
        "cog_res_deg": S.RES_EXPORT_1KM_DEG, "domain_bbox": list(bbox),
        "value": "calibrated landslide susceptibility 0-1 (3a T_ML, XGBoost; 90 m model "
                 "block-averaged to 0.01°). Relative, not an absolute frequency: trained "
                 "case-control 1:3.",
        "classes": [{"label": lab, "min": lo, "color": f"#{hx}"} for lab, lo, hx in
                    zip(LABELS, (0.0, *T_ML_BREAKS), PALETTE_HEX, strict=True)],
        "png_note": "Baixo is transparent (TPI convention)",
    }, indent=2))
    return mldir / "t_ml.png"


def _write_cog(path: Path, arr: np.ndarray, transform, *, desc: str, nodata) -> None:
    prof = {"driver": "COG", "width": arr.shape[1], "height": arr.shape[0], "count": 1,
            "dtype": str(arr.dtype), "crs": "EPSG:4326", "transform": transform,
            "nodata": nodata, "compress": "deflate"}
    with rasterio.open(path, "w", **prof) as dst:
        dst.write(arr, 1)
        dst.set_band_description(1, desc)


def importance_rows(imp: dict, groups: dict) -> list[dict]:
    feat_gain = imp.get("gain_share_feature", {})
    perm = imp.get("permutation_pr_auc_drop_test", {})
    rows = []
    for g, cols in groups.items():
        for c in cols:
            rows.append({"feature": c, "group": g, "gain_share": feat_gain.get(c),
                         "group_gain_share": imp.get("gain_share", {}).get(g),
                         "group_permutation_pr_auc_drop": perm.get(g)})
    return sorted(rows, key=lambda r: -(r["gain_share"] or 0))


def export_metrics(case: str = "rs_2024") -> dict:
    """Contract keys into data/metrics.json + chart files under data/ml/."""
    from tp.metrics import merge_metrics

    met = json.loads((S.MODEL_DIR / "metrics_ml.json").read_text())
    a = met.get("3a", {})
    bb = met.get("3b", {})
    am = a.get("models", {})

    def auc(name):
        return ((am.get(name) or {}).get("test") or {}).get("roc_auc")

    b3a = joblib.load(S.path("models", "bundle_3a.joblib")) if S.path("models", "bundle_3a.joblib").exists() else None
    fi = {}
    if b3a and a.get("importance"):
        fi = {r["feature"]: r["gain_share"] for r in importance_rows(a["importance"], b3a["groups"])}
    blob = {
        "rf_auc": auc("rf_all"), "xgb_auc": auc("xgb_all"), "rule_auc": auc("rule_slope"),
        "feature_importance": fi,
        "landslide_ml": {
            "split": "TEST only (50 km blocks for 3a; held-out events for 3b, RS 2024 unseen)",
            "t_ml": {k: (v or {}).get("test") for k, v in am.items()},
            "trigger_daily": {k: (v or {}).get("test") for k, v in (bb.get("daily") or {}).items()},
            "trigger_horizons": bb.get("horizons"),
        },
    }
    reasons = {k: "3a model not evaluated yet (run `python -m ml.local evaluate`)"
               for k in ("rf_auc", "xgb_auc", "rule_auc") if blob[k] is None}
    merge_metrics(case, blob, reasons=reasons, collections={
        "ml_labels": "NASA COOLR Global Landslide Catalog + RS 2024 inventory (UFRGS/Cemaden)",
        "ml_rain": "GPM IMERG V07 (NASA POWER IMERG_PRECTOT points; GES DISC OPeNDAP grids)",
        "ml_soil": "SMAP L4 SPL4SMGP 008 + SPL4SMLM 008", "ml_terrain": "NASADEM_HGT 001",
        "ml_landcover": "MCD12Q1 061", "ml_ndvi": "VNP13C2 002"}, path=DATA_OUT / "metrics.json")
    mldir = DATA_OUT / "ml"
    mldir.mkdir(parents=True, exist_ok=True)
    if b3a and a.get("importance"):
        (mldir / "feature_importance.json").write_text(json.dumps(
            {"model": "xgb_all (3a T_ML)", "rows": importance_rows(a["importance"], b3a["groups"]),
             "rf_permutation": a.get("importance_rf", {}).get("permutation_pr_auc_drop_test")}, indent=2))
    (mldir / "model_metrics.json").write_text(json.dumps(met, indent=2, default=str))
    b3b_path = S.path("models", "bundle_3b.joblib")
    if b3b_path.exists() and bb.get("importance"):
        b3b = joblib.load(b3b_path)
        (mldir / "feature_importance_3b.json").write_text(json.dumps(
            {"model": "xgb_all (3b trigger, daily)", "group_labels": GROUP_LABELS,
             "importance_type": "gain_share = XGBoost total_gain share (TRAIN fit); "
                                "group_permutation_pr_auc_drop = TEST PR-AUC drop when the group is shuffled",
             "rows": importance_rows(bb["importance"], b3b["groups"])}, indent=2))
    (mldir / "ml_card.json").write_text(json.dumps(ml_card(met), indent=2, default=str))
    return blob


GROUP_LABELS = {"rain": "Rain (IMERG)", "soil": "Soil (SMAP L4)", "terrain": "Terrain (NASADEM)",
                "landcover": "Surface (MCD12Q1 + VIIRS NDVI)", "climate": "Climate (IMERG/SMAP means)",
                "soil_texture": "Soil texture (SMAP L4 constants)"}
CARD_KEYS = ("n", "n_pos", "base_rate", "threshold", "precision", "recall", "f1", "accuracy",
             "pr_auc", "roc_auc", "tp", "fp", "fn", "tn")
REGION_EVENT = {"south_southeast_brazil": "rs_2024", "north_america": "wa_2025"}


def _slim(m: dict | None) -> dict | None:
    return {k: m.get(k) for k in CARD_KEYS} if m else None


def ml_card(met: dict) -> dict:
    """Small app file for 'Did we get it right?': TEST only, GFS (operational) first."""
    a, b = met.get("3a", {}), met.get("3b", {})
    hz = b.get("horizons") or {}
    card = {
        "split": {
            "3a": "TEST = ~20 % of ~50 km blocks (10 km buffer), S+SE Brazil; GLC <= 5 km + RS 2024",
            "3b": "TRAIN/VAL = dated GLC/COOLR landslides worldwide 2015-2025 (95 % 2015-2018), "
                  "split by event; TEST = unseen events RS 2024 (dated 2024-05-01, window "
                  "Apr 29-May 2) + WA 2025 (8 dated WGS reports)",
            "rows": {k: (v or {}).get("split_rows") for k, v in (("3a", a), ("3b", b))},
        },
        "t_ml": {k: _slim((v or {}).get("test")) for k, v in (a.get("models") or {}).items()},
        "trigger": {},
        "low_skill_horizons": ["7d", "14d"],
        "models": {"xgb_all": "XGBoost (all features)", "rf_all": "RandomForest(200)",
                   "rule_tpi": "TPI > 0.6 rule", "xgb_rain_only": "XGBoost rain only",
                   "xgb_rain_soil": "XGBoost rain + soil", "climatology": "XGBoost no rain/soil"},
    }
    for h, d in hz.items():
        if not isinstance(d, dict):
            continue
        card["trigger"][h] = {}
        for src in ("gfs", "perfect"):
            blk = d.get(src)
            if not isinstance(blk, dict) or "reason" in blk:
                card["trigger"][h][src] = {"reason": (blk or {}).get("reason", "not run")}
                continue
            card["trigger"][h][src] = {
                name: {"test": _slim(v.get("test")),
                       "by_event": {REGION_EVENT.get(r, r): _slim(m)
                                    for r, m in (v.get("by_region") or {}).items()}}
                for name, v in blk.items()}
    return card
