# W2 — static 90 m feature stack (lead-ml t175)

Status: DONE — offline tests green, real smoke verified on `SMOKE_BBOX`
(-51.5, -29.6, -50.5, -28.9) plus 3 global points. Scope change (point mode
`sample_static_points`, NDVI -> VNP13C2) implemented and verified.

## Files changed (worktree `tipping-point-data-wt/ml`, branch `unit/ml-local`)

- `ml/local/sources_static.py` (new, ~1240 lines) — full API + CLI.
- `ml/tests/test_local_sources_static.py` (new) — 14 offline tests.
- Nothing else touched; no commits made.

## API implemented (exactly as briefed)

```python
fetch_nasadem(bbox=REGION_TRAIN, workers=4) -> list[Path]   # DATA_DIR/nasadem/{zip,hgt}
build_terrain(bbox=REGION_TRAIN, res=RES_STATIC_DEG) -> list[Path]  # st90_<cell>.tif, 6 bands
fetch_landcover(year=LANDCOVER_YEAR, bbox=REGION_TRAIN) -> Path     # landcover_mcd12q1_<year>.tif
fetch_ndvi(year=NDVI_YEAR, bbox=REGION_TRAIN) -> Path               # ndvi_vnp13c2_<year>.tif (global)
build_static(bbox=REGION_TRAIN, res=RES_STATIC_DEG) -> Path         # -> index.json (+ static90.vrt)
sample_static(lon, lat) -> pd.DataFrame                            # TERRAIN + LANDCOVER + hand_m
static_on_grid(transform, shape, bands=None) -> dict[str, np.ndarray]
is_land(lon, lat) -> np.ndarray[bool]
random_land_points(n, bbox, rng) -> (lon, lat)
sample_static_points(lon, lat, keep_dem=False) -> pd.DataFrame     # 3b global mode
```

CLI: `python -m ml.local.sources_static
<nasadem|terrain|landcover|ndvi|all|points> --bbox W S E N [--year] [--workers]
[--keep-dem] [--lon .. --lat ..]`.

## Verified real outputs (ML_DATA_DIR=$PWD/../ml-smoke-cache)

- `sample_static(-51.0,-29.3)` -> elev 327 m, slope 28.2 deg, lc=2 (EBF),
  ndvi 0.83, hand_m=32 m; (-51.2,-29.45) elev 533 m slope 5.0 deg; (-50.9,-29.1)
  elev 868 m slope 0.28 deg.
- 1000 `random_land_points`: slope 0.08-37.6 deg (Serra Geral >30 deg OK),
  elevation 9-997 m, landcover {2:444, 8:150, 9:337, 10:1, 11:1, 13:48, 14:19},
  ndvi 0.38-0.88 (med 0.78), hand_m 0-393 m, all bands 100 % finite.
- `static_on_grid` on the 0.01 deg Now grid over the smoke bbox vs shared
  `data/static/south_brazil/terrain/slope_deg.tif`: **corr = 0.9776** (n=6900);
  mean 10.1 deg ours vs 11.9 deg ref (ref is a different DEM/resampling — the
  correlation is the sanity check). VRT opens: 9 bands, windowed reads fine.
- `sample_static_points(85.3,27.7 / -75.6,6.2 / -121.9,47.5)` (Nepal /
  Colombia / Cascades): elev 1289/1589/194 m, slope 1.8/16.7/3.8 deg, lc
  13/13/5, ndvi 0.26/0.48/0.81, hand NaN (outside Now domain). 22 s cold,
  0.0 s on cache hit. Zips+hgts deleted after compute (keep_dem=False verified;
  keep_dem=True retains them).

## Data-source facts (verified live)

- NASADEM_HGT.001: CMR granules.json + `Authorization: Bearer` on
  lp-prod-protected zip (303 -> signed URL, ~11 MB/zip); zip members are
  `s30w052.hgt|.num|.swb` — extracted `.hgt` keeps the bare tile-id name because
  GDAL's SRTMHGT driver requires `[NS]dd[EW]ddd.hgt` basenames.
- MCD12Q1.061: OPeNDAP at `.../collections/C2484079608-LPCLOUD/granules/<UR>`;
  DMR lists vars under `/MCD12Q1/...` — working FQ path `/MCD12Q1/Data_Fields/
  LC_Type1` (+ `/MCD12Q1/{XDim,YDim}` sinusoidal pixel centres). `.dap.nc4?
  dap4.ce=` returns netCDF4; slab CE `/...[r0:r1][c0:c1]` works (ranges
  INCLUSIVE — point-mode index windows are exact). One request per MODIS tile.
- MOD13A3.061: OPeNDAP DMR -> **404** (probed once per fetch_ndvi call, printed
  to stderr) — rasterio has no HDF4 driver, can't read its granules either.
