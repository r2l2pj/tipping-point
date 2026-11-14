# implement — ingest (GEE asset ingest unit, t9r)
# L8a — SWOT series -> GEE table asset — implementation report

Ruling: validation.yml L8a (data-plan). Interface per conductor update:
module IS the CLI (`python -m pipeline.fetch.swot`), no tools/ file,
bucket env `NOW_GCS_BUCKET`. Scope respected: `pipeline/fetch/swot.py`,
`pipeline/tests/test_fetch_swot.py`, this report. No helpers spawned,
no pushes.

- `pipeline/fetch/ingest.py` — NEW. Contract exports (confirmed by lead):
  `stage_to_gcs(local_path,*,bucket,case,name)->str` (uploads to
  `gs://<bucket>/staging/<case>/<name><ext>` via google.cloud.storage),
  `ingest_asset(gcs_uri,*,asset_id,kind,force=False)->dict` (kind "image" ->
  `ee.data.startIngestion`, kind "table" -> `ee.data.startTableIngestion`;
  idempotent skip -> `status:"exists"` when `ee.data.getAsset` finds the
  asset unless force; ONLY 404/NOT_FOUND getAsset errors mean absence —
  auth/permission/service errors propagate via `_is_not_found`),
  `append_manifest(case,entry)->Path` (exact 2-arg contract signature;
  upserts rows keyed by `name` in `data/<case>/assets.json`, reads
  `events.DATA_DIR` at call time). Path-safety: `case`/`name` validated
  against `[A-Za-z0-9][A-Za-z0-9_-]*`, bucket/project/suffix/asset_id
  reject separators + `..` (`_validate_segment`/`_validate_path_safe`/
  `_validate_asset_id`). Helpers: `gee_project()` (GEE_PROJECT env, default
  `hermes-fesolucoes`), `staging_bucket()` (**NOW_GCS_BUCKET** primary,
  `GCS_BUCKET` accepted alias — conductor contract), `asset_id()`,
  `staging_uri()`. ee + google.cloud.storage are lazy `importlib` imports
  inside functions so fakes inject via `sys.modules`; missing
  GOOGLE_APPLICATION_CREDENTIALS -> RuntimeError naming the var before any
  SDK call.

  Review fixes (NEEDS-FIX r1, amended): (a) `append_manifest` is now
  exactly `(case, entry)` — `data_dir` kwarg dropped, tests monkeypatch
  `events.DATA_DIR`; (b) getAsset non-404 errors propagate; (c)
  case/name/bucket/asset_id validated before interpolation.
  `tools/ingest_assets.py --case` now uses `choices=` from config.yaml
  case keys.
- `tools/ingest_assets.py` — NEW. Batch/registry CLI: `--case rs_2024|wa_2025`
  or `--event <id>` (via `tp.events.case_key`), `--dry-run`, `--manifest-only`,
  `--force`. `ASSET_REGISTRY` is data-driven: `product`/`kind`/`patterns`
  (globs under `data/` with `{case}`); covers nisar_gcov
  (`{case}/cog/*gcov*.tif` + `raw/nisar/gcov_*.tif` from
  `pipeline/tp/nisar.py`), opera_dswx_wtr (`{case}/cog/*dswx*.tif`),
  landslide_catalog + municipal_boundaries geojson (kind table). Appending
  flood_nisar/water_nisar = one registry dict. Asset name = file stem minus
  `<case>_` prefix. Dry-run prints plan only (no creds, no manifest write);
  manifest-only writes `status:"pending"` rows; real run stages -> ingests
  -> appends manifest per asset.
- `pipeline/tests/test_ingest.py` — NEW, 16 tests. Fake `ee` (module type,
  records `init`/`image`/`table` calls, `getAsset` raises not-found or a
  caller-supplied error) and fake `google.cloud.storage` (records uploads)
  injected via `sys.modules`; covers staging path, image-vs-table dispatch,
  idempotent skip, `--force`, 404-vs-403-vs-PERMISSION_DENIED getAsset
  handling, case/name/bucket/asset_id validation, manifest upsert,
  missing-creds RuntimeError naming the var, bucket env + alias, registry
  resolution, dry-run with ALL env vars deleted (no manifest written),
  manifest-only, and a full fake real-run path. No network, no real ee/gcs.

## Decisions

- `config.yaml` untouched (conductor owns it); bucket from env only.
- `data/<case>/assets.json` envelope: `{schema: "tipping-point/gee-assets@1",
  updated_at, assets: [...]}` matching repo manifest conventions.
