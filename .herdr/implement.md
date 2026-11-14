# implement — ingest (GEE asset ingest unit, t9r) — round 3 fixes

## Changed (this round)

- `pipeline/fetch/ingest.py` — `_validate_segment` now allows dots in
  names (`[A-Za-z0-9][A-Za-z0-9._-]*`): callers pass real filenames like
  `wa_2025_nisar_gcov_20251228.tif` as `name` (cross-unit break caught by
  lead-analysis, test_fetch_nisar::test_execute_stages_ingests_and_appends).
  Still rejects `/`, `\`, `..` (incl. embedded `a..b`), empty, leading
  non-alnum, and trailing `.`.
- `pipeline/fetch/nisar.py` — `_edl_credentials`/`_gdal_auth_env` now
  delegate to shared `fetch.edl` (`ensure_earthdata`/`gdal_auth_env`); the
  edlauth merge had deleted `tp_nisar._edl_credentials`, breaking
  `execute_plan` with AttributeError (pre-existing suite failure on main,
  fixed here to get pipeline/tests green). Side effect: token creds now
  use the correct `GDAL_HTTP_HEADERS` Bearer option.
- `pipeline/tests/test_ingest.py` — `test_invalid_case_and_name_rejected`
  updated: real filename with dots asserted valid; `a..b` and trailing `.`
  asserted invalid.
- `pipeline/fetch/opera.py` — user-restyled `_ingest` importlib fix kept
  (f"{__package__}.ingest" + top-level importlib, moved bucket check
  before EDL login).

## Commands run

- `$UV run pytest pipeline/tests -q` — **288 passed** (was 1 failed:
  test_fetch_nisar::test_execute_requires_creds — tp_nisar creds helpers
  removed by the edlauth merge).
- `$UV run ruff check .` — **All checks passed**.

## Contract (unchanged)

`stage_to_gcs(local_path,*,bucket,case,name)->str`,
`ingest_asset(gcs_uri,*,asset_id,kind,force=False)->dict`,
`append_manifest(case,entry)->Path`; NOW_GCS_BUCKET + GCS_BUCKET alias;
`data/<case>/assets.json` upserts keyed by name; segment/path validation
on case/name/bucket/project/asset_id; `tools/ingest_assets.py` registry
CLI (`--case` choices from config.yaml, `--event`, `--dry-run`,
`--manifest-only`, `--force`).

## Commands run

- `git rebase main` — clean; prior work already merged as e2109d5, so the
  rebase produced no new commit.
- `$UV run pytest pipeline/tests -q` — **211 passed** (was 1 failed:
  test_fetch_opera, fixed via opera.py importlib change; confirmed the
  failure pre-existed on main by stashing).
- `$UV run ruff check .` — **All checks passed**.
- `env -u GOOGLE_APPLICATION_CREDENTIALS -u GCP_SA_KEY $UV run python
  tools/ingest_assets.py --case rs_2024 --dry-run` — exit 0, correct plan.

## Files in this commit

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

**Main-RED fix (2814643, rebased onto main e52403d):** `_load_ingest` now
resolves via `importlib.import_module(f"{__package__}.ingest")` at call
time + `import ingest` fallback (opera e52403d pattern) — `from fetch
import ingest` reused the cached package attribute once real ingest.py was
imported, bypassing sys.modules fakes. ingest/bucket/GEE_PROJECT checks
moved before Earthdata creds; autouse `isolated_environment` fixture added;
fakes injected at both `fetch.ingest` and `pipeline.fetch.ingest`;
missing-module test blocks all three import names. Full suite: 227 passed.

**Main-RED fix 2 (f2be704, rebased onto main cddea0b):** edlauth removed
`tp.nisar._edl_credentials`/`_gdal_auth_env` — now
`from .edl import ensure_earthdata, gdal_auth_env` (canonical helper, same
dict shape + 'Earthdata credentials required' RuntimeError; token travels
via GDAL_HTTP_HEADERS). No asf session needed — metadata search is
anonymous. Full suite: 278 passed.

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
- `pipeline/fetch/edl.py` (NEW): shared EDL helper, tiny API as requested —
  `edl_token()`, `edl_auth_headers()` (Bearer dict for plain HTTPS fetches),
  `ensure_earthdata()` ({"token"} | {"user","password"} | {"netrc"} or the
  existing-style clean "Earthdata credentials required" RuntimeError), plus
  the per-mechanism appliers `earthaccess_login()` (strategy='environment'
  for token AND user/pass — earthaccess 0.19 reads EARTHDATA_TOKEN itself;
  EDL_USER/PASS map onto EARTHDATA_USERNAME/PASSWORD; strategy='netrc' for
  netrc), `asf_session()` (`ASFSession().auth_with_token(token)`, None when
  no token), and `gdal_auth_env()` (token -> GDAL_HTTP_HEADERS Bearer only —
  NO cross-host forwarding flag; user/pass -> GDAL_HTTP_USERPWD; netrc ->
  GDAL_HTTP_NETRC=YES).
- `pipeline/tp/nisar.py`: deleted `_edl_credentials`/`_earthdata_login`/
  `_gdal_auth_env` — `execute_plan()` and `search()` now go through
  `fetch.edl`. `gdal_subset_cmd()` prints
  `GDAL_HTTP_HEADERS="Authorization: Bearer $EARTHDATA_TOKEN"` when a token
  is set (env-var reference — token value never printed), else keeps
  GDAL_HTTP_NETRC=YES. Docstrings updated.
- `pipeline/tp/hls_fetch.py`: `_edl_login()` delegates to
  `fetch.edl.earthaccess_login()` wrapped in the module's _EDL_MSG (now
  mentions EARTHDATA_TOKEN); lazy import kept; `import os` dropped.
  `fetch_scenes()` also pins `edl.edl_auth_headers()` onto
  `earthaccess.get_requests_https_session()` — the shared session granule
  downloads run on — so EARTHDATA_TOKEN rides as Authorization: Bearer
  (no-op {} when user/pass or netrc auth is in use).
- `pipeline/tp/modis_nrt.py`: `search()` calls `edl.earthaccess_login()`
  inside the existing never-raises try (no creds -> [] as before);
  `fetch_nrt()` pins `edl.edl_auth_headers()` onto the earthaccess
  HTTPS session the same way before `earthaccess.download`.
- `pipeline/scripts/01_get_nisar.py`: --execute help text mentions
  EARTHDATA_TOKEN.
- `pipeline/fetch/{__init__,boundaries,glc,inventory}.py`: NOT changed —
  all are public no-auth fetchers (IBGE, US Census TIGERweb, COOLR public
  FeatureServer, Zenodo public API); per plan, nothing to do. Note:
  `zenodo_inventory.py` does not exist; the Zenodo inventory fetcher is
  `pipeline/fetch/inventory.py`.
- asf_search: no in-scope call site uses it (only `live/fetch.py`, out of
  scope, and that one is metadata-only/no-auth). `edl.asf_session()` covers
  the mechanism for the parallel fetch units.
- `tp/validation.py` also calls `earthaccess.login()` but is NOT in my file
  scope — left untouched.
- Tests: new `pipeline/tests/test_edl.py` (14 tests, no network);
  `test_nisar.py` fakes now capture earthaccess.login kwargs + rasterio.Env
  kwargs; new tests assert Bearer header in GDAL_HTTP_HEADERS, USERPWD
  fallback, token-only env-var reference in printed gdalwarp cmd, and clean
  error drops EARTHDATA_TOKEN too; `test_hls_fetch.py` + `test_modis_nrt.py`
  gained token/no-creds/fallback login-wiring tests plus one test each
  asserting `Authorization: Bearer faketoken` lands on the earthaccess
  download session (review NEEDS-FIX: edl_auth_headers wired at both
  download call sites).

## Commands run

- `$UV sync` — OK ("Resolved 141 packages", 119 installed).
- `$UV run ruff check .` — "All checks passed!".
- `$UV run pytest pipeline/tests -q` — **114 passed in 2.80s** (after the
  review NEEDS-FIX wiring; 112 before).

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

## Review round 2 (NEEDS-FIX, on merged f8d3f94 — rebased onto main a3d1b7d)

- `collect_records` now wraps `load_records` per file:
  `(TypeError, json.JSONDecodeError)` → stderr
  `warning: skipping malformed series file <path>: <err>` + skip. Valid
  files still load; all-corrupt still exits cleanly via the existing
  "no SWOT records" SystemExit. Chosen policy: warn+skip (not abort).
- +3 tests (bad-among-good warn+skip both for scalar and invalid-JSON,
  all-malformed clean exit) → 17 in test_fetch_swot.py.
- `$UV run pytest pipeline/tests -q` → 208 passed, 1 failed:
  `test_fetch_opera.py::test_fetch_scenes_download_ingests` —
  PRE-EXISTING on clean main (verified via stash; order-dependent,
  passes in isolation). Sibling unit's flake, out of scope.
- `$UV run ruff check .` → clean.

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

## OPERA full-suite isolation follow-up

Rebased unit/opera-dswx onto main (3ae2211), including the ingest library.
Changed only pipeline/fetch/opera.py, pipeline/tests/test_fetch_opera.py,
and this report.

- _ingest() now uses call-time importlib.import_module with the current
  package name. Unlike `from . import ingest`, this respects sys.modules
  replacement even when the package retains a cached real ingest attribute.
- The download test patches both fetch.ingest and pipeline.fetch.ingest;
  monkeypatch restores those entries. Added an explicit cached-package
  attribute regression test using a disposable sentinel (no real SDK call).
- Autouse fixture clears all eight requested credential/config variables
  before every test and registers restoration even for variables created
  directly by _edl_login (EARTHDATA_USERNAME/PASSWORD).
- NOW_GCS_BUCKET is validated before Earthdata login for non-deduplicated
  assets. Regression test verifies a missing bucket causes zero login calls.

Commands/results:
- git rebase main: successful.
- $UV run pytest pipeline/tests/test_fetch_opera.py -q: 15 passed.
- $UV run pytest pipeline/tests live/tests ml/tests -q: 268 passed,
  27 warnings (raster georeferencing and dependency deprecations).
- $UV run ruff check .: All checks passed.

The original OPERA commit is already on main behind two other-unit commits;
this fix is a separate commit rather than amending unrelated HEAD history.
EARTHDATA_TOKEN-first auth wired everywhere in scope; EDL_USER/EDL_PASS and
~/.netrc fallbacks preserved; clean RuntimeError when nothing set. Committed
on branch `unit/edlauth` as `e83bab6` (amended once for the review
NEEDS-FIX; no push, no merge).

## Review round 2 (NEEDS-FIX — credential-leak sanitization)

- `fetch/edl.py::earthaccess_login` no longer interpolates the raw
  exception (`{e}`) into the raised RuntimeError — an underlying auth
  error can echo credentials. It now raises a fixed
  "Earthdata login failed: check EARTHDATA_TOKEN, EDL_USER/EDL_PASS, or
  ~/.netrc credentials" message, still chained `from e` for tracebacks.
- Committed as `fa749fd` on `unit/edlauth` (small follow-up commit — HEAD
  was the `8e8b6ce` report commit; amending it would have folded a source
  change into a docs commit).
- `ruff check .` clean; `pytest pipeline/tests -q` — **211 passed, 1
  failed**: `test_fetch_opera.py::test_fetch_scenes_download_ingests`,
  which is NOT mine and NOT caused by this change (verified by stashing
  the edit — it still fails). Cause: cross-unit test-isolation bug —
  `test_ingest.py` imports real `fetch.ingest` at collection time, binding
  it as a `fetch` package attribute, which defeats `opera._ingest()`'s
  `from . import ingest` + the test's `sys.modules["fetch.ingest"]` fake.
  Already fixed upstream: `e52403d "Keep OPERA ingest mocks isolated from
  cached package imports"` on main / unit/nisar-update / unit/opera-dswx.
  This worktree predates that merge; not cherry-picked (out of scope).
