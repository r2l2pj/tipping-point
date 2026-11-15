# R1 — OPERA DSWx-S1 near-real-time flood candidates (Now domain)

Branch `unit/r1-dswx` (off main 864cf95). Contract: `.crew/plans/response.md`.

## What changed
- `pipeline/fetch/opera.py` — the retired stub `main()` is unchanged, so
  `test_fetch_opera` still passes. Added `search_dswx_s1()`, an anonymous
  CMR umm_json search by bbox and time that pages with CMR-Search-After and
  returns `{id, acquired, available(=CMR Insert date), wtr_url}` oldest
  first. Added `download_wtr()`, a PO.DAAC B01_WTR GeoTIFF download using the
  EARTHDATA_TOKEN bearer from `fetch/edl.py`. It retries 3x, and its errors
  carry the HTTP status only, never the token.
- `pipeline/tp/response_dswx.py` (new) — grid, rolling state, evaluation,
  polygonize, people, `run()`.
- `pipeline/scripts/13_response_dswx.py` (new) — the CLI. Takes the bbox from
  `config.yaml domains.now`. Has a `--dry-run`.
- `pipeline/tests/test_response_dswx.py` (new) — 9 hermetic tests on synthetic
  rasters. search and fetch are injected, so nothing touches the network.
- `data/SCHEMA.md` — a new `data/now/response/` section.
- `data/now/response/{flood_dswx.geojson, dswx_state.npz, dswx_meta.json}` —
  real outputs.

## Method
- WTR classes: water = {1 open, 2 partial, 3 inundated vegetation}; dry = 0.
  Everything else is unobserved: 250 HAND, 251 layover/shadow, 254 ocean,
  255 fill. Checked against the real GeoTIFF colormap and value histogram
  (T21HVC 2026-11-14: values 0/1/3/251/255).
- Grid: EPSG:4326 at **0.0025°** over [-58,-34,-48,-22.5], which is
  4600×4000 cells of about 280×230 m. Each 30 m UTM tile is reprojected
  with Resampling.average of a 1/0/nan indicator. A cell is water when its
  observed water fraction is ≥ 0.5. Tiles are processed one at a time, with
  a bounded prefetch of 8 tiles in memory and 4 download threads.
- Per cell, the state keeps the latest observation (`cur`, `cur_t`, `cur_g`)
  and a baseline of all EARLIER observations (`water_n`/`obs_n`, uint16).
  - A newer pass moves the previous latest observation into the baseline.
  - Tiles from the same pass (within 1 h) merge.
  - Late-arriving older observations go straight into the baseline.
  - Counts are halved at 60 observations, which acts as a rolling window.
  - Water at cells that were flood candidates is never added to their own
    baseline, so a long flood can't absorb itself.
- Candidate = latest observation is water, within `window_h` = 144 h of the
  newest acquisition, with ≥ `min_obs` = 2 earlier observations and earlier
  water frequency < 0.2. With 2 to 4 earlier observations, that means dry
  every time. Water cells with less baseline are reported as
  `unbaselined_water_km2` and are never candidates.
- change: `new` means not in the previous run's candidate mask; otherwise
  `persisting`. `receded` = a previous candidate whose latest recent
  observation is dry.
- Polygons: `tp/polygonize.mask_to_geojson` with 150 m simplify and a
  0.25 km² minimum. If the file exceeds 5 MB, simplify and minimum area
  double until it fits.
- Per-polygon props come from one rasterized id grid: `acquired` = newest
  observation (hour precision), `granules` = the GranuleURs feeding the
  polygon, `people` = GPW 2020 sum.
- Bootstrap: the first run (no state, or a state whose bootstrap failed)
  ingests the previous 30 days. The bootstrap fails if 0 granules are
  ingested, more than 20% fail, or the search fails. Failure is recorded in
  `_null_reasons.bootstrap`, the km2/people keys are null, and an empty
  FeatureCollection is written.
