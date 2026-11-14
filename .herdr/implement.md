# ml-skeleton — implementation report

Implemented the `ml/` package skeleton per `.crew/briefs/ml-skeleton.md` and the
file sketch in `.crew/reports/ml-predict.md` §5.4. Scope: `ml/**` only (plus this
report). No git commands run.

## Files changed

- `ml/__init__.py` — package docstring.
- `ml/config.py` — AOI/event window (from `events/rs-2024-05.json`), 0.25°
  block size, 90 m cell, feature column lists (§5.1), GEE collection+asset IDs
  (IMERG_V07, SPL4SMGP/008, NASADEM, MCD12Q1), N1/N2 sampling plan + excluded
  heavy-rain windows (§2.3), XGBoost params + monotone constraints (§5.2),
  thresholds/class breaks, `asset_path()` guard.
- `ml/labels.py` — `load_points` (geopandas, multi-shapefile) →
  `dedupe_to_grid` (one point per 90 m cell in UTM, cap 10k) →
  `mapped_footprint` (convex hull) → `assign_peak24h_times` (end of max-24h
  window from any regular rain cube — synthetic-capable) → `to_gee_table`
  (GEE-ready CSV) → `upload_to_gee` (guarded earthengine CLI). Missing files
  raise FileNotFoundError naming the Zenodo DOIs. CLI `--via-gee`/`--upload`.
- `ml/gee_features.py` — all ee code behind `init_ee()` (clear RuntimeError
  when unauthenticated): `static_stack`, `imerg_daily_p99` (one-time asset),
  `dynamic_stack(t)` (latency-honest SMAP at t−3 d), `features_at`,
  `assign_peak24h_gee`, `sample_n1` (≥250 m from slides, 50% slope>10°),
  `sample_n2`, `export_table`/`export_image`. Pure-python `calm_days`
  (deterministic ~200 days skipping Sep/Nov/Jun 2023) is unit-tested.
- `ml/dataset.py` — `load_features`/`write_features` (csv + parquet if pyarrow
  present — pyarrow is not a declared dep, so CSV is the exercised path),
  `block_ids`, `iter_block_folds` (GroupKFold on block_id), `inner_block_split`
  (early-stopping holdout by whole blocks), `prepare_X` (lc → category).
- `ml/train.py` — `build_classifier` (§5.2 params, monotone constraints
  filtered to present features), `fit_fold`/`cross_validate` (5 folds, inner
  block ES, OOF predictions), `train_final` → `slide_xgb.json` + `.meta.json`.
- `ml/evaluate.py` — `metric_bundle` (ROC-AUC/PR-AUC/TPR@FPR5), `evaluate_cv`
  (event-window + all-negatives, per fold), baselines `slope_only_score` and
  `manual_tpi_score` (exact R×S×T piecewise from `gee/01_tpi_index.js`, rain
  percentiles fit on the train side only), `threshold_at_fpr` (lowest threshold
  with FPR≤target), `importance` (gain + SHAP, guarded), `update_metrics_json`
  (merges under `landslide_ml` in `data/<event>/metrics.json` per SCHEMA).
- `ml/predict.py` — `load_feature_bands` (static COG + dynamic resampled
  nearest to the static grid via rasterio.warp), `predict_frame`, `apply_masks`
  (slope<5°, MODIS water lc==0), `predict_series` → uint8 multiband DEFLATE COG
  (`hazard_slide_series.tif`, prob×100, nodata 255) + `hazard_classes.tif`
  (classes 0–3 per the SCHEMA tpi legend).
- `ml/lead_time.py` — `read_hazard_series`, `first_crossing_frame`,
  `cluster_lead_times` (8-connected clusters via scipy.ndimage),
  `summarize` → `alert.lead_hours_median`/`warned_before_pct` +
  `landslide_ml.lead_hours` in metrics.json. Anchor is a required CLI arg
  (config holds an empty ANCHOR_TIMES — the report §2.2 requires an
  independently sourced timestamp, and none exists in the repo).
- `ml/models/` — `model_card.md` (data, CC-BY-4.0 attributions, limits),
  `.gitkeep`, `.gitignore` (trained `*.json`/`*.ubj` not committed).
- `ml/tests/` — `test_dataset.py` (block-CV fold+cell disjointness, XGBoost
  monotonicity on a synthetic rain sweep, metric_bundle/threshold on toy data,
  manual-TPI product structure), `test_pipeline.py` (calm_days, dedupe/
  footprint/peak-24h on synthetic geopandas+xarray, missing-file guard,
  predict_series→COG + lead_time on synthetic rasters).
- `ml/README.md` — updated to describe the implemented layout/commands.

## Commands run and results

- `uv sync` (real binary `~/.local/share/mise/installs/uv/0.13.8/.../uv` — the
  mise shim has no version set): env resolved/built OK.
- `uv run pytest ml/tests/` → **7 passed** (covers all three required: fold
  disjointness, rain-sweep monotonicity, toy metrics; plus pipeline extras).
- `uv run ruff check ml/` → clean.
- `uv run python -m ml.train --help` and all other CLIs (`evaluate`, `predict`,
  `lead_time`, `labels`, `gee_features`) resolve.
- Synthetic end-to-end (1920-row table, 32 blocks, tmp output dir):
  `ml.train` → 5 folds, ES best_iters 202–419, model+meta saved;
  `ml.evaluate` → metrics.json with cv/baselines/threshold/importance/
  data_versions in the §5.3 shape; `ml.predict` → 3-band uint8 hazard COG +
  class raster; `ml.lead_time` → lead_hours merged into metrics.json.
- Guards verified: `ml.gee_features static` → RuntimeError "run
  `earthengine authenticate` and set GEE_PROJECT"; missing shapefiles →
  FileNotFoundError with Zenodo DOIs; `upload_to_gee` → GEE_PROJECT guard.

## Notes / deviations

- `models/` lives at `ml/models/` because the write scope is `ml/**`; the
  sketch places it at repo root otherwise.
- pyarrow is not in pyproject deps, so parquet paths exist but CSV is what was
  exercised end-to-end (`load_features`/`write_features` support both).
- metrics.json ML results are nested under `landslide_ml` to preserve the
  SCHEMA sections (`alert.lead_hours_*` are filled by lead_time).
- One benign pandas4 deprecation warning in tests (`DatetimeIndex.values` via
  xarray coords).
