"""Combine the trained 3b trigger models and the TPI rule on TEST — no retraining.

Question (conductor 2026-11-15): does an AND / OR / majority-vote / weighted
blend of the calibrated horizon scores beat each member alone?

Members: ``xgb_all`` (monotone XGBoost), ``rf_all`` (RandomForest 200) and
``rule_tpi`` (TPI > 0.6). Reference single rows also include the served
``xgb_rain_soil``. Every member votes and blends at its own VAL-F1 threshold on
its VAL calibrator (``bundle_3b.joblib: horizon_cal``) — the exact scores
``evaluate_3b`` reports, so the single rows are asserted to reproduce
``ml/models/metrics_ml.json`` before any combination is trusted.

Combinations, per horizon (TEST = RS 2024 + WA 2025, GFS 00Z forecast):

  and       all members fire                      (score = vote count)
  or        at least one member fires             (score = vote count)
  majority  at least 2 of 3 fire — trio only      (score = vote count)
  blend     weighted mean of the calibrated scores (rule_tpi counts as its
            0..1 TPI value). Weights (simplex grid, step ``WEIGHT_STEP``) and
            the decision threshold both maximise F1 on VAL only, scored with
            observed IMERG standing in for the forecast — the same
            perfect-forecast proxy the training-time horizon calibration used.
            GFS never touches VAL (the archive only covers TEST events).

    python -m ml.local.combine                  -> ml/models/combined_metrics.json
    python -m ml.local.combine --md report.md   # also write the markdown report
"""
from __future__ import annotations

import argparse
import itertools
import json
import logging
from pathlib import Path

import joblib
import numpy as np

from . import models as M
from . import scoring as SC
from . import settings as S

log = logging.getLogger(__name__)

MEMBERS = ("xgb_all", "rf_all", "rule_tpi")
REFERENCE = ("xgb_all", "rf_all", "xgb_rain_soil", "rule_tpi", "direct_rain")  # singles rows
COMBOS: dict[str, tuple[str, ...]] = {
    "xgb+rf": ("xgb_all", "rf_all"),
    "xgb+tpi": ("xgb_all", "rule_tpi"),
    "rf+tpi": ("rf_all", "rule_tpi"),
    "xgb+rf+tpi": ("xgb_all", "rf_all", "rule_tpi"),
}
WEIGHT_STEP = 0.05          # blend simplex grid step (VAL search)
VAL_SOURCE = "perfect"      # VAL has no GFS archive; the training-time proxy
ASSERT_KEYS = ("n", "n_pos", "n_neg", "base_rate", "threshold", "precision",
               "recall", "f1", "accuracy", "tn", "fp", "fn", "tp",
               "pr_auc", "roc_auc", "pr_auc_raw", "roc_auc_raw", "n_dropped_nan")
ASSERT_ATOL = 1e-6


# ------------------------------------------------------------------ blend fit
def weight_grid(k: int, step: float = WEIGHT_STEP) -> list[tuple[float, ...]]:
    """All k-member weight vectors on the simplex, grid step ``step``."""
    n = round(1.0 / step)
    return [tuple(c / n for c in cuts)
            for cuts in itertools.product(range(n + 1), repeat=k) if sum(cuts) == n]


def fit_blend(cal_scores: dict[str, np.ndarray], y, members: tuple[str, ...],
              step: float = WEIGHT_STEP) -> dict:
    """Weights + threshold maximising F1 on (VAL) ``y``. VAL only — never TEST.

    F1 ties break toward the better VAL PR-AUC, then the wider class margin."""
    from sklearn.metrics import average_precision_score

    X = np.column_stack([np.asarray(cal_scores[m], dtype=float) for m in members])
    y = np.asarray(y, dtype=int)
    best: tuple[float, float, float, tuple, float] | None = None
    for w in weight_grid(len(members), step):
        s = X @ np.asarray(w)
        thr = M.best_f1_threshold(s, y)
        f1 = SC.metrics(y, s, thr).get("f1") or -1.0
        if len(np.unique(y)) == 2 and np.isfinite(s).all():
            ap = average_precision_score(y, s)
            margin = float(s[y == 1].mean() - s[y == 0].mean())
        else:
            ap = margin = -1.0
        key = (f1, ap, margin)
        if best is None or key > (best[0], best[1], best[2]):
            best = (f1, ap, margin, w, thr)
    f1, ap, margin, w, thr = best
    return {"weights": dict(zip(members, w, strict=True)),
            "threshold": float(thr), "val_f1": float(f1), "val_pr_auc": float(ap),
            "grid_step": step}


