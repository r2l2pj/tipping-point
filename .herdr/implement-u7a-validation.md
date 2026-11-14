# U7a — Mode-3 validation (write_metrics merge fix + 04_validation auto/cloud modes)

Unit: U7a for lead-analysis, worktree branch `unit/u7a`. Model: SWE-2 High.

## What changed

### `pipeline/tp/validation.py` (rewritten)

- `write_metrics(event_id, flat, **kwargs)` now routes through
  `tp.metrics.merge_metrics` — existing keys survive a validation merge.
  **Fixes the file-overwrite bug** (was `path.write_text(json.dumps(...))`).
- `mosaic_tiles(paths, ref) -> np.ndarray` — merges OPERA WTR tiles onto a
  reference grid via `rio.reproject_match` (nearest). Per pixel, in path
  order: first observed class (0|1|2) wins — over later tiles and over
  cloud; 253 fills pixels no tile observed; uncovered stays 255. So
  `cloud_pct` on a mosaic = pixels optical never saw.
- `cloud_pct(wtr)` — `100 * px==253 / px!=255&!nan` (nodata-safe).
- `compare_wtr(water_nisar, wtr)` — iou/f1/cloud_pct on a WTR array already
  on the NISAR grid; water = WTR 1|2; 253/255/nan excluded; NISAR nan -> 0.
- `validate_flood(water_nisar_path, opera_wtr)` — accepts a single path
  (unchanged behaviour), a list of paths (mosaicked), or a pre-mosaicked
  array. Backed by `mosaic_tiles` + `compare_wtr`.
- `search_download_opera(bbox, temporal, dest, short_name=...)` — now takes
  the collection short name and returns `(paths, granule native-ids)` for
  `collections` provenance. `earthaccess` still imported inside the
  function (lazy).
- `opera_product(case_id)` / `case_key(event_id)` — case->collection map;
  `wa_2025` -> `OPERA_L3_DSWX-S1_V1`, default `OPERA_L3_DSWX-HLS_V1`.
- `hls_fmask_cloud_pct(path)` — cloud fraction from an HLS Fmask raster;
  docstring notes the assumption: bit 1 (value 2) = cloud, cirrus/shadow
  not counted; over px != 255.
- `cloud_only_merge(event_id, pct, source, path)` — merges `cloud_pct` +
  `iou`/`f1` = null with the "optical fully clouded" reason and
  `collections.cloud_pct_source`.

### `pipeline/scripts/04_validation.py` (rewritten)

- `--opera-wtr FILE` — unchanged single-file path (now also merges, never
  overwrites, metrics.json; records `collections.opera_dswx`).
- `--auto` — `tp.config.case()` for aoi/event_date; `earthaccess`
  search+download of the case OPERA product over `temporal
  =event_date..+5d`, `bbox=aoi`, into `data/raw/opera/<event>/`
  (gitignored); mosaics tiles onto the `<caso>_water_nisar_<AAAAMMDD>.tif`
  grid (nearest) and merges iou/f1/cloud_pct + collections (short name +
  granule native-ids). Falls back to tile-grid clipped to the AOI
  (`rio.clip_box`) for cloud_pct when the NISAR mask is missing; iou/f1
  merge as null + reason.
- `--hls-cloud FILE` — Fmask bit-1 cloud fraction; feeds `cloud_pct` when
  OPERA is unavailable; source recorded in `collections.cloud_pct_source`.
- `--cloud-only` — merges cloud_pct + iou/f1 null with reasons (RS case).
- Arg validation: needs one of `--opera-wtr|--auto|--hls-cloud`;
  `--opera-wtr`+`--auto` exclusive; `--cloud-only` needs a cloud source.
- `earthaccess` never imported at parse/module level (`--help` clean).

### `pipeline/tests/test_validation.py` (new, 9 tests)

- case/product mapping; mosaic prefers first observed value (incl. beating
  cloud, and path-order wins on conflicts); mosaic reprojects a coarser
  tile onto the ref grid; hand-computed iou=0.5 / f1=2/3 /
  cloud_pct=200/14 on synthetic rasters (`write_tif` fixture);
  `validate_flood` accepts a path list; Fmask bit-1 decode (bit2 not
  counted, 255 excluded); cloud-only merge leaves iou/f1 null with reasons
  and preserves pre-existing keys; `write_metrics` preserves existing keys
  (the bug fix).

## Commands run and results

