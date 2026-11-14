# U27 — NISAR 14-day pass list in the `now` job (S8)

## What changed

- `live/fetch.py`: added a CLI (`python -m live.fetch nisar-passes
  --bounds W S E N --days 14 --out <path> [--manifest <path>]`).
  `_nisar_passes_cli` wraps `nisar_recent_passes()` in try/except: on any
  failure (asf_search missing, network down) it logs a WARNING to stderr,
  writes `[]` to `--out`, and still exits 0 — the pass list can never fail
  the job. `--manifest` additionally patches the `nisar_passes` key of an
  existing data/now/manifest.json (the key the Agora card reads; the root
  manifest copies the now node verbatim via tp.manifest.build_root).
- `live/jobs.py` `now()`: new step `now.nisar_passes` after the three fetch
  steps, before `now.tpi`. Writes `data/now/nisar_passes.json` (ships in the
  fallback export regardless) and updates `data/now/manifest.json`
  nisar_passes. Imported `NISAR_LOOKBACK_DAYS` from live.config; bounds come
  from `NOW_BOUNDS` (tp.config.now_bounds).
- `live/tests/test_jobs.py`: updated the two locked-order assertions to
  include `live.fetch` (nisar-passes) between geosfp and now_tpi; added
  `test_nisar_passes_cli_never_fails` (search raises -> writes [], updates
  manifest to [], rc=0) and `test_nisar_passes_cli_writes_passes`.

## Commands run

- `$UV run pytest live/tests -q` -> 106 passed
- `$UV run ruff check live/` -> All checks passed!
- `$UV run python -m live.jobs now --dry-run` -> shows `[now.nisar_passes]
  python -m live.fetch nisar-passes --bounds -58.0 -34.0 -48.0 -22.5
  --days 14 --out .../data/now/nisar_passes.json --manifest
  .../data/now/manifest.json` between geosfp and now.tpi

## Notes

- Manifest wiring: `tools/build_manifest.py` -> `tp.manifest.build_root`
  copies `data/now/manifest.json` verbatim into `cases.now`, so updating
  that file's `nisar_passes` key propagates to the root manifest. The
  standalone `data/now/nisar_passes.json` is also written so the file ships
  via export_fallback even if the manifest is absent/stale.
- Edit kept to the `now()` step list + one import; `_refresh_alerts`
  untouched (u-quiet-hours unit owns it).
