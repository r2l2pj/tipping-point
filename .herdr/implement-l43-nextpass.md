# L43-1: next NISAR pass for the Now domain

Branch `l43-nextpass`, commit `2da093b` (not pushed). Deadline 12:30 BRT — committed ~11:20 BRT.

## What changed

- **`live/nisar_next.py` (new)** — `python -m live.nisar_next --now-dir data/now`.
  - Pure fns: `next_pass(last_utc, now, repeat_days=12)` (smallest last+n*12 strictly
    > now, n>=1) and `covers(footprint, munis)` (sorted IBGE codes whose mesh
    intersects the footprint).
  - `load_recent_passes`: reads `nisar_passes.json`; falls back to
    `manifest.json["nisar_passes"]` because `jobs.py now` only writes the file on
    the GEE path — on `NOW_LOCAL=1` (what now.yml runs) the list lives in the
    manifest only.
  - `latest_frames`: `pipeline.fetch.nisar.search_gcov_domain` over NOW_BOUNDS for
    `NISAR_L2_GCOV_V1` + `NISAR_L2_GCOV_BETA_V1` (45 d window), newest granule per
    (track, look, frame); `get` injectable for tests.
  - `build`: per frame, anchor = newest CMR start, refreshed by a newer L0
    overpass on the same track/look (GCOV has multi-day latency; the L0 pass is
    itself observed). Emits the contract envelope verbatim
    (repeat_days=12, method string, passes sorted soonest first with
    utc/track/direction lowercased/frame/last_observed_utc/covers).
  - `inject_zones`: stamps `next_nisar_pass_utc` + `next_nisar_track` on every
    feature of `geojson/priority_zones.geojson` AND
    `response/priority_zones.geojson` (soonest pass whose footprint intersects;
    explicit null when none covers it). Rewrites compact like
    `tp.response_summary._write_fc`, atomic tmp+replace.
  - `inject_alerts`: same two fields on `alerts.json` `active[]` — footprint over
    the alert centroid first, else over its municipality mesh (name+uf match).
  - `run`: writes the product (indent=2, ensure_ascii=False) then guards each
    stamp separately — a bad geojson never costs the product. Build failures
    propagate; the workflow step is continue-on-error.
- **`live/now_manifest.py`** — added `"nisar_next_passes": "nisar_next_passes.json"`
  to `OPTIONAL_PRODUCTS` (auto-spread by `run_now.build_manifest`, pruned by
  `finalize` when the file is absent).
- **`.github/workflows/now.yml`** — new step "Next NISAR passes (expected,
  best-effort)" with `continue-on-error: true`, `PYTHONPATH=.:pipeline`, placed
  after the P(trigger) step (i.e. after R3/R4, before "Finalize now manifest").
- **`live/tests/test_nisar_next.py` (new)** — 12 hermetic tests: next_pass edge
  cases (boundary, multi-cycle skip, future anchor), covers, file→manifest
  fallback, collection merge/full-frame tiebreak, L0 re-anchor, zone+alert stamps
  (centroid + municipality fallback + nulls), empty/broken inputs, atomic writes.

## Commands run (all python under systemd-run --user --scope -p MemoryMax=2G, venv ~/code/r2l2pj/tipping-point-data/.venv, PYTHONPATH=.:pipeline)

- `pytest -q live/tests/test_nisar_next.py` → 12 passed
- `pytest -q pipeline/tests live/tests gee/tests` → 890 passed (no data/ tree writes)
- `ruff check live/nisar_next.py live/tests/test_nisar_next.py` → clean
  (one pre-existing RUF046 in now_manifest.py left untouched)
- `python -m live.nisar_next --now-dir ~/.cache/lead-analysis/l43-nextpass/now`
  (files copied via `git show origin/fallback:data/now/...`; fallback untouched)
  → `74 expected passes -> .../nisar_next_passes.json (zones=134, alerts=5)`

## Real run output — first 5 passes (of 74)

```json
{"utc": "2026-11-15T21:43:50Z", "track": 154, "direction": "descending", "frame": 100, "last_observed_utc": "2026-11-03T21:43:50Z", "covers": ["4100509", ...38 codes]}
{"utc": "2026-11-15T21:43:50Z", "track": 154, "direction": "descending", "frame": 101, "last_observed_utc": "2026-11-03T21:43:50Z", "covers": ["4100459", ...139 codes]}
{"utc": "2026-11-15T21:43:50Z", "track": 154, "direction": "descending", "frame": 102, "last_observed_utc": "2026-11-03T21:43:50Z", "covers": ["4101002", ...37 codes]}
{"utc": "2026-11-15T21:43:50Z", "track": 154, "direction": "descending", "frame": 103, "last_observed_utc": "2026-11-03T21:43:50Z", "covers": ["4300109", ...134 codes]}
{"utc": "2026-11-15T21:43:50Z", "track": 154, "direction": "descending", "frame": 104, "last_observed_utc": "2026-11-03T21:43:50Z", "covers": ["4300034", ...25 codes]}
```

Tracks seen: 14 (2,10,24,31,53,60,74,82,103,111,125,132,154,161). Track 132
ascending re-anchored on the 2026-11-14 L0 overpass → next 2026-11-26T09:37:37Z
(7 frames). All 67 zone features in both copies stamped (0 nulls); all 5 active
alerts stamped (Florianópolis → 2026-11-17T09:15:53Z track 2; Taquarussu →
2026-11-24T09:23:05Z track 103).

## Notes for lead-analysis / lead-app

- `direction` values are "ascending"/"descending" (lowercase) per contract.
- Zones/alerts get explicit `null` fields when no expected pass covers them
  (never missing keys).
- CMR metadata search is anonymous — the step needs no EARTHDATA_TOKEN.
- `nisar_passes.json` input is optional by design; on NOW_LOCAL runs only the
  manifest key exists and the module reads that.
