# ml/ — predictive model (PLAN.md "Nível 3")

Two learned layers, both trained locally from NASA/NOAA sources (ruling L25:
no GEE on any path). Code: `ml/local/`; EDA: `ml/eda/`; decisions and EDA
findings: `.crew/research/ml-eda.md`.

| Layer | What it predicts | Training data | Held-out TEST |
|---|---|---|---|
| **3a T_ML** susceptibility | P(landslide-prone cell), static | GLC/COOLR points ≤ 5 km in S+SE Brazil + RS 2024 inventory sample; 3:1 random land negatives > 5 km from any known slide | ~20 % of ~50 km blocks (+10 km buffer) |
| **3b trigger** (LHASA-v2 style) | P(landslide on day t) → P within **24 h / 48 h / 72 h / 7 d / 14 d** | dated, rain-triggered GLC/COOLR points ≤ 5 km **worldwide** since SMAP start (2015-03-31) — S/SE Brazil alone has ~23, South America 92; point + date extraction only; negatives = same point on 5 event-free dates + one point 5-50 km away on the same day | **RS 2024** (UFRGS/Cemaden) and **WA 2025** (WGS dated reports) — whole events, never seen in training |

Models compared on the same TEST rows: **XGBoost** (monotone in rain/soil),
**RandomForest(200)** (the doc's model), and the rules (**slope rule** for 3a,
**TPI > 0.6** for 3b). Ablations: 3a terrain → +land cover → all; 3b
**rain-only → +soil → all**. Metrics per split and horizon: precision, recall,
F1, accuracy, confusion matrix, base rate, PR-AUC, ROC-AUC (TEST is read once,
by `evaluate`). Threshold and calibration (isotonic ≥ 30 positives, else
Platt) are fit on VAL only.

Horizons: issue time 00 UTC of day D; P_H = 1 − ∏(1 − p_k) over days D..D+H−1
with observed IMERG up to D−1 and forecast rain after. Two forecast sources
are scored: `perfect` (observed IMERG — retrospective upper bound, never the
headline) and `gfs` (NOAA GFS 00Z run of D — the operational number).
**Training never sees GFS:** the daily model is fit on observed IMERG, and the
horizon calibrators/thresholds are fit on VAL with observed IMERG standing in
for the forecast ("perfect-forecast proxy"); GFS is used only at TEST and at
inference (the GFS archive starts 2021-03-23, so most training years have none). 7 d and
14 d carry "low forecast skill" (GFS skill drops after ~5 days).

## Features

| Group | Columns | Source |
|---|---|---|
| rain | rain_24h, rain_72h, rain_7d, rain_24h_rel, rain_72h_rel (÷ local P99) | GPM IMERG V07 (NASA POWER `IMERG_PRECTOT` per point; GES DISC OPeNDAP grids) |
| soil (dynamic) | sm_surface, sm_rootzone, sm_profile_wetness | SMAP L4 `SPL4SMGP` 008 (one 10:30 UTC granule/day, OPeNDAP subset) |
| soil texture | clay, sand, porosity | SMAP L4 land-model constants `SPL4SMLM` 008 |
| terrain | elevation, slope, aspect_sin/cos, curvature, relief (+ hand_m in the Now domain) | NASADEM_HGT 001, 90 m |
| land cover | landcover (IGBP), ndvi | MCD12Q1 061; VIIRS VNP13C2 002 annual mean |
| climate (3a) | rain_mean, soil_mean | IMERG monthly `GPM_3IMERGM` 07; SMAP L4 mean |

## Run locally

Prerequisites: an Earthdata token (Earthdata Login → Generate Token) with the
GES DISC, LP DAAC and NSIDC EULAs accepted, exported as `EARTHDATA_TOKEN`
(never commit it). Python env: `uv sync --group dev` (matplotlib is dev-only,
used by the EDA). GFS comes from the public NOAA AWS bucket (no auth).