- hermes-fesolucoes is NOT EE-registered (see
  `.herdr/implement-gee-verify.md`: `earthengine.computations.create`
  denied) — real runs blocked; dry-run + fake tests are the proof.
# unit/opera-dswx — OPERA DSWx-HLS fetch + ingest (validation.yml ruling L8a)

## Files created (2 source + this report)

- `pipeline/fetch/opera.py` (417 lines) — module IS the CLI:
  `python -m pipeline.fetch.opera --event <id>|--case <id>
  [--start/--end] [--limit N] [--no-download|--dry-run]`
- `pipeline/tests/test_fetch_opera.py` (329 lines, 13 tests)

## What it does

- `$UV sync` — OK.
- `$UV run pytest pipeline/tests/test_ingest.py -q` — **16 passed** (0.04 s).
- `$UV run ruff check .` — **All checks passed** (fixed: removed unused
  `noqa` directives; non-404 getAsset re-raise needs no noqa).
- `env -u GOOGLE_APPLICATION_CREDENTIALS -u NOW_GCS_BUCKET -u GCS_BUCKET
  $UV run python tools/ingest_assets.py --event rs-2024-05 --dry-run` —
  exit 0; listed the 2 real rs_2024 geojson table assets with correct
  `gs://<NOW_GCS_BUCKET>/staging/rs_2024/...` and
  `projects/hermes-fesolucoes/assets/tipping-point/rs_2024/...` URIs; no
  assets.json written.

## Files committed (unit/ingest worktree only)

pipeline/fetch/ingest.py, tools/ingest_assets.py, pipeline/tests/test_ingest.py
## Changes

### `pipeline/fetch/swot.py` (new, ~330 lines)
- `series_files` / `load_records` / `collect_records` — reads every
  `data/<case>/series/<case>_swot_*.json` (tp.swot orient='records' list,
  or a dict carrying `records`/`features` + optional `reaches` geometry
  map). Returns records + merged per-reach geometry map.
- `build_feature_collection` — one Feature per observation, properties
  `{reach_id (str), t_utc (ISO w/ UTC offset via fromisoformat — 'Z'
  handled natively), river_m (float), source: 'SWOT Hydrocron'}`.
- Geometry resolution order per record: record-carried geometry
  (`geometry` dict or lon/lat field pairs) collapsed to centroid/Point
  via shapely → event `swot` block per-reach geometry (`reaches` /
  `reach_geometry` / `geometry` / `geometries` / `endpoints` keys;
  `[lon,lat]` pairs → Point, endpoint lists → LineString) → file-level
  `reaches` map → null (valid GeoJSON; still ingests as plain table).
- `content_hash` — sha256 over canonical FC JSON (sort_keys, tight
  separators) → stable across runs.
- `recorded_hash` — tolerant `data/<case>/assets.json` reader: accepts
  `{"swot_series": {...}}`, `{"assets": {...}}` and list-of-entries
  layouts (canonical writer is `append_manifest` on unit/ingest).
- `run(case_id, ...)` — collect → FC → hash → skip-check → stage
  (`data/<case>/staging/swot_series.geojson`) → `stage_to_gcs` →
  `ingest_asset(kind='table')` → `append_manifest` entry carrying
  `{name, asset_id, kind, gcs_uri, hash, features, source, ingested_at}`.
  Asset id: `projects/<GEE_PROJECT>/assets/tipping-point/<case>/swot_series`.
- Ingest contract import: `importlib` loop over
  `pipeline.fetch.ingest` / `fetch.ingest` / `tp.ingest` (covers both the
  `python -m pipeline.fetch.swot` namespace layout and the tests'
  `pipeline/`-on-sys.path layout, plus a tp fallback); all three names
  `None` when absent → `--dry-run` unaffected, real run exits with a
  clear "merge unit/ingest first" message.
- CLI: `--case`/`--event` (mutually exclusive, required; `--case` accepts
  case key or event id via `case_key`), `--dry-run`, `--force`,
  `--project` (env GEE_PROJECT), `--bucket` (env NOW_GCS_BUCKET).
  `--event`/`_default_event` (`events/<case-dash>-*.json` glob) supplies
  the event `swot` block for reach geometry only.
- Idempotent: same recorded hash + no `--force` → prints skip, no
  staging/upload/ingest/manifest calls. Changed hash or `--force` →
  re-ingests (`force` forwarded to `ingest_asset`).

