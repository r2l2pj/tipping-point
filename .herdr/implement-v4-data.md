# v4-data-delta — implementation report

Aligned the data repo with project doc v4 per `.crew/briefs/v4-data-delta.md`.
Scope respected: `gee/**`, `live/**`, `ml/**`, `pipeline/**`, `config.yaml`,
`events/**`, `data/SCHEMA.md`, this report. No git commands, no child agents,
no GEE/network calls.

Note: `/home/lucascoelho/code/r2l2pj/.crew/tp4.txt` does not exist (only
`tp3.txt`, searched the tree). The brief's inline spec was complete and was
followed literally; tp3.txt's same-named sections were used as context.

## Changes

### 1. TPI → weighted geometric mean (R·S·T)^(1/3)
- `config.yaml`: added `thresholds.tpi_weights: [0.3333, 0.3333, 0.3334]`
  (uniform default = plain geomean); header comment now says doc v4. All
  pre-existing keys untouched — readers (`live/config.py`,
  `tools/build_simular.py`) unaffected.
- `gee/01_tpi_index.js`, `gee/07_tpi_wa.js`: pasted `TPI_W` weights + `tpi(r,s,t)`
  helper using `r.pow(wR)·s.pow(wS)·t.pow(wT)`; both TPI bands now geomean.
- `live/tpi.py`: `tpi()` computes `r**wr * s**ws * t**wt`; `weights` param
  defaults to `TPI_WEIGHTS` read from config.yaml (`live/config.py`).
- `ml/evaluate.py`: `manual_tpi_score` same geomean; `weights` param defaults
  to `cfg.TPI_WEIGHTS` (`ml/config.py` now reads config.yaml for this key only).
- Tests updated to assert geomean semantics (`0.5^(1/3)`, weights honoured).

### 2. config.yaml / events alignment
- `config.yaml` already matched the v4 example exactly (rs 2024-05-02 10/5,
  wa 2025-12-10 10/10, all listed thresholds, seed 42) — only `tpi_weights` added.
- `events/wa-2025-12.json`: `reference_date` → `2025-12-10` (top-level + `gee`
  block — the Dec 4/16 GCOV pair still brackets it); `gee.window` →
  `2025-11-30..2025-12-20` (= event ±10/10) plus explicit `window_before_days`/
  `window_after_days`.
- `events/rs-2024-05.json`: `gee.window` → `2024-04-22..2024-05-07`
  (= event −10/+5, matching gee/01) + same explicit window fields. Top-level
  broad `window` kept (feeds SWOT/search queries).
- `gee/07_tpi_wa.js`: `ref` → `2025-12-10`, series `sequence(-10, 10)`.

