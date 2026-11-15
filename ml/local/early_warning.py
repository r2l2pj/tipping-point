"""Early warning (pre-onset) variant — conductor 2026-11-15, Lucas: "warn BEFORE it starts".

Question: using only forecasts issued strictly BEFORE the documented onset of each
held-out event (k >= 1 day), does the trigger separate the event's landslide points
from (a) the same points on same-season days of other years / other dates,
(b) the same points earlier in the run-up (pre-event days whose window does not yet
reach the onset) and (c) other places on the same issue days?

* Onset per event: ``ML_EVENT_ONSETS`` (L37, first documented damage) via
  ``by_lead.event_day(..., "onset")``; per-point dated events use the point's date.
* Positives: positive-point issue rows with ``label_<h> = 1`` and issue day < onset.
  Issue days >= onset ("in-event": rain already falling, damage started) are DROPPED.
  So horizons are 48h / 72h / 7d: a 24h window issued before the onset day cannot
  contain it.
* Negatives: ``same_season`` = same-point other-date rows within +-45 days of the
  onset's day of year; ``pre_event`` = positive-point rows whose window ends before
  the onset (label 0); ``elsewhere`` = elsewhere-same-day points (5-50 km) issued
  before the onset.
* Retrained model ``ew_direct``: the v2 direct per-horizon XGBoost, refit with the
  in-event issue rows removed from TRAIN and VAL (the model cannot learn "it is
  already pouring"). Calibration + VAL-F1 thresholds for EVERY scored model are
  refit on the same pre-onset VAL rows.
* Baselines (no training): ``fc_max72_rel`` = max 3-day forecast rain / local P99
  3-day, ``fc_max24_rel`` = max daily forecast rain / local P99 daily; TPI > 0.6.
* Metrics: pooled, event-weighted (mean over events with >= MIN_POS positives),
  per event, per negative type. Source: NOAA GFS 00Z (rows with an archived run)
  and perfect forecast (observed IMERG) as the upper bound.

    ML_EVENT_ONSETS=... python -m ml.local.early_warning   -> ml/models/early_warning_v2.json
"""
from __future__ import annotations

import argparse
import json
import logging

import joblib
import numpy as np
import pandas as pd

from . import models as M
from . import scoring as SC
from . import settings as S
from .by_lead import event_day
from .combo import _smap, elsewhere_issue_rows
from .table import gfs_jobs, gfs_reader, horizon_rows, load_rows
from .trainer import _calibrated, _load_table, _present, _rain

log = logging.getLogger(__name__)
EW_HORIZONS = ("48h", "72h", "7d")
SEASON_DAYS = 45
MIN_POS = 20
BASELINES = ("fc_max72_rel", "fc_max24_rel")
KEYS = ("n", "n_pos", "base_rate", "threshold", "accuracy", "precision", "recall", "f1",
        "pr_auc", "roc_auc", "tp", "fp", "fn", "tn")


def in_event(df: pd.DataFrame) -> np.ndarray:
    """Positive-point issue rows issued on/after the onset (the event has started)."""
    pos_pt = (df["label"] == 1).to_numpy()
    issue = pd.to_datetime(df["issue_date"]).dt.normalize()
    return pos_pt & (issue >= event_day(df, "onset")).to_numpy()


def _doy_gap(a: pd.Series, b: pd.Series) -> np.ndarray:
    d = np.abs(pd.to_datetime(a).dt.day_of_year.to_numpy() - pd.to_datetime(b).dt.day_of_year.to_numpy())
    return np.minimum(d, 365 - d)


def ew_rows(hz: pd.DataFrame, h: str, elsewhere: pd.DataFrame | None = None) -> pd.DataFrame:
    """Pre-onset evaluation rows for horizon ``h`` with ``y`` and ``neg_set``."""
    hz = hz[~in_event(hz)].copy()
    lab = hz[f"label_{h}"].to_numpy()
    pos_pt = (hz["label"] == 1).to_numpy()
    hz["neg_set"] = np.where(lab == 1, "positive", np.where(pos_pt, "pre_event", "same_point"))
    sp = hz["neg_set"] == "same_point"
    # same-point negatives: keep same-season dates only (onset doy of the point's event)
    onset = event_day(hz, "onset")
    keep = ~sp.to_numpy() | (_doy_gap(hz["issue_date"], onset) <= SEASON_DAYS)
    hz = hz[keep].copy()
    hz.loc[hz.neg_set == "same_point", "neg_set"] = "same_season"
    parts = [hz]
    if elsewhere is not None and len(elsewhere):
        el = elsewhere.copy()
        el = el[(pd.to_datetime(el["issue_date"]) < event_day(el, "onset")).to_numpy()]
        el["neg_set"] = "elsewhere"
        parts.append(el)
    out = pd.concat(parts, ignore_index=True, sort=False)
    out["y"] = (out["neg_set"] == "positive").astype(int)
    return out