### `pipeline/tests/test_fetch_swot.py` (new, 11 tests)
Fixture series JSON under tmp `data/<case>/series/`; `_FakeStorage` /
`_FakeEE` / `_FakeManifest` fixtures monkeypatch the three contract
names on `fetch.swot` — no network, no real ee/storage.
- FC shape + exact properties; null-geometry (plain-table) case.
- Record lon/lat → Point; record LineString → centroid Point; event
  `swot.endpoints` → LineString.
- `--dry-run` writes staging GeoJSON + prints plan, contract untouched.
- Real run: stage_to_gcs args, ingest_asset `kind='table'` + asset_id,
  manifest entry hash/kind.
- Idempotent skip on same hash (zero contract calls), re-ingest on
  changed hash, `--force` re-ingest (`force=True` forwarded).
- `main(["--case", ..., "--dry-run"])` end-to-end; no-records SystemExit.
# impl-grefassets — L8a reference layers -> GEE table assets

Branch `unit/ref-assets`, worktree `tipping-point-data-wt/ref-assets`.
Commit `bce9bba` (amended after review NEEDS-FIX) — 4 files, +691/-11.
NOT pushed/merged.

Review fix: `--window` was a store_true that passed `event['window']` (the
full event window) into the GLC query — broader than the configured filter.
Now `--window START,END` takes explicit ISO dates (argparse `type=`
validation, rejects bad input); without it `glc_catalog` falls back to
`event['glc_window']` as before.

## Files
# implement — L8a nisar-update (unit/nisar-update)

Daily NISAR new-pass search + ingest. Module IS the CLI per interface update —
no tools/ file.

## Files (2 created, 942 lines, amended commit 1013642 on unit/nisar-update)

- `pipeline/fetch/nisar.py` (~460 lines) — search/plan/execute + CLI
  `python -m pipeline.fetch.nisar [--case] [--since] [--alerts-geojson] [--dry-run|--execute]`
- `pipeline/tests/test_fetch_nisar.py` (~480 lines) — 16 offline tests,
  fake `asf_search` + fake `fetch.ingest` injected via `sys.modules`

- `uv sync` (via $UV run): 119 packages, `tp` editable install OK.
- `uv run pytest pipeline/tests/test_fetch_swot.py -q` → **11 passed**.
- `uv run ruff check .` → **All checks passed** (after fixes:
  `fromisoformat` native 'Z', `_centroid` restructured — shapely errors
  propagate instead of a blind except, unused test var dropped).
- `uv run python -m pipeline.fetch.swot --help` → argparse usage OK.
- Dry-run vs a temp fixture (`data/rs_2024/series/rs_2024_swot_20240502.json`,
  2 records, one with lon/lat): plan JSON correct
  (`features:2, with_geometry:1`), staged FC verified — null geometry on
  the bare record, Point on the record carrying lon/lat. Fixture removed
  after.
- `--event rs-2024-05 --dry-run` → resolves case + loads event OK.
- Real run without unit/ingest merged → exits 1 with the
  "ingest contract unavailable" message (as designed).

## Review round 1 (NEEDS-FIX)

- `load_records` assumed non-list JSON was a dict → scalar/null series
  file crashed on `.get`. Now raises `TypeError` naming the file;
  `reaches` must be a dict; `records`/`features` must be a list; GeoJSON
  Feature entries are flattened (properties + geometry).
- Same crash class fixed in `recorded_hash`: scalar `assets.json`,
  scalar `assets` value and scalar `swot_series` entry all return None.
- +3 tests (scalar series files, FC-dict series file, malformed
  assets.json shapes) → 14 total. `ruff check .` clean; TRY004 →
  TypeError.

## Notes / assumptions

- `assets.json` schema is owned by unit/ingest's `append_manifest`; my
  reader tolerates dict-keyed, `assets`-nested, and list layouts.
- `stage_to_gcs` is called with a `Path` local_path, keyword-only
  bucket/case/name per contract.
- Smoke-test fixture files were deleted; `git status` shows only the two
  scope files (+ this report).

Files: 2 created (+ .herdr/implement.md). Tests: 11, all passing.
- CMR search for `OPERA_L3_DSWX-HLS_V1` over case AOI + window via
  `earthaccess.search_data` — **lazy import, unauthenticated**. Sorted
  least-cloudy first (missing cloud last), `--limit N` slices post-sort.
- `--event` reads aoi + window from `events/*.json` via `tp.events`
  (default window: reference_date .. window.end, same convention as
  `tp.hls_fetch`); `--case` reads the config.yaml case via `tp.config`
  (event_date .. event_date + window_after_days) or, with `--event`,
  overrides only the output case name. `--start/--end` override bounds.