- Incremental: the search starts at `last_acquired − 24 h` and skips
  granule ids already seen. The seen list keeps 72 h of ids.
- Write order: geojson → state → meta, all written atomically. Meta goes
  last.

## Commands and results
Creds: `export EARTHDATA_TOKEN="$(grep '^EARTHDATA_TOKEN=' ~/code/r2l2pj/tipping-point-data/.env | cut -d= -f2-)"`
(the token was never printed). Run with `PYTHONPATH=$PWD/pipeline .venv/bin/python`.
`/usr/bin/time` is not installed on this host, so peak RSS comes from a
wrapper that reads `resource.getrusage(RUSAGE_CHILDREN).ru_maxrss` (KiB)
for the child process.

```
PYTHONPATH=$PWD:$PWD/pipeline .venv/bin/python -m pytest pipeline/tests live/tests ml/tests gee/tests -q
  -> 645 passed, 3 failed
     The 3 failures are live/tests/test_publish_fallback.py. They are
     PRE-EXISTING: the same 3 fail on a clean detached worktree of base
     864cf95. Not touched here.
.venv/bin/ruff check <my 4 py files>  -> All checks passed!

# real run 2: bootstrap (fresh state)
13_response_dswx.py --gpw data/static/... --gpw <shared checkout>/data/static/south_brazil/static/gpw_pop.tif
  1167 granules since 2026-10-16T03:43Z, 0 failed
  latest_acquired 2026-11-14T09:06:12Z, latency_h 6.8 (newest pass)
  flooded_km2 1544.138, new_km2 1544.138 (first pass: all new), receded null (reason given)
  people_flooded 9029.1, features 948, coverage_pct 70.03, unbaselined_water_km2 1413.4
  PEAK RSS 881 MiB, wall 366 s
# real run 3/4: incremental
  68 granules since 2026-11-13T09:06Z, 0 new -> n_granules 0, new_km2 0.0, receded_km2 0.0,
  948 features all "persisting"; PEAK RSS 683 MiB, wall 4-5 s
```
Outputs: flood_dswx.geojson 559 kB, dswx_state.npz 1.6 MB, dswx_meta.json 1.2 kB.

## Caveats
- **GPW:** the public Neon copy
  `.../data/static/south_brazil/static/gpw_pop.tif` returns **HTTP 404**,
  and the worktree has no local copy. For the real run I pointed `--gpw` at
  the shared checkout's file (read only). The CLI default tries the local
  repo path, then the Neon `/vsicurl/` URL. If both fail, people is null
  and `_null_reasons.people` says why. Someone needs to upload GPW to Neon
  before CI can count people.
- An earlier run, which I discarded, used `min_obs=3`. Most cells have only
  1–2 earlier observations in 30 days (each Sentinel-1 track repeats about
  every 12 days), so about 39,000 km² of water stayed unbaselined. I made
  `min_obs=2` the default (unbaselined is now 1,413 km²). A 45–60 day
  bootstrap would make the baseline stronger.
- Candidates are not validated. Expect false positives from wet soil, rice
  paddies (RS) and wind-roughened or shallow water. These are labelled
  `candidate`, never `confirmed`.
- `acquired` per polygon is truncated to the hour, because the state stores
  uint16 hours since 2024-01-01 (good until 2031).
- Incremental gap: a granule published more than 24 h after a later
  acquisition has already been ingested is missed. Its acquisition is
  older than the overlap window.
- The "now" mosaic covers 144 h, so 70% of the domain has an observation;
  the rest is ocean or has no recent pass.
- Shared venv: before your note I ran plain `uv run` 3 times (dry-run,
  first pytest, an import check). Each one re-pointed the shared editable
  `tp` install at this worktree. I've checked it since: the `.pth` points
  at the shared checkout's `pipeline/` again. All later runs used
  `.venv/bin/python` with PYTHONPATH.
- Not wired into `data/now/manifest.json` or `live/` (that's R3 / lead-live).
  Not merged, not published.