# implement — Modo Agora inputs (IMERG Early, SMAP L4, GEOS-FP)

Branch `unit/now-inputs`, worktree `now-inputs`. Scope per the interface update:
three `python -m` fetch modules + one offline test file. No tools/ file, no
now_inputs.py, no live/ edits. Not pushed, not merged.

## Files created (4)

| File | Lines | Role |
|---|---|---|
| `pipeline/fetch/imerg.py` | ~440 | GPM_3IMERGHHE v07 -> daily mm/day + rolling 3-day COGs -> `now/imerg_early` |
| `pipeline/fetch/smap.py` | ~330 | SPL4SMGP v008 sm_rootzone_wetness -> regular-grid daily COG -> `now/smap_l4` |
| `pipeline/fetch/geosfp.py` | ~380 | GMAO GDS `.ascii` fcast PRECTOT -> per-lead-day COGs D+1..D+3 -> `now/geosfp` |
| `pipeline/tests/test_fetch_now.py` | ~620 | 28 offline tests (fake earthaccess, rasterio, requests, ingest) |

Not modified: `.gitignore` (see Open items), any existing file.

## Interface, as implemented

- CLI per module: `--date`, `--bounds W S E N` (default `NOW_BOUNDS` = v4 manifest
  bounds -58,-34,-48,-22.5), `--days`, `--out-dir`, `--assets-json`, `--bucket`
  (env `NOW_GCS_BUCKET`, default `tipping-point-data`), `--project` (env
  `GEE_PROJECT`), `--dry-run`, `--no-download`, `--no-ingest`, `--force`.
