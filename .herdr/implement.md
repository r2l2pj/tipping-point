# implement — ingest (GEE asset ingest unit, t9r)

## Changed

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