- Per granule picks the B01_WTR band link (`wtr_link` prefers `B01_WTR`,
  rejects BWTR/WTR-1/WTR-2 via `_wtr_rank`), downloads just that file —
  **auth happens here only**: `EARTHDATA_TOKEN` bearer first (ruling L6,
  same env-strategy pattern as `live/fetch.py`), then `EDL_USER/EDL_PASS`
  mapped only when BOTH are set, then `~/.netrc` — `RuntimeError` naming
  what's missing. (Review fix: a partial EDL pair no longer leaks an
  empty `EARTHDATA_*` var and lets earthaccess try netrc first.)
- Clips to AOI bbox (reprojected into the raster CRS) and writes a COG
  (GTiff fallback) to gitignored
  `data/raw/opera/<case>/opera_dswx_<yyyymmdd>.tif`.
- Stages via `stage_to_gcs(local_path, bucket=$NOW_GCS_BUCKET, case, name)`
  → `ingest_asset(uri, asset_id=projects/$GEE_PROJECT/assets/tipping-point/
  <case>/opera/opera_dswx_<yyyymmdd>, kind='image')` → `append_manifest`.
- **Idempotent**: `data/<case>/assets.json` is read for existing
  `asset_id`s; hits are skipped before auth/download/stage
  (`status: "exists"`). Records: `{granule, date, cloud_cover, wtr,
  local_path, asset_id, gcs_uri, status}` printed as JSON.

## Assumptions (flag for lead)

1. Ingest module assumed to land as `pipeline/fetch/ingest.py` — imported
   lazily via `from . import ingest` (guarded), so `--no-download` /
   `--dry-run` work without it; download path errors cleanly if absent.
2. `assets.json` dedup read assumed at `data/<case>/assets.json`, entries
   under an `"assets"` list (tolerates a bare list too).
3. `OPERA_L3_DSWx-HLS` collection searched as a single `short_name`
   string (per PLAN.md "OPERA_L3_DSWX-HLS_V1"); no version split.
4. Reuses `tp.hls_fetch` sort/record/cloud/date helpers rather than
   duplicating them; adds an OPERA native-id date fallback regex.

## Commands + results

- `$UV sync` — ok
- `$UV run pytest pipeline/tests/test_fetch_opera.py -q` — **13 passed**
- `$UV run ruff check .` — **all checks passed** (one justified
  `# noqa: BLE001` on the COG-driver fallback, matching live/fetch.py
  precedent)
- Regression: `pytest test_fetch_opera + test_hls_fetch +
  test_fetch_inventory` — **27 passed**
- `python -m pipeline.fetch.opera --event rs-2024-05 --dry-run` and
  `--case rs_2024 --dry-run` — both print resolved params JSON
  (verified end-to-end, no creds needed)

No network in tests: earthaccess, rasterio(.mask/.warp), and
fetch.ingest are all faked via `sys.modules`.

Commit: files above on `unit/opera-dswx`, not pushed.
- `pipeline/fetch/reference.py` (new, ~287 lines) — library + CLI per the
  interface update: `python -m pipeline.fetch.reference --kind
  glc|inventory|boundaries|all [--case id (repeatable) | --all] [--refresh]
  [--dry-run] [--force] [--window]`. No tools/ file.
  - Product map: `boundaries` -> `geojson/municipal_boundaries.geojson` ->
    table asset `municipal_boundaries`; `glc`/`inventory` ->
    `geojson/landslide_catalog.geojson` -> table asset `landslide_catalog`;
    under `projects/<GEE_PROJECT>/assets/tipping-point/<case>/`.
  - `--kind all` = boundaries + the case's catalog source (wa-* -> glc,
    else inventory — same split as `pipeline/scripts/12_aux_products.py`).
  - INGEST CONTRACT: `stage_to_gcs` / `ingest_asset` / `append_manifest`
    imported from `fetch.ingest` inside try/except (relative then absolute)
    -> `None` when unit/ingest is unmerged; `--dry-run` still works, real
    runs raise a clean `RuntimeError` naming the module.
  - `t_utc`: added to catalog feature properties only when a date exists
    (`t_utc`, then `event_date|date|datetime|timestamp`; ISO strings +
    epoch s/ms; naive treated as UTC) -> `%Y-%m-%dT%H:%M:%SZ`. Nothing is
    invented — rs inventory points stay date-less. If no feature gains
    `t_utc` (boundaries always; undated catalogs) the SOURCE file is staged
    byte-identical (no staging copy). Attribution props pass through
    untouched.
  - Idempotent via `data/<case>/assets.json` (tolerant reader: list,
    dict-with-`assets`, or name-keyed dict — matches fetch.ingest's
    `tipping-point/gee-assets@1` doc) -> `skipped` unless `--force`.
  - `--refresh` re-runs `fetch.{boundaries,glc,inventory}` for every event
    mapping to the case before ingesting; in `--dry-run` it reports
    `would re-fetch <ids>` without side effects.
  - `--window START,END` (with `--refresh --kind glc`): explicit ISO-date
    filter forwarded to `glc_catalog(window=...)`; without it the fetcher
    is called bare and `event['glc_window']` applies inside glc.py — the
    whole `event['window']` is never used.
  - Env: `GEE_PROJECT` (default `hermes-fesolucoes`), `NOW_GCS_BUCKET`
    (then `GCS_BUCKET` — same order/gate as fetch.ingest and live/).
    Missing bucket on a real run -> `RuntimeError` naming the var.
  - Staging copies, when needed, go to `data/<case>/staging/<file>`.