- Collections: `projects/<GEE_PROJECT>/assets/tipping-point/now/{imerg_early,smap_l4,geosfp}`.
- Asset ids: `.../<collection>/<YYYYMMDD>` (imerg 3-day: `<YYYYMMDD>_3day`;
  geosfp tag = forecast valid date).
- Outputs: `data/now/cog/<leaf>/<leaf>_<tag>.tif`, EPSG:4326 float32, NaN nodata.
- Idempotency: `data/now/assets.json`, read by a schema-tolerant scan of
  `asset_id`/`asset`/`id` keys; matching ids skip unless `--force`.
- Auth split: CMR search is anonymous in all three; `download_granules` is the
  only place that calls `earthaccess.login`. Order: `EARTHDATA_TOKEN` bearer →
  `EDL_USER`+`EDL_PASS` pair (mapped onto `EARTHDATA_USERNAME`/`PASSWORD`,
  partial pairs rejected — same rule as `fetch/opera.py`, review fix) →
  `~/.netrc`. Clean `RuntimeError` otherwise. `GOOGLE_APPLICATION_CREDENTIALS`
  is read by Google's libraries, not by these modules.
- Ingest contract: `stage_to_gcs(path, bucket=, case='now', name=)`,
  `ingest_asset(uri, asset_id=, kind='image', force=)`,
  `append_manifest('now', entry)`. Looked up best-effort by module name at import;
  if absent, status is `ingest_unavailable` and `--dry-run` / `--no-download` /
  `--no-ingest` still work.

