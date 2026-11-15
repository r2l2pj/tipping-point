# L45 W-coh: WA 2025 NISAR GUNW coherence-loss candidates (t195)

Branch `nisar-coh`, worktree `tipping-point-data-wt/nisar-coh`. Done at
~14:25 BRT (hard stop 14:45).

## What was built

12-day L-band coherence-loss layer for wa_2025 from NISAR GUNW Beta
wrapped-interferogram grids: `coh_ref - coh_evt >= 0.3` where
`coh_ref >= 0.4`, water (GUNW mask ∪ NISAR GCOV water) and
`elev >= 600 m` (NASADEM) excluded, NISAR flood-candidate pixels removed
from landslide candidates, MMU 3 ha (9 px @60 m). **Beta, candidates only —
never confirmed.**

## Files changed / added

- `pipeline/tp/coherence_loss.py` — NEW pure functions: `point_hit_stats`,
  `baseline_hit_pct` (200-seed random placement on steep valid non-water
  terrain), `polygonize_mmu` helpers.
- `pipeline/tests/test_coherence_loss.py` — NEW hermetic tests (27 pass).
- `tools/wa_nisar_coherence.py` — NEW driver: CMR granules, HTTPS range
  reads, target-grid warp, DEM/slope, masks, candidates, PNGs, index,
  JSON stats, metrics merge.
- `pipeline/tp/manifest.py` — forwards `label`, `attribution`, `note`
  (`kind`, `legend` already forwarded).
- `pipeline/tests/test_manifest.py` — `LAYER_KEYS` extended +
  `test_nisar_extra_fields_forwarded_from_index`.
- `data/SCHEMA.md` — documents `kind: "nisar_extra"` layer contract.
- `data/wa_2025/cog/wa_2025_nisar_coherence_{ref,event,loss}_60m.tif` —
  EPSG:32610, 60 m, same grid as `wa_2025_nisar_pre_20251204_60m.tif`
  (2797×1410). ref/event float32 coherence; loss uint8 (1=loss, 2=loss+HV
  drop vote).
- `data/wa_2025/geojson/nisar_coherence_candidates.geojson` — 4362
  polygons ≥ MMU, props `status=candidate`,
  `source="NISAR GUNW coherence loss"`, `area_km2`, `px`, `slope_class`,
  `mean_slope_deg`, `mean_coh_drop`, `hv_vote`, `near_flood_candidate`.
- `data/wa_2025/png/nisar_coherence_loss_20251216.png` — EPSG:3857 RGBA,
  transparent bg, #e6550d polygons rendered in.
- `data/wa_2025/png/nisar_coherence_event_20251216.png` — grayscale event
  coherence (optional extra layer).
- `data/wa_2025/png/_index.json` — `nisar_coherence_loss` +
  `nisar_coherence_event` entries, `kind: "nisar_extra"`, legend,
  attribution `"NISAR (NASA/ISRO), ASF DAAC — Beta product"`, note.
- `data/wa_2025/nisar_coherence.json` — full stats/method/caveats.
- `data/manifest.json` — rebuilt by `tools/build_manifest.py`; both
  layers forwarded with all extra fields.
- `data/metrics.json` — merged ONLY under `cases.wa_2025`:
  `nisar_coh_loss_km2` 1877.126, `nisar_coh_loss_steep_km2` 309.636,
  `nisar_coh_flood_overlap_pct` 0.0, hit 100m/500m + baselines,
  `nisar_coh_threshold`, `nisar_coherence` object, `nisar_coh_method`,
  collections record `nisar_gunw_coherence`.

## Granules used (CMR `NISAR_L2_GUNW_BETA_V1`, track 172 D, X05010)

- ref 064: `NISAR_L2_PR_GUNW_005_172_D_064_006_4000_SH_20251122T031814_20251122T031849_20251204T031815_20251204T031850_X05010_N_F_J_001`
- ref 065: `NISAR_L2_PR_GUNW_005_172_D_065_006_4000_SH_20251122T031848_20251122T031923_20251204T031849_20251204T031924_X05010_N_F_J_001`
- event 064: `NISAR_L2_PR_GUNW_006_172_D_064_007_4000_SH_20251204T031815_20251204T031850_20251216T031815_20251216T031850_X05010_N_F_J_001`
- event 065: `NISAR_L2_PR_GUNW_006_172_D_065_007_4000_SH_20251204T031849_20251204T031924_20251216T031849_20251216T031924_X05010_N_F_J_001`

Frame 065 mosaics 064 to fill the AOI (065 alone covers ~32% of the grid).

## Access method (no whole-granule download)

