"""Lead 0-3 d: TPI + forecast vs the ML trigger, scored on the TARGET DAY (conductor, Lucas).

For each target day d and lead k = 0..3, the forecast is issued on d-k: observed IMERG
(POWER IMERG Final) up to d-k-1 + the NOAA GFS 00Z run of d-k for days d-k..d (L28/L28b
alignment, ``scoring.rain_series``). Every score is computed for day d itself:

* ``tpi_critical`` / ``tpi_high``: TPI(d) = (R·S·T)^(1/3) from the 72 h rain ending d vs the
  IMERG Final 3-day climatology of d's day of year, SMAP root-zone wetness, slope;
  alarm at > 0.6 (Crítico) / > 0.4 (Alto+).
* ``ml_v2``: served daily model xgb_rain_soil at day d, VAL-calibrated, VAL-F1 threshold.
* ``rain_p99``: untrained rule — 72 h rain ending d / local 3-day P99; alarm at >= 1.
* ``random``: uniform score; alarm rate = base rate (precision = base, lift = 1).

Rows (TEST events only, held out of training): positives = TEST landslide points with
target day = the event's documented onset (L37; per-report date for WA), issued on onset-k;
negatives = the same points issued on event-free dates (target = date + k, also event-free)
+ points 5-50 km away issued on onset-k (target = onset). GFS rows need an archived, cached, finite forecast for every day d-k..d; events with
no GFS positives drop out. k = 0 is the 00Z run of day d itself (available ~03:30Z, so not
"before" a midnight onset). Soil (SMAP) is held at the issue day for every row (no look-ahead).

    ML_EVENT_ONSETS=... python -m ml.local.lead03   -> ml/models/lead03.json + lead03.csv
"""
from __future__ import annotations

import argparse
import csv
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
from .table import gfs_jobs, gfs_reader, load_rows
from .trainer import _load_table, _rain

log = logging.getLogger(__name__)
LEADS = (0, 1, 2, 3)
N_BOOT = 200
MIN_POS = 8
MODELS = ("tpi_critical", "tpi_high", "ml_v2", "rain_p99", "random")
KEYS = ("n", "n_pos", "base_rate", "threshold", "accuracy", "precision", "recall", "f1",
        "pr_auc", "roc_auc", "tp", "fp", "fn", "tn")


def target_rows(k: int, te: pd.DataFrame, el_all: pd.DataFrame) -> pd.DataFrame:
    """TEST rows for lead ``k`` with ``issue_date``, ``target`` (= issue + k) and ``y``.

    Positives: the run-up row issued on onset - k (SMAP held at that issue day).
    Same-point negatives: issued on their own event-free date (the cached GFS run),
    target = that date + k (also event-free: >= 30 d from any event).
    Elsewhere: points 5-50 km away issued on onset - k, target = onset."""
    on = event_day(te, "onset")
    issue = pd.to_datetime(te.issue_date)
    pos = te[(te.label == 1) & (issue == on - pd.Timedelta(days=k))].copy()
    pos = pos.drop_duplicates(["event_id", "lon", "lat"])
    pos["neg_set"] = "positive"
    neg = te[te.label == 0].copy()
    neg["issue_date"] = pd.to_datetime(neg["date"]).dt.normalize()
    neg = neg.drop_duplicates(["event_id", "lon", "lat", "issue_date"])
    neg["neg_set"] = "same_point"
    el = el_all[pd.to_datetime(el_all.issue_date) == event_day(el_all, "onset") - pd.Timedelta(days=k)]
    el = el.reset_index(drop=True)
    sm = _smap(el, False)
    for c in sm.columns:
        el[c] = sm[c].to_numpy()
    el["neg_set"] = "elsewhere"
    rows = pd.concat([pos, neg, el], ignore_index=True, sort=False)
    rows["issue_date"] = pd.to_datetime(rows.issue_date).dt.normalize()
    rows["target"] = rows.issue_date + pd.Timedelta(days=k)
    rows["y"] = (rows.neg_set == "positive").astype(int)
    rows["label"] = rows["y"]
    return rows


def score(rows: pd.DataFrame, k: int, rain, gfs, ml: dict) -> dict[str, np.ndarray]:
    lon, lat = rows.lon.to_numpy(), rows.lat.to_numpy()
    issue = rows.issue_date.to_numpy()
    r = SC.rain_series(rain, lon, lat, issue, k + 1, source="gfs", gfs_daily=gfs)
    p99d, p99_3 = rain.p99_daily(lon, lat), rain.p99_3day(lon, lat)
    rf = SC.rain_feats_at(r, k, p99d, p99_3)
    doy = pd.to_datetime(rows.target).dt.day_of_year.to_numpy()
    p90, p95, p99 = rain.pctl_3day(lon, lat, doy)
    tpi = SC.tpi_score(rf["rain_72h"].to_numpy(), p90, p95, p99,
                       rows["sm_rootzone_wetness"].to_numpy(), rows["slope"].to_numpy())
    base = rows.reset_index(drop=True)
    feats = pd.concat([base.drop(columns=[c for c in S.RAIN + ["doy_sin", "doy_cos"] if c in base]),
                       rf.reset_index(drop=True), SC.doy_at(issue, k)], axis=1)
    p_ml = ml["cal"](M.predict(ml["model"], feats, ml["cols"]))
    rng = np.random.default_rng(S.SEED + k)
    return {"tpi_critical": tpi, "tpi_high": tpi, "ml_v2": p_ml,
            "rain_p99": rf["rain_72h_rel"].to_numpy(dtype=float), "random": rng.random(len(rows))}


