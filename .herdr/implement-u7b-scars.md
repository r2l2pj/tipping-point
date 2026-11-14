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

## Commands run + results

- `uv sync` — ok (env synced, tipping-point-pipeline 0.1.0 editable).
- `uv run pytest pipeline/tests -q` — 49 passed (16 new tests across the two
  new test files, incl. the 4-way column-alias parametrization).
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
