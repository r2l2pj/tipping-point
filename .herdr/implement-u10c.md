# implement — U10c terrain slope module (unit/u10c)

## Changed

- `pipeline/tp/terrain.py` (new): `slope_deg(src, cellsize=None) ->
  (float32 ndarray degrees, profile)` — Horn 3x3 (gdaldem) on a rasterio
  dataset, path/URL, or ndarray. Geographic CRS (EPSG:4326 etc.) gets
  per-row pixel size in metres from WGS84 degree lengths at each row's
  latitude (`_deg_len_m`); projected CRS uses `transform.a/.e` directly.
  Nodata: NaN where the centre cell is nodata, missing neighbours fall back
  to the centre value (gdaldem rule) so holes stay holes, not halos.
  Returns a write-ready profile (`dtype=float32`, `nodata=nan`) for dataset
  input, `None` for arrays. rasterio imported lazily; numpy only dep.
- `pipeline/scripts/00_terrain.py` (new): `--dem <path-or-url>`,
  `--out data/raw/aux/slope.tif` (default), `--event/--aoi <id>` optional —
  clips to the event's `aoi.bbox` via `from_bounds`, reads one pixel of Horn
  context through a `MemoryFile` dataset, trims back so edge slopes stay
  honest. Writes a float32 COG. data/raw/ is gitignored — written, not
  committed.
- `pipeline/tests/test_terrain.py` (new): 6 tests — 20 deg tilted plane in
  EPSG:32610 and on an EPSG:4326 grid (plane built in sphere-metres so the
  test discriminates degrees-as-metres bugs), nodata hole handling,
  ndarray input, script `--help`, and an end-to-end script run.

## Commands run

1. `uv sync` (mise uv 0.13.8 full path — `uv` shim has no version set): OK.
2. `uv run pytest -q`: **52 passed** (46 existing + 6 new).
3. `uv run ruff check .`: **All checks passed!**
4. Smoke test, `--event` AOI path (not covered by tests): built a 0.01 deg
   EPSG:4326 DEM over rs-2024-05's bbox, ran
   `00_terrain.py --dem /tmp/dem_rs.tif --out /tmp/slope_rs.tif --event rs-2024-05`
   → wrote 150x200 float32 COG whose bounds equal the event bbox exactly;
   interior slope ~0.0205 deg with the correct per-latitude drift
   (geodesic sizing live).

## Result

All spec points implemented and proven: 52 green, ruff clean, AOI clip
verified end-to-end. Committed on `unit/u10c`; not pushed.
