# implement — U11c HLS post-event scene fetch

## Changed (new files, branch unit/u11c)

- `pipeline/tp/hls_fetch.py` — earthaccess search/download for HLS v2.0
  scenes over an event's AOI for post-event dates.
  - `SHORT_NAMES = ("HLSL30_2.0", "HLSS30_2.0")` — configurable;
    `_collection_ref()` splits "<SHORT>_<VER>" into CMR short_name+version
    (e.g. `HLSL30_2.0` -> `HLSL30`/`2.0`); `_verify_collections()` confirms
    each collection exists in CMR at run time (`search_datasets` count=1),
    raising RuntimeError listing unresolved specs.
  - `resolve_window(event, after, before)` — post-event window defaults to
    `reference_date .. window.end`, per-date overrides.
  - `search_granules(...)` — one `search_data` call per short_name over
    `aoi.bbox`+temporal, merged and sorted by cloud cover ascending
    (missing last, then date); `limit` keeps the N least-cloudy.
    **No credentials needed** — CMR granule/collection search is public
    (review fix r1: login moved out of the search path).
  - `fetch_scenes(..., download=True)` — records + per-granule download to
    `data/raw/hls/<event_id>/<granule>/` (gitignored via `data/raw/`).
    `_edl_login()` is called only on this download path.
  - Record schema (documented in module docstring, consumed by the later
    hls_scar unit): `{granule, date, cloud_cover, local_path}` — granule =
    CMR native-id, date = `YYYY-MM-DD` (TemporalExtent start, native-id
    YYYYDDD fallback), cloud_cover = UMM CloudCover / AdditionalAttributes /
    properties (null if absent), local_path = download dir or null.
  - Auth: `EDL_USER`/`EDL_PASS` mapped to `EARTHDATA_USERNAME/PASSWORD`
    (same convention as `live/fetch.py`) or `~/.netrc`, resolved at
    download time only; earthaccess is imported inside functions only —
    module/`--help`/`--dry-run`/search need no creds. Missing creds on a
    download -> clean `RuntimeError` naming both options (no interactive
    prompt: strategy is chosen from which credential source exists).
- `pipeline/scripts/10_hls_fetch.py` — thin CLI: `--event` required;
  `--after`/`--before`/`--limit`/`--no-download`/`--dry-run` optional.
  `--dry-run` prints resolved params (event, short_names, bbox, temporal,
  limit, download, out_dir) as JSON with no creds. Normal run prints the
  record list as JSON.
- `pipeline/tests/test_hls_fetch.py` — 11 offline tests: fake CMR granule
  dicts exercise `_collection_ref`, field extraction (native-id, date,
  cloud-cover incl. AdditionalAttributes + missing), sort order
  (least-cloudy first, missing last), `resolve_window` defaults/overrides
  from a fake event dict, plus subprocess `--help` and `--dry-run` runs.
  Auth-split tests (review fix r1): fake `earthaccess` in `sys.modules` +
  poisoned `_edl_login` prove `search_granules`/`download=False` never
  log in, and `download=True` logs in before `earthaccess.download`.

## Commands run

1. `uv sync` (mise uv 0.13.8 path) — env synced, no new deps.
2. `.venv/bin/python -m pytest pipeline/tests/test_hls_fetch.py -q` —
   11 passed.
3. `.venv/bin/python -m pytest -q` — 57 passed (46 existing + 11 new),
   9 pre-existing pandas warnings.
4. `.venv/bin/ruff check <3 new files>` — All checks passed.
   (`ruff format --check` not applied: 44 existing repo files are
   unformatted; style matches surrounding code.)
5. `.venv/bin/python pipeline/scripts/10_hls_fetch.py --event wa-2025-12
   --dry-run` — prints resolved params: short_names [HLSL30_2.0, HLSS30_2.0],
   bbox [-122.6,47.0,-121.5,48.5], temporal [2025-12-10, 2026-01-31].
6. Real CMR run, no creds (`env -u EDL_* -u EARTHDATA_* HOME=/tmp/nohome
   ... --no-download --limit 3`) — returned 3 real granules sorted by
   cloud cover (e.g. `HLS.L30.T10TET.2025362T184953.v2.0`, 2025-12-28,
   0.0), local_path null. Proves the unauthenticated-search fix end to end.
7. Same env, download path (`--limit 1`, no `--no-download`) — real CMR
   search succeeded unauthenticated, then `_edl_login()` raised the clean
   RuntimeError naming EDL_USER/EDL_PASS + ~/.netrc. Verified post-fix.

## Notes / follow-ups

- `earthaccess.search_data(short_name=..., version=...)` is used — CMR
  indexes HLS v2.0 as short_name `HLSL30`/`HLSS30` version `2.0`; passing
  the `_2.0` spec form raw returns zero hits, hence `_collection_ref`.
- `local_path` is the per-granule directory (HLS granules fan out to ~15
  band COGs + metadata), not a single file — glob bands inside it.
- Downstream `hls_scar` unit reads the printed JSON list directly.