- `pipeline/fetch/glc.py` (scope addition) — `_query_params` default is now
  bbox-only (`where: "1=1"`, no `event_date` clause): the GLC is a
  reference catalog and community reports lag (259 all-time features in the
  wa bbox vs 0 in Dec-2025). Optional window via `event['glc_window']` or
  `glc_catalog(event, window=...)`. `event_date` epoch-ms -> ISO
  normalization kept.

- `pipeline/tests/test_fetch_reference.py` (new, 15 tests incl. 3-param
  refresh) — a fake `fetch.ingest` module built on fake
  `google.cloud.storage` (`Client.bucket().blob().upload_from_filename`)
  and fake `ee.data` (`getAsset`/`startTableIngestion`) injected via
  `sys.modules["fetch.ingest"]` + `importlib.reload`, proving the real
  `from .ingest import` contract path. Covers: product->asset mapping +
  `gs://` staging URI, `t_utc` present on dated / absent on undated
  features (incl. `event_date: null`), attribution preserved, source staged
  byte-identical when undated, idempotent skip + `--force`, manifest entry
  written, `--refresh` calls the matching fetcher per kind, `--window`
  forwards the event window (glc only), `--kind all --all` mapping,
  wa -> glc pick, dry-run zero side-effects, dry-run works when ingest
  names are `None` (+ RuntimeError on real run), missing product, missing
  bucket env. No network.

- `pipeline/tests/test_fetch_glc.py` — default assert now `where == "1=1"`
  (no DATE clause); new `test_query_params_window_opt_in` covers both
  `event['glc_window']` and the explicit `window=` arg.

## Commands & results

- `$UV sync` — ok (editable install; `pipeline/` on sys.path via .pth, so
  `tp`/`fetch` are top-level and `python -m pipeline.fetch.reference` works
  through the namespace package).
- `$UV run pytest pipeline/tests -q` — **108 passed** (was 92 before this
  unit: +16 new, glc suite updated 4->5).
- `$UV run pytest pipeline/tests/test_fetch_reference.py -q` — 16 passed.
- `$UV run ruff check .` — All checks passed (after fixing FURB162 + 2
  style hits).
- `python -m pipeline.fetch.reference --kind all --all --dry-run` — prints
  all four `case/product -> projects/hermes-fesolucoes/assets/tipping-
  point/<case>/<name>` lines against the real data/.
- `python -m pipeline.fetch.reference --kind boundaries --case rs_2024`
  (no ingest.py, no creds) — clean `RuntimeError: fetch.ingest is not
  importable ... only --dry-run works`.
- `--dry-run --refresh` — `rs_2024/landslide_catalog: dry-run -> ...
  (would re-fetch rs-2024-05)`.

## Notes for the lead

- Real ingestion is blocked on `hermes-fesolucoes` not being registered
  with Earth Engine + no GCS creds in this env — dry-run + fake-SDK tests
  are the proof, same caveat as unit/ingest.
- `fetch.ingest` doesn't exist on this branch yet; contract verified
  against the sibling worktree copy (entry shape `{"schema",
  "assets":[{name,gee_id,gcs_uri,kind,status,ingested_at,operation?}]}`;
  staged URI is `gs://<bucket>/staging/<case>/<name><ext>`).
- No subagents used. Two `todo` items logged (L8a reference unit, glc fix).
## Behaviour

