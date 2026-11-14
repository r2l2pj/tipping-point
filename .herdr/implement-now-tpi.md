# implement-now-tpi — report (unit NOW-TPI, L8)

Branch `unit/now-tpi`, worktree `nowtpi`. Scope: `gee/now_tpi.py`,
`tools/now_tpi.py`, `gee/tests/test_now_tpi.py` (all new). No other files
touched. No push. No helpers spawned.

## Review revision (supersedes earlier design decision 1)

Lead requested fixes to e53e426; these checks ran on e53e426 plus the
revision diff committed with this report. Scope explicitly expanded by
lead to `pipeline/tp/metrics.py`.

- Removed all production `unittest.mock` usage. `tp.metrics.merge_metrics`
  now explicitly accepts the live manifest node `now`, documented in its
  docstring, without relaxing validation for other names. Real tmp-file
  merge test verifies `cases['now']` and preservation of another case.
- Each fixed derived asset id is deleted before its toAsset export.
  Only not-found/404 errors are ignored; permission and other errors abort.
  Tests assert deletion precedes export, not-found permits export, and
  permission failure prevents export. Dry-run also describes deletion.
- No live assets were deleted and no live exports were run.

Commands (uv is the exact requested binary):

```text
$HOME/.local/share/mise/installs/uv/0.13.8/uv-x86_64-unknown-linux-gnu/uv run pytest gee/tests pipeline/tests -q
206 passed in 6.07s

$HOME/.local/share/mise/installs/uv/0.13.8/uv-x86_64-unknown-linux-gnu/uv run ruff check gee/now_tpi.py tools/now_tpi.py gee/tests/test_now_tpi.py pipeline/tp/metrics.py
All checks passed!

env -u GEE_PROJECT -u GCP_SA_KEY -u GEE_SERVICE_ACCOUNT_KEY $HOME/.local/share/mise/installs/uv/0.13.8/uv-x86_64-unknown-linux-gnu/uv run python tools/now_tpi.py --dry-run
exit 0; printed planned reads, writes, and cases['now'] metrics
```

## What was built

- `gee/now_tpi.py`
  - Pure tag logic: `daily_tags`, `select_obs_rain3` (newest `_3day`
    product, else 3 newest dailies), `forecast_leads` (3 newest GEOS-FP
    valid dates), `day_plans` (per-day source plan for k=0..3).
  - d0 = observed rain3 from IMERG Early + SMAP L4 latest.
    d1..d3 = GEOS-FP valid-date tags, rain3 blends `3-k` most recent observed
    dailies with the first `k` leads (same window as `live/tpi.py`
    `projected_rain3`). SMAP L4 is held at its latest tag for every day.
  - Scoring reuses `gee.tpi.rain_score`, `gee.tpi.geomean` (config
    `tpi_weights`), `gee.tpi.SLOPE_FLOOD_MAX_DEG`, and config thresholds
    `soil_wetness_range`, `slope_range_deg`, `hand_range_m`.
  - Climatology: `static/south_brazil/clim_rain` filtered by `doy`, bands
    p90/p95/p99. Terrain from `static/south_brazil/{slope,hand}` (not
    NASADEM-derived as in `gee.tpi`).
  - 16 single-band Images `now/{tpi_slide,tpi_flood,r,s}_d{0..3}`, each
    `.set()` with `data_date` (YYYY-MM-DD of the driving source) and
    `updated_at` (ISO UTC).
  - `run()`: `common.init`, compute, `toAsset` export per image, then
    metrics merge.
  - Metrics: `merge_metrics` under `cases['now']` with `tpi_now_computed_at`,
    `tpi_now_dates` (4 dates), `forecast_source='geos-fp'`, and
    `collections` (input and derived asset ids).
  - `main()`: `--dry-run` (offline, no `ee` calls), `--no-metrics`,
    `--project`, `--metrics-path`.
- `tools/now_tpi.py`: thin wrapper for `now.yml`. It puts repo root and
  `pipeline/` on `sys.path`, then calls `gee.now_tpi.main`.
- `gee/tests/test_now_tpi.py`: 14 tests with the `Fake` recorder from
  `conftest.py`.

## Design decisions to review

1. **`cases['now']` vs `tp.config`.** `now` is not a `config.yaml` case, so
   `merge_metrics`'s `case_id`/`event_id` lookups would raise. I pin both
   to return `"now"` only inside `_merge_now_metrics` (a `patch.object` on
   `tp.metrics`). `config.yaml` is untouched. Alternative: add a `now`
   resolver to `tp.metrics`. That would touch another unit's file, so I did
   not.
2. **Input tag format.** The now/ input assets follow `unit/now-inputs`
   (`pipeline/fetch/{imerg,smap,geosfp}.py`): tags are `YYYYMMDD`, and IMERG
   also has `YYYYMMDD_3day`. The geosfp tag is the forecast valid date.
3. **Static asset names** `static/south_brazil/{clim_rain,slope,hand}` are
   taken from the task spec. I did not verify they exist on `unit/now-inputs`
   or `unit/ref-assets`. Dry-run lists them as reads.
4. **Export scale** is 1000 m (`SCALE_M`), matching `gee.tpi`.
   `static/south_brazil` slope/HAND are ~30 m. The export resamples to
   1 km, which is the now/ grid. Confirm this is what the app expects.
5. **AOI** is hard-coded to `live/config.NOW_BOUNDS` (Sul do Brasil), since
   `common.case_cfg` only covers the two cases.

## Commands run

Ruff, pytest, and dry-run used
`$HOME/.local/share/mise/installs/uv/0.13.8/uv-x86_64-unknown-linux-gnu/uv`.

- `uv run pytest gee/tests/test_now_tpi.py -q`
  - 14 passed.
- `uv run ruff check gee/now_tpi.py tools/now_tpi.py gee/tests/test_now_tpi.py`
  - Before fixes: 9 errors (import sort, FURB192, UP037, RUF100 x3, ISC004,
    PERF102 x2). After fixes: "All checks passed!"
- `uv run pytest -q` (full suite)
  - 247 passed, 1 failed:
    `pipeline/tests/test_validation.py::test_edl_token_env_wires_bearer_header`.
  - It passes alone. It also fails with `gee/tests/test_now_tpi.py`
    excluded (233 passed, 1 failed). My three files are untracked and no
    pipeline test imports them, so this is a pre-existing order-dependent
    failure, not caused by this unit. I did not investigate it further.
- `env -u GEE_PROJECT -u GCP_SA_KEY -u GEE_SERVICE_ACCOUNT_KEY uv run python tools/now_tpi.py --dry-run`
  - exit 0. Prints 3 collection reads, 3 static reads, 16 writes, and the
    metrics plan, with no credentials and no `ee` calls.

## Not verified

- No live EE run. Export and getMapId behaviour is only exercised through
  the fake recorder.
- `tools/now_tpi.py` is not run from `now.yml` yet. The workflow still calls
  `live/run_now.py`. Wiring it in is a separate change.