# ---------------------------------------------------------------------- votes
def vote_count(cal_scores: dict[str, np.ndarray], thresholds: dict[str, float],
               members: tuple[str, ...]) -> np.ndarray:
    """Votes per row; NaN where any member score is NaN (metrics drops them)."""
    votes = np.zeros(len(next(iter(cal_scores.values()))))
    finite = np.ones(len(votes), bool)
    for m in members:
        p = np.asarray(cal_scores[m], dtype=float)
        finite &= np.isfinite(p)
        votes += np.nan_to_num(p) >= thresholds[m]
    return np.where(finite, votes, np.nan)


# -------------------------------------------------------------------- assert
def assert_matches(ref: dict, horizon: str, source: str, name: str, got: dict) -> None:
    """The recomputed single-model TEST row must equal metrics_ml.json."""
    want = ref["3b"]["horizons"][horizon][source][name]["test"]
    bad = [(k, want.get(k), got.get(k)) for k in ASSERT_KEYS
           if want.get(k) != got.get(k)
           and not (want.get(k) is not None and got.get(k) is not None
                    and np.isclose(float(got[k]), float(want[k]),
                                   atol=ASSERT_ATOL, rtol=1e-9))]
    if bad:
        raise AssertionError(
            f"{name} {horizon}/{source}: recomputed TEST metrics diverge from "
            f"metrics_ml.json: {bad}")


# ------------------------------------------------------------------------ run
def run(source: str = "gfs", step: float = WEIGHT_STEP) -> dict:
    from .trainer import _gfs, _load_table, _rain, _raw

    b = joblib.load(S.DATA_DIR / "models" / "bundle_3b.joblib")
    hz = _load_table("horizon_3b_features")
    ht = hz[hz.split == "test"]
    ht = ht[SC.source_mask(ht, source)].reset_index(drop=True)   # L33: GFS rows need a run
    hv = hz[hz.split == "val"].reset_index(drop=True)
    rain = _rain()
    gfs = _gfs() if source == "gfs" else None
    ref_path = S.MODEL_DIR / "metrics_ml.json"
    ref = json.loads(ref_path.read_text())
    names = sorted({m for c in COMBOS.values() for m in c} | set(REFERENCE))
    entries = {m: b["entries"][m] for m in names}
    res: dict = {"task": "3b trigger model combinations (no retraining)",
                 "source_test": source, "source_val": VAL_SOURCE,
                 "members": {k: list(v) for k, v in COMBOS.items()},
                 "weight_grid_step": step,
                 "singles_asserted_against": str(ref_path),
                 "horizons": {}}
    for h, H in S.HORIZONS.items():
        cal = b["horizon_cal"][h]
        sc_t = SC.horizon_scores(ht, H, entries, rain, source=source, gfs_daily=gfs)
        sc_v = SC.horizon_scores(hv, H, entries, rain, source=VAL_SOURCE)
        y_t = ht[f"label_{h}"].to_numpy()
        y_v = hv[f"label_{h}"].to_numpy()
        thr = {m: cal[m]["thr"] for m in names}
        cal_t = {m: cal[m]["cal"](sc_t[m]) for m in names}
        cal_v = {m: cal[m]["cal"](sc_v[m]) for m in names}
        blk: dict = {"singles": {}, "combos": {}}
        for m in REFERENCE:
            met = _raw(SC.metrics(y_t, cal_t[m], thr[m]), y_t, sc_t[m])
            assert_matches(ref, h, source, m, met)
            blk["singles"][m] = met
        for cname, members in COMBOS.items():
            votes = vote_count(cal_t, thr, members)
            cb = {"members": list(members),
                  "member_thresholds": {m: float(thr[m]) for m in members},
                  "and": SC.metrics(y_t, votes, float(len(members)))
                  | {"votes_needed": len(members)},
                  "or": SC.metrics(y_t, votes, 1.0) | {"votes_needed": 1}}
            if len(members) == 3:
                cb["majority"] = SC.metrics(y_t, votes, 2.0) | {"votes_needed": 2}
            bl = fit_blend(cal_v, y_v, members, step)
            s_blend = sum(bl["weights"][m] * np.asarray(cal_t[m], dtype=float)
                          for m in members)
            cb["blend"] = (SC.metrics(y_t, s_blend, bl["threshold"])
                           | {"weights": bl["weights"], "val_f1": bl["val_f1"],
                              "val_pr_auc": bl["val_pr_auc"]})
            blk["combos"][cname] = cb
        res["horizons"][h] = blk
        log.info("combine %s %s done (blend f1_val: %s)", source, h,
                 {c: round(cb["blend"]["val_f1"], 3) for c, cb in blk["combos"].items()})
    return res