- Iterates events/*.json with a `nisar` block (`--case` accepts case key
  `wa_2025` or event id `wa-2025-12`). rs_2024 (maturity null, empty frames)
  still searched — spec says "with a nisar block".
- Known scenes = `nisar.frames[*].scenes` granule_ids/dates (dict+list+flat
  shapes tolerated, same as `tp.nisar.download_plan`) + any string matching
  `nisar_gcov_\d{8}` inside data/<case>/assets.json entries (tolerates list,
  {"assets":[...]} and {"<id>":{...}} shapes).
- `since` = --since > max known scene date > min(window.start or
  reference_date, today). REVIEW-FIX: window.end fallback could be
  future-dated and dropped passes between the event and its end; the
  min(..., today) clamp keeps a future window.start from returning empty.
- Search: lazy `import asf_search`, `search(shortName=<collections.BETA or
  NISAR_L2_GCOV_BETA_V1>, intersectsWith=<wkt>, start=<since>)` per AOI.
  VERIFIED LIVE (anonymous): 14 GCOV granules over the wa AOI since
  2025-12-16. NB: adding `dataset=DATASET.NISAR` alongside shortName silently
  defeats the filter (9176 results, all NISAR products) — kept shortName only.
- Dedup: granule_id/date in known -> "known-scene"; asset_id in assets.json
  -> "asset-exists"; second granule on an already-planned date ->
  "duplicate-date". One asset per date (nisar_gcov_<yyyymmdd>); event's
  declared (track,look,frame) wins the same-date pick (stable pre-sort).
- --alerts-geojson: data/now/alerts.json candidates' centroids -> ±0.25 deg
  box AOIs appended to the search (GeoJSON FeatureCollections also accepted).
- --execute: creds EARTHDATA_TOKEN > tp.nisar._edl_credentials
  (EDL_USER/EDL_PASS/netrc); GDAL env = GDAL_HTTP_BEARER (token) or
  tp.nisar._gdal_auth_env. Per scene: `tp.nisar._subset_h5_variable` ->
  data/raw/nisar/<case>_nisar_gcov_<ymd>.tif (gitignored), then
  stage_to_gcs -> ingest_asset(asset_id=projects/<GEE_PROJECT>/assets/
  tipping-point/<case>/nisar/nisar_gcov_<ymd>, kind='image') ->
  append_manifest. Requires NOW_GCS_BUCKET + GEE_PROJECT + fetch.ingest;
  clear RuntimeError naming whichever is missing. Idempotent: assets.json
  re-read per case at execute time; second run over same plan is a no-op.
- fetch.ingest imported lazily inside _load_ingest() (try/except) so
  --dry-run works while ingest.py only exists on unit/ingest. Contract names
  used exactly: stage_to_gcs(local_path, *, bucket, case, name),
  ingest_asset(gcs_uri, *, asset_id, kind='image', force=False),
  append_manifest(case, entry). asset_id is passed as the FULL gee path;
  manifest entry carries both asset_id (short) and gee_asset — reconcile if
  ingest lands expecting the short name.

## Plan JSON (--dry-run stdout)

`{schema: tipping-point/nisar-update@1, generated_at, alert_aois, cases:
{<case>: {event_id, collection, since, bbox, search_aois[], known_dates[],
found, new_scenes[{granule_id,date,track,look,frame,asset_id,gee_asset,
variable,h5_path,h5_url,cog}], skipped[{granule_id,reason}]}}}` — plus
`executed[]` when run with --execute.

## Commands + results

- `$UV sync` — ok (141 resolved, asf_search 14.1.0)
- `$UV run pytest pipeline/tests/test_fetch_nisar.py -q` — 16 passed
- `$UV run pytest pipeline/tests -q` — 107 passed (whole suite)
- `$UV run ruff check .` — All checks passed (auto-fixed import sort)
- LIVE `python -m pipeline.fetch.nisar --case wa_2025 --dry-run` — real asf
  metadata search OK: found 14, new dates 2025-12-23/28, 2026-01-02/04/09
  (declared frame 064 picked over 065 on shared dates), h5_url resolves to
  nisar.asf.earthdatacloud.nasa.gov .h5 links.

## Notes / risks

- --execute untested end-to-end (no creds/GCS/GEE in env; fetch.ingest absent
  on this branch). All ingest calls verified against the fake module.
- GDAL_HTTP_BEARER for EARTHDATA_TOKEN assumed (GDAL >=3.9); user/pass path
  reuses tp.nisar._gdal_auth_env unchanged.
