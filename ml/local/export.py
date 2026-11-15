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


def best_val_model(entries: dict) -> str:
    """3a model for the T_ML layer: highest VAL PR-AUC (never chosen on TEST)."""
    cands = {k: (e.get("val") or {}).get("pr_auc") for k, e in entries.items() if e.get("kind") != "rule"}
    cands = {k: v for k, v in cands.items() if v is not None}
    return max(cands, key=cands.get) if cands else "xgb_all"


def export_t_ml(*, bbox=S.REGION_SERVE, model: str | None = None, write_90m: bool = False,
                out: Path | None = None) -> Path:
    from . import sources_dynamic as D
    from . import sources_static as ST

    b = joblib.load(S.path("models", "bundle_3a.joblib"))
    model = model or best_val_model(b["entries"])
    e = b["entries"][model]
    log.info("t_ml model: %s (best VAL PR-AUC)", model)
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
    write_t_ml_png(km, tr_km, bbox, model=model,
                   val_pr_auc=(e.get("val") or {}).get("pr_auc"))
    if tmp90 is not None:
        tmp90.close()
        with rasterio.open(out.with_name("t_ml_90m.tmp.tif")) as src:
            prof = src.profile | {"driver": "COG", "compress": "deflate"}
            with rasterio.open(out.with_name("t_ml_90m.tif"), "w", **prof) as dst:
                dst.write(src.read())
        out.with_name("t_ml_90m.tmp.tif").unlink()
    return out


T_ML_BREAKS = (0.2, 0.4, 0.6)   # same class breaks + palette as TPI (Baixo..Crítico)


