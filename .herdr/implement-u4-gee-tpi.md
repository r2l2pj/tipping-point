# U4 — Mode-1 GEE completion, replay TPI (lead-analysis)

Includes spec delta L4 (outputs under `data/<case-id>/`) + L5 (R/S/T score
COGs for geotiff.js popup reads), applied mid-unit.

## Public API (pinned, implemented exactly)

`gee/common.py`:

- consts `GEE_PROJECT` (env), `COL_IMERG='NASA/GPM_L3/IMERG_V07'`,
  `BAND_IMERG='precipitation'`, `COL_SMAP='NASA/SMAP/SPL4SMGP/008'`,
  `BAND_SMAP='sm_rootzone_wetness'`, `IMG_NASADEM='NASA/NASADEM_HGT/001'`,
  `BAND_DEM='elevation'`, `CLIM_YEARS=(2001,2023)`,
  `ASSET_ROOT='projects/{GEE_PROJECT}/assets/tipping-point'`.
- `init(project=None)->str` — SA key path from `GCP_SA_KEY` first,
  `GEE_SERVICE_ACCOUNT_KEY` fallback (D10); raw-JSON env values also
  accepted (temp file, same convention as `live/build_static.py`); no key →
  default `ee.Initialize` creds; `RuntimeError` names what is missing;
  returns the resolved project.
- `case_cfg(case_id)->dict` — `{aoi,event_date,window_before_days,
  window_after_days}` via `tp.config.case`.
- `aoi_geom(case_id)` — `ee.Geometry.Rectangle(aoi)`.
- `event_dates(case_id)->list[str]` — YYYY-MM-DD, event ± window days
  (wa_2025: 21, rs_2024: 16).
- Helpers added for the unit (not pinned): `event_id`, `case_dir`/`data_dir`
  (L4; defers to `tp.config.case_id()` when that helper lands — absent in
  this tree), `asset_path`.
- `import ee` at module level; all ee objects built inside functions —
  `--dry-run` never initializes.

## What changed

- `gee/__init__.py` (new) — package docstring.
- `gee/common.py` (new) — pinned API above.
- `gee/tpi.py` (new) — Python port of 01/07.
  `python -m gee.tpi --case rs_2024|wa_2025 [--dry-run|--tasks]`:
  IMERG rain3 = 0.5·sum, climatology p90/95/99 per-pixel (2001-2023, 3-day
  windows every 3 days, ±30 d DOY) exported once as asset
  `<caso>_clim_rain3_2001_2023` then reused (`ee.data.getAsset` check);
  SMAP 24 h mean over `soil_wetness_range`; T_slide slope 15-30°; T_flood
  HAND-proxy (`focalMin` `hand_proxy_radius_m`) rescaled 5-15 m, zeroed
  slope > 5°; geomean with `tpi_weights` from config.yaml; `tpi_at` emits
  4 bands (R, S, TPI_slide, TPI_flood); daily multiband series with band =
  YYYYMMDD. Exports: `<caso>_tpi_{slide,flood}_<Ymd>.tif`,
  `<caso>_{r,s}_series_<Ymd>.tif`, `<caso>_t_{slide,flood}_<Ymd>.tif`
  (float32, EPSG:4326) → `data/<case>/cog/`; rain3/rain7/soil per-day
  area-mean JSON → `data/<case>/series/<caso>_rain_soil_<Ymd>.json`;
  `<caso>_tpi_{slide,flood}_series` assets for gee/08 + gee/09.
  `--dry-run` lists dates + planned exports with no `ee.Initialize`;
  `--tasks` prints the export parameter JSON (also offline).
- `gee/check_series.py` (new) —
  `python -m gee.check_series <cog> --case <id>`: rasterio checks — values
  in [0,1], series band count == window days, band names are YYYYMMDD
  dates, TPI peak on/before event day (r_series/s_series skip the peak
  check; `t_{slide,flood}` static COGs check single-band + range). On
  success records collections `{imerg, smap_l4, nasadem}` via
  `tp.metrics.merge_metrics` → `data/<case>/metrics.json` (`--metrics-path`
  override); exits 1 listing every failure.
- `gee/tests/` (new) — `conftest.py` recording Fake `ee` (monkeypatched per
  test, real `ee` untouched for other suites), `test_common.py` (12),
  `test_tpi.py` (8), `test_check_series.py` (8): event_dates counts (21/16),
  R breakpoints (gte at p90/p95/p99, 0.5 mid/high, clamp), geomean weights
  (config + explicit + uniform 1/3), 4-band emission, export targets (L4
  paths + L5 stems + image/bands), `--dry-run` 21 dates + ≥3 exports with
  `init` rigged to explode, `--tasks` params, check_series pass/fail incl.
  static + score-series rules and collections merge.
- `gee/01_tpi_index.js`, `gee/07_tpi_wa.js` — parity: TPI_flood series
  COG + asset; band names = YYYYMMDD via `bandNames`; per-day
  rain3/rain7/soil `Export.table.toDrive` CSV (`<caso>_rain_soil_<Ymd>`);
  climatology exported once as `<caso>_clim_rain3_2001_2023` behind
  `CLIM_ASSET_EXISTS` guard (set true after the one-time export); L5
  exports for `r_series`, `s_series`, `t_slide`, `t_flood` (float32,
  unclassified); `tpiAt` carries R/S bands. Drive descriptions renamed to
  contract stems `<caso>_tpi_{slide,flood}_<Ymd>` (dropped `_series` from
  the COG names; asset ids unchanged — `*_tpi_{slide,flood}_series` still
  feed 08/09). Pasted constants/weights still match config.yaml.

## Commands + results

- `$UV sync` → OK.
- `$UV run python -m pytest gee/tests pipeline/tests live/tests ml/tests -q`
  → **88 passed** (59 baseline-after-U3 + 29 gee), 9 warnings (pre-existing).
- `$UV run ruff check .` → All checks passed.
- `node --check gee/01_tpi_index.js gee/07_tpi_wa.js gee/03_rf_susceptibility.js`
  → OK.
- `env -u GCP_SA_KEY -u GEE_SERVICE_ACCOUNT_KEY -u GEE_PROJECT $UV run
  python -m gee.tpi --case wa_2025 --dry-run` → prints 21 dates
  (2025-11-30..2025-12-20) + 10 exports, no creds, exit 0.
- `python -m gee.tpi --case rs_2024 --tasks` → JSON plan, 16 dates,
  `"bands": 16` series + `"bands": 1` statics.

## Flags for the lead

- `tp.config.case_id()` (delta L4) does not exist in this tree —
  `gee.common.case_dir()` validates via `case_cfg` and uses the case key,
  deferring to `tp.config.case_id` once it lands.
- Band names changed to YYYYMMDD: `gee/09_impact.js` still works (selects
  event band by index 10), but `tools/build_simular.py` parses
  `*_TPI_slide` CSV columns from gee/08 — it will need a small update to
  sort date-named columns when the series asset is re-exported. Both files
  are outside my scope; left untouched.
- `live/build_static.py` reads `GCP_SA_KEY` as JSON content while
  `.env.example` documents a key-file path — `gee.common.init` accepts
  both.
- `data/SCHEMA.md` modifications in the worktree belong to U2 — unstaged.
