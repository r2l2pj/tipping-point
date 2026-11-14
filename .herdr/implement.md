# implement.md — gee-features

Implemented the brief in `/home/lucascoelho/code/r2l2pj/.crew/briefs/gee-features.md`
plus the additional unit (`gee/08_simular_table.js` from tp3.txt "Código GEE 5").
Scope kept to `gee/**`, `events/**` (+ this report). No code was executed in GEE
(paste-in scripts only, per brief). No git commands, no child agents.

## Files changed

### New
- `gee/05_p99_climatology.js` — per-pixel P99 of daily IMERG rain, 2001-2023
  (`NASA/GPM_L3/IMERG_V07`, sum×0.5 per day → `Reducer.percentile([99])`), clipped
  to the RS AOI, `Export.image.toAsset` →
  `projects/<GEE_PROJECT>/assets/tipping-point/p99_daily_rain_2001_2023`.
  Flagged as heavy one-time run, per §5.1 "compute P99 once as an asset".
- `gee/06_ml_features.js` — the feature-table script (ml-predict §5.1 + §2.3):
  - Static stack @90 m EPSG:4326 (`reduceResolution`+`reproject` from NASADEM 30 m):
    `slope_mean`, `slope_max`, `relief_500m` (focalMax−focalMin 500 m), `curv`
    (laplacian8), `aspect_sin`, `aspect_cos`, `elev`, `hand_proxy`
    (dem − focalMin 1500 m, radius from `04`), `lc` (MCD12Q1 LC_Type1 2023, mode).
  - `dynamicAt(t)`: `rain_24h` (t−24h,t], `rain_ante_2d` (t−72h,t−24h],
    `rain_7d` (t−7d,t], `rain_rel` = rain_24h / max(P99,1 mm),
    `sm_profile_wetness` mean over [t−4d,t−3d] (latency-honest per §3).
  - Sampling: `pos` = `rs2024_initiation` points (label 1); N1 = 10k slope>10° +
    10k anywhere, inside `convexHull(slides)∩aoi`, ≥250 m from points, t drawn from
    distinct positive `t_utc`; N2 = ~60 positive + 60 random cells × 200 random calm
    days 2015-04-01→2024-03-31 excluding whole months Jun/Sep/Nov 2023.
  - `lon`, `lat`, `block_id` (`floor(lon/0.25)`_`floor(lat/0.25)`) stamped on
    points before sampling (geometry dropped by `geometries:false`).
  - Extraction groups by `t_utc` (~210 values), `sampleRegions` static+dynamic per
    group, flatten → `Export.table.toDrive` CSV (~50k rows).
  - `Export.image.toDrive` COGs: `rs_2024_static_stack` (90 m),
    `rs_2024_dynamic_stack` (daily frames over the event window 2024-04-20→05-31,
    bands `0_rain_24h` etc., ~0.1° grid).
- `gee/07_tpi_wa.js` — `01` verbatim adapted to `events/wa-2025-12.json`
  (AOI [-122.6,47.0,-121.5,48.5], ref 2025-12-12, exports `tpi_slide_series_wa`
  COG + `wa_2025_tpi_slide_series` asset).
- `gee/08_simular_table.js` — doc v3 "Código GEE 5": reads
  `projects/<GEE_PROJECT>/assets/tipping-point/rs_2024_tpi_slide_series`, max TPI
  per day within 2 km of the 6 towns (coords as in doc), `Export.table.toDrive`
  CSV `simular_rs_2024` for `tools/build_simular.py`.

### Modified
- `gee/01_tpi_index.js` — added `Export.image.toAsset` →
  `rs_2024_tpi_slide_series` (required for `08`/Telegram `/simular`; band names
  `0_TPI_slide`…). Kept the existing Drive COG export.
- `gee/02_lead_time.js` — points at `…/rs2024_initiation` (UFRGS via labels.py);
  old GLC path kept as commented fallback with a note that GLC has zero RS-2024
  rows (§2.1). Reads `t_utc` (ISO) instead of `event_date` ms — with a comment
  that lead-time vs assigned t is circular and the public claim needs an
  independent anchor timestamp (§2.2).
- `events/rs-2024-05.json`, `events/wa-2025-12.json` — added a `gee` block
  (aoi_bbox + reference_date + window for RS) for bbox/date syncing.
- `gee/README.md` — new run order 01 → 05 → 06 → 02 → 07 → 08, asset
  dependencies, note that `01` must do BOTH exports for `/simular`.

## Contract for `ml/labels.py` (must match)
- Asset `projects/<GEE_PROJECT>/assets/tipping-point/rs2024_initiation`: point FC,
  deduped to 90 m, property `t_utc` = ISO-8601 end of the cell's max-IMERG 24 h
  window inside Apr 27–May 6 2024 (§2.2). `02` and `06` both read `t_utc`.

## Deviations / flags
- N1 footprint: used `convexHull(initiation points)∩AOI` as proxy for the mapped
  footprint — §2.3 says to check inventory metadata; if labels.py uploads a real
  footprint asset, swap it in (commented in `06`).
- N1 exclusion uses 250 m buffers on initiation *points* (no polygon asset yet).
- N2 calm-day rain windows excluded as whole months (Jun/Sep/Nov 2023) — §2.3
  dates were unverified; conservative choice.
- `cell_id` (§5.1) not stamped — `dataset.py` can generate row ids; all other
  §5.1 columns are produced.

## Commands run / results
- `node --check` on all six `gee/*.js` (new + edited): syntax OK.
- `python3 -m json.tool` on both `events/*.json`: valid JSON.
- No GEE execution possible from here (Code Editor scripts; brief says JS only).