# -------------------------------------------------------------------- report
def _f(v, nd=3) -> str:
    return "—" if v is None else f"{v:.{nd}f}"


def _row(name: str, rule: str, m: dict, extra: str = "") -> str:
    return (f"| {name} | {rule} | {m['n']} | {m['n_pos']} | {_f(m['base_rate'])} | "
            f"{_f(m['precision'])} | {_f(m['recall'])} | {_f(m['f1'])} | "
            f"{_f(m['accuracy'])} | {_f(m['pr_auc'])} | {_f(m['roc_auc'])} | {extra} |")


def horizon_md(res: dict, h: str) -> str:
    blk = res["horizons"][h]
    L = [f"### 3b P(landslide within {h}) — TEST, NOAA GFS 00Z",
         "",
         "| model | rule | n | n_pos | base | P | R | F1 | acc | PR-AUC | ROC | detail |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for m, met in blk["singles"].items():
        L.append(_row(m, "single", met, f"thr {met['threshold']:.3f}"))
    for cname, cb in blk["combos"].items():
        for rule in ("and", "or", "majority", "blend"):
            if rule not in cb:
                continue
            met = cb[rule]
            extra = (f"w={met['weights']} thr {met['threshold']:.3f} (val f1 {met['val_f1']:.3f})"
                     if rule == "blend" else f">= {met['votes_needed']} vote(s)")
            L.append(_row(cname, rule, met, extra))
    return "\n".join(L)


def report_md(res: dict) -> str:
    L = ["# 3b trigger model combinations — TEST (RS 2024 + WA 2025), NOAA GFS 00Z",
         "",
         "No retraining: members vote and blend at their own VAL-F1 thresholds on",
         "VAL-calibrated scores. Blend weights + threshold were chosen on VAL only",
         f"({VAL_SOURCE}-forecast proxy, grid step {res['weight_grid_step']}). Single",
         "rows reproduce `ml/models/metrics_ml.json` (asserted in `combine.py`).",
         "PR-AUC/ROC on vote rows use the vote count as score. `rule_tpi` blends",
         "as its 0..1 TPI value (identity calibrator). n = TEST issue-day rows.",
         ""]
    for h in res["horizons"]:
        L += [horizon_md(res, h), ""]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--source", default="gfs", choices=["gfs", "perfect"])
    ap.add_argument("--step", type=float, default=WEIGHT_STEP)
    ap.add_argument("--out", default=None, help="metrics JSON (default ml/models/combined_metrics.json)")
    ap.add_argument("--md", default=None, help="also write the markdown report here")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    res = run(source=a.source, step=a.step)
    out = Path(a.out) if a.out else S.MODEL_DIR / "combined_metrics.json"
    out.write_text(json.dumps(res, indent=2, default=str))
    log.info("wrote %s", out)
    md = report_md(res)
    if a.md:
        Path(a.md).parent.mkdir(parents=True, exist_ok=True)
        Path(a.md).write_text(md + "\n")
        log.info("wrote %s", a.md)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