def train_ew_direct(tab: pd.DataFrame, rain) -> dict:
    """v2 direct per-horizon XGBoost, refit without in-event issue rows (TRAIN + VAL)."""
    tr = horizon_rows(tab[tab.split == "train"], splits=("train",))
    va = _load_table("horizon_3b_features")
    va = va[va.split == "val"]
    tr, va = tr[~in_event(tr)], va[~in_event(va)]
    cols = _present(S.SOIL_TEXTURE, tab) + S.DIRECT_RAIN
    models, info = {}, {}
    for h in EW_HORIZONS:
        H = S.HORIZONS[h]
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
        info[h] = {"train_rows": len(parts[0]), "train_pos": int(parts[0].label.sum()),
                   "val_rows": len(parts[1]), "val_pos": int(parts[1].label.sum()),
                   "best_iteration": getattr(models[h], "best_iteration", None)}
        log.info("ew_direct %s: %s", h, info[h])
    return {"kind": "direct", "models": models, "cols": cols, "info": info}


def _scores(rows: pd.DataFrame, H: int, entries: dict, rain, source: str, gfs=None) -> dict:
    """Raw scores per entry + the two raw-feature baselines."""
    rows = rows.reset_index(drop=True)
    out = SC.horizon_scores(rows, H, entries, rain, source=source, gfs_daily=gfs)
    lon, lat = rows.lon.to_numpy(), rows.lat.to_numpy()
    r = SC.rain_series(rain, lon, lat, rows.issue_date.to_numpy(), H, source=source, gfs_daily=gfs)
    f = SC.direct_feats(r, H, rain.p99_daily(lon, lat), rain.p99_3day(lon, lat))
    for b in BASELINES:
        v = f[b].to_numpy(dtype=float)
        out[b] = np.where(np.isfinite(v), v, np.nan)
    return out


def _m(y, p, thr) -> dict:
    m = SC.metrics(y, p, thr)
    return {k: m.get(k) for k in KEYS}


def _block(rows: pd.DataFrame, cal: dict, raw: dict) -> dict:
    y = rows["y"].to_numpy()
    ev = rows["event_id"].astype(str).to_numpy()
    ns = rows["neg_set"].to_numpy()
    res = {}
    for name, p in raw.items():
        c = cal[name]
        pc = c["cal"](p)
        pooled = _m(y, pc, c["thr"])
        per = {e: _m(y[ev == e], pc[ev == e], c["thr"]) for e in sorted(set(ev[y == 1]))}
        big = [v for v in per.values() if (v["n_pos"] or 0) >= MIN_POS and v["pr_auc"] is not None]
        ew = {k: (float(np.mean([v[k] for v in big])) if big else None)
              for k in ("accuracy", "precision", "recall", "f1", "pr_auc", "roc_auc", "base_rate")}
        ew["events_n"] = len(big)
        by_neg = {t: _m(y[(y == 1) | (ns == t)], pc[(y == 1) | (ns == t)], c["thr"])
                  for t in ("same_season", "pre_event", "elsewhere") if (ns == t).any()}
        res[name] = {"pooled": pooled, "event_weighted": ew, "by_event": per, "by_negatives": by_neg}
    return res


