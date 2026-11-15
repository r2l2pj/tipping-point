# G4+G5 — SMAP data date + stale-Now watchdog alarm

Branch: `live-g45` (worktree live-g45). No push/merge — lead-live reviews.

## What changed

### G4 — `data_dates.smap` as a real date + latency label

- `live/fetch.py` — new pure `smap_date_fields(granule, now_utc) -> dict`
  (stdlib only: `re`, `datetime`, `zoneinfo`):
  - `smap`: ISO UTC `"YYYY-MM-DDTHH:MM:SSZ"` parsed from the granule
    native-id's `_YYYYMMDDTHHMMSS_` segment (`_SMAP_TS_RE`); falls back to
    the raw granule string when the segment is absent or `strptime`
    rejects it (e.g. day-of-month 40)
  - `smap_granule`: file name with the CMR `_<hash>` suffix after `.h5`
    stripped (`_SMAP_CMR_SUFFIX_RE`), e.g.
    `SMAP_L4_SM_gph_20261110T223000_Vv8013_001.h5`
  - `smap_age_days`: whole days between granule stamp and `now_utc`,
    counted on America/Sao_Paulo calendar days (None when unparseable)
  - `smap_label`: pt-BR `SMAP de dd/mm, hoje|1 dia|N dias`
- `live/run_now.py` — computes `smap_fields` once right after
  `fetch_smap_l4` and splats it into `data_dates` of BOTH summary.json and
  manifest.json (one source, no drift)
- `data/SCHEMA.md` — `data_dates` bullet documents the four smap_* keys;
  the `data/now/manifest.json` and `summary.json` examples updated
- `live/tests/test_fetch.py` — 5 new tests: ISO parse + CMR suffix strip,
  age 0 (`hoje`) / 1 (`1 dia`) / 5, BRT day boundary (2026-11-15T02:00Z is
  still 14/11), unparseable keeps raw string, `.h5` without CMR suffix

### G5 — now-watchdog stale-data alarm (L13b)

- `live/staleness.py` — new stdlib-only module, `python -m live.staleness
  [--max-age-s N] [manifest.json]` (file arg or stdin; `--max-age-s`
  defaults to `$STALE_AFTER_S` or 21600). Exit 1 + `::error::Now data
  stale: updated_at <ts> is <h> h old (> 6 h, L13b)` when the published
  `updated_at` is too old; missing/invalid `updated_at` or unreadable JSON
  also exits 1 with a clear `::error::`
- `.github/workflows/now-watchdog.yml`:
  - new `stale_after_s` workflow_dispatch input (default `"21600"`)
  - new steps BEFORE dispatch: `actions/checkout@v4` +
    `actions/setup-python@v5` (3.12) — needed for `git fetch` +
    `python -m live.staleness`
  - new FINAL step (after dispatch, `if: always()`) that does
    `git fetch --depth 1 origin fallback` then pipes
    `git show FETCH_HEAD:data/now/manifest.json` into the module — git,
    not the raw CDN (no propagation lag). Job failure emails the owner;
    that is the alarm.
- `live/tests/test_workflows.py` — `test_now_watchdog_dispatches_stale_
  now_runs` now locates the dispatch step by content (steps[0] is now
  checkout); new `test_now_watchdog_stale_alarm_after_dispatch` asserts
  the stale step exists, is the LAST step, runs `git fetch` +
  `FETCH_HEAD:data/now/manifest.json`, carries `STALE_AFTER_S` 21600, and
  that the `stale_after_s` input default is 21600
- `live/tests/test_staleness.py` — 9 tests: fresh, stale message format,
  exact boundary (> not >=), missing/empty/non-str/non-dict `updated_at`,
  invalid timestamp, naive timestamp treated as UTC, `main()` via file,
  stdin, bad JSON, `$STALE_AFTER_S` env

## Commands run + results

- `uv run --no-sync pytest -q live/tests/test_fetch.py
  live/tests/test_staleness.py live/tests/test_workflows.py
  live/tests/test_run_now.py` → 68 passed (1 arithmetic slip in my own
  new test, fixed)
- `uv run --no-sync pytest -q pipeline/tests live/tests ml/tests`
  (PYTHONPATH=pipeline:.) → **765 passed**, 0 failed
- CLI smoke:
  `echo '{"updated_at":"2020-01-01T00:00:00Z"}' | python -m live.staleness`
  → `::error::Now data stale: ... is 60251.1 h old (> 6 h, L13b)`, exit 1;
  fresh manifest → "within 6 h — OK", exit 0
- `git status -- data/` after the suite: clean — nothing needed restoring
  this run (no `git checkout -- data` required; verified)

## Not touched (per task scope)

`live/notify.py`, `live/alerts.py`, `.github/workflows/now.yml` (parallel
worker), no new deps, no credentials.
