# Real run — t11n (NISAR GCOV, OPERA list, GLC, RS, IMERG)

## Code fixes (branch fix/realrun, worktree ../tipping-point-data-wt/realrun; rebased onto main d2526b6)
1. `99152ce` tp/nisar.py — earthaccess `DataGranule` is a dict subclass; `getattr(g,"meta")` was always {}, so `_granule_meta`/`_native_id` never saw native-id → "4 granules found but none match track 172D frame 064" while the exact granule was in results. Now reads `g["meta"]["native-id"]` (umm GranuleUR fallback).
2. `2da8164` tp/nisar.py — `CPL_VSIL_CURL_USE_HEAD=NO`: ASF TEA answers HEAD with the same 303→signed URL and empty body; GDAL cached it as file content → "not a supported format". Verified: plain GET returns real HDF5 bytes.
3. `421c78a` tp/nisar.py — /vsicurl/ range reads through the 303 redirect starved (~12 MB/min, 38 GB rx, no COG after 20 min). `execute_plan` now linear-downloads each granule HDF5 once into `data/raw/nisar/h5/` (Bearer on TEA only; `_StripAuthOnCrossHost` drops Authorization on the CloudFront redirect) and subsets locally. Also emits the declared no-event scene (which="ref") — was silently dropped by download_plan.
4. `51ad3ca` fetch/imerg.py — `open_mfdataset` requires dask (not a dep); IMERG v07 time is 'seconds since 1980-01-06' calendar 'julian' → undecodable without cftime. Now per-file `xr.open_dataset(decode_times=False)` + manual epoch decode + concat. Verified on a real 3B-HHR-E granule.
5. `5586663` data/wa_2025/geojson/landslide_catalog.geojson — regenerated GLC catalog (259 features, all-time bbox).

Tests: 53/53 nisar+fetch_now pass on the branch; full suite 417 pass, 1 PRE-EXISTING failure `test_edl_token_env_wires_bearer_header` (fails identically on clean main — order-dependent earthaccess token-state flake, passes in isolation). ruff clean.

## Real-run results
- OPERA DSWx WTR list (`python -m pipeline.fetch.opera --case wa_2025 --no-download --limit 5`) — real CMR granules:
  | granule | date | cloud% |
  | OPERA_L3_DSWx-HLS_T10TFT_20251219T185709Z_..._S2B_30_v1.1 | 2025-12-19 | 10 |
  | OPERA_L3_DSWx-HLS_T10TFT_20251219T190831Z_..._S2A_30_v1.1 | 2025-12-19 | 73 |
  | OPERA_L3_DSWx-HLS_T10TFT_20251220T184943Z_..._L8_30_v1.1 | 2025-12-20 | 78 |
  | OPERA_L3_DSWx-HLS_T10TFT_20251217T190821Z_..._S2C_30_v1.1 | 2025-12-17 | 82 |
  | OPERA_L3_DSWx-HLS_T10TFT_20251212T184948Z_..._L9_30_v1.1 | 2025-12-12 | 86 |
  (all tile T10TFT; no download/ingest — GEE roles pending)
- GLC: `fetch.glc.glc_catalog` regenerated landslide_catalog.geojson — **259 features**.
- RS inventory: already current (16027 pts, regenerated 14:25 today) — skipped.
- IMERG: `--days 3 --dry-run` plan OK (GPM_3IMERGHHE.07, daily+3day for 2026-11-12..14). Real anonymous CMR search: 144 half-hourly granules 2026-11-11..12 (~6.4 MB each). Real download+subset of `3B-HHR-E.MS.MRG.3IMERG.20261111-S000000-E002959.0000.V07C.HDF5` (6,718,873 B): subset 100×115 over now_bounds, time 2026-11-11T00:00, max 5.38 mm/h, mean 0.0104 mm/h.
- NISAR: DONE in worktree then copied to main checkout. 3 granules downloaded linearly (~7.0/7.0/6.9 GB each, ~20 MB/s) → 9 COGs + manifest data/wa_2025/nisar_products.json (ref/pre/post + dated keys, collection NISAR_L2_GCOV_BETA_V1).
  | file | size |
  | gcov_hhhh_ref.tif | 374,430,478 B |
  | gcov_hvhv_ref.tif | 373,891,538 B |
  | gcov_mask_ref.tif | 366,873 B |
  | gcov_hhhh_pre.tif | 376,072,077 B |
  | gcov_hvhv_pre.tif | 375,410,424 B |
  | gcov_mask_pre.tif | 367,134 B |
  | gcov_hhhh_post.tif | 362,327,939 B |
  | gcov_hvhv_post.tif | 361,700,535 B |
  | gcov_mask_post.tif | 365,715 B |
  COGs: EPSG:4326, 13131×9629 over the AOI; gcov_hhhh_pre finite 0.769, min 3.5e-4, max 112651 (linear gamma0); mask 0–255.
  granule IDs: ref NISAR_L2_PR_GCOV_004_172_D_064_4005_DHDH_A_20251110T031814_20251110T031849_X05009_N_F_J_001; pre NISAR_L2_PR_GCOV_006_172_D_064_4005_DHDH_A_20251204T031815_20251204T031850_X05009_N_F_J_001; post NISAR_L2_PR_GCOV_007_172_D_064_4005_DHDH_A_20251216T031815_20251216T031850_X05009_N_F_J_001.

## Notes
- `python -m pipeline.fetch.reference --refresh` runs the fetch step but then REQUIRES NOW_GCS_BUCKET for GEE ingest (absent from .env) — geojson regenerates before the error; ingest waits for impl-gsl10.
- Earlier /vsicurl attempt left data/raw/nisar/h5/*.h5.part in MAIN checkout (gitignored, stale — safe to delete).