def write_t_ml_png(km: np.ndarray, transform, bbox, model: str = "xgb_all",
                   val_pr_auc: float | None = None) -> Path:
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
        "model": model, "model_selected_by": "best VAL PR-AUC", "val_pr_auc": val_pr_auc,
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
    served = best_val_model(b3a["entries"]) if b3a else "xgb_all"
    served_test = (am.get(served) or {}).get("test") or {}
    fi = {}
    if b3a:   # importance of the SERVED T_ML model (gain share TRAIN fit + permutation on TEST)
        from .trainer import _load_table, group_importance
        te = _load_table("table_3a")
        te = te[te.split == "test"]
        groups = {g: c for g, c in b3a["groups"].items()
                  if g in (b3a["entries"][served].get("groups") or b3a["groups"])}
        imp = group_importance(b3a["entries"][served], te, groups)
        fi = {r["feature"]: r["gain_share"] for r in importance_rows(imp, groups)
              if r["gain_share"] is not None}
        a["importance_served"] = imp
    rf, xs = auc("rf_all"), auc(served)
    blob = {
        # doc contract keys — all on the SAME 3a TEST set (n, n_pos below)
        "xgb_auc": xs, "rf_auc": rf, "rule_auc": auc("rule_slope"),
        "xgb_model": served, "xgb_model_selected_by": "best VAL PR-AUC",
        "xgb_pr_auc": served_test.get("pr_auc"),
        "rf_pr_auc": ((am.get("rf_all") or {}).get("test") or {}).get("pr_auc"),
        "xgb_all_auc": auc("xgb_all"),
        "t_ml_test_n": served_test.get("n"), "t_ml_test_n_pos": served_test.get("n_pos"),
        "t_ml_test_base_rate": served_test.get("base_rate"),
        "t_ml_note": (f"rf_auc = RandomForest(200) on all 3a features; xgb_auc = served T_ML ({served}). "
                      + ("RandomForest beats the served XGBoost on ROC. " if rf and xs and rf > xs
                         else "The served XGBoost beats RandomForest on ROC. ")
                      + ("With all features RandomForest beats XGBoost (xgb_all_auc)."
                         if rf and auc("xgb_all") and rf > auc("xgb_all") else "")),
        "feature_importance": fi,
        "feature_importance_model": served,
        "landslide_ml": {
            "split": "TEST only (50 km blocks for 3a; held-out events for 3b: RS 2024 + WA 2025, never seen)",
            "detail": "ml/ml_card.json (every model / horizon / forecast source / event)",
            "t_ml": {k: _slim((v or {}).get("test")) for k, v in am.items()},
            "trigger_gfs_headline": {
                h: {k: _slim(((d.get("gfs") or {}).get(k) or {}).get("test"))
                    for k in ("xgb_all", "rf_all", "rule_tpi")}
                for h, d in (bb.get("horizons") or {}).items() if isinstance(d, dict)},
        },
    }
    reasons = {k: "3a model not evaluated yet (run `python -m ml.local evaluate`)"
               for k in ("rf_auc", "xgb_auc", "rule_auc") if blob[k] is None}
    merge_metrics(case, blob, reasons=reasons, collections={
        "ml_labels": "NASA COOLR Global Landslide Catalog + RS 2024 inventory (UFRGS/Cemaden)",
        "ml_rain": "GPM IMERG V07 (NASA POWER IMERG_PRECTOT points; GES DISC OPeNDAP grids)",
        "ml_soil": "SMAP L4 SPL4SMGP 008 + SPL4SMLM 008", "ml_terrain": "NASADEM_HGT 001",
        "ml_landcover": "MCD12Q1 061", "ml_ndvi": "VNP13C2 002"}, path=DATA_OUT / "metrics.json")
    # merge_metrics escapes non-ASCII; restore the file's own format so only ML keys diff
    mp = DATA_OUT / "metrics.json"
    mj = json.loads(mp.read_text())
    wa = (mj.get("cases") or {}).get("wa_2025")
    if isinstance(wa, dict):   # G9 (lead-analysis): replace the stale "no local WA table" reason
        why = ("WA 2025 is a 3b TRIGGER TEST event only (8 dated WGS reports); its scores are "
               "in landslide_ml / ml_card trigger.by_event.wa_2025. 3a T_ML AUCs are regional "
               "(S+SE Brazil), so there is no WA rf_auc / rule_auc / feature importance.")
        nr = wa.setdefault("_null_reasons", {})
        for k in ("rf_auc", "rule_auc", "feature_importance"):
            if wa.get(k) is None:
                nr[k] = why
    mp.write_text(json.dumps(mj, indent=2, ensure_ascii=False) + "\n")
    mldir = DATA_OUT / "ml"
    mldir.mkdir(parents=True, exist_ok=True)
    if b3a and a.get("importance_served"):
        g_served = {g: c for g, c in b3a["groups"].items()
                    if g in (b3a["entries"][served].get("groups") or b3a["groups"])}
        (mldir / "feature_importance.json").write_text(json.dumps(
            {"model": f"{served} (3a T_ML, served)", "group_labels": GROUP_LABELS,
             "rows": importance_rows(a["importance_served"], g_served),
             "rf_permutation": a.get("importance_rf", {}).get("permutation_pr_auc_drop_test")}, indent=2))
    (mldir / "model_metrics.json").write_text(json.dumps(met, indent=2, default=str))
    # keep the source of truth in sync (importance_served is computed here)
    (S.MODEL_DIR / "metrics_ml.json").write_text(json.dumps(met, indent=2, default=str))
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
             "pr_auc_raw", "roc_auc_raw",
             "pr_auc", "roc_auc", "tp", "fp", "fn", "tn")
REGION_EVENT = {"south_southeast_brazil": "rs_2024", "north_america": "wa_2025"}


def _slim(m: dict | None) -> dict | None:
    return {k: m.get(k) for k in CARD_KEYS} if m else None