def run() -> dict:
    if not S.EVENT_ONSETS:
        raise SystemExit("set ML_EVENT_ONSETS (L37 onsets) first")
    b = joblib.load(S.DATA_DIR / "models" / "bundle_3b.joblib")
    tab = _load_table("table_3b")
    rain = _rain()
    ew = train_ew_direct(tab, rain)
    ents = {"ew_direct": ew, "direct_rain": b["entries"]["direct_rain"],
            "xgb_rain_soil": b["entries"]["xgb_rain_soil"], "rule_tpi": b["entries"]["rule_tpi"]}

    hz = _load_table("horizon_3b_features")
    val, test = hz[hz.split == "val"], hz[hz.split == "test"]
    el = elsewhere_issue_rows(tab)
    sm = _smap(el.reset_index(drop=True), False)
    el = el.reset_index(drop=True)
    for c in sm.columns:
        el[c] = sm[c].to_numpy()
    gfs = gfs_reader(gfs_jobs(load_rows("horizon_3b")))

    out = {"definition": __doc__.split("\n\n")[1].strip(),
           "onsets": {k: str(v) for k, v in S.EVENT_ONSETS.items()},
           "season_days": SEASON_DAYS, "min_pos_event_weighted": MIN_POS,
           "ew_direct_training": ew["info"], "horizons": {}}
    for h in EW_HORIZONS:
        H = S.HORIZONS[h]
        vr = ew_rows(val, h).reset_index(drop=True)
        vraw = _scores(vr, H, ents, rain, "perfect")
        cal = {}
        for name, p in vraw.items():
            if name == "rule_tpi":
                cal[name] = {"cal": M.Calibrator(), "thr": S.TPI_RULE_THRESHOLD,
                             "val": SC.metrics(vr.y, p, S.TPI_RULE_THRESHOLD)}
            else:
                ok = np.isfinite(p)
                cal[name] = _calibrated({}, p[ok], vr.y.to_numpy()[ok])
        hres = {"val": {n: {k: c["val"].get(k) for k in KEYS} for n, c in cal.items()},
                "val_rows": {"n": len(vr), "n_pos": int(vr.y.sum()),
                             "negatives": vr.neg_set.value_counts().to_dict()}}
        tr_ = ew_rows(test, h, el).reset_index(drop=True)
        hres["test_rows"] = {"n": len(tr_), "n_pos": int(tr_.y.sum()),
                             "negatives": tr_.neg_set.value_counts().to_dict(),
                             "k_days_pos": (event_day(tr_[tr_.y == 1], "onset")
                                            - pd.to_datetime(tr_.loc[tr_.y == 1, "issue_date"])
                                            ).dt.days.value_counts().sort_index().to_dict()}
        ok = SC.source_mask(tr_, "gfs", gfs, H)
        g = tr_[ok].reset_index(drop=True)
        hres["gfs"] = _block(g, cal, _scores(g, H, ents, rain, "gfs", gfs))
        hres["perfect"] = _block(tr_, cal, _scores(tr_, H, ents, rain, "perfect"))
        out["horizons"][h] = hres
        log.info("early warning %s done: test %s", h, hres["test_rows"])
    return out


def report_md(res: dict, source: str = "gfs") -> str:
    f = lambda v: "—" if v is None else f"{v:.3f}"
    L = []
    for h, hr in res["horizons"].items():
        t = hr["test_rows"]
        L += [(f"\n#### {h} — TEST {source}, issue days before onset (k days: {t['k_days_pos']}); "
               f"pos {t['n_pos']}, negatives {t['negatives']}"), "",
              "| model | VAL PR | pooled acc / P / R / F1 | pooled PR / ROC | event-weighted F1 / PR / ROC (n ev) |",
              "|---|---|---|---|---|"]
        for n, r in hr[source].items():
            p, e = r["pooled"], r["event_weighted"]
            L.append(f"| {n} | {f(hr['val'][n]['pr_auc'])} | {f(p['accuracy'])} / {f(p['precision'])} / "
                     f"{f(p['recall'])} / {f(p['f1'])} | {f(p['pr_auc'])} / {f(p['roc_auc'])} | "
                     f"{f(e['f1'])} / {f(e['pr_auc'])} / {f(e['roc_auc'])} ({e['events_n']}) |")
        evs = sorted({e for r in hr[source].values() for e in r["by_event"]})
        L += ["", "| model · PR-AUC / recall | " + " | ".join(evs) + " |", "|---|" + "---|" * len(evs)]
        for n, r in hr[source].items():
            L.append(f"| {n} | " + " | ".join(
                f"{f(r['by_event'].get(e, {}).get('pr_auc'))} / {f(r['by_event'].get(e, {}).get('recall'))}"
                for e in evs) + " |")
        L += ["", "| model · PR / ROC vs | same_season | pre_event | elsewhere |", "|---|---|---|---|"]
        for n, r in hr[source].items():
            bn = r["by_negatives"]
            L.append(f"| {n} | " + " | ".join(
                f"{f(bn.get(t, {}).get('pr_auc'))} / {f(bn.get(t, {}).get('roc_auc'))}"
                for t in ("same_season", "pre_event", "elsewhere")) + " |")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", action="store_true", help="print the report from the saved JSON")
    a = ap.parse_args(argv)
    path = S.MODEL_DIR / "early_warning_v2.json"
    if not a.md:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
        path.write_text(json.dumps(run(), indent=1, default=str))
    res = json.loads(path.read_text())
    print(report_md(res, "gfs"))
    print("\n\n### perfect forecast (upper bound)\n")
    print(report_md(res, "perfect"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
