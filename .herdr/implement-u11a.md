# U11a — SWOT fetch — implementation report

Data-plan U11a per hc brief. Scope respected: `pipeline/tp/swot.py`,
`pipeline/scripts/05_swot.py`, `pipeline/tests/test_swot.py` (new), this
report. No events files touched, no helpers spawned, no pushes.

## Changes

### `pipeline/tp/swot.py`
- Kept the Hydrocron timeseries fetch (`soto.podaac.../hydrocron/v1/timeseries`,
  `feature=Reach`, public/no-auth, `output=csv`,
  `fields=reach_id,time_str,wse`, 60 s timeout). Refactored into
  `fetch_reach(reach_id, start, end) -> df`.
- Output records now carry `river_m` (wse renamed, meters) after dropping
  fill values (`wse <= -1e9`), plus `reach_id` and `time_str` — feeds the
  `imerg_smap_daily` series merge.
- `river_series(reach_ids, start, end, out_path)` — fetches each reach,
  `pd.concat`, `sort_values("time_str")` (ISO strings sort lexically), writes
  `orient="records"` JSON.
- `series_path(event_id)` — `data/<case>/series/<caso>_swot_<AAAAMMDD>.json`
  via `event_data_dir` + `artifact_stem(event_id, 'swot')`, fixing the
  `swot_river.json` naming violation (verified:
  `wa_2025_swot_20251210.json`). Per ruling L4 the `<case>` dir comes from
  `event_data_dir` (sibling unit); my tests assert only filename + the
  `series/` parent, so they hold under both layouts.
- `resolve_reach_ids(cli_ids, event)` — CLI values win; else
  `event['swot']['reach_ids']`; missing/null/empty -> `ValueError` with a
  message naming the fix (pass `--reach-id` or add the `swot` block to
  `events/<id>.json`).

### `pipeline/scripts/05_swot.py`
- `--reach-id` now optional + repeatable (`action="append"`); defaults to
  `event['swot']['reach_ids']`.
- `resolve_reach_ids` ValueError is routed through `ap.error` -> usage +
  clear message on stderr, exit 2 (no traceback):
  `no SWOT reach ids: pass --reach-id or add 'swot': {'reach_ids': [...]}
  to events/wa-2025-12.json`
- Output path via `series_path` (was `swot_river.json`); multiple reaches
  merged sorted by `time_str` inside `river_series`.

### `pipeline/tests/test_swot.py` (new, 6 tests, all offline)
- Fixture CSV text -> parsed df (columns `reach_id,time_str,river_m`).
- Fill-value drop (`-999999999999` row removed).
- Naming via `artifact_stem` (`wa_2025_swot_20251210.json` under `series/`).
- Multi-reach merge: `requests.get` monkeypatched per `feature_id`; merged
  df monotonic on `time_str`, both reaches present, JSON round-trips.
- Missing `reach_ids` error path: real `wa-2025-12` event (no `swot` block),
  `{'swot': {'reach_ids': []}}`, `{'swot': None}` -> ValueError.
- Default/override: `swot.reach_ids` used when no CLI id; CLI wins otherwise.

## Commands run & results

- `uv sync` — ok (created `.venv`, installed 118 pkgs, incl. editable
  `tipping-point-pipeline`).
- `uv run pytest -q` (repo root: pipeline + live + ml) — **52 passed**
  (46 baseline + 6 new), 9 pre-existing warnings, 0 failures.
- `uv run ruff check pipeline/` — **All checks passed** (after `--fix`
  import sort in the new test file).
- `uv run python pipeline/scripts/05_swot.py --event wa-2025-12` — clean
  `error: no SWOT reach ids: ...` message, exit 2 (wa event has no `swot`
  block yet, as expected until the events worker adds it).
- `ruff format --check pipeline/` — NOT clean repo-wide (19 pre-existing
  files would be reformatted, including untouched ones); repo style is not
  ruff-format, so `ruff check` is the applied gate. My files follow existing
  style.

## Notes

- Note: `pandas.read_csv` parses the numeric reach ids as int64 in output
  records (leading zeros lost if the API ever emits them); `time_str` +
  `river_m` (what the merge consumes) are unaffected.
- Empty `data/wa-2025-12/{cog,geojson,series}` dirs may exist locally from
  `event_data_dir` during tests — empty, git-invisible.
- No new deps; requests/pandas already in pyproject.
