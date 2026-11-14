# U10b — NISAR fetch execution

Branch `unit/u10b`. Touched only: `pipeline/tp/nisar.py`,
`pipeline/tp/events.py` (scope update L4), `pipeline/scripts/01_get_nisar.py`,
`pipeline/tests/test_nisar.py`, `.herdr/implement-u10b.md`.

Scope update (ruling L4): data folders are per CASE id, so
`event_data_dir(event_id)` now yields `data/<case_key>/{cog,geojson,series}`
(`case_key('wa-2025-12') == 'wa_2025'`) and the manifest is
`data/<case_key>/nisar_products.json`. `data/raw/nisar/` stays a shared
(gitignored) dir — the ruling scoped case folders, not `raw/`; flag if the
conductor wants raw per-case too.

## What changed

### `pipeline/tp/nisar.py`
- `execute_plan(event, dry_run=False) -> list[Path]`
  - Iterates `download_plan(event)`; entries with an `h5_path` are fetched,
    entries without (WATERMASK, `h5_path=None`) contribute only their
    collection id to the manifest.
  - Resolves each granule via `earthaccess.search_data` on the step's
    collection + AOI bbox + a ±1-day temporal window around the scene
    date(s) (`_find_granule`). Steps sharing a granule (same
    collection/date/track/frame, e.g. GCOV HHHH+HVHV+mask) hit CMR once.
    Picks the granule matching the event's `track`/`frame` (e.g. `172D`,
    `064`), preferring the last date in a `a_to_b` pair for GUNW.
  - `_subset_h5_variable` streams `NETCDF:"/vsicurl/<h5_url>":<h5_path>`
    (shared builder `_netcdf_dsn`, also used by `gdal_subset_cmd`) with a
    lazy `import rasterio`, `rasterio.Env(**gdal_env)`, `rasterio.open`,
    `WarpedVRT(src, crs="EPSG:4326")` + `windows.from_bounds` clip to the
    event `aoi.bbox`, writing a COG to `data/raw/nisar/<out>` (plan `out`
    names, e.g. `gcov_hhhh_pre.tif` — the `<product>_<var>_<scene>` plan
    naming already used by `--plan` and downstream scripts).
  - Writes `data/<case_key>/nisar_products.json` via `event_data_dir`:
    `{product: {"collection": <id>, <role>: <granule native_id>}}`
    (roles: pre/post/ref/evt). Empty plans still write a manifest and need
    no creds.
  - Creds at call time via `_edl_credentials()`: `EDL_USER`/`EDL_PASS`
    (or `EARTHDATA_USERNAME`/`EARTHDATA_PASSWORD`) env, else `~/.netrc`
    (or `$NETRC`) containing `machine urs.earthdata.nasa.gov`; otherwise a
    `RuntimeError` naming what's missing — no obscure crashes. Env creds
    are mapped onto `EARTHDATA_*` for `earthaccess.login()` and onto
    `GDAL_HTTP_USERPWD`; netrc uses `GDAL_HTTP_NETRC=YES`.
  - CMR search / no-results / no-track-match / no-.h5-link / GDAL failures
    all raise `RuntimeError` with the step context.
  - `dry_run=True` returns the planned `data/raw/nisar/` paths with no
    creds and no network.
- `download_plan` tolerates both event shapes (scene dates flat on the
  frame or nested under `"scenes"`; GUNW absent) and now carries
  `track`/`frame` into each entry for granule matching.
- Helpers: `_edl_credentials`, `_earthdata_login`, `_gdal_auth_env`,
  `_step_dates`, `_track_look`, `_native_id`, `_find_granule`,
  `_subset_h5_variable`, `_netcdf_dsn`.

### `pipeline/tp/events.py` (scope update L4)
- New `case_key(event_id)` helper (`'rs-2024-05'->'rs_2024'`).
- `event_data_dir(event_id)` → `data/<case_key>/{cog,geojson,series}`;
  signature unchanged so `02_flood_mask`/`03`/`04`/`05`/`06` need no edits.
- `artifact_stem` reuses `case_key` (same derivation as before).

### `pipeline/scripts/01_get_nisar.py`
- `--execute` runs `execute_plan`; `RuntimeError` → `ap.error` (exit 2,
  clean refusal). `--dry-run` lists paths without downloading.
- `--plan` unchanged: fully offline, no creds, no network.

### `pipeline/tests/test_nisar.py` (+6 tests)
- `test_event_data_dir_uses_case_key` — `event_data_dir('wa-2025-12')`
  → `<DATA_DIR>/wa_2025` with `cog/` etc.; `case_key('rs-2024-05')`.
- `test_execute_plan_writes_tifs_and_manifest` — fake `rasterio` injected
  into `sys.modules` (`rasterio`, `rasterio.vrt`, `rasterio.windows`),
  fake `earthaccess`, `EDL_*` env, `DATA_DIR` → tmp. Asserts 8 tifs under
  `raw/nisar/`, 4 CMR calls, manifest roles/collections/native-ids
  (incl. wrong-track granule filtered out).
- `test_execute_plan_requires_creds` — `RuntimeError("Earthdata credentials…")`
- `test_execute_plan_dry_run_needs_nothing` — planned paths, nothing written
- `test_plan_flag_runs_offline` — subprocess `--plan`, rc 0, no creds
- `test_execute_flag_refuses_without_creds` — subprocess `--execute`, rc≠0

## Commands run

- `$UV sync` — ok
- `$UV run pytest pipeline/tests live/tests ml/tests -x -q` — **52 passed**
  (46 baseline + 6 new), 9 pre-existing pandas warnings
- `$UV run ruff check pipeline/` — **All checks passed**
- `01_get_nisar.py --event wa-2025-12 --plan` — prints plan + gdalwarp cmds, no creds
- `... --execute --dry-run` — lists 8 `data/raw/nisar/*.tif` paths, no creds
- `... --execute` (no creds, empty HOME) — exit 2, clear "Earthdata credentials required" error
- `execute_plan(load_event("rs-2024-05"))` (temp DATA_DIR) — `[]` + `{}` manifest at `rs_2024/`, no creds
- `download_plan` on a nested-`scenes` GCOV-only event — 6 GCOV + watermask entries

## Notes / decisions

- Output filenames come from the plan's `out` field (`<product>_<var>_<scene>.tif`
  e.g. `gcov_hhhh_pre.tif`) — the naming `--plan` and `02_flood_mask.py`
  already reference; files land in `data/raw/nisar/` per spec.
- `data/raw/` is gitignored; only `data/<event>/nisar_products.json` is
  a committable artifact.
- `rasterio` stays a lazy import inside `_subset_h5_variable` so `import tp.nisar`
  and `--plan` don't pay for it; tests swap it via `sys.modules`.
- Not run: a real `/vsicurl/` fetch (needs EDL creds + network). Creds,
  search, DSN and write paths are covered by fakes; `gdal_subset_cmd`
  remains the documented fallback.
