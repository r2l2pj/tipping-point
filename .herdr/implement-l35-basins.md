# L35-U1 — upstream-basin topology (rs_2024, wa_2025)

Commit: `8934237` on branch `l35-basins`

## What changed

- `pipeline/tp/upstream.py` — pure functions:
  - `upstream_sets(df, ids=None)`: {hybas_id: sorted upstream ids} via
    NEXT_DOWN reverse walk (children adjacency + memoized reuse across
    targets); basin itself included.
  - `rasterize_ids(gdf, like_path)`: (int64 array, GTiff profile) —
    HYBAS_ID per cell on exactly the grid of like_path (same
    transform/shape/crs); 0 where no basin (ocean). HYBAS_ID max is
    7.1e9 > uint32, hence int64 (spec allowed uint32/int64).
  - `build_case(gdf, aoi_bbox, like_path, out_dir)`: AOI basins via
    sindex intersects; upstream sets; writes basins.geojson (union of
    upstream sets; HYBAS_ID, NEXT_DOWN, SUB_AREA, UP_AREA),
    upstream.json ({hybas_id: [ids]} for AOI basins + union_bbox
    [w,s,e,n] + upstream_area_km2 map = UP_AREA), basin_id_90m.tif.
- `tools/build_upstream_basins.py --case` — downloads
  hybas_<sa|na>_lev01-12_v1c.zip from data.hydrosheds.org into
  intermediate/upstream/_hydrobasins/ (extracts lev10; lev9 fallback;
  zip kept). GEE fallback documented in docstring (not needed —
  downloads succeeded). Region map: rs_2024->sa, wa_2025->na. AOI and
  ref date read from data/events.json via tp.config.event_id; like =
  data/<case>/cog/<case>_tpi_flood_<ref>.tif (rs 20240502, wa 20251210).
- `pipeline/tests/test_upstream.py` — hermetic test (4 cases: chain
  topology, ids subset, rasterize w/ >uint32 ids + ocean zeros,
  build_case end-to-end incl. upstream basin outside AOI). tmp_path
  only; conftest data/ guard clean.
- `.gitignore` — added `intermediate/` (was not covered).

## Commands + results

- `curl -I https://data.hydrosheds.org/file/hydrobasins/standard/hybas_{sa,na}_lev01-12_v1c.zip` -> 200
- downloaded both zips (~316/334 MB) -> extracted lev10 shp set only:
  hybas_sa_lev10_v1c (123,438 basins), hybas_na_lev10_v1c (111,181),
  EPSG:4326; NEXT_DOWN all resolve in-table, 0 dups.
- `python tools/build_upstream_basins.py --case rs_2024 --case wa_2025`:
  - rs_2024: 252 AOI basins, 620 union basins, union_bbox
    [-54.550567,-30.9625,-49.941667,-28.1375], largest upstream area
    71,807.9 km2 (hybas 6100015560, Jacuí/Guaíba), 95.45 % of AOI cells
    carry a basin id.
  - wa_2025: 104 AOI basins, 174 union basins, union_bbox
    [-122.804167,46.595833,-120.658333,49.325], largest upstream area
    8,183.0 km2 (hybas 7100016090, Skagit), 90.86 % of AOI cells.
- verification: basin_id_90m.tif dtype int64, transform/shape/crs ==
  tpi_flood ref exactly, nodata=0; unique raster ids == n AOI basins;
  geojson ids == union of sets exactly; every upstream set includes the
  basin itself; UP_AREA ≈ ΣSUB_AREA of set (71,807.9 vs 71,805.6;
  8,183.0 vs 8,183.1).
- `pytest pipeline/tests/test_upstream.py` -> 4 passed; `ruff check` on
  the three files -> clean.

Outputs under intermediate/upstream/<case>/ (gitignored): basins.geojson,
upstream.json, basin_id_90m.tif; shared inputs + zips under
intermediate/upstream/_hydrobasins/ (~947 MB total).
