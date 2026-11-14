# ml/ — predictive model

LHASA-v2-style learned trigger (spec: `.crew/reports/ml-predict.md` §5):
XGBoost on GPM IMERG rain, SMAP L4 wetness and NASADEM/MODIS terrain, trained
offline on the RS-2024 post-disaster inventories and applied to produce the
`hazard_slide_series` COG the app replays. Labels are UFRGS/Cemaden
inventories (CC-BY-4.0), never model inputs.

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
    --out data/rs-2024-05/cog/hazard_slide_series.tif
```

GEE steps require `earthengine authenticate` + `GEE_PROJECT` (see `.env.example`);
the inventory shapefiles are Zenodo downloads, not committed — the code raises
clear errors when either is missing.
