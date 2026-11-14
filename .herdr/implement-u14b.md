# U14b — L8 live refactor: GEE-canonical orchestration + local fallback

Branch `unit/u14b`, commit `67b2fcd`. Scope held to the listed files;
`.github/workflows/`, `pipeline/`, `gee/`, `tools/` untouched.

## What changed

- **`live/jobs.py` (new)** — argparse orchestrator with `now`, `nisar`,
  `validation`, `reference` subcommands; every subcommand has `--dry-run`.
  - `now`: runs the locked sequence via `[sys.executable, -m/-script]`
    subprocesses (fetch.imerg `--bounds W S E N --days 7` → fetch.smap
    `--bounds` → fetch.geosfp `--days 3` → `tools/now_tpi.py` →
    `tools/export_fallback.py --now`), then refreshes `data/now/alerts.json`
    from the exported bundle via `alerts.load_now_regions`, then runs
    `tools/build_manifest.py` as the final step (manifest LAST). Prints one line per step plus parsed
    `<asset> <date>` stdout lines (falls back to the step's last stdout
    line). Any nonzero exit → `SystemExit`, later steps skipped, data/
    untouched.
  - `nisar`: case keys from `data/manifest.json` `cases` (minus `now`) when
    present, else `['rs_2024','wa_2025']`; passes `--alerts-geojson
    data/now/alerts.json` only when the file exists.
  - `validation`: active = today within `[event_date-2d, event_date+45d]`;
    dates from `events/*.json` if present else `data/events.json`
    (`reference_date`, `start_date` fallback). event→case id mapping via
    root manifest `event_id` when available, else first two id tokens
    (`rs-2024-05` → `rs_2024`). Runs `fetch.opera --case` + `fetch.swot
    --case` per active case; prints `no active case` and exits 0 otherwise.
  - `reference`: `--kind glc --case wa_2025`, `--kind inventory --case
    rs_2024`, `--kind boundaries` (no case).
  - Subprocess env = inherited os.environ (EARTHDATA_TOKEN, GCP_SA_KEY,
    GOOGLE_APPLICATION_CREDENTIALS, GEE_PROJECT, NOW_GCS_BUCKET pass
    through).
- **`live/run_now.py`** — default now delegates to `jobs.main(["now"])`
  (forwards `--dry-run`); `--local` runs the pre-existing numpy pipeline
  verbatim (code untouched). `--help` works credentialess.
- **`live/config.py`** — added `GEE_PROJECT` (env, default
  `hermes-fesolucoes`), `ASSET_ROOT`, `NOW_COLLECTIONS`, `STATIC_DOMAIN`;
  nothing removed.
- **`live/build_static.py`** — added `--assets`/`--dry-run` and
  `build_assets(ee)`: creates `static/south_brazil/clim_rain` as an
  ImageCollection container (`ee.data.createAsset`), then 366
  `Export.image.toAsset` inserts `doy_001..doy_366` each carrying a `doy`
  property, reusing the existing `_climatology_for_month` ee computation
  (doy→month via leap-year 2000); plus `slope` and `hand` exports at 30 m
  from the existing `_terrain`. Population stays npz-only (not in the
  analysis asset list). Default npz build unchanged.
- **`live/alerts.py`** — added `load_now_regions(now_dir)`: scans data/now
  recursively for a class-bearing areas GeoJSON/JSON (critical/areas
  filenames first; alerts/manifest/summary excluded), returns features'
  props + geometry + class/label; else derives per-layer max-class regions
  from `summary.json` `classes_km2`/`class_counts` (top-level or under
  `layers`); else `[]`. Contains the required `L8: export shape pending`
  comment. Grid-based `emit_alerts` kept for `--local`.
- **Tests** — `live/tests/test_jobs.py` (new): locked step order, bounds
  args, abort-on-nonzero skips later steps, dry-run spawns nothing, nisar
  alerts flag conditional + manifest case keys, validation no-active /
  window edges / opera+swot per active case, reference kinds/cases, CLI
  dispatch. `test_build_static.py`: kept GPW tests, added fake-ee
  `build_assets` tests (366 doy inserts with `doy` props + slope/hand at
  correct scale/assetId, no pop asset, dry-run starts nothing).

## Commands run & results

- `$UV run pytest live/tests/ -q` → **51 passed** (was 41 before; +10 new)
- `$UV run ruff check live/` → **All checks passed**
- `$UV run python live/run_now.py --help` → OK (no creds)
- `$UV run python live/jobs.py --help` / `jobs.py validation --help` → OK
- `$UV run python live/jobs.py now --dry-run` → prints the 6 locked
  commands in order, no subprocesses spawned
- `$UV run python live/jobs.py validation` → `no active case`, exit 0
  (today 2026-11-14 is outside wa-2025-12's [2025-12-08, 2026-01-24]
  window)
- `$UV run python live/jobs.py reference --dry-run` → glc/wa_2025,
  inventory/rs_2024, boundaries (no case)
- `$UV run python live/run_now.py --dry-run` → delegates to jobs now
  --dry-run correctly
- Smoke-tested `load_now_regions` on a synthetic bundle → geojson features
  preferred, summary fallback works, empty dir → `[]`

## Notes

- `pipeline.fetch.{imerg,smap,geosfp,nisar,opera,swot,reference}` and
  `tools/{now_tpi,export_fallback,build_manifest}.py` do not exist yet in
  this worktree — they are other leads' deliverables; the orchestrator
  only invokes them per the locked CLI contract.
- Lead update applied: the critical-areas filename is not pinned, so
  `load_now_regions` tolerantly scans data/now for any class-bearing
  JSON/GeoJSON before falling back to summary class counts.
- Review fix (lead-live blocker): `build_manifest` is now the final `now`
  step, after the alerts refresh. Any failure in an earlier step (or in the
  alerts refresh) skips the manifest, so the last-good manifest is kept.
  Covered by `test_now_manifest_is_last_after_alerts_refresh` and
  `test_now_failure_before_alerts_skips_alerts_and_manifest`.
