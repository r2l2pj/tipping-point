# Unit R2 — NISAR GCOV radar flood candidates (Now domain)

Branch `unit/r2-nisar` (off main 864cf95). Contract: `.crew/plans/response.md`.

## What changed
- `pipeline/fetch/nisar.py` (extended, existing BETA flow untouched):
  `PROD_GCOV_COLLECTION="NISAR_L2_GCOV_V1"`, `search_gcov_domain(bbox, start, end)`
  (anonymous CMR umm_json, paged via CMR-Search-After), `cmr_gcov_granule()` parser
  (cycle/track/A|D/frame/mode/pols/F|P, start/end, CMR Insert date, .h5 URL, footprint),
  `pair_latest_pass(granules, bbox, max_gap_days=36, min_overlap_km2=200)`: passes
  newest-first; per frame, pre = most recent earlier granule on the SAME
  track+direction+frame within 36 d; one granule per frame/date (full frame first,
  then larger domain overlap); newer unpairable passes reported in `skipped`.
- `pipeline/tp/response_nisar.py` (new):
  - Reads ONLY the co-pol term (HHHH, else VVVV) and ONLY the HDF5 chunks over the
    domain: chunk index from h5py (`chunk_iter`), ranges coalesced (≤16 MiB spans),
    fetched as parallel HTTP range reads against the signed CloudFront URL (one
    bearer GET resolves the ASF TEA 303; token/signed URL never logged), inflated +
    unshuffled in numpy, block-averaged (linear power) onto a snapped 100 m UTM grid.
    No new deps (h5py/fsspec/aiohttp already in the venv).
  - Native samples < −40 dB treated as nodata (see "Finding" below).
  - Detection = `tp.flood_mask.flood_mask` UNCHANGED (wa_2025 method: −3 dB drop,
    Otsu with −15 dB clamp/fallback, pre-water excluded → permanent water never
    counts, HAND gate with `data/static/south_brazil/terrain/hand_m.tif`, bright-flood
    layer, 3 ha MMU). Same call with pre/post swapped → `receded_km2`.
  - People: GPW count raster sampled at flood-pixel centres, area-weighted
    (pixel km² / cell km²), summed per labelled polygon. Local
    `data/static/south_brazil/static/gpw_pop.tif` → Neon `/vsicurl/` → null + reason.
  - Polygons (pixel centres inside the 4326 bbox only), EPSG:4326, 1e-5° precision.
    Props: source `nisar-gcov`, pre, post, track, frame, direction, acquired (post
    UTC), area_km2, people, status `candidate`, change `new`, flood_type
    (`open-water` | `bright`), confidence (`standard` | `lower`).
  - Meta (written LAST): generated_at, latest_acquired, latency_h (CMR Insert − post
    acquisition end), n_granules, pairs[] (ids, grid, io bytes, threshold, km²),
    flooded_km2 (= open_water + bright), open_water_km2, bright_km2, new_km2,
    receded_km2, people_flooded, n_polygons, newer_unpaired, method, _null_reasons.
    No pair → meta only with nulls + `_null_reasons`, stale geojson removed, no
    synthetic output.
- `pipeline/scripts/14_response_nisar.py` (new CLI): bbox from `config.yaml
  domains.now`; flags `--out --res --lookback-days --max-gap-days --min-overlap-km2
  --gpw --no-hand`.
- `pipeline/tests/test_response_nisar.py` (new, 14 tests, hermetic): synthetic
  GCOV-shaped HDF5 (chunked+shuffle+gzip) → block average == naive mean, sub-floor
  samples dropped, non-multiple res rejected, grid clip; CMR parse + paging; pairing
  (same track/frame/dir, 36 d gap, partial vs full, sliver overlap); end-to-end
  (1 km² new flood found, permanent lake excluded, people, latency, meta == disk),
  receded via swap, no-pair null meta + stale removal, GPW-missing nulls, CMR failure.
