"""When x where: does P_trigger(H) x T_ML beat each alone on TEST? (conductor 2026-11-15)

3b knows WHEN (same point, other dates) but not WHERE (5-50 km away, same
day); 3a T_ML knows WHERE. Scores on the TEST issue-day rows:

  trigger   calibrated P_trigger(H)              (3b, xgb_all)
  t_ml      calibrated T_ML                      (3a served model)
  product   P_trigger(H) x T_ML
  gated     P_trigger(H) where T_ML >= its VAL F1 threshold, else 0

Negative sets: ``same_point`` (horizon rows with label 0: pre-window issue
days + other dates at the positive's own point), ``elsewhere`` (each TEST
elsewhere-same-day point on the SAME issue days as its event, label 0),
``both``. Ranking metrics only (PR-AUC, ROC-AUC) — no threshold was tuned for
the combinations. T_ML is trained on S+SE Brazil, so RS 2024 is the in-domain
read; WA 2025 is out of domain and tiny.

    python -m ml.local.combo [--source gfs|perfect]   -> ml/models/combo_when_where.json
"""
from __future__ import annotations

import argparse
import json
import logging

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score

from . import models as M
from . import scoring as SC
from . import settings as S
from .table import LOOKBACK_DAYS, gfs_jobs, gfs_reader, load_rows
from .trainer import _load_table, _rain

log = logging.getLogger(__name__)
SMAP_COLS = ["sm_surface", "sm_rootzone", "sm_profile_wetness", "sm_rootzone_wetness"]


def elsewhere_issue_rows(t3b: pd.DataFrame, lookback: int = LOOKBACK_DAYS) -> pd.DataFrame:
    """TEST elsewhere-same-day points replicated over their event's issue days (label 0)."""
    e = t3b[(t3b.split == "test") & (t3b.neg_type == "elsewhere_same_day")].copy()
    end = pd.to_datetime(e["date"]).dt.normalize()
    for ev, (_, b) in S.LABEL_WINDOWS.items():
        end[(e.event_id == ev).to_numpy()] = pd.Timestamp(b)
    out = []
    for j in range(lookback + 1):
        g = e.copy()
        g["issue_date"] = end - pd.Timedelta(days=j)
        out.append(g)
    rows = pd.concat(out, ignore_index=True).drop(columns=SMAP_COLS, errors="ignore")
    rows["lead_days"] = -2
    for h in S.HORIZONS:
        rows[f"label_{h}"] = 0
    return rows


def _smap(rows: pd.DataFrame, fetch: bool = True) -> pd.DataFrame:
    from . import sources_dynamic as D
    lon, lat = rows.lon.to_numpy(), rows.lat.to_numpy()
    d = rows.issue_date.values.astype("datetime64[D]")
    if fetch:
        D.fetch_smap_points(lon, lat, d)
    return D.SmapPoints.load().sample(lon, lat, d).reset_index(drop=True)


def _auc(y, p) -> dict:
    ok = np.isfinite(p)
    y, p = np.asarray(y)[ok], np.asarray(p)[ok]
    if len(np.unique(y)) < 2:
        return {"n": len(y), "n_pos": int(y.sum()), "pr_auc": None, "roc_auc": None}
    return {"n": len(y), "n_pos": int(y.sum()), "base_rate": float(y.mean()),
            "pr_auc": float(average_precision_score(y, p)), "roc_auc": float(roc_auc_score(y, p))}


def run(source: str = "gfs", fetch: bool = True, trigger: str = "xgb_all") -> dict:
    b3 = joblib.load(S.DATA_DIR / "models" / "bundle_3b.joblib")
    b3a = joblib.load(S.DATA_DIR / "models" / "bundle_3a.joblib")
    from .export import best_val_model
    served = best_val_model(b3a["entries"])
    tml_e = b3a["entries"][served]

    hz = _load_table("horizon_3b_features")
    hz = hz[hz.split == "test"].copy()
    hz["neg_set"] = "same_point"
    t3b = _load_table("table_3b")
    el = elsewhere_issue_rows(t3b)
    el = pd.concat([el.reset_index(drop=True), _smap(el, fetch)], axis=1)
    el["neg_set"] = "elsewhere"
    rows = pd.concat([hz, el], ignore_index=True, sort=False)
    log.info("combo rows: %d same-point/pos + %d elsewhere", len(hz), len(el))

    p_tml = tml_e["cal"](M.predict(tml_e["model"], rows, tml_e["cols"]))
    rain = _rain()
    gfs = gfs_reader(gfs_jobs(load_rows("horizon_3b"))) if source == "gfs" else None
    ents = {trigger: b3["entries"][trigger]}
    res: dict = {"source": source, "trigger_model": trigger, "t_ml_model": served, "t_ml_val_threshold": float(tml_e["thr"]),
                 "n_elsewhere_points": int(el[["lon", "lat"]].drop_duplicates().shape[0]),
                 "horizons": {}}
    region = rows.region.to_numpy()
    for h, H in S.HORIZONS.items():
        raw = SC.horizon_scores(rows, H, ents, rain, source=source, gfs_daily=gfs)[trigger]
        cal = b3["horizon_cal"][h][trigger]["cal"]
        trig = cal(raw)
        scores = {"trigger": trig, "t_ml": p_tml, "product": trig * p_tml,
                  "gated": np.where(p_tml >= tml_e["thr"], trig, 0.0)}
        y = rows[f"label_{h}"].to_numpy()
        is_pos = y == 1
        blk = {}
        for scope, m_scope in (("all", np.ones(len(rows), bool)),
                               ("rs_2024", region == "south_southeast_brazil")):
            for negs in ("same_point", "elsewhere", "both"):
                keep = m_scope & (is_pos | ((~is_pos) & ((rows.neg_set == negs).to_numpy()
                                                         if negs != "both" else True)))
                blk[f"{scope}/{negs}"] = {k: _auc(y[keep], v[keep]) for k, v in scores.items()}
        res["horizons"][h] = blk
        log.info("combo %s %s: RS elsewhere ROC trig %.3f tml %.3f prod %.3f", source, h,
                 blk["rs_2024/elsewhere"]["trigger"]["roc_auc"] or -1,
                 blk["rs_2024/elsewhere"]["t_ml"]["roc_auc"] or -1,
                 blk["rs_2024/elsewhere"]["product"]["roc_auc"] or -1)
    return res


def table_md(res: dict, scope: str = "rs_2024") -> str:
    L = ["| H | negatives | trigger PR / ROC | T_ML PR / ROC | product PR / ROC | gated PR / ROC | n / n_pos |",
         "|---|---|---|---|---|---|---|"]
    for h, blk in res["horizons"].items():
        for negs in ("same_point", "elsewhere", "both"):
            r = blk[f"{scope}/{negs}"]
            f = lambda m: (f"{m['pr_auc']:.3f} / {m['roc_auc']:.3f}" if m["pr_auc"] is not None
                           else "—")
            L.append(f"| {h} | {negs} | {f(r['trigger'])} | {f(r['t_ml'])} | {f(r['product'])} | "
                     f"{f(r['gated'])} | {r['trigger']['n']} / {r['trigger']['n_pos']} |")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="gfs", choices=["gfs", "perfect"])
    ap.add_argument("--no-fetch", action="store_true")
    ap.add_argument("--trigger", default="xgb_all")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    res = run(a.source, fetch=not a.no_fetch, trigger=a.trigger)
    out = S.MODEL_DIR / f"combo_when_where_{a.source}_{a.trigger}.json"
    out.write_text(json.dumps(res, indent=2))
    print(table_md(res, "rs_2024"))
    print()
    print(table_md(res, "all"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