def t_ml_ranking_note(a: dict) -> str:
    """Plain statement: which 3a model wins on calibrated vs raw TEST PR-AUC."""
    ms = {k: (v or {}).get("test") or {} for k, v in (a.get("models") or {}).items()}
    def best(key):
        c = {k: m.get(key) for k, m in ms.items() if m.get(key) is not None and k != "rule_slope"}
        return (max(c, key=c.get), c) if c else (None, {})
    bc, cc = best("pr_auc")
    br, rr = best("pr_auc_raw")
    if not bc:
        return ""
    s = (f"3a TEST PR-AUC, calibrated (served scores): best = {bc} {cc[bc]:.3f}"
         + "".join(f"; {k} {v:.3f}" for k, v in sorted(cc.items(), key=lambda kv: -kv[1]) if k != bc))
    if br:
        s += (f". Raw (pre-calibration) scores: best = {br} {rr[br]:.3f}"
              + "".join(f"; {k} {v:.3f}" for k, v in sorted(rr.items(), key=lambda kv: -kv[1]) if k != br))
        s += (". Isotonic calibration (VAL-fit step function) creates ties that lower ranking metrics"
              + ("; on raw scores a different model ranks best than on calibrated scores." if br != bc else "."))
    return s


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
        "t_ml_ranking_note": t_ml_ranking_note(a),
        "trigger": {},
        "low_skill_horizons": ["7d"],
        "models": {"xgb_all": "XGBoost (all features)", "rf_all": "RandomForest(200)",
                   "rule_tpi": "TPI > 0.6 rule", "xgb_rain_only": "XGBoost rain only",
                   "xgb_terrain_landcover": "XGBoost terrain + land cover (served T_ML)",
                   "xgb_terrain_only": "XGBoost terrain only", "rule_slope": "Slope rule 15-30°",
                   "nowcast_xgb_all": "XGBoost nowcast (observed rain only, no forecast)",
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


SERVE_BANDS = (S.TERRAIN + S.LANDCOVER + S.SOIL_TEXTURE + ["p99_daily", "p99_3day"])


def p99_power_lattice(bbox, step: float = 0.5):
    """(p99_daily(lat, lon), p99_3day(lat, lon)) interpolators from POWER on a lattice."""
    from scipy.interpolate import RegularGridInterpolator

    from . import sources_dynamic as D

    w, s, e, n = bbox
    lats = np.arange(s + step / 2, n, step)
    lons = np.arange(w + step / 2, e, step)
    LA, LO = np.meshgrid(lats, lons, indexing="ij")
    D.fetch_power_points(LO.ravel(), LA.ravel())
    pr = D.PowerRain.load()
    grids = [f(LO.ravel(), LA.ravel()).reshape(LA.shape) for f in (pr.p99_daily, pr.p99_3day)]
    out = []
    for g in grids:
        f = RegularGridInterpolator((lats, lons), g, bounds_error=False, fill_value=None)

        def at(lat, lon, f=f):
            pts = np.column_stack([np.clip(np.ravel(lat), lats[0], lats[-1]),
                                   np.clip(np.ravel(lon), lons[0], lons[-1])])
            return f(pts).reshape(np.shape(lat)).astype(np.float32)
        out.append(at)
    return out[0], out[1]


def export_serving_static(out: Path | None = None, bbox=S.REGION_SERVE,
                          res: float = S.RES_EXPORT_1KM_DEG) -> Path:
    """ml_static_now.tif — every non-dynamic input of serve.TriggerModel on the Now grid.

    Bands (descriptions = names): TERRAIN (avg from 90 m), landcover (nearest),
    ndvi, clay/sand/porosity (SPL4SMLM, nearest), p99_daily/p99_3day — the SAME
    definition and source as training (``PowerRain``: NASA POWER IMERG_PRECTOT full
    daily series, 99th pct), sampled on a 0.5° lattice and bilinearly interpolated
    (p99 is a smooth climatology; a 0.1° IMERG cube over 2001-2023 = ~8.4k
    OPeNDAP calls, ~7 h). EPSG:4326, float32, NaN.
    lead-live builds the static/texture/p99 inputs of ``predict`` from these bands.
    """
    from . import sources_dynamic as D
    from . import sources_static as ST

    p99_daily_at, p99_3day_at = p99_power_lattice(bbox)
    w, s, e, n = bbox
    W, H = round((e - w) / res), round((n - s) / res)
    arr = np.full((len(SERVE_BANDS), H, W), np.nan, dtype=np.float32)
    step = 100  # 1° windows
    for r0 in range(0, H, step):
        for c0 in range(0, W, step):
            h, wd = min(step, H - r0), min(step, W - c0)
            tr = from_origin(w + c0 * res, n - r0 * res, res, res)
            st = ST.static_on_grid(tr, (h, wd), bands=S.TERRAIN + S.LANDCOVER)
            cl = D.climate_on_grid(tr, (h, wd))
            lon = w + (c0 + np.arange(wd) + 0.5) * res
            lat = n - (r0 + np.arange(h) + 0.5) * res
            LON, LAT = np.meshgrid(lon, lat)
            p99 = {"p99_daily": p99_daily_at(LAT, LON), "p99_3day": p99_3day_at(LAT, LON)}
            for i, b in enumerate(SERVE_BANDS):
                src = st.get(b) if b in st else cl.get(b) if b in cl else p99.get(b)
                if src is not None:
                    arr[i, r0:r0 + h, c0:c0 + wd] = src
    out = out or S.DATA_DIR / "serve" / "ml_static_now.tif"
    out.parent.mkdir(parents=True, exist_ok=True)
    prof = {"driver": "COG", "width": W, "height": H, "count": len(SERVE_BANDS),
            "dtype": "float32", "crs": "EPSG:4326", "transform": from_origin(w, n, res, res),
            "nodata": np.nan, "compress": "deflate"}
    with rasterio.open(out, "w", **prof) as dst:
        dst.write(arr)
        for i, b in enumerate(SERVE_BANDS, start=1):
            dst.set_band_description(i, b)
    fin = {b: float(np.isfinite(arr[i]).mean()) for i, b in enumerate(SERVE_BANDS)}
    log.info("ml_static_now: %s %dx%d finite %s", out, W, H, fin)
    return out


V1_STATIC_SHA256 = "721b92280fd6785581c78842056f8cd91a8619af7b26c6c367e16b3459be3612"


def export_serving_v2(v1_static: Path, t_ml_tif: Path | None = None,
                      out_dir: Path | None = None) -> dict:
    """v2 serving assets (conductor: NEW names, never overwrite static-v1 v1 assets).

    ml_static_now_v2.tif = the v1 static stack (sha-checked) with its ``t_ml`` band
    replaced by the L32 GLC-only T_ML (same 0.01° Now grid); p_trigger_xgb_v2.json /
    .meta.json = the retrained served trigger. Returns {name: sha256}.
    """
    import hashlib
    import shutil

    def sha(p: Path) -> str:
        return hashlib.sha256(Path(p).read_bytes()).hexdigest()

    if sha(v1_static) != V1_STATIC_SHA256:
        raise ValueError(f"{v1_static} is not the published v1 ml_static_now.tif")
    t_ml_tif = t_ml_tif or DATA_OUT / "cog" / "t_ml.tif"
    out_dir = out_dir or S.DATA_DIR / "serve" / "v2"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "ml_static_now_v2.tif"
    with rasterio.open(v1_static) as src, rasterio.open(t_ml_tif) as tm:
        if (src.width, src.height, src.transform) != (tm.width, tm.height, tm.transform):
            raise ValueError("t_ml grid differs from the v1 static stack")
        prof = src.profile
        arr = src.read()
        desc = list(src.descriptions)
        arr[desc.index("t_ml")] = tm.read(1).astype(arr.dtype)
    prof.update(driver="COG", compress="deflate")
    with rasterio.open(out, "w", **prof) as dst:
        dst.write(arr)
        for i, b in enumerate(desc, start=1):
            dst.set_band_description(i, b)
    files = {"ml_static_now_v2.tif": out}
    for name in ("p_trigger_xgb.json", "p_trigger_xgb.meta.json"):
        dst = out_dir / name.replace("p_trigger_xgb", "p_trigger_xgb_v2")
        shutil.copy(S.MODEL_DIR / name, dst)
        files[dst.name] = dst
    shas = {k: sha(v) for k, v in files.items()}
    (out_dir / "SHA256SUMS").write_text("".join(f"{v}  {k}\n" for k, v in shas.items()))
    log.info("serving v2: %s", shas)
    return shas


# --------------------------------------------------------------------------- v2 card
BUILTIN_EVENT_NAMES = {"rs_2024": "Rio Grande do Sul, Brazil (May 2024)",
                       "wa_2025": "Western Washington, US (Dec 2025)"}


def ml_card_v2(met: dict) -> dict:
    """v1 ``ml_card`` shape + v2 keys. ``by_event`` = per held-out TEST event (not region).

    New keys: version, t_ml_excludes_rs (L32), gfs_rows_with_run_only (L33), test_events,
    generalise (per-event spread + LOEO range), by_lead (daily k table, when present).
    """
    from . import generalise as G
    from .events import load_registry

    card = ml_card(met)
    hz = (met.get("3b") or {}).get("horizons") or {}
    for h, d in hz.items():
        for src in ("gfs", "perfect"):
            blk = (d or {}).get(src)
            if not isinstance(blk, dict) or "reason" in blk:
                continue
            for name, v in blk.items():
                if isinstance(v, dict) and name in card["trigger"].get(h, {}).get(src, {}):
                    card["trigger"][h][src][name]["by_event"] = {
                        ev: _slim(m) for ev, m in (v.get("by_event") or {}).items()}
    try:
        reg = load_registry(S.TEST_EVENTS_FILE)
    except Exception:  # noqa: BLE001 - names are cosmetic
        reg = {}
    events: dict[str, dict] = {}
    for h in hz:
        for src in ("perfect", "gfs"):
            blk = (hz.get(h) or {}).get(src) or {}
            ref = blk.get("xgb_rain_soil") or next((v for v in blk.values() if isinstance(v, dict)), {})
            for ev, m in ((ref or {}).get("by_event") or {}).items():
                e = events.setdefault(ev, {"id": ev, "name": (reg[ev].name if ev in reg else
                                                             BUILTIN_EVENT_NAMES.get(ev, ev)),
                                           "n_pos_24h": None, "sources": []})
                if src not in e["sources"]:
                    e["sources"].append(src)
                if h == "24h" and src == "perfect":
                    e["n_pos_24h"] = m.get("n_pos")
    card.update({
        "version": "v2",
        "t_ml_excludes_rs": True,
        "gfs_rows_with_run_only": True,
        "test_events": sorted(events.values(), key=lambda e: e["id"]),
        "generalise": {src: {h: {m: {k: r[k] for k in ("pooled", "events_n", "event_median",
                                                         "event_min", "event_max", "worst_event",
                                                         "loeo_min", "loeo_max")}
                                 for m, r in ms.items()}
                             for h, ms in G.summary(met, src, "pr_auc").items()}
                       for src in ("gfs", "perfect")},
        "low_skill_horizons": ["7d"],
    })
    card["split"]["3a"] = ("v2 (L32): TRAIN/VAL = GLC landslides in ~50 km blocks, S+SE Brazil, "
                           "no RS 2024 points; TEST = held-out Brazilian event slides vs random land")
    card["split"]["3b"] = ("TRAIN/VAL = dated GLC/COOLR landslides worldwide, split by event; "
                           "TEST = held-out events (see test_events), never seen in training; "
                           "GFS scored only on issue days with an archived run")
    if met.get("by_lead"):
        card["by_lead"] = _slim_by_lead(met["by_lead"])
    return card


def _slim_by_lead(bl: dict, scopes=("pooled", "pooled_clean3a")) -> dict:
    """App-size by_lead: notes + the pooled scopes only, metric leaves cut to CARD_KEYS."""
    def walk(v):
        if isinstance(v, dict):
            if "n_pos" in v:
                return {k: v[k] for k in CARD_KEYS if k in v}
            return {k: walk(x) for k, x in v.items()}
        return v
    out = {k: v for k, v in bl.items() if k != "scopes"}
    sc = bl.get("scopes") or {}
    out["scopes"] = {k: walk(sc[k]) for k in scopes if k in sc}
    return out


def export_card_v2() -> dict:
    """data/ml/ml_card_v2.json + data/ml/model_metrics_v2.json (v1 files untouched)."""
    met = json.loads((S.MODEL_DIR / "metrics_ml.json").read_text())
    mldir = DATA_OUT / "ml"
    mldir.mkdir(parents=True, exist_ok=True)
    card = ml_card_v2(met)
    (mldir / "ml_card_v2.json").write_text(json.dumps(card, indent=2, default=str))
    (mldir / "model_metrics_v2.json").write_text(json.dumps(met, indent=2, default=str))
    log.info("ml_card_v2: %d horizons, %d TEST events", len(card["trigger"]), len(card["test_events"]))
    return card


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    if sys.argv[1:] == ["card-v2"]:
        export_card_v2()
