"""train (TRAIN fit + VAL calibration/threshold) and evaluate (TEST only).

train_3a / train_3b write ``DATA_DIR/models/bundle_3a.joblib`` / ``bundle_3b.joblib``
(every fitted model incl. ablations and baselines, with its VAL calibrator and
threshold) and the small serving files in ``ml/models/``:
``t_ml_xgb.json`` (+ .meta.json) and ``p_trigger_xgb.json`` (+ .meta.json).

evaluate_3a / evaluate_3b load the bundles and score TEST for the first time.
Output: ``ml/models/metrics_ml.json`` (everything) — export copies the
contract keys into data/metrics.json.
"""
from __future__ import annotations

import json
import logging

import joblib
import numpy as np
import pandas as pd

from . import models as M
from . import scoring as SC
from . import settings as S

log = logging.getLogger(__name__)


def _bundle_path(task: str):
    return S.path("models", f"bundle_{task}.joblib")


def _load_table(name: str) -> pd.DataFrame:
    p = S.DATA_DIR / "tables" / f"{name}.csv"
    if not p.exists():
        raise FileNotFoundError(f"{p} missing — run `python -m ml.local table` first")
    df = pd.read_csv(p)
    for c in ("date", "issue_date"):
        if c in df:
            df[c] = pd.to_datetime(df[c], errors="coerce")
    return df


def _split(df):
    return df[df.split == "train"], df[df.split == "val"], df[df.split == "test"]


def _present(cols, df):
    return [c for c in cols if c in df.columns and df[c].notna().any()]


def _calibrated(entry: dict, p_val, y_val) -> dict:
    cal = M.fit_calibrator(p_val, y_val)
    entry["cal"] = cal
    entry["thr"] = M.best_f1_threshold(cal(p_val), y_val)
    entry["val"] = SC.metrics(y_val, cal(p_val), entry["thr"])
    return entry


def _raw(m: dict, y, p_raw) -> dict:
    """Add ranking metrics on the RAW (pre-calibration) scores. Isotonic calibration
    is a step function: its ties lower PR-/ROC-AUC. Calibrated values describe the
    served product; raw values describe the model's ranking ability."""
    from sklearn.metrics import average_precision_score, roc_auc_score
    y = np.asarray(y, dtype=int)
    p_raw = np.asarray(p_raw, dtype=float)
    ok = np.isfinite(p_raw)
    if ok.any() and len(np.unique(y[ok])) == 2:
        m["pr_auc_raw"] = float(average_precision_score(y[ok], p_raw[ok]))
        m["roc_auc_raw"] = float(roc_auc_score(y[ok], p_raw[ok]))
    else:
        m["pr_auc_raw"] = m["roc_auc_raw"] = None
    return m


def _pos_weight(train: pd.DataFrame, use: bool) -> float | None:
    if not use:
        return None
    n1 = max(1, int(train.label.sum()))
    return float((len(train) - n1) / n1)


# ============================================================== 3a susceptibility
def train_3a(*, balanced: bool = False) -> dict:
    tab = _load_table("table_3a")
    tr, va, _ = _split(tab)
    log.info("3a: train %d (%d pos) / val %d (%d pos)", len(tr), tr.label.sum(), len(va), va.label.sum())
    entries: dict[str, dict] = {}
    groups = {g: _present(c, tab) for g, c in S.FEATURE_GROUPS_3A.items()}
    for abl, gs in S.ABLATIONS_3A.items():
        cols = [c for g in gs for c in groups[g]]
        if not cols:
            continue
        m = M.fit_xgb(tr, va, cols, pos_weight=_pos_weight(tr, balanced))
        entries[f"xgb_{abl}"] = _calibrated({"kind": "xgb", "model": m, "cols": cols, "groups": gs},
                                            M.predict(m, va, cols), va.label.to_numpy())
    cols_all = entries["xgb_all"]["cols"]
    rf = M.fit_rf(tr, cols_all, balanced=balanced)
    entries["rf_all"] = _calibrated({"kind": "rf", "model": rf, "cols": cols_all},
                                    M.predict(rf, va, cols_all), va.label.to_numpy())
    entries["rule_slope"] = _calibrated({"kind": "rule", "model": None, "cols": ["slope"]},
                                        SC.slope_rule(va.slope), va.label.to_numpy())
    joblib.dump({"task": "3a", "entries": entries, "groups": groups}, _bundle_path("3a"))
    save_t_ml_model(entries, len(tr), len(va))
    return {k: v["val"] for k, v in entries.items()}