def _m(y, p, thr) -> dict:
    m = SC.metrics(y, p, thr)
    m = {k: m.get(k) for k in KEYS}
    m["lift"] = (m["precision"] / m["base_rate"]) if m.get("base_rate") and m.get("precision") is not None else None
    return m


def _boot(rows, p, thr, seed=S.SEED) -> dict:
    ev = rows.event_id.astype(str).to_numpy()
    y = rows.y.to_numpy()
    evs = np.unique(ev[y == 1])
    idx = {e: np.flatnonzero(ev == e) for e in evs}
    rng = np.random.default_rng(seed)
    acc: dict[str, list] = {k: [] for k in ("precision", "recall", "f1", "pr_auc", "roc_auc", "lift")}
    for _ in range(N_BOOT):
        take = np.concatenate([idx[e] for e in rng.choice(evs, len(evs), replace=True)])
        if len(np.unique(y[take])) < 2:
            continue
        m = _m(y[take], p[take], thr)
        for k, lst in acc.items():
            if m.get(k) is not None:
                lst.append(m[k])
    return {k: [round(float(np.percentile(v, 5)), 4), round(float(np.percentile(v, 95)), 4)] if v else None
            for k, v in acc.items()}


def run() -> dict:
    b = joblib.load(S.DATA_DIR / "models" / "bundle_3b.joblib")
    e = b["entries"]["xgb_rain_soil"]
    d = b["daily"]["xgb_rain_soil"]
    ml = {"model": e["model"], "cols": e["cols"], "cal": d["cal"], "thr": d["thr"]}
    rain = _rain()
    gfs = gfs_reader(gfs_jobs(load_rows("horizon_3b")))
    hz = _load_table("horizon_3b_features")
    te = hz[hz.split == "test"].copy()
    el_all = elsewhere_issue_rows(_load_table("table_3b"))
    out = {"definition": __doc__.split("\n\n")[1].strip(), "onsets": {k: str(v) for k, v in S.EVENT_ONSETS.items()},
           "thresholds": {"tpi_critical": 0.6, "tpi_high": 0.4, "ml_v2": ml["thr"], "rain_p99": 1.0,
                          "random": "1 - base rate"},
           "leads": {}}
    for k in LEADS:
        sub = target_rows(k, te, el_all)
        ok = SC.source_mask(sub, "gfs", gfs, k + 1)
        sub = sub[ok].reset_index(drop=True)
        sc = score(sub, k, rain, gfs, ml)
        y = sub.y.to_numpy()
        base = float(y.mean())
        thr = {"tpi_critical": 0.6, "tpi_high": 0.4, "ml_v2": ml["thr"], "rain_p99": 1.0,
               "random": 1.0 - base}
        ev = sub.event_id.astype(str).to_numpy()
        blk = {"rows": {"n": len(sub), "n_pos": int(y.sum()), "negatives": sub.neg_set.value_counts().to_dict()}}
        for n in MODELS:
            p = np.asarray(sc[n], dtype=float)
            per = {x: _m(y[ev == x], p[ev == x], thr[n]) for x in sorted(set(ev[y == 1]))}
            big = [v for v in per.values() if (v["n_pos"] or 0) >= MIN_POS and v["pr_auc"] is not None]
            ew = {kk: (float(np.mean([v[kk] for v in big if v[kk] is not None])) if big else None)
                  for kk in ("precision", "recall", "f1", "pr_auc", "roc_auc", "lift")}
            ew["events_n"] = len(big)
            blk[n] = {"pooled": _m(y, p, thr[n]), "ci90_event_bootstrap": _boot(sub, p, thr[n]),
                      "event_weighted": ew, "by_event": per}
        out["leads"][str(k)] = blk
        log.info("lead03 k=%d done (%d rows, %d pos)", k, len(sub), int(y.sum()))
    return out


