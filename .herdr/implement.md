# ml-combine — trigger-model combinations on TEST (unit/ml-combine)

## What I changed

New files only — no edits to lead-ml's code:

- `ml/local/combine.py` — evaluates combinations of the trained 3b trigger
  models + TPI rule on TEST, no retraining. Members vote/blend at their own
  VAL-F1 thresholds on VAL-calibrated scores (from `bundle_3b.joblib
  horizon_cal`), exactly as `trainer.evaluate_3b` scores them. Per horizon
  (24h/48h/72h/7d/14d, TEST = RS 2024 + WA 2025, GFS 00Z):
  - singles: `xgb_all`, `rf_all`, `rule_tpi`, served `xgb_rain_soil`
    (reference rows; **asserted equal to `ml/models/metrics_ml.json`**
    before combinations are computed — key list in `ASSERT_KEYS`, tol 1e-6)
  - combos `xgb+rf`, `xgb+tpi`, `rf+tpi`, `xgb+rf+tpi` as **and** / **or**
    (vote count ≥ k / ≥ 1), **majority** (≥ 2 of 3, trio only; score = vote
    count, so PR-AUC/ROC still exist) and **blend**: weighted mean of
    calibrated scores (`rule_tpi` = its 0..1 TPI value, identity calibrator);
    weights on a 0.05 simplex grid + threshold chosen to maximise **VAL F1**
    (ties → VAL PR-AUC → class margin). VAL is scored with `source="perfect"`
    — the same perfect-forecast proxy the training-time horizon calibration
    used (no GFS exists for VAL).
- `ml/tests/test_local_combine.py` — offline synthetic tests: weight-grid
  simplex, vote counts + NaN, and/or/majority confusion, blend prefers the
  informative member, the metrics_ml.json assert passes/fails correctly.
- `ml/models/combined_metrics.json` — full results (per horizon × combo ×
  rule: n, n_pos, base_rate, P/R/F1/acc, confusion, PR-/ROC-AUC; blend rows
  carry weights + val_f1/val_pr_auc).
- `~/code/r2l2pj/.crew/research/ml-combined.md` — markdown tables (outside
  this worktree, delivered to the conductor).

## Commands run

```bash
uv run pytest ml/tests/test_local_combine.py -x -q     # 5 passed (uv binary:
   ~/.local/share/mise/installs/uv/0.13.8/...          #  mise shim had no default set)
uv run ruff check ml/local/combine.py ml/tests/test_local_combine.py  # clean
ML_DATA_DIR=~/.cache/tipping-point-ml uv run python -m ml.local.combine \
    --source gfs --md ~/code/r2l2pj/.crew/research/ml-combined.md
# -> ml/models/combined_metrics.json; ~53 min (GFS reads + per-day RF/XGB predict)
```

## Results

- The singles assert held on all 5 horizons — recomputed TEST metrics equal
  `metrics_ml.json` (e.g. xgb_all 24h: P 0.984 / R 0.411 / F1 0.579,
  PR-AUC 0.752 / ROC 0.934).
- Blends are the only combos that matter: vote-AND trades recall for
  precision past xgb_all alone; vote-OR recalls more at lower precision;
  neither beats the best single's F1 at 24–72h. The VAL-fit **blend** does,
  modestly: 24h `xgb+rf` blend F1 **0.659** vs xgb_all 0.579 (R 0.493 vs
  0.411, P 0.993), PR-AUC 0.762 vs 0.752. 72h blend 0.576 vs 0.575 F1,
  PR-AUC 0.668 vs 0.665 — flat. At 7d/14d blends edge the singles' PR-AUC
  (0.865→xgb_all 0.860; 0.977→0.969) with similar F1.
- Blend weights are sane: VAL drops `rule_tpi` to 0–0.2 weight everywhere;
  xgb+rf sit ~0.4/0.6–0.5/0.5.
- Headline conclusion stands: no combination beats the best single model by
  enough to change the served choice; blends are a marginal F1/PR gain at
  short horizons.

Memory stayed under the 8 GB cap (single process, the same footprint as
`evaluate_3b` — no OOM).