Dataset paths verified with ONE `h5py` visit via earthaccess fsspec
fileobj (EARTHDATA_TOKEN bearer, never printed):
`.../wrappedInterferogram/HH/coherenceMagnitude`, `.../mask`,
`.../projection` under `/science/LSAR/GUNW/grids/frequencyA/`.
Grid is already EPSG:32610 @ 20 m. Bulk reads via GDAL
`NETCDF:"/vsicurl/<asf-https-url>":/<dataset>` AOI windows (h5py/fsspec
range reads hit expiring pre-signed CloudFront URLs mid-read — 403 — so
GDAL was used for the arrays; h5py still used for the path visit as
required). Coherence reprojected 20 m→60 m with average resampling;
mask flags with nearest.

GUNW mask semantics (HDF5 attrs): 3-digit code — MSD water flag
(1=water), next two digits = ref/secondary subswath (0=invalid);
255=fill. Water flag union across pairs + GCOV water layer.

## Results (data/wa_2025/nisar_coherence.json)

- valid px (both pairs): 3,943,770; water excluded 495,230 (12.6%);
  elev ≥600 m excluded 963,674 (24.4%).
- loss: 521,424 px = **1877.1 km²** total; steep (≥15°) 309.6 km²;
  gentle 1567.5 km²; 4362 polygons ≥ MMU; 4820 px high-confidence
  (loss + HV drop ≥2 dB).
- flood overlap: **0.0 km² / 0.0%** — measured on the pre-flood-exclusion
  mask; the NISAR flood-candidate pixels are already removed by the
  water mask (Dec-10 GCOV water contains the flooded areas).
- OPERA DSWx-S1 new-water (classes 1+3) overlap: 7.96 km², 0.42%.
- Landslide hit rate vs 15 WGS catalog points: **10/15 (66.7%) @100 m**,
  15/15 (100%) @500 m. Random baseline (200 seeds, same area on steep
  valid non-water terrain): **60.9% @100 m**, 100% @500 m.

## Honest interpretation

The layer detects broad storm-window decorrelation, not landslides
specifically: the random-placement baseline nearly matches the real hit
rate (66.7% vs 60.9% @100 m), and 1877 km² is ~48% of the valid AOI —
the Dec 8-12 atmospheric river decorrelated most non-water terrain via
rain, soil-moisture change, wet snow and forest movement. The product is
labeled Beta + candidates-only in every artifact (legend, note,
geojson `status`, metrics method text). Steep-subset (309.6 km²) is the
landslide-plausible fraction; HV drop vote flags 4.8k px.

## Thresholds (reused from config.yaml via tp.landslide_coh)

`coherence_ref_min=0.4`, `coherence_drop=0.3`,
`coh_max_elev_m=600` (snow cap), `mmu_ha=3`, `hv_drop_db=2`,
steep class ≥15° (= `slope_range_deg[0]`).

## Commands run

- `python tools/wa_nisar_coherence.py` then `--no-fetch` re-runs
  (npz-cached coherence grids; all under
  `systemd-run --user --scope -p MemoryMax=2G`,
  `PYTHONPATH=.:pipeline`).
- `python tools/build_manifest.py` → manifest.json (3 cases).
- `pytest -q pipeline/tests live/tests gee/tests` → **915 passed**
  (31.7 s).
- `pytest -q pipeline/tests/test_coherence_loss.py` → 27 passed.

## Bugs found & fixed during run

1. Pre-signed CloudFront URL expired mid-read under h5py/fsspec →
   switched bulk array reads to GDAL `/vsicurl/` (re-signs per request).
2. `rasterio.warp.reproject` nearest leaves denormal bit-pattern
   (~1e-45) for mapped-zero px → flag thresholds changed to `>= 0.5`.
3. `WarpedVRT` nearest 4326→32610 returned 97% ones for binary masks
   (correct value 2.1%) → `_reproject_match` uses direct `reproject`.
4. Flood mask was not excluded from candidates → now excluded
   (landslide_coh semantics); overlap reported pre-exclusion.

## Caveats stated in products

Beta pre-calibration; December decorrelation confounds (rain, snow below
600 m cap, soil moisture, forest) — candidates never confirmed; water
union = GUNW mask + GCOV layer; flood overlap ≈0 because flood px are
water-masked.

## Not done / known limitations

- Candidate geojson is 21 MB (4362 pixel-boundary polygons, not
  simplified — honest MMU set; could be simplified later if app payload
  matters).
- `nisar_coherence_event` grayscale PNG is 4.6 MB.
- Cache `~/.cache/lead-analysis/nisar-coh/` (coh npz + NASADEM mosaic)
  deleted after commit per task rules; re-run fetches again.