def save_t_ml_model(entries: dict, n_train: int, n_val: int) -> str:
    """Serving T_ML = the XGB ablation with the best VAL PR-AUC (never TEST)."""
    xgbs = {k: e for k, e in entries.items() if e["kind"] == "xgb"}
    name = max(xgbs, key=lambda k: (xgbs[k]["val"].get("pr_auc") or -1))
    xgb = xgbs[name]
    M.save_xgb(xgb["model"], S.MODEL_DIR / "t_ml_xgb.json", {
        "task": "3a susceptibility T_ML", "model": name, "selected_by": "best VAL PR-AUC",
        "features": xgb["cols"],
        "calibrator": xgb["cal"].to_dict(), "threshold_val_f1": xgb["thr"],
        "n_train": n_train, "n_val": n_val,
        "neg_ratio": S.NEG_RATIO_3A, "seed": S.SEED,
    })
    return name


def _score(entry: dict, df: pd.DataFrame) -> np.ndarray:
    if entry["kind"] == "rule":
        return SC.slope_rule(df.slope)
    return M.predict(entry["model"], df, entry["cols"])


def group_importance(entry: dict, test: pd.DataFrame, groups: dict, *, repeats: int = 5,
                     seed: int = S.SEED) -> dict:
    """Gain share per group (XGB) + permutation PR-AUC drop on TEST per group."""
    from sklearn.metrics import average_precision_score
    out: dict = {}
    model, cols = entry["model"], entry["cols"]
    if entry["kind"] == "xgb":
        gain = model.get_booster().get_score(importance_type="total_gain")
        tot = sum(gain.values()) or 1.0
        out["gain_share"] = {g: float(sum(gain.get(c, 0.0) for c in cs) / tot)
                             for g, cs in groups.items()}
        out["gain_share_feature"] = {c: float(gain.get(c, 0.0) / tot) for c in cols}
    y = test.label.to_numpy()
    if len(np.unique(y)) < 2:
        return out
    base = average_precision_score(y, M.predict(model, test, cols))
    rng = np.random.default_rng(seed)
    drops = {}
    for g, cs in groups.items():
        cs = [c for c in cs if c in cols]
        if not cs:
            continue
        d = []
        for _ in range(repeats):
            shuf = test.copy()
            idx = rng.permutation(len(shuf))
            for c in cs:
                shuf[c] = shuf[c].to_numpy()[idx]
            d.append(base - average_precision_score(y, M.predict(model, shuf, cols)))
        drops[g] = float(np.mean(d))
    out["permutation_pr_auc_drop_test"] = drops
    out["test_pr_auc_base"] = float(base)
    return out


def evaluate_3a() -> dict:
    b = joblib.load(_bundle_path("3a"))
    tab = _load_table("table_3a")
    _, _, te = _split(tab)
    res = {"split_rows": tab.split.value_counts().to_dict(), "models": {}}
    for name, e in b["entries"].items():
        raw = _score(e, te)
        p = e["cal"](raw)
        res["models"][name] = {"test": _raw(SC.metrics(te.label, p, e["thr"]), te.label, raw),
                               "val": e["val"]}
    res["importance"] = group_importance(b["entries"]["xgb_all"], te, b["groups"])
    res["importance_rf"] = group_importance(b["entries"]["rf_all"], te, b["groups"])
    return res


# ================================================================== 3b trigger
CLIM_COLS = S.TERRAIN + S.LANDCOVER + S.SOIL_TEXTURE + ["doy_sin", "doy_cos"]


def _rain():
    from . import sources_dynamic as D
    return D.PowerRain.load()


def _gfs():
    """GFS reader over the same per-event boxes `fetch-points` downloaded."""
    from .table import gfs_jobs, gfs_reader, load_rows
    return gfs_reader(gfs_jobs(load_rows("horizon_3b")))