- **VNP13C2.002** (collection C2631828311-LPCLOUD, VIIRS/NPP monthly 0.05 deg
  CMG global, HDF5): no OPeNDAP endpoint (constructed DMR -> 404) and HDF5-over-
  vsicurl unsupported -> direct `.h5` downloads, 148 MB/month -> 1.78 GB/year.
  SDS `HDFEOS/GRIDS/VIIRS_Grid_monthly_VI_CMG/Data Fields/CMG 0.05 Deg monthly
  NDVI` (int16, scale 1e-4, fill -15000..-12000) + `pixel reliability`
  (int8, keep <=1; fill -4..-1). Grid is GEOGRAPHIC -180..180/-90..90 @ 0.05 deg
  — no reprojection. Annual mean of the 12 months of NDVI_YEAR; ONE global file
  `ndvi_vnp13c2_<year>.tif` (19 MB deflate) serves both modes.
- HAND: `data/static/south_brazil/terrain/hand_m.tif` found in the worktree
  itself (same 1150x1000 @ 0.01 deg Now grid as the shared copy); NaN outside.

## Proof

- `uv run pytest ml/tests/test_local_sources_static.py -q` -> **14 passed**
  (synthetic tilted plane recovers slope within 0.05 deg and aspect <0.5 deg;
  bowl -> curvature = Laplacian*100; step -> relief; NaN centre -> NaN; real
  .hgt bytes through the SRTMHGT driver -> build_terrain -> sample; mocked
  CMR/OPeNDAP; cache-hit no-refetch; keep_dem retains files).
- `uv run ruff check ml/local/sources_static.py
  ml/tests/test_local_sources_static.py` -> All checks passed.

## Smoke cost measured

- Downloads: NASADEM 45 MB zips (+99 MB hgt), MCD12Q1 ~1 MB (OPeNDAP),
  VNP13C2 1.78 GB. Total ~1.83 GB, ~80 s wall (4 parallel downloads,
  ~24 MB/s to LP DAAC cloud).
- Disk: cache 2.0 GB total; static90 = 129 MB (4 tiles ~29 MB each + ndvi 19 MB
  + lc 9 KB). Peak RAM per terrain tile ~200 MB (merge 3612^2 + bands).

## Extrapolation to REGION_TRAIN (-58,-34,-39,-14)

- NASADEM: 19x20 = 380 cells + 1-cell halo ring (bbox edges sit ON degree
  boundaries) = ~462 tiles; ~5 GB zips + ~12 GB hgt; terrain compute ~2-4 s/
  tile sequential -> ~15-30 min; static90/terrain ~13 GB. (If disk matters,
  deleting zips after extract halves it — not done automatically.)
- MCD12Q1: ~20-25 granules x ~1 MB OPeNDAP, minutes.
- NDVI: identical global fetch (1.78 GB once, reused by both modes).
- `sample_static_points` for ~2.5k global GLC: ~7.3 s per distinct 1 deg cell
  (zip ~11 MB + extract + window compute + one CMR + OPeNDAP slab per MODIS
  tile). If points cluster into ~400-800 cells: ~4-9 GB downloads, ~50-100 min
  serial; per-point cost near zero once a cell is fetched. Results persist in
  `DATA_DIR/static_points.parquet` — **NOTE: no parquet engine installed
  (pyarrow/fastparquet absent, no-new-deps rule) -> the file is a pickle
  payload at the .parquet path; `pd.to_parquet` activates automatically if an
  engine is added.**

## Decisions / notes for lead-ml

- Tile edges: 2-px (90 m) halo read from adjacent cells where present;
  bbox-boundary/ocean edges -> NaN (cells fully outside DEM -> all-NaN tile
  still written, keeps the index regular).
- Ocean/void: hgt nodata -32768 -> NaN; ocean elevation 0 kept (is_land
  excludes via landcover water/fill anyway).
- Aspect: downhill direction clockwise from north; flat (slope=0) ->
  aspect_sin=aspect_cos=0; NaN where elevation NaN.
- Curvature: Laplacian of elevation x100 (units 1/100 m), Zevenbergen-Thorne
  style second differences on metric per-row dx/dy (tp.terrain._deg_len_m).
- Neighbour rule identical to `tp.terrain._horn` (verified equal in test).
- `is_land`: finite elevation AND landcover in 1..16 (IGBP water is class 17;
  brief's "water (0)" also excluded, plus 255 fill).
- `static_on_grid`: average resampling for terrain/ndvi/hand, nearest for
  landcover; window-by-window only (no full-domain arrays).
- VRT written over the tile union; index.json is the canonical manifest
  (build_static returns its path).
