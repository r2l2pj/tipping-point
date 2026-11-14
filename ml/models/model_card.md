# Model card — `slide_xgb` (RS-2024 landslide trigger)

**What.** `XGBClassifier` (binary:logistic) that scores P(landslide | terrain,
recent IMERG rain, antecedent SMAP wetness) per 90 m cell per frame, in the
style of NASA's LHASA v2 (Stanley et al. 2021, doi:10.3389/feart.2021.640043).
Trained offline in Python; GEE only computes features.

**Training data.**
- Positives: post-disaster landslide inventories for the Apr–May 2024 Rio
  Grande do Sul event — UFRGS/LATITUDE initiation points
  (doi:10.5281/zenodo.15618462) and Cemaden rupture points
  (doi:10.5281/zenodo.13225473), both **CC-BY-4.0**, deduped to one per 90 m
  cell. Inventories are labels/validation only, never model inputs.
- Negatives: N1 spatial (~20k cells inside the mapped footprint, ≥250 m from
  any polygon, half on slopes >10°) and N2 temporal (~200 calm days, 2015–2024,
  excluding known heavy-rain windows) at ≈1:4.
- Label time: end of each cell's max-24h IMERG window, Apr 27–May 6 2024.

**Features (NASA-only).** IMERG V07 rain (24 h / antecedent 2 d / 7 d /
P99-relative), SMAP SPL4SMGP/008 profile wetness at t−3 d, NASADEM slope,
relief, curvature, aspect, elevation, HAND proxy, MODIS MCD12Q1 land cover.

**Settings.** LHASA Table 4 values with max_depth 3, monotone +1 on
rain/soil/slope/relief ("more rain never lowers hazard"), eval_metric aucpr,
early stopping on inner spatial blocks. See `ml/config.py`.

**Validation.** 5-fold GroupKFold on 0.25° blocks; headline metric is the
event-window ROC/PR-AUC (positives vs N1) plus TPR@FPR5; baselines = slope-only
and the manual TPI_slide = R×S×T. An event-window AUC >0.95 would be treated
as leakage, not success.

**Limits.**
- One event, no per-landslide timestamps — label times are assigned, and the
  lead-time claim needs an independently sourced anchor.
- IMERG underestimates short convective bursts (LHASA scored 0% TPR on a Rio
  de Janeiro inventory); scores are "hazard (0–1)", not calibrated
  probabilities — thresholds are set at fixed FPR.
- No lithology/fault predictors (NASA-only-inputs rule).

**Attribution.** UFRGS/LATITUDE inventory, Cemaden inventory, SC-2020
inventories — all CC-BY-4.0. Method after Stanley et al. 2021 (LHASA v2).