## Math (covered by tests)

- IMERG: daily = sum(mm/h half-hourly) * 0.5. 3-day = rolling sum over 3
  calendar days, min_periods=3, so the first two days are NaN. Test: 2 mm/h
  gives 48 mm/day and 144 mm rolling. Partial last day counted (24 steps -> 24 mm).
- SMAP: EASE 2-D cell lat/lon scattered -> regular 0.09 deg grid via linear
  griddata with nearest fill. Coverage is gridded over all source cells, so a
  hole stays NaN. Mirrors the `run_now` rule.
- GEOS-FP: PRECTOT (mm/s) * 3600 summed per UTC day. Newest run whose coverage
  reaches end of D+N (last step >= 23:00). Runs that are too short fall back to
  older runs. Time decode and parsers are the live/fetch.py logic, condensed.

## Commands run

- `$UV sync` — resolved 141 packages, ok.
- `$UV run pytest pipeline/tests/test_fetch_now.py -q` — first run: 5 failed,
  21 passed. Fixed: fake rasterio never created the output file (test fake, now
  touches on close), `_gds_vector` missing trailing newline (test fake), and
  SMAP coverage mask computed over valid points only (real bug, now gridded
  over all source cells). Rerun: **26 passed**; **28** after the review fix
  (+ partial-creds rejection + netrc fallback tests).
- `$UV run ruff check .` — first run: 8 errors (unused noqa x4, unused unpacked
  vars x3, missing `check=` x1). All fixed. Rerun: **All checks passed**.
- `$UV run pytest pipeline/tests -q` — **119 passed** (full suite, no regressions).
- Review NEEDS-FIX applied + amended (`128844d` → `d782095`): `_edl_login` in
  imerg.py/smap.py now requires the full `EDL_USER`+`EDL_PASS` pair before
  mapping (partial creds rejected, `~/.netrc` fallback added), mirroring
  `fetch/opera.py` on unit/opera-dswx.
- `$UV run python -m pipeline.fetch.{imerg,smap,geosfp} --dry-run --date 2026-11-14`
  — each prints valid plan JSON, rc=0.
- `python -m pipeline.fetch.imerg --no-download` with no GEE_PROJECT — clean
  `RuntimeError: set GEE_PROJECT or pass --project`, raised before any network call.

## Not verified (no network, no creds)

- Real CMR short names/versions (GPM_3IMERGHHE/07, SPL4SMGP/008) and the
  SMAP `native-id` date format were not checked online. Test fakes assume them.
- The GEOS-FP `.ascii` path is the live/fetch.py logic, copied; not run against
  the live server.
- rasterio COG driver path is covered only by the GTiff-fallback fake.
- No earthaccess download, no GCS staging, no GEE ingest was exercised. Those
  need real credentials and unit/ingest.

## Open items / deviations

