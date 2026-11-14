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

## Commands run

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

## Commands run

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