```bash
export EARTHDATA_TOKEN=...                       # from your .env, never committed
export ML_DATA_DIR=~/.cache/tipping-point-ml     # downloads + intermediate tables (default)

# 0) proof it all works on real data, tiny (≈ 10-15 min cold, < 2 min warm)
ML_DATA_DIR=/tmp/tp-ml-smoke uv run python -m ml.local all --smoke

# 1) full run, stage by stage (each stage is resumable — re-run after a crash)
uv run python -m ml.local labels         # GLC/COOLR (global) + RS 2024 + WA 2025 -> inventory.csv
uv run python -m ml.local fetch-region   # 3a grids for S+SE Brazil: NASADEM 90 m, MCD12Q1, NDVI, IMERG monthly, SMAP mean/texture
uv run python -m ml.local sample         # positives/negatives + frozen TRAIN/VAL/TEST split files (ml/models/split_*.csv)
uv run python -m ml.local fetch-points   # 3b point extraction: IMERG series, SMAP at (point, date), terrain, GFS for TEST events
uv run python -m ml.local table          # feature tables (CSV under ML_DATA_DIR/tables)
uv run python -m ml.local db             # SQLite store -> data/ml/tipping_point_ml.sqlite
uv run python -m ml.local eda            # EDA -> ml/eda/figures/*.png + ml/eda/eda_summary.json
uv run python -m ml.local train          # fit on TRAIN; early stopping, calibration, threshold on VAL
uv run python -m ml.local evaluate       # TEST metrics (first and only read of TEST) -> ml/models/metrics_ml.json
uv run python -m ml.local export         # data/cog/t_ml.tif, data/metrics.json (merged), data/ml/*.json
```

Flags: `--balanced` (class-weight positives), `--no-gfs` (skip the GFS
download/evaluation), `--t-ml-90m` (also write a 90 m T_ML COG), `--force`
(re-download label sources). `--smoke` writes models/exports under
`$ML_DATA_DIR/smoke_models` and `smoke_export` — never into tracked files.
`ML_SMOKE_GLC_N=<n>` sets how many GLC points a smoke run samples (default 16).

### What each stage costs (measured on the smoke, extrapolated)

| Stage | Full-run cost | Notes |
|---|---|---|
| fetch-region | NASADEM ≈ 462 tiles: ~5 GB zips + ~12 GB .hgt, terrain 15-30 min; NDVI 1.8 GB once; MCD12Q1 minutes; SMAP soil_mean 8 yr × every 8 d ≈ 365 granule subsets | the 17 GB DEM is the big one; delete `nasadem/*.zip` after `terrain` to halve it |
| fetch-points | POWER: one ~575 KB series per 0.1° cell; SMAP: one OPeNDAP subset per distinct date (~2.5 s); static: ~7 s per distinct 1° cell; GFS: ~64 6-h buckets per TEST issue day, per-event box | RS 2024 capped at 3000 points (`RS2024_MAX_POINTS`) |
| table → evaluate | minutes, < 4 GB RAM | XGBoost `hist`, RF 200 trees |

### Outputs

| Path | What | Committed? |
|---|---|---|
| `ml/models/split_3a_blocks.csv`, `split_3b_events.csv`, `split_summary.json` | frozen split decision (re-read by every later stage) | yes, after the full `sample` |
| `ml/models/metrics_ml.json` | every metric: VAL + TEST, per model, ablation, horizon, forecast source, region | `git add -f` (models/ ignores *.json) |
| `ml/models/t_ml_xgb.json`, `p_trigger_xgb.json` (+ `.meta.json`) | serving models (calibrators + thresholds in meta) | `git add -f` when publishing |
| `data/ml/tipping_point_ml.sqlite` | **landslides**, **samples** (`split` column), **features**, split_units, meta; views `v_3a`, `v_3b`, `v_3b_horizon` | no (gitignored; publish deliberately) |
| `data/cog/t_ml.tif`, `data/metrics.json`, `data/ml/*.json` | app-facing exports | yes |

```bash
sqlite3 data/ml/tipping_point_ml.sqlite \
  "SELECT task, split, label, count(*) FROM samples GROUP BY 1,2,3"
uv run python -c "from ml.local.store import read_task; print(read_task('3b').describe())"
```

