# U28 — S17, S19, S20, N1 (A8 dropped: already landed on main in 8d7b34b)

## Changes
- **S17** `.env.example`: dropped EDL_USER/EDL_PASS. Now lists EARTHDATA_TOKEN (canonical),
  DATABASE_URL (unset = .data/tipping-point.sqlite; `sqlite:<path>` or `postgres://` Neon),
  GEE_PROJECT, GOOGLE_APPLICATION_CREDENTIALS (local path) / GCP_SA_KEY (JSON contents, CI).
  All values are empty.
- **S19** Removed `GCS_BUCKET` from live/config.py, `upload_gcs` (plus the gs:// docstring and
  os/subprocess imports) from live/publish.py, and the step-6 docstring line, `--no-upload`
  flag, NOW_GCS_BUCKET upload call and `import os` from live/run_now.py. live/publish.py stays
  because `write_outputs` is still used by run_now and its tests. test_run_now no longer
  passes `--no-upload`. pipeline/fetch/ingest.py was not touched (out of scope).
- **S20** live/config.py: `STATIC_DOMAIN = _now_name()` uses `tp.config.now_name()` (already on
  main; lazy-import pattern like now_bounds), so it no longer hard-codes either spelling. On main
  config.yaml now says `south_brazil` and gee/now_tpi.py already reads now_name(), so live and gee
  now resolve to the same `static/<name>/` path. The build_static `--assets` help text uses
  STATIC_DOMAIN. pipeline/tests/test_domains.py adds tests for now_name() (main had none).
- **N1** .github/workflows/now.yml header: "every 3 h" -> "every 6 h".

## Tests added
- pipeline/tests/test_domains.py: now_name reads config.yaml; missing name raises.
- live/tests/test_config.py (new): STATIC_DOMAIN == config.yaml domains.now.name; no
  GCS_BUCKET; .env.example has the 5 vars, no EDL_*, no non-empty values.
- live/tests/test_run_now.py: `--no-upload` rejected, no upload_gcs, no NOW_GCS_BUCKET in run_now.
- live/tests/test_workflows.py: now.yml header says 6 h.

## Commands
- `$UV run pytest live/tests pipeline/tests gee/tests -q` -> 591 passed (rebased on main 5237b68)
- `$UV run ruff check live/ pipeline/` -> All checks passed!
