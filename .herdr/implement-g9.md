# G9: fix stale null reasons for cases.wa_2025 (rf_auc, rule_auc, feature_importance)

Worktree: g9-wa-null, branch g9-wa-null. Only `data/metrics.json` edited —
`cases.wa_2025._null_reasons` for the three keys. Values stay `null`. No other
key touched.

## Why the old reasons were stale

Old text claimed "no trained model / ml pipeline configured for RS-2024".
False: `ml/` trains XGBoost/RF models (`ml/local/`, `bundle_3a/3b.joblib`,
`ml/models/metrics_ml.json`, `ml/models/split_3b_events.csv`, `data/ml/`).
Per `split_3b_events.csv` line `wa_2025,8,test,True`, WA 2025 is a forced,
held-out 3b trigger TEST event (8 dated WGS positives), never trained on.

The `cases.*` contract keys `rf_auc` / `rule_auc` / `feature_importance` are
written only for `rs_2024` by `ml/local/export.py::export_metrics`
(case="rs_2024") from the **3a susceptibility** model's TEST on S+SE Brazil
blocks (`metrics_ml.json` `3a.models.{rf_all,rule_slope,...}.test`). The 3a
grid/model covers only the Brazil domain; WA 2025 is never evaluated there, so
the per-case keys legitimately stay null — the reasons just described a world
that no longer exists.

## Where the WA numbers DO live (verified in ml/models/metrics_ml.json)

- Per-issue-lead TEST metrics:
  `by_lead.scopes.wa_2025.<lead 0-13>.{both|same_point|elsewhere}.{xgb_all|xgb_rain_soil|rf_all|rule_tpi|xgb_all_perfect|served_product}`
  (`by_lead.horizon_for_lead` maps lead 0->24h, 1->48h, 2->72h, 3-6->7d, 7-13->14d).
  lead 0, `both` negatives: `rf_all` n_pos=8, PR-AUC 0.694, ROC-AUC 0.920;
  `rule_tpi` PR-AUC 0.176, ROC-AUC 0.564.
- Pooled per-horizon TEST metrics (region `north_america` = wa_2025, per
  `export.py` `REGION_EVENT`):
  `3b.horizons.<24h|48h|72h|7d|14d>.{gfs|perfect}.<model>.by_region.north_america`.
  24h gfs: `rf_all` n=208/n_pos=8, PR-AUC 0.320, ROC-AUC 0.932;
  `rule_tpi` PR-AUC 0.279, ROC-AUC 0.610.
- Surfaced for the app at `data/ml/ml_card.json`
  `trigger.<h>.<src>.<model>.by_event.wa_2025`.
- Feature importance is model-level, not per-event: 3b trigger importance at
  `metrics_ml.json` `3b.importance` (gain_share / gain_share_feature /
  permutation_pr_auc_drop_test), exported to `data/ml/feature_importance_3b.json`;
  served 3a T_ML importance in `data/ml/feature_importance.json`.

## Before / after

- `rf_auc`: before "No local labeled WA feature CSV/parquet table or trained
  model exists. The ml pipeline is configured for RS-2024; ..." -> after
  "cases.* rf_auc is the per-case key written only for rs_2024 by ml.local
  export from the 3a susceptibility TEST on S+SE Brazil blocks
  (metrics_ml.json 3a.models.rf_all.test.roc_auc); WA 2025 is not a 3a
  evaluation case — it is a held-out 3b trigger TEST event
  (ml/models/split_3b_events.csv: wa_2025,8,test,True), so no per-case RF AUC
  exists; its RandomForest TEST numbers live at metrics_ml.json
  by_lead.scopes.wa_2025.<lead 0-13>.{both|same_point|elsewhere}.rf_all
  (lead 0 = 24h: n_pos 8, PR-AUC 0.694, ROC-AUC 0.920) and
  3b.horizons.<24h|48h|72h|7d|14d>.{gfs|perfect}.rf_all.by_region.north_america
  (24h gfs: PR-AUC 0.320, ROC-AUC 0.932), surfaced as data/ml/ml_card.json
  trigger.<h>.<src>.rf_all.by_event.wa_2025."
- `rule_auc`: same stale text -> "cases.* rule_auc is the per-case key written
  only for rs_2024 by ml.local export from the 3a slope-rule TEST on S+SE
  Brazil blocks (metrics_ml.json 3a.models.rule_slope.test.roc_auc); WA 2025 is
  a held-out 3b TEST event, so no per-case rule AUC exists; the TPI>0.6 rule's
  WA TEST metrics live at metrics_ml.json
  by_lead.scopes.wa_2025.<lead 0-13>.{both|same_point|elsewhere}.rule_tpi
  (lead 0 = 24h: PR-AUC 0.176, ROC-AUC 0.564) and
  3b.horizons.<24h|48h|72h|7d|14d>.{gfs|perfect}.rule_tpi.by_region.north_america
  (24h gfs: PR-AUC 0.279, ROC-AUC 0.610), surfaced as data/ml/ml_card.json
  trigger.<h>.<src>.rule_tpi.by_event.wa_2025."
- `feature_importance`: before "no trained model: ml/models holds only
  model_card.md (no slide_xgb.json) and no labeled feature table exists, so
  importance is undefined" -> "cases.* feature_importance is the served 3a
  T_ML model's gain share written only for rs_2024 by ml.local export
  (bundle_3a -> data/ml/feature_importance.json); WA 2025 is a held-out 3b
  TEST event, and importance is a property of the trained models, not a
  per-event metric — the 3b trigger model's importance lives at
  metrics_ml.json 3b.importance (gain_share / gain_share_feature /
  permutation_pr_auc_drop_test, exported to
  data/ml/feature_importance_3b.json)."

## Producer script check

No script writes these wa_2025 reasons into metrics.json. The stale sentence
lives in `data/wa_2025/ml_metrics.json` (a static per-case data file with a
single `"reason"` field, not per-key), and nothing reads it into
metrics.json — grep for `ml_metrics`, `evaluation_run`, `model_family`
finds no producer. `merge_metrics` (pipeline/tp/metrics.py) only writes
reasons passed by callers. So no script line to edit; the reasons were merged
in by an earlier audit pass.

## Verification

- `python -c "import json; json.load(open('data/metrics.json'))"` — loads;
  rf_auc/rule_auc/feature_importance still null with reasons present.
- `systemd-run --user --scope -p MemoryMax=2G env PYTHONPATH=.:pipeline
  ~/code/r2l2pj/tipping-point-data/.venv/bin/python -m pytest -q
  pipeline/tests/test_metrics.py` — 10 passed.
- Same runner on `pipeline/tests/test_metrics_c1.py` (null/reason pairing
  audit on metrics.json) — 6 passed.
- `git diff --stat`: 1 file changed, 3 insertions, 3 deletions.