Serving (lead-live's Now job): `ml.local.serve.TriggerModel.load().predict(...)`
returns `p_trigger_24h … p_trigger_14d` grids from SMAP (held), IMERG (D−7..D−1),
GFS (D..D+13) and the static stack — pure numpy + xgboost, no network.

Tests: `uv run pytest ml/tests` (offline; HTTP mocked, synthetic arrays).

## Honesty rules

- RS 2024 points are undated per point. They are NOT dated from rain (that
  would take the label from a model input): every RS point gets the fixed date
  2024-05-01 for the daily table, and horizon labels score "landslide within the
  main triggering window Apr 29 - May 2, 2024" (`RS2024_TRIGGER_WINDOW`).
- GLC/COOLR 3b positives are 95 % from 2015-2018 (systematic ingestion stopped
  ~2019); TEST is 2024-25 — a temporal-transfer test, reported as such.
- GLC/COOLR is a *reported* catalog (news-based; location accuracy varies) —
  only ≤ 5 km points are used, and its spatial bias toward roads/towns carries
  into 3b. WA 2025 reports are "assumed to be from the storms" (WGS).
- Case-control sampling: base rates in the tables are not real-world
  frequencies; calibrated P is relative to the sampling design. Report PR-AUC
  with its base rate, never accuracy alone.
- `perfect` forecast rows are an upper bound; only `gfs` rows are an
  operational skill claim.

---

## Legacy (GEE-era RS-2024 replay model)

LHASA-v2-style learned trigger (spec: `.crew/reports/ml-predict.md` §5):
XGBoost on GPM IMERG rain, SMAP L4 wetness and NASADEM/MODIS terrain, trained
offline on the RS-2024 post-disaster inventories and applied to produce the
`hazard_slide_series` COG the app replays. Labels are UFRGS/Cemaden
inventories (CC-BY-4.0), never model inputs.

Deviation from the doc (v4, intentional): the learned model is offline
XGBoost, superseding the doc's in-GEE `smileRandomForest` (block-CV and
monotone constraints aren't available in GEE). `metrics.json` still uses the
contract keys — `rf_auc` holds the learned-model AUC, `rule_auc` the manual
slope-rule baseline. The manual-TPI baseline in `evaluate.py` uses the v4
geometric mean `TPI = (R·S·T)^(1/3)`, same as `gee/01_tpi_index.js`.

## Layout

```
config.py        AOI, event window, feature names, GEE IDs, sampling plan, thresholds
labels.py        inventories → dedupe to 90 m cells → footprint → peak-24h t → GEE CSV
gee_features.py  static stack, dynamic(t), N1/N2 sampling, exports (needs GEE auth)
dataset.py       feature table I/O, block_id (0.25°), GroupKFold folds
train.py         block-CV + early stop on inner blocks → models/slide_xgb.json
evaluate.py      ROC/PR-AUC, TPR@FPR5, baselines (slope, manual TPI), SHAP → metrics.json
predict.py       static COG + per-frame dynamic → hazard uint8 multiband COG + classes
lead_time.py     first frame ≥ threshold per cluster vs anchor → lead_hours
tests/           pytest; all logic below runs on synthetic inputs
models/          model_card.md only — trained artifacts are not committed
```

Run order: `labels → gee_features → dataset → train → evaluate → predict → lead_time`.
Outputs follow `data/SCHEMA.md`: `data/<event>/cog/hazard_slide_series.tif`,
`hazard_classes.tif`, and `metrics.json` (section `landslide_ml`, `alert`).

## Commands

```bash
uv run pytest ml/tests/                      # tests
uv run python -m ml.train --features features.parquet
uv run python -m ml.predict --static static.tif --dynamic dyn_*.tif \
    --out data/rs-2024-05/cog/rs_2024_hazard_slide_series_20240502.tif
```

GEE steps require `earthengine authenticate` + `GEE_PROJECT` (see `.env.example`);
the inventory shapefiles are Zenodo downloads, not committed — the code raises
clear errors when either is missing.