### 3. gee/09_impact.js (new)
Reads the TPI-series assets from `01`/`07` (event-day band index =
window_before_days = 10 for both cases). For each case: GPWv4
(`CIESIN/GPWv411/GPW_Population_Count`, epoch nearest the event date —
`gpw_year` recorded) summed inside `TPI_slide > 0.6` → `people_critical`;
VIIRS `VNP46A2` `DNB_BRDF_Corrected_NTL` (Mandatory_Quality_Flag==0) mean of
7 nights after vs 30 before in the affected area → `night_light_drop_pct`.
For wa, when `HAS_NISAR_FLOOD=true` + the `wa_2025_flood_nisar` asset is
uploaded (command in comments; NISAR isn't in GEE), also `people_flooded` and
`flooded_km2`, and the light mask becomes critical ∪ flood. Prints the metric
dict + exports a 1-row CSV to merge into `data/<case>/metrics.json`. The
README documents the GPW 2020 epoch note.

### 4. manifest + PNG contract
- `live/render.py`: new `warp_to_3857` (nearest-neighbor reproject of a 4326
  class grid to EPSG:3857) and `layer_png_3857` → `(png_bytes, bounds_lonlat)`.
- `live/run_now.py`: PNG layers now carry `{id, date, png, cog, legend,
  bounds, forecast}` — `bounds` is the 3857 image extent back in lon/lat for
  the MapLibre image source; `forecast: true` on D+1..D+3.
  `build_manifest` emits the v4 `now` node (`schema: tipping-point/now@2`,
  mode/bounds/data_dates/layers + kept extras products/nisar_passes).
- `live/publish.py`: docstring updated (write order unchanged — manifest last).
- `pipeline/tp/polygonize.py`, `pipeline/tp/flood_mask.py`: docstring notes —
  COGs/GeoJSON stay EPSG:4326, only app PNGs are 3857.
- `data/SCHEMA.md`: rewritten — root manifest shape
  `cases/{<case>:{mode,bounds,layers,metrics,data_dates}, now:{...}}`,
  now/manifest.json, CRS rules, file naming, flat metrics keys.

### 5. File naming + metrics keys
- `tp/events.py`: new `artifact_stem(event_id, layer)` →
  `<caso>_<camada>_<AAAAMMDD>` (e.g. `wa_2025_flood_nisar_20251210`).
- `02_flood_mask.py`: renames the three COGs post-write;
  `03_landslide_coh.py`: output name uses the stem; `04_validation.py`: reads
  the renamed water COG; `06_polygonize.py` docstring notes .geojson inherits
  the stem.
- `ml/predict.py`: classes COG derives from `--out` stem
  (`rs_2024_hazard_slide_series_20240502.tif` → `rs_2024_hazard_classes_20240502.tif`);
  docstring/README/lead_time examples updated.
- `gee/01`/`07` Drive export descriptions renamed to the convention.
- metrics.json v4 flat keys: `04_validation.py` writes `{event, iou, f1,
  cloud_pct}` flat; `ml/lead_time.py` writes top-level `lead_time_mean_h`,
  `lead_time_median_h`, `warned_before_pct` (details still under
  `landslide_ml.lead_hours`); `ml/evaluate.py` adds top-level `rf_auc`
  (learned-model AUC), `rule_auc` (slope rule = doc's "regra manual de
  declividade"), `tpi_manual_auc`, `feature_importance` via new `flat` param.
- `live/summary.json` gains `people_critical` (alias of `people_exposed`) and
  `forecast_source: "geos-fp"`.

### 6. Docs
- `gee/README.md`: run order lists `09` after `01` (asset prerequisites noted).
- `ml/README.md`: XGBoost-supersedes-in-GEE-RF deviation noted; `rf_auc`
  contract key explained.

## Commands run (results)

- `uv run pytest pipeline/tests live/tests ml/tests -q` → **46 passed**
  (real uv binary; mise shim has no version). Includes new tests:
  `test_tpi_geomean` (uniform + config + weighted), `test_layer_png_3857`
  (RGBA PNG, 3857-derived bounds), geomean assertions in ml tests.
- `uv run ruff check live/ ml/ pipeline/` → clean.
- `node --check gee/*.js` → all 9 parse (incl. new `09_impact.js`).
- `uv run python -c "import yaml; yaml.safe_load(open('config.yaml'))"` →
  loads; all v4 keys present incl. `tpi_weights`.
- End-to-end smoke of `live/run_now.run()` with mocked fetchers + synthetic
  static npz (1° AOI): wrote 8 PNG layers (189×163 px, 4-band RGBA on the
  3857 grid), manifest.json LAST with v4 `now` shape (layer =
  `{id,date,png,cog:null,legend,bounds,forecast}`), summary.json with
  `people_critical`/`forecast_source`, alerts.json, prev_classes.npz.
- `update_metrics_json(..., flat=...)` verified to merge flat keys + nested
  `landslide_ml`.
- `pipeline/scripts/02_flood_mask.py --help`, `04_validation.py --help` resolve;
  `artifact_stem` verified → `wa_2025_flood_nisar_20251210`,
  `rs_2024_tpi_slide_20240502`.

## Deviations / notes for the conductor

- `tp4.txt` absent (see above) — brief's inline values were the spec.
- Out-of-scope staleness (not in "May change", untouched):
  - `now/manifest.json` seed still says `tipping-point/now@1` — regenerated
    as `now@2` on the next `run_now` run.
  - `data/events.json` (app index) still has wa `reference_date`
    `2025-12-12` — should be `2025-12-10` when that file is next editable.
- `gee/09_impact.js` `people_flooded`/`flooded_km2` need the NISAR flood mask
  uploaded as a GEE asset (NISAR is not in the catalog); `HAS_NISAR_FLOOD`
  gate documented in the script.
- `rf_auc` holds the XGBoost AUC (intentional model deviation, documented in
  ml/README); `rule_auc` maps to the slope-only baseline, `tpi_manual_auc`
  also emitted for the geomean manual TPI.
- Impact metrics land in metrics.json by merging the exported CSV printout —
  GEE cannot write repo files; that step stays manual per repo convention.