def train_3b(*, balanced: bool = False) -> dict:
    tab = _load_table("table_3b")
    tr, va, _ = _split(tab)
    log.info("3b: train %d (%d pos) / val %d (%d pos)", len(tr), tr.label.sum(), len(va), va.label.sum())
    groups = {g: _present(c, tab) for g, c in S.FEATURE_GROUPS_3B.items()}
    entries: dict[str, dict] = {}
    for abl, gs in S.ABLATIONS_3B.items():
        cols = [c for g in gs for c in groups[g]]
        m = M.fit_xgb(tr, va, cols, pos_weight=_pos_weight(tr, balanced))
        entries[f"xgb_{abl}"] = {"kind": "xgb", "model": m, "cols": cols, "groups": gs}
    cols_all = entries["xgb_all"]["cols"]
    entries["rf_all"] = {"kind": "rf", "model": M.fit_rf(tr, cols_all, balanced=balanced), "cols": cols_all}
    clim_cols = _present(CLIM_COLS, tab)
    entries["climatology"] = {"kind": "xgb", "model": M.fit_xgb(tr, va, clim_cols), "cols": clim_cols}
    entries["nowcast_xgb_all"] = {"kind": "nowcast", "model": entries["xgb_all"]["model"], "cols": cols_all}
    entries["rule_tpi"] = {"kind": "tpi", "model": None, "cols": []}
    entries["direct_rain"] = train_direct(tab, _rain())

    # daily (target-day) calibration on VAL
    yv = va.label.to_numpy()
    daily = {}
    rain = _rain()
    for name, e in entries.items():
        if e["kind"] in ("xgb", "rf"):
            daily[name] = _calibrated(dict(e), M.predict(e["model"], va, e["cols"]), yv)
    # horizon calibration on VAL issue rows (perfect forecast = observed IMERG)
    hz = _load_table("horizon_3b_features")
    hz_cal: dict = {}
    for hname, H in S.HORIZONS.items():
        hv = hz[hz.split == "val"]
        if hv.empty:
            continue
        yv_h = hv[f"label_{hname}"].to_numpy()
        sc = SC.horizon_scores(hv, H, entries, rain, source="perfect")
        hz_cal[hname] = {}
        for name, p in sc.items():
            if name == "rule_tpi":
                hz_cal[hname][name] = {"cal": M.Calibrator(), "thr": S.TPI_RULE_THRESHOLD,
                                       "val": SC.metrics(yv_h, p, S.TPI_RULE_THRESHOLD)}
            else:
                hz_cal[hname][name] = _calibrated({}, p, yv_h)
    joblib.dump({"task": "3b", "entries": entries, "daily": daily, "horizon_cal": hz_cal,
                 "groups": groups}, _bundle_path("3b"))
    save_p_trigger(entries, hz_cal, S.SERVE_TRIGGER, len(tr), len(va))
    return {"daily_val": {k: v["val"] for k, v in daily.items()},
            "horizon_val": {h: {k: v["val"] for k, v in d.items()} for h, d in hz_cal.items()}}


def train_direct(tab: pd.DataFrame, rain) -> dict:
    """One XGBoost per horizon on issue-day rows (v2): TRAIN issue rows built from the
    TRAIN daily rows exactly like VAL/TEST (``horizon_rows``), VAL issue rows for
    early stopping; observed IMERG stands in for the forecast (as for calibration)."""
    from .table import horizon_rows
    tr = horizon_rows(tab[tab.split == "train"], splits=("train",))
    hz = _load_table("horizon_3b_features")
    va = hz[hz.split == "val"]
    cols = _present(S.SOIL_TEXTURE, tab) + S.DIRECT_RAIN
    models = {}
    for h, H in S.HORIZONS.items():
        parts = []
        for df in (tr, va):
            df = df.reset_index(drop=True)
            lon, lat = df.lon.to_numpy(), df.lat.to_numpy()
            r = SC.rain_series(rain, lon, lat, df.issue_date.to_numpy(), H, source="perfect")
            f = SC.direct_feats(r, H, rain.p99_daily(lon, lat), rain.p99_3day(lon, lat))
            d = pd.concat([df.drop(columns=[c for c in S.DIRECT_RAIN if c in df]), f], axis=1)
            d["label"] = d[f"label_{h}"].to_numpy()
            parts.append(d)
        models[h] = M.fit_xgb(parts[0], parts[1], cols)
        log.info("direct %s: train %d rows (%d pos), val %d (%d pos), best_iter %s", h,
                 len(parts[0]), int(parts[0].label.sum()), len(parts[1]), int(parts[1].label.sum()),
                 getattr(models[h], "best_iteration", None))
    return {"kind": "direct", "models": models, "cols": cols}


def by_neg_type(df: pd.DataFrame, p: np.ndarray, thr: float) -> dict:
    """All positives vs one negative type at a time: 'when' skill
    (same_point_other_date) vs 'where' skill (elsewhere_same_day)."""
    y = df.label.to_numpy()
    nt = df.neg_type.fillna("").astype(str).to_numpy()
    out = {}
    for t in sorted(set(nt[y == 0])):
        m = (y == 1) | (nt == t)
        out[t] = SC.metrics(y[m], np.asarray(p)[m], thr)
    return out