- `$UV sync` — ok.
- `$UV run pytest pipeline/tests -q` — **41 passed** (baseline was 33).
- `$UV run ruff check .` — **clean** (after `--fix` sorted one import block
  and a blind `except Exception` was narrowed to `(RioXarrayError, ValueError)`).
- `python pipeline/scripts/04_validation.py --help` — resolves;
  `earthaccess` not imported at parse (verified via `sys.modules`).
- End-to-end smoke (in-process, `tp.events.DATA_DIR` patched to a tmpdir):
  - `--event rs-2024-05 --hls-cloud fmask.tif --cloud-only` ->
    `cloud_pct=13.33`, iou/f1 null + reasons, `cloud_pct_source=hls_fmask`.
  - `--event rs-2024-05 --opera-wtr wtr.tif` with a synthetic
    `rs_2024_water_nisar_20240502.tif` -> `iou=0.5`, `f1=0.667`,
    `cloud_pct=14.29`, collections `opera_dswx` + `cloud_pct_source`
    recorded. metrics.json merged (no overwrite).
- Not run: `--auto` download path (needs Earthdata creds — none in unit
  scope); code path covered by parse + unit tests of its pieces.

## Notes

- No new dependencies, no credentials touched.
- `earthaccess` granule ids recorded as
  `collections.opera_dswx_granules` (comma-joined native-ids) alongside
  `collections.opera_dswx` = collection short name.

## Rework — spec delta L4 (data/<case>/ dirs + root metrics.json)

Second commit on unit/u7a.

- `tp.validation.case_key()` renamed to `case_id(event_id)`; it delegates to
  `tp.config.case_id()` via `getattr` once that function lands with the
  metrics-root rework (`unit/u3-metrics-root`, owned by another lead —
  config.py/metrics.py are outside this unit's write scope). Until then it
  falls back to the `<caso>` naming convention, so tests stay green on this
  branch either way.
- `04_validation.py` no longer calls `tp.events.event_data_dir` (being
  updated by the other lead). Case data dir is resolved as
  `DATA_DIR / case_id(event)` → `data/rs_2024/cog/<caso>_water_nisar_…`;
  OPERA downloads go to `data/raw/opera/<case>/`.
- All `merge_metrics` calls pass just the event id (no `path=`), so the
  updated auto-routing to root `data/metrics.json` keyed by case applies
  the moment it lands; the signature is unchanged per the delta.
- Tests updated (`case_key` → `case_id`); merge tests still pass `path=`
  to a tmp file for isolation.

Re-verified after rework:

- `pytest pipeline/tests -q` — 41 passed; `ruff check .` — clean;
  `python pipeline/scripts/04_validation.py --help` — resolves, no
  earthaccess import.
- Smoke (DATA_DIR patched to tmpdir): `--hls-cloud … --cloud-only` →
  cloud_pct 13.33 + nulls/reasons; `--opera-wtr` found the NISAR COG at
  `data/rs_2024/cog/rs_2024_water_nisar_20240502.tif` (new case-dir layout)
  and merged iou=0.5/f1=0.667/cloud_pct=14.29.

## Rework — ruling L6 (EARTHDATA_TOKEN auth)

Third commit on unit/u7a.

- `tp.validation._edl_login()` (lazy earthaccess import) logs in with
  `earthaccess.login(strategy="environment")` — honors `EARTHDATA_TOKEN`
  bearer auth, and first maps the repo's `EDL_USER`/`EDL_PASS` fallback
  onto `EARTHDATA_USERNAME`/`EARTHDATA_PASSWORD` (same convention as
  `live/fetch.py`). `search_download_opera` calls it; still lazy — no
  earthaccess at import/parse.
- New tests (11 total):
  - `test_edl_login_environment_strategy` — fake earthaccess module via
    `sys.modules`; asserts `login(strategy="environment")` and the
    EDL_USER/EDL_PASS → EARTHDATA_* mapping. No network.
  - `test_edl_token_env_wires_bearer_header` — real earthaccess with a fake
    `EARTHDATA_TOKEN`: asserts `auth.token["access_token"]`,
    `authenticated`, and `get_session().headers["Authorization"] ==
    "Bearer fake-token-123"`. The environment-token path is purely local
    (earthaccess sets the token dict without a request) — no network.

Re-verified: 43 tests pass, `ruff check .` clean, `--help` resolves with
no earthaccess import.
