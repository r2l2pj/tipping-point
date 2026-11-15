"""Metrics by lead time: how early does the trigger see the event? (conductor 2026-11-15)

For lead k = 0..13 days, forecasts are issued on D-k (NOAA GFS 00Z run of
D-k, L28b day labels) and scored on the SHORTEST horizon whose window
[D-k, D-k+H-1] still reaches the event day D::

    k = 0 -> 24h · 1 -> 48h · 2 -> 72h · 3-6 -> 7d   (v2 horizons; 14d dropped)

Event day D: RS 2024 = 2024-05-01 (fixed, never rain-dated; settings.RS2024_DATE);
WA 2025 = each point's reported date. Rows per k:

  positives   every TEST positive point issued on D-k (label 1 by construction)
  same_point  TEST same-point negatives: the positive points on their own
              event-free dates (>= 30 d from any event), scored with H(k) (label 0)
  elsewhere   TEST elsewhere-same-day points (5-50 km away) issued on D-k (label 0)

Thresholds are the VAL-fit per-horizon F1 thresholds (no re-tuning); the TPI
rule uses 0.6. Precision/accuracy depend on the case-control negative ratio,
so they are not real-world rates; recall and ROC-AUC are ratio-free.
No retraining: reads the frozen bundles and cached features/GFS.

    python -m ml.local.by_lead [--no-fetch]   -> metrics_ml.json["by_lead"]
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
from .combo import _smap, elsewhere_issue_rows
from .table import gfs_jobs, gfs_reader, load_rows
from .trainer import _load_table, _rain

log = logging.getLogger(__name__)
LEADS = range(7)   # longest horizon 7 d (v2)
MODELS_GFS = ("xgb_all", "xgb_rain_soil", "rf_all", "rule_tpi", "direct_rain")
MODELS_PERFECT = ("xgb_all", "xgb_rain_soil", "direct_rain")
CLEAN_3A_KM = 5.0  # T_ML-clean: >= 5 km from every 3a TRAIN/VAL positive


def clean_3a_mask(lon, lat, km: float = CLEAN_3A_KM) -> np.ndarray:
    """True where T_ML never saw a training/VAL positive within ``km`` (3a used RS
    2024 points as positives: 59 % of RS 3b TEST positives are 3a TRAIN points)."""
    from . import labels as L
    r3a = pd.read_csv(S.DATA_DIR / "tables" / "rows_3a.csv")
    ref = r3a[(r3a.label == 1) & r3a.split.isin(["train", "val", "buffer"])]
    return L.min_dist_km(np.asarray(lon), np.asarray(lat), ref.lon.to_numpy(), ref.lat.to_numpy()) >= km


def scopes(rows: pd.DataFrame) -> dict[str, np.ndarray]:
    """Per TEST event, plus the T_ML-clean subset of each (positives only filtered;
    negatives are kept whole — elsewhere negatives are all >= 5 km already)."""
    out = {}
    pos = (rows.neg_set == "positive").to_numpy()
    out["pooled"] = np.ones(len(rows), bool)
    out["pooled_clean3a"] = ~pos | rows.clean_3a.to_numpy()
    for ev in sorted(rows.loc[pos, "event_id"].dropna().unique()):
        m = (rows.event_id == ev).to_numpy()
        out[ev] = m
        out[f"{ev}_clean3a"] = m & (~pos | rows.clean_3a.to_numpy())
    return out


def horizon_for_lead(k: int) -> str:
    """Shortest horizon whose window [D-k, D-k+H-1] still contains D."""
    for h, H in sorted(S.HORIZONS.items(), key=lambda kv: kv[1]):
        if H >= k + 1:
            return h
    raise ValueError(f"lead {k} d exceeds the longest horizon")


def event_day(df: pd.DataFrame, anchor: str = "event_day") -> pd.Series:
    """D per row: the event's fixed day (S.EVENT_DAYS, e.g. RS 2024) else the row's date.
    ``anchor="onset"`` (L37): the documented onset (S.EVENT_ONSETS) where one is given."""
    d = pd.to_datetime(df["date"]).dt.normalize()
    days = dict(S.EVENT_DAYS)
    if anchor == "onset":
        days.update(S.EVENT_ONSETS)
    for ev, day in days.items():
        d[(df["event_id"] == ev).to_numpy()] = pd.Timestamp(day)
    return d


def lead_rows(anchor: str = "event_day") -> pd.DataFrame:
    """TEST rows with ``k`` (lead days; -1 for same-point negatives) and ``neg_set``."""
    hz = _load_table("horizon_3b_features")
    hz = hz[hz.split == "test"].copy()
    pos = hz[hz.label == 1].copy()
    pos["k"] = (event_day(pos, anchor) - pos["issue_date"]).dt.days
    pos = pos[pos.k.isin(LEADS)]
    pos["neg_set"] = "positive"
    neg = hz[hz.label == 0].copy()
    neg["k"] = -1
    neg["neg_set"] = "same_point"
    el = elsewhere_issue_rows(_load_table("table_3b"))
    el["k"] = (event_day(el, anchor) - el["issue_date"]).dt.days
    el = el[el.k.isin(LEADS)].reset_index(drop=True)
    el["neg_set"] = "elsewhere"
    rows = pd.concat([pos, neg, el], ignore_index=True, sort=False)
    rows["y"] = (rows.neg_set == "positive").astype(int)
    rows["clean_3a"] = clean_3a_mask(rows.lon, rows.lat)
    return rows


def run(fetch: bool = False, anchor: str = "event_day") -> dict:
    b = joblib.load(S.DATA_DIR / "models" / "bundle_3b.joblib")
    b3a = joblib.load(S.DATA_DIR / "models" / "bundle_3a.joblib")
    from .export import best_val_model
    tml = b3a["entries"][best_val_model(b3a["entries"])]

    rows = lead_rows(anchor)
    el = rows.neg_set == "elsewhere"
    sm = _smap(rows[el].reset_index(drop=True), fetch)
    for c in sm.columns:
        rows.loc[el, c] = sm[c].to_numpy()
    from .combo import t_ml_scores
    rows["t_ml"] = t_ml_scores(rows, tml)
    log.info("by_lead rows: %s", rows.neg_set.value_counts().to_dict())

    rain = _rain()
    gfs = gfs_reader(gfs_jobs(load_rows("horizon_3b")))
    ents = b["entries"]
    out: dict = {
        "definition": ("lead k: forecast issued on D-k (GFS 00Z run of D-k), scored on the "
                       "shortest horizon whose window reaches the event day D; RS 2024 D = "
                       "2024-05-01 (fixed), WA 2025 D = reported date"),
        "anchor": anchor,
        "anchor_days": {ev: str(d) for ev, d in ({**S.EVENT_DAYS, **S.EVENT_ONSETS}
                                                  if anchor == "onset" else S.EVENT_DAYS).items()},
        "horizon_for_lead": {str(k): horizon_for_lead(k) for k in LEADS},
        "negatives": {"same_point": "TEST positive points on event-free dates (>=30 d gap), "
                                    "scored with the horizon of k",
                      "elsewhere": "TEST elsewhere-same-day points (5-50 km away) issued on D-k",
                      "both": "same_point + elsewhere (headline)"},
        "thresholds": "VAL-fit per-horizon F1 thresholds (calibrated P); rule_tpi 0.6",
        "precision_note": "case-control negatives: precision/accuracy are not real-world rates",
        "served_note": "served_product = calibrated P_trigger(xgb_rain_soil) x T_ML (the live "
                       "layer); ranking metrics only, no threshold was fit for it",
        "scopes": {},
    }
    scores: dict[str, dict[str, np.ndarray]] = {}
    for h in sorted({horizon_for_lead(k) for k in LEADS}, key=S.HORIZONS.get):
        ks = [k for k in LEADS if horizon_for_lead(k) == h]
        m = rows.k.isin(ks) | (rows.neg_set == "same_point")
        sub = rows[m]
        H = S.HORIZONS[h]
        ok = SC.source_mask(sub, "gfs")          # L33: GFS only where the issue day has a run
        g_ok = SC.horizon_scores(sub[ok], H, {n: ents[n] for n in MODELS_GFS}, rain,
                                 source="gfs", gfs_daily=gfs)
        g = {}
        for n, p in g_ok.items():
            full = np.full(len(sub), np.nan)
            full[ok] = p
            g[n] = full
        pf = SC.horizon_scores(sub, H, {n: ents[n] for n in MODELS_PERFECT}, rain, source="perfect")
        cal = b["horizon_cal"][h]
        sc = {n: (cal[n]["cal"](p), p, cal[n]["thr"]) for n, p in g.items()}
        for n in MODELS_PERFECT:
            sc[f"{n}_perfect"] = (cal[n]["cal"](pf[n]), pf[n], cal[n]["thr"])
        sc["served_product"] = (sc["xgb_rain_soil"][0] * sub["t_ml"].to_numpy(), None, None)
        scores[h] = {"idx": np.flatnonzero(m.to_numpy()), **sc}
        log.info("by_lead %s scored (%d rows)", h, len(sub))

    sc_masks = scopes(rows)
    out["clean_3a_note"] = ("<scope>_clean3a keeps only positives >= 5 km from every 3a "
                            "TRAIN/VAL/buffer positive: the only scope where served_product "
                            "(x T_ML) is leak-free")
    out["n_pos_clean3a"] = {ev: int(((rows.neg_set == "positive") & rows.clean_3a
                                     & (rows.event_id == ev) & (rows.k == 0)).sum())
                            for ev in rows.loc[rows.neg_set == "positive", "event_id"].unique()}
    for scope, smask in sc_masks.items():
        blk = {}
        for k in LEADS:
            h = horizon_for_lead(k)
            s = scores[h]
            sub = rows.iloc[s["idx"]]
            in_scope = smask[s["idx"]]
            is_k = (sub.k == k).to_numpy() | (sub.neg_set == "same_point").to_numpy()
            ns = sub.neg_set.to_numpy()
            y = sub.y.to_numpy()
            kb = {"horizon": h}
            for negs in ("both", "same_point", "elsewhere"):
                keep = in_scope & is_k & ((ns == "positive") | (ns == negs if negs != "both"
                                                                 else ns != "positive"))
                mb = {}
                for name, (pc, praw, thr) in ((n, v) for n, v in s.items() if n != "idx"):
                    if thr is None:
                        mb[name] = _rank(y[keep], pc[keep])
                    else:
                        mb[name] = _with_raw(SC.metrics(y[keep], pc[keep], thr), y[keep], praw[keep])
                kb[negs] = mb
            blk[str(k)] = kb
        out["scopes"][scope] = blk
    return out


def _with_raw(m: dict, y, p_raw) -> dict:
    from .trainer import _raw
    return _raw(m, y, p_raw)


def _rank(y, p) -> dict:
    from sklearn.metrics import average_precision_score, roc_auc_score
    ok = np.isfinite(p)
    y, p = np.asarray(y)[ok], np.asarray(p)[ok]
    two = len(np.unique(y)) == 2
    return {"n": int(len(y)), "n_pos": int(y.sum()), "n_neg": int(len(y) - y.sum()),
            "pr_auc": float(average_precision_score(y, p)) if two else None,
            "roc_auc": float(roc_auc_score(y, p)) if two else None}


def _f(v, nd=3):
    return "—" if v is None else f"{v:.{nd}f}"


def table_md(res: dict, scope: str = "rs_2024", negs: str = "both") -> str:
    blk = res["scopes"][scope]
    L = ["| k (days before D) | H | n_pos / n_neg | model | acc | prec | rec | F1 | PR-AUC | ROC |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for k, kb in blk.items():
        r = kb[negs]
        first = True
        for name in ("xgb_rain_soil", "direct_rain", "xgb_all", "rf_all", "rule_tpi",
                     "xgb_all_perfect", "direct_rain_perfect", "served_product"):
            m = r[name]
            head = (f"| {k} | {kb['horizon']} | {m['n_pos']} / {m['n_neg']} " if first
                    else "| | | ")
            first = False
            L.append(head + f"| {name} | {_f(m.get('accuracy'))} | {_f(m.get('precision'))} | "
                     f"{_f(m.get('recall'))} | {_f(m.get('f1'))} | {_f(m['pr_auc'])} | "
                     f"{_f(m['roc_auc'])} |")
    return "\n".join(L)


def compact_md(res: dict, scope: str = "rs_2024", negs: str = "both") -> str:
    """One row per k: recall + ROC for every model (the pitch view)."""
    blk = res["scopes"][scope]
    names = ("xgb_rain_soil", "direct_rain", "xgb_all", "rf_all", "rule_tpi", "xgb_all_perfect")
    L = ["| k | H | n_pos / n_neg | " + " | ".join(f"{n} rec / F1 / PR / ROC" for n in names)
         + " | served_product PR / ROC |",
         "|" + "---|" * (len(names) + 4)]
    for k, kb in blk.items():
        r = kb[negs]
        cells = [f"{_f(r[n]['recall'], 2)} / {_f(r[n]['f1'], 2)} / {_f(r[n]['pr_auc'], 2)} / "
                 f"{_f(r[n]['roc_auc'], 2)}" for n in names]
        sp = r["served_product"]
        L.append(f"| {k} | {kb['horizon']} | {r['xgb_all']['n_pos']} / {r['xgb_all']['n_neg']} | "
                 + " | ".join(cells) + f" | {_f(sp['pr_auc'], 2)} / {_f(sp['roc_auc'], 2)} |")
    return "\n".join(L)


def recall_line(res: dict, model: str = "xgb_rain_soil", scope: str = "rs_2024") -> str:
    blk = res["scopes"][scope]
    f = lambda v: "—" if v is None else f"{v * 100:.0f} %"
    parts = [f"{k} d {f(blk[k]['both'][model].get('recall'))}" for k in blk]
    return f"Recall vs lead days ({model}, {scope}, GFS): " + " · ".join(parts)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true", help="fetch missing SMAP for elsewhere rows")
    ap.add_argument("--merge", action="store_true", help="merge by_lead_v2.json into metrics_ml.json")
    ap.add_argument("--anchor", default="event_day", choices=["event_day", "onset"],
                    help="L37: k to the fixed event day, or to the documented onset (ML_EVENT_ONSETS)")
    a = ap.parse_args(argv)
    suffix = "" if a.anchor == "event_day" else "_onset"
    if a.merge:
        path = S.MODEL_DIR / "metrics_ml.json"
        met = json.loads(path.read_text())
        met["by_lead" + suffix] = json.loads((S.MODEL_DIR / f"by_lead_v2{suffix}.json").read_text())
        path.write_text(json.dumps(met, indent=2, default=str))
        return 0
    if a.anchor == "onset" and not S.EVENT_ONSETS:
        ap.error("--anchor onset needs ML_EVENT_ONSETS=<event_id>=<YYYY-MM-DD>,...")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    res = run(fetch=a.fetch, anchor=a.anchor)
    # own file (merged into metrics_ml.json["by_lead"] by --merge), so by_lead can run
    # next to `evaluate`, which rewrites metrics_ml.json
    (S.MODEL_DIR / f"by_lead_v2{suffix}.json").write_text(json.dumps(res, indent=2, default=str))
    for scope in ("pooled", "pooled_clean3a", *sorted(k for k in res["scopes"] if k not in ("pooled", "pooled_clean3a"))):
        print(f"\n== {scope}")
        print(compact_md(res, scope))
    print()
    print(recall_line(res, scope="pooled"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
