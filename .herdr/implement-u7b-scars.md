# U7b — landslide products: catalog hit-pct + HLS scar candidates

Branch `unit/u7b`, worktree `tipping-point-data-wt/u7b`.

## Files written (scope only)

- `pipeline/tp/landslide_hit.py` (new) — `landslide_hit(points_csv,
  tpi_class_cog, valid_classes=(2,3)) -> dict`. CSV lat/lon columns are
  tolerant (`lat|latitude|y`, `lon|lng|long|longitude|x`), optional `source`
  column (GLC|inventory) is collected into `collections`. Points are warped
  EPSG:4326 -> raster CRS, sampled nearest-pixel on the event-day class COG;
  nodata (masked, tagged nodata, or 255), non-finite and out-of-bounds points
  are skipped and counted. Returns `points_total/valid/hit/skipped`,
  `hit_pct` = 100*hits/valid, and `collections` (`landslide_catalog`,
  `tpi` stem).
- `pipeline/tp/hls_scar.py` (new) — `detect(pre_tif, post_tif, out_tif,
  slope_tif=None, min_slope_deg=MIN_SLOPE_DEG, min_px=MIN_BLOB_PX,
  dnbr_min=DNBR_MIN)`. Config-driven defaults: `slope_range_deg[0]` and
  `min_blob_pixels` via `tp.config.threshold`. Band layout documented: 1 band
  = precomputed NBR; >=2 bands = `[NIR, SWIR2]`; post band 3 = Fmask (bit1
  cloud + bit2 shadow masked; absent -> no mask, `fmask="absent"` in stats).
  Candidate = dNBR > dnbr_min & post-Fmask clear & slope > min & finite;
  blobs < min_px dropped via `scipy.ndimage.label`. Writes uint8 class COG
  (0 none, 1 candidate) on the post grid, native CRS. Module docstring
  declares the missing-GUNW limitation; everything is "candidates" — the word
  "confirmed" does not appear in the module (asserted in test).
- `pipeline/scripts/08_landslide_hit.py` (new) — `--event --points
  --tpi-classes`; merges `landslide_hit_pct` + collections via `tp.metrics`.
- `pipeline/scripts/09_hls_scar.py` (new) — `--event --pre --post [--slope]
  [--dnbr-min]`; writes `data/<event>/cog/<caso>_landslide_candidates_
  <AAAAMMDD>.tif` (the SCHEMA stem shared with 03, per contract); merges
  `landslide_hls_candidates` (patch count) + `landslide_hls_candidate_px` +
  `collections.hls` (pre/post granule stems) via `tp.metrics`.
- `pipeline/tests/test_landslide_hit.py` (new) — synthetic uint8 class COG
  (EPSG:32610) + CSV; hand-computed 75% pct with nodata + out-of-bounds
  skips; column-alias parametrization; source-column collections; missing
  lat column raises; all-nodata -> 0.0.
- `pipeline/tests/test_hls_scar.py` (new) — synthetic NIR/SWIR2 pre/post:
  dNBR fires (16 px patch), Fmask bit1/bit2 mask 2 px (bit3 snow does not),
  slope gate halves the scar, <min_px blob dropped, 1-band precomputed NBR
  accepted, "candidates"/limitation wording asserts.

## L4 rework (spec delta — data dirs are data/<case>/)

- `09_hls_scar.py`: dropped `tp.events.event_data_dir`/`artifact_stem`
  (events.py is being changed by another lead). Output dir is now
  `config.REPO_ROOT/data/<case>/cog/` via `config.case_id(args.event)`
  (accepts `wa_2025` or `wa-2025-12`); the dated stem is built inline as
  `<cid>_landslide_candidates_<event_date>` from
  `config.case(cid)["event_date"]` — canonical config date (2025-12-10),
  not the stale events/*.json reference_date.
- `08_landslide_hit.py`: no data dirs of its own; docstring example
  updated `data/wa-2025-12/cog/` -> `data/wa_2025/cog/`. `--event` help in
  09 now documents both id forms.
- `merge_metrics(args.event, ...)` calls unchanged — the auto-route to
  root `data/metrics.json` keyed by case is internal (signature
  unchanged).
- New test `test_scripts_use_case_data_dirs_not_event_dirs` greps both
  scripts for `event_data_dir` and `data/<event-id>` literals.

## L7 rework (ruling — HLS scar + cloud fraction move into GEE)

- `gee/hls_scar.py` (new) — `python -m gee.hls_scar --case wa_2025
  [--dry-run|--tasks]`; server-side port of tp/hls_scar.py (local raster
  path kept as fallback). Merged `NASA/HLS/HLSL30/v002` (NIR B05, SWIR2
  B07) + `NASA/HLS/HLSS30/v002` (NIR B8A, SWIR2 B12), fill -9999/255
  masked; median composites on Fmask-clear pixels per case window
  (pre = event - before .. event, post = event .. event + after).
  candidate = dNBR>dnbr_min & NASADEM slope>slope_range_deg[0] &
  blob>=min_blob_pixels via connectedPixelCount/connectedComponents;
  --ndvi-vote adds NDVI_post<pre as a second vote. Merges `cloud_pct`
  (mean Fmask bits1-3, AOI+window), `landslide_hls_candidates`,
  `landslide_hls_candidate_px` + collections.HLS; candidate mask →
  planned `{case}_landslide_candidates` asset export.
- `gee/tests/test_hls_gee.py` (new) — u5-style sys.modules stub: fake ee
  call-recorder + gee.common stub only when U4's module is absent. No
  `gee/common.py` or `__init__.py` created (u4 owns them).
- Corrected Fmask decode (research r-fmask.md, applied to BOTH paths):
  bit1 cloud | bit2 cloud-adjacent | bit3 cloud shadow | bit4 snow are
  masked (FMASK_BAD=0b11110; was wrongly bits1-2); snow gated since Dec
  storms leave cover; water left in (runout reaches channels); fill 255.
  `cloud_pct` uses bits1-3 per the research note. dnbr_min=0.1 documented
  as exploratory burn-severity — calibration needed.

## Commands run + results

- `uv sync` — ok (env synced, tipping-point-pipeline 0.1.0 editable).
- `uv run pytest pipeline/tests gee/tests -q` — 59 passed (17 local +
  9 gee fake-ee tests across the three new test files).
- `uv run ruff check .` — clean after `ruff check --fix` (import sort +
  unnecessary `int()`).
- `uv run python pipeline/scripts/08_landslide_hit.py --help` and
  `09_hls_scar.py --help` — both resolve.

## Notes / decisions

- 09 writes the same `landslide_candidates` stem as 03 per SCHEMA's
  writer table (coherence vs HLS optical are alternate producers; the wa
  GUNW path is dropped, so no collision in practice).
- Script 08 was not run end-to-end (no real catalog CSV or TPI COG in the
  worktree; running it would write `data/<event>/metrics.json`, outside
  scope). Coverage is via pytest.
- No new dependencies (pandas/scipy/rasterio/rioxarray already declared).