1. **`.gitignore` not changed.** `now.yml` runs `git-auto-commit` with
   `file_pattern: data/now/**`. The default `--out-dir` writes COGs to
   `data/now/cog/<leaf>/`, so a CI run would commit those rasters. Needs one of:
   `data/now/cog/imerg_early/`, `data/now/cog/smap_l4/`, `data/now/cog/geosfp/`
   added to `.gitignore`, or the workflow `file_pattern` narrowed. I did not do
   this because it is outside the file scope. Owner: lead-live or conductor.
2. **Ingest module name is a guess.** `_INGEST_CANDIDATES` tries
   `tp.ingest`, `tools.ingest`, and a few variants. Once unit/ingest lands, replace
   this with the real import path.
3. **Duplicated plumbing.** `write_cog`, `manifest_asset_ids`,
   `stage_and_ingest`, `_load_ingest`, and `_resolve_*` are copied across the
   three modules. Moving them into one shared helper module would be cleaner,
   but that would be a fourth file, which the scope excluded.
4. **Flag coupling.** `imerg.py` duplicates `run_now.py` logic in
   `_subset` and the 0.5 factor. This is intentional so the module has no import
   from `live/`.
pipeline/fetch/ingest.py, pipeline/fetch/opera.py,
pipeline/tests/test_ingest.py, .herdr/implement.md
EE init via shared `gee.common.init` (SA creds from
GCP_SA_KEY/GEE_SERVICE_ACCOUNT_KEY or ADC via
GOOGLE_APPLICATION_CREDENTIALS); `data/<case>/assets.json` upserts keyed
by name; `_is_not_found` = status/code first, text fallback with auth
veto.

## Addendum — ruling L9 (bounds default source)

- `--bounds` default is no longer the constant: each module resolves the AOI
  lazily via `now_bounds()` → `tp.config.now_bounds()` (config.yaml
  `domains.now`), falling back to the bundled `NOW_BOUNDS` while the helper
  doesn't exist here. `parse_args` uses `default=None`; `main`, `run` and
  `plan` all resolve `None` → `now_bounds()`, so programmatic callers get the
  same default. `--bounds` still overrides. New test
  `test_now_bounds_resolution` covers fallback, config-wins and CLI override.
- Branch was already rebased onto current main (my commit is `f52fe7c`,
  followed by report commit `cddea0b`), so the L9 change went in as a NEW
  commit — amending would have folded the shared implement.md report commit
  into mine.
- **Pre-existing failure, not mine:**
  `pipeline/tests/test_fetch_nisar.py::test_execute_requires_creds` —
  `fetch/nisar.py:365` calls `tp_nisar._edl_credentials`, removed by edlauth's
  `d2ccd35` (`fetch/edl.py` now owns EDL auth). unit/nisar-update drift,
  fails without my change. Owner: lead-gathering to route a fix.
- Since the rebase, `fetch/ingest.py` + `fetch/edl.py` exist:
  `_INGEST_CANDIDATES` resolves the real contract (`fetch.ingest` /
  `pipeline.fetch.ingest`) and my `_edl_login` duplicates `edl.earthaccess_login`
  semantics — a future cleanup could swap to `fetch.edl` (left as-is: out of
  the L9 scope).

---

## unit/nowbounds — drop NOW_BOUNDS fallback (2026-11-14, devin)

Conductor ruling: tp.config.now_bounds() exists on main (unit/domains), so
the fetch modules' bundled constant + ImportError fallback are dead weight.

### Changed
- `pipeline/fetch/imerg.py`, `pipeline/fetch/smap.py`,
  `pipeline/fetch/geosfp.py`: removed `NOW_BOUNDS` constant (+ its comment
  block) and the `try/except ImportError` fallback in `now_bounds()` — now
  imports and returns `tp.config.now_bounds()` directly; docstrings updated.
  `--bounds` still overrides (unchanged call sites).
- `pipeline/tests/test_fetch_now.py::test_now_bounds_resolution`: dropped
  the "helper absent -> bundled fallback" leg; keeps the config-wins leg
  (monkeypatched `tp.config.now_bounds`) and the `--bounds` override leg.
- Not touched: `live/config.py` (lead-live's), `test_domains.py`
  KNOWN_MIRRORS (per ruling — tolerated entries stay listed).

Diff: 4 files, +9/-46. Commit `acbf002` on `unit/nowbounds`.

### Commands run
- `$UV sync` — resolved 141 pkgs, installed 119 (uv 0.13.8, mise path).
- `$UV run pytest pipeline/tests -q` — **294 passed**, 9 warnings, 11s.
- `$UV run ruff check .` — **All checks passed!**