def save_p_trigger(entries: dict, hz_cal: dict, name: str, n_train: int, n_val: int,
                   extra: dict | None = None):
    """Serving file for lead-live: one daily XGB + its per-horizon VAL calibrators."""
    xgb = entries[name]
    M.save_xgb(xgb["model"], S.MODEL_DIR / "p_trigger_xgb.json", {
        "task": "3b daily trigger; P_H = 1 - prod(1 - p_k) over the horizon days",
        "model": name, "features": xgb["cols"], "horizons": S.HORIZONS,
        "horizon_calibrators": {h: v[name]["cal"].to_dict() for h, v in hz_cal.items() if name in v},
        "horizon_thresholds_val_f1": {h: v[name]["thr"] for h, v in hz_cal.items() if name in v},
        "rain_source_train": "NASA POWER IMERG_PRECTOT daily (UTC)",
        "soil_source": "SMAP L4 SPL4SMGP 008 T10:30Z, held at issue day",
        "output_keys": [f"p_trigger_{h}" for h in S.HORIZONS],
        "n_train": n_train, "n_val": n_val, "seed": S.SEED, **(extra or {}),
    })


def evaluate_3b(*, gfs: bool = True) -> dict:
    b = joblib.load(_bundle_path("3b"))
    tab = _load_table("table_3b")
    _, _, te = _split(tab)
    res: dict = {"split_rows": tab.split.value_counts().to_dict(), "daily": {}, "horizons": {}}
    for name, e in b["daily"].items():
        raw = M.predict(e["model"], te, e["cols"])
        p = e["cal"](raw)
        res["daily"][name] = {"test": _raw(SC.metrics(te.label, p, e["thr"]), te.label, raw),
                              "val": e["val"],
                              "test_by_neg_type": by_neg_type(te, p, e["thr"])}
    res["importance"] = group_importance(b["entries"]["xgb_all"], te, b["groups"])
    rain = _rain()
    hz = _load_table("horizon_3b_features")
    sources = ["perfect"] + (["gfs"] if gfs else [])
    for hname, H in S.HORIZONS.items():
        ht = hz[hz.split == "test"]
        cal = b["horizon_cal"].get(hname, {})
        if ht.empty or not cal:
            res["horizons"][hname] = {"reason": "no TEST or VAL issue rows"}
            continue
        res["horizons"][hname] = {}
        ht_all = ht
        for src in sources:
            ht = ht_all[SC.source_mask(ht_all, src)].reset_index(drop=True)
            try:
                sc = SC.horizon_scores(ht, H, b["entries"], rain, source=src,
                                       gfs_daily=_gfs() if src == "gfs" else None)
            except Exception as exc:  # noqa: BLE001 - GFS archive gaps must not kill the run
                res["horizons"][hname][src] = {"reason": f"{type(exc).__name__}: {exc}"}
                continue
            block = {}
            for name, p in sc.items():
                c = cal.get(name)
                if c is None:
                    continue
                pc = c["cal"](p)
                y = ht[f"label_{hname}"].to_numpy()
                reg = ht.region.to_numpy()
                evs = ht.event_id.astype(str).to_numpy()
                block[name] = {"test": _raw(SC.metrics(y, pc, c["thr"]), y, p),
                               "by_region": {r: _raw(SC.metrics(y[reg == r], pc[reg == r], c["thr"]),
                                                     y[reg == r], p[reg == r])
                                             for r in sorted(set(reg))},
                               "by_event": {ev: _raw(SC.metrics(y[evs == ev], pc[evs == ev], c["thr"]),
                                                     y[evs == ev], p[evs == ev])
                                            for ev in S.TRIGGER_TEST_EVENTS if (evs == ev).any()},
                               # pooled TEST without one event at a time (generalisation spread)
                               "loeo": {ev: {k: v for k, v in SC.metrics(
                                   y[evs != ev], pc[evs != ev], c["thr"]).items()
                                   if k in ("n", "n_pos", "precision", "recall", "f1", "pr_auc", "roc_auc")}
                                   for ev in S.TRIGGER_TEST_EVENTS if (evs == ev).any()}}
            res.setdefault("horizon_rows", {}).setdefault(hname, {})[src] = {
                "n": len(ht), "n_dropped_no_gfs_run": int(len(ht_all) - len(ht))}
            res["horizons"][hname][src] = block
        ht = ht_all
    return res


def write_metrics(res: dict) -> dict:
    path = S.MODEL_DIR / "metrics_ml.json"
    old = json.loads(path.read_text()) if path.exists() else {}
    old.update(res)
    path.write_text(json.dumps(old, indent=2, default=str))
    return old