def write_csv(res: dict, path) -> None:
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["lead_days", "model", "scope", "n", "n_pos", "base_rate", "threshold", "accuracy",
                    "precision", "recall", "f1", "pr_auc", "roc_auc", "lift", "pr_auc_ci90", "f1_ci90"])
        for k, blk in res["leads"].items():
            for n in MODELS:
                r = blk[n]
                rows = [("pooled", r["pooled"], r["ci90_event_bootstrap"])]
                rows += [(f"event:{x}", m, {}) for x, m in r["by_event"].items()]
                for scope, m, ci in rows:
                    w.writerow([k, n, scope] + [m.get(c) for c in ("n", "n_pos", "base_rate", "threshold",
                                                                  "accuracy", "precision", "recall", "f1",
                                                                  "pr_auc", "roc_auc", "lift")]
                               + [ci.get("pr_auc"), ci.get("f1")])


def report_md(res: dict) -> str:
    f = lambda v: "—" if v is None else f"{v:.3f}"
    L = ["| lead | model | base | acc | P | R | F1 | PR-AUC [90% CI] | ROC | lift | event-wtd F1 / PR (n) |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for k, blk in res["leads"].items():
        for n in MODELS:
            p, ci, e = blk[n]["pooled"], blk[n]["ci90_event_bootstrap"], blk[n]["event_weighted"]
            L.append(f"| {k} d | {n} | {f(p['base_rate'])} | {f(p['accuracy'])} | {f(p['precision'])} | "
                     f"{f(p['recall'])} | {f(p['f1'])} | {f(p['pr_auc'])} {ci.get('pr_auc')} | {f(p['roc_auc'])} | "
                     f"{f(p['lift'])} | {f(e['f1'])} / {f(e['pr_auc'])} ({e['events_n']}) |")
    evs = sorted({x for blk in res["leads"].values() for x in blk["ml_v2"]["by_event"]})
    L += ["", "Per event, recall / precision at the threshold (lead 0 · 1 · 2 · 3 d):", "",
          "| event | model | " + " | ".join(f"{k} d" for k in res["leads"]) + " |",
          "|---|---|" + "---|" * len(res["leads"])]
    for x in evs:
        for n in MODELS:
            cells = []
            for blk in res["leads"].values():
                m = blk[n]["by_event"].get(x)
                cells.append(f"{f(m['recall'])} / {f(m['precision'])}" if m else "—")
            L.append(f"| {x} | {n} | " + " | ".join(cells) + " |")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", action="store_true")
    a = ap.parse_args(argv)
    path = S.MODEL_DIR / "lead03.json"
    if not a.md:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
        res = run()
        path.write_text(json.dumps(res, indent=1, default=str))
        write_csv(res, S.MODEL_DIR / "lead03.csv")
    print(report_md(json.loads(path.read_text())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


class _EarlyObs:
    """Observed rain from IMERG Early (GPM_3IMERGDE cache); climatology stays IMERG Final
    (POWER) — the live Now setup. Forecast days are overwritten by GFS in rain_series."""

    def __init__(self, final):
        from . import sources_dynamic as D
        self.final = final
        self.early = D.ImergDaily.load()

    def daily(self, lon, lat, dates):
        return self.early.daily(lon, lat, dates)

    def __getattr__(self, name):
        return getattr(self.final, name)


def early_sensitivity(event: str = "rs_2024") -> dict:
    """Lead 0-3 d on one event (positives + elsewhere-same-day rows, where Early is cached):
    observed IMERG Final vs IMERG Early, same rows, same GFS, Final climatology."""
    b = joblib.load(S.DATA_DIR / "models" / "bundle_3b.joblib")
    e, d = b["entries"]["xgb_rain_soil"], b["daily"]["xgb_rain_soil"]
    ml = {"model": e["model"], "cols": e["cols"], "cal": d["cal"], "thr": d["thr"]}
    final = _rain()
    early = _EarlyObs(final)
    gfs = gfs_reader(gfs_jobs(load_rows("horizon_3b")))
    hz = _load_table("horizon_3b_features")
    te = hz[(hz.split == "test") & (hz.event_id == event)].copy()
    el_all = elsewhere_issue_rows(_load_table("table_3b"))
    el_all = el_all[el_all.event_id == event]
    out = {}
    for k in LEADS:
        sub = target_rows(k, te, el_all)
        sub = sub[sub.neg_set != "same_point"].reset_index(drop=True)
        sub = sub[SC.source_mask(sub, "gfs", gfs, k + 1)].reset_index(drop=True)
        y = sub.y.to_numpy()
        thr = {"tpi_critical": 0.6, "tpi_high": 0.4, "ml_v2": ml["thr"], "rain_p99": 1.0}
        row = {"n": len(sub), "n_pos": int(y.sum())}
        for tag, rn in (("final", final), ("early", early)):
            sc = score(sub, k, rn, gfs, ml)
            row[tag] = {n: {kk: _m(y, np.asarray(sc[n], float), thr[n])[kk]
                            for kk in ("recall", "precision", "pr_auc", "roc_auc")}
                        for n in thr}
        out[str(k)] = row
        log.info("early sensitivity k=%d: %s", k, {t: row[t]["ml_v2"] for t in ("final", "early")})
    return out