- Outputs: `data/now/response/flood_nisar.geojson` (16 kB), `nisar_meta.json`.

## Real end-to-end run (2026-11-15 ~03:41Z)
```
export EARTHDATA_TOKEN=$(grep '^EARTHDATA_TOKEN=' ~/code/r2l2pj/tipping-point-data/.env | cut -d= -f2- ...)
PYTHONPATH=pipeline python3 -I rss.py .venv/bin/python pipeline/scripts/14_response_nisar.py \
  --gpw ~/code/r2l2pj/tipping-point-data/data/static/south_brazil/static/gpw_pop.tif
```
- CMR: 95 NISAR_L2_GCOV_V1 granules over the domain in the last 60 d.
- Newest pass: cycle 035 track 024 D, frames 100+101, acquired 2026-11-06T21:16:03Z.
  Frame 100 skipped (domain overlap < 200 km²); frame 101 paired:
  - pre  `NISAR_L2_PR_GCOV_034_024_D_101_2005_DHDH_M_20261025T211603_20261025T211633_P05036_N_F_J_001`
  - post `NISAR_L2_PR_GCOV_035_024_D_101_4005_DHDH_A_20261106T211603_20261106T211610_P05036_N_P_J_001`
- Grid EPSG:32723, 100 m, 790×3183; valid 1599.9 km²; term HHHH; threshold −15.0 dB
  (fallback — no bimodal tile); bytes read: pre 66.3 MB (256 chunks, 32 requests),
  post 54.1 MB (1008 chunks, 19 requests) — of 1.5 GB / multi-GB files.
- **flooded_km2 = 12.06** (open-water 12.06, bright 0.0), receded 0.0,
  **people_flooded = 313**, 16 polygons, latency_h = 99.7, `_null_reasons` = {}.
- **Peak RSS 525 MB** (537964 kB), wall 26.1 s, exit 0. `/usr/bin/time` is not
  installed on this host; peak RSS measured with `getrusage(RUSAGE_CHILDREN).ru_maxrss`
  of the child process (same number `time -v` reports).
- Leak check: no `Bearer`/cloudfront/signature/JWT strings in outputs or logs.

## Finding fixed during the real run
First run gave an "adaptive" threshold of **−38.1 dB** (0.91 km²): real GCOV carries a
separate ~1e-6 (−60 dB) sample population (~6 % of the post), far below the L-band
noise floor, so Otsu split noise from ocean. Fixed at read time (`MIN_POWER_DB = −40`,
native samples below → nodata), NOT in the shared algorithm; flood_mask.py untouched.

## Caveats for the lead
- The newest pass only clips the domain's NE corner (around −48.0, −24.0). It is the
  literal newest pair; most of the domain is not covered by this pass.
- Pre is 20 MHz mode (2005, "M"), post is 40 MHz (4005, partial frame): both are
  multilooked to 100 m, but a cross-mode calibration offset is possible. Pairing
  keeps track+direction+frame as specified; it does not force the same mode.
- **Neon public GPW copy is 404** (`.../data/static/south_brazil/static/gpw_pop.tif`).
  Without `--gpw`, a worktree/CI run gets `people_flooded: null` + reason (verified on
  run 1). The real run used the shared checkout's GPW file (read only).
- HAND is 0.01° (~1 km), so the bright layer (HAND < 5 m) is very restrictive → 0 km².

## Tests / lint
- `PYTHONPATH=pipeline uv run --no-sync pytest pipeline/tests live/tests ml/tests gee/tests`
  → **650 passed, 3 failed**. The 3 failures are `live/tests/test_publish_fallback.py`
  (rsync size exclusions); they fail identically on a pristine `git archive` of HEAD
  864cf95 → pre-existing (local rsync 3.5.1), unrelated.
- `--no-sync` + `PYTHONPATH=pipeline`: the shared `.venv` editable install currently
  points at the r1-dswx worktree; a plain `uv run` would re-sync it under that agent.
- `ruff check` on the 4 touched files: all checks passed.
