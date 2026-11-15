# 03 — Observation & response products (radar, optical, impact)

Science review, Tipping Point (NASA Space Apps 2026, "Dancing with the SARs").
Audience: geoscience / remote sensing. Written 2026-11-15 ~10:45 BRT.

**Code basis.** All `path:line` citations are against `tipping-point-data`
**origin/main @ 90b86c1** (2026-11-15 10:30 BRT). Published Now numbers come from
the **origin/fallback @ f5fbab1** snapshot (the app's primary data branch, L26),
run `generated_at` 2026-11-15T11:39Z. The local `main` checkout is 136 commits
behind origin; do not review against it. Decision IDs refer to
`.crew/plans/decisions.md`.

**Shared rule across this section.** Every satellite-derived flood or landslide
product is labelled `status: "candidate"`, never "confirmed"
(`pipeline/tp/response_nisar.py:20`, `pipeline/tp/response_dswx.py:52`,
`pipeline/tp/response_dist.py:23-24`; AGENTS.md hard rule; C3 gates NISAR
numbers on a NISAR pair existing).

---

## Summary table

| Product | Sensor / product | Where | Headline number | Validation |
|---|---|---|---|---|
| (a) Radar flood candidates | NISAR L-band GCOV, HH co-pol | WA 2025 replay; Now (R2) | WA 27.99 km² within swath (74.1 % AOI cover); Now 2.34 km² / 82 people | vs OPERA DSWx-S1, see (d) |
| (b) Near-real-time flood candidates | OPERA DSWx-S1 (Sentinel-1 C-band) | Now (R1) | 127.7 km² standard / 492 people; 288.1 km² all candidates | none in Now (no reference) |
| (c) Landslide scar candidates | OPERA DIST-ALERT-HLS | RS 2024, WA 2025, Now (R4) | RS 2,849 / 20.02 km²; Now 685 / 3.30 km² | RS: 17.5 % of 16,027 points within 100 m, 46.1 % within 500 m; 55.1 % of candidates within 500 m |
| (d) Flood-map validation | NISAR vs OPERA DSWx-S1 / DSWx-HLS | WA 2025 | like-for-like IoU 0.082, F1 0.151 | optical HLS 92.9 % cloud |
| (e) Impact | GPW v4.11 2020; VIIRS VNP46A2; IBGE | cases + Now (R3) | RS 1.49 M people in Critical; WA NTL −43.9 % | n/a |
| (f) River WSE | SWOT L2 HR RiverSP (Hydrocron) | WA 4 reaches, RS 13 reaches | series only | all obs quality 1–2 (suspect/degraded) |
| (g) Rain check | IMERG Final V07 vs 7 INMET gauges | RS 2024 | r median 0.887, bias +5.5 % | n = 182 station-days |

---

## (a) NISAR GCOV flood candidates

### a.1 Products and granules

- **WA 2025 replay** — collection `NISAR_L2_GCOV_BETA_V1` (pre-calibration BETA,
  Feb 2026 release), track **172 descending, frame 064**
  (`events/wa-2025-12.json`, `README.md:215-222`):
  - pre `NISAR_L2_PR_GCOV_006_172_D_064_4005_DHDH_A_20251204T031815_…` (2025-12-04)
  - post `NISAR_L2_PR_GCOV_007_172_D_064_4005_DHDH_A_20251216T031815_…` (2025-12-16)
  - a no-event reference 2025-11-10 is declared but not used in the flood rule.
  - Post is **4–5 days after the Skagit crest** (Dec 11–12); the README says
    early-receding water can be missed (`README.md:271-273`).
- **Now (R2)** — production collection `NISAR_L2_GCOV_V1`
  (`.crew/plans/response.md`; BETA has no Now coverage). Latest published pair
  (`data/now/response/nisar_meta.json`, fallback):
  - pre `NISAR_L2_PR_GCOV_034_024_D_101_…_20261025T211603…` (2026-10-25)
  - post `NISAR_L2_PR_GCOV_035_024_D_101_…_20261106T211603…_P…` (2026-11-06; partial frame)
  - track 24 D, frame 101, UTM EPSG:32723, valid overlap 1,599.9 km²,
    `latency_h` 99.7 (acquired 11-06 21:16Z, available 11-11 00:58Z). 102 granules
    were found in the 60-day search window. CMR latency is 4–6 d (`response.md`).

### a.2 Pairing rule (same geometry)

`pipeline/fetch/nisar.py:610-665` (`pair_latest_pass`): a pass is
(cycle, track, look). The newest pass wins. For each of its frames that overlaps the
domain by ≥ **200 km²** (`:457`), the pre image is the most recent earlier granule
on the **same track + look direction + frame**, at most **36 days** earlier
(3 × 12-day repeat, `:456`). Newer passes that could not be paired are reported
in `newer_unpaired`. So this is a strict repeat-pass pair with the same
geometry. That matters because the change rule assumes identical incidence.

### a.3 Reading and polarisation

- Only the **co-pol covariance term HHHH** (VVVV if HH is absent) is read
  (`response_nisar.py:6, 39`), from frequency A
  (`/science/LSAR/GCOV/grids/frequencyA`). HVHV is downloaded for WA
  (`README.md:228`) but **no flood rule uses it**. The `hv_drop_db: 2` threshold
  only feeds the coherence landslide vote (`pipeline/tp/landslide_coh.py:33`), and
  that path is moot because no GUNW pair crosses the WA event.
- Now: HDF5 chunk byte-ranges are read over HTTP. Data are multilooked as a block
  mean of **linear** power onto a **100 m** UTM grid (`TARGET_RES_M`,
  `response_nisar.py:40`; 25 looks for the pre image, 100 looks for the post image
  in this run). Native samples below **−40 dB** count as nodata
  (`MIN_POWER_DB`, `:41-46`). The comment there explains why: a −60 dB swath-edge
  population had pushed Otsu to a −38 dB "water" threshold on 024D_101.
- WA: subset to the AOI and warped to EPSG:4326 at native resolution. The
  `flood_warned` denominator note calls this "~12 m"
  (`metrics.json wa_2025.flood_warned_denominator_label`).

### a.4 Change-detection rule

`pipeline/tp/flood_mask.py`. Both cases run the same function. R2 calls it
unchanged (`response_nisar.py:13-16, 424-436`).

1. dB = 10·log10(γ⁰ linear) (`:35-40`), then a **3×3 median filter**
   (`:196-197`).
2. Water threshold **T**: per-scene Otsu over 512 px tiles of the post image. Only
   tiles that are **bimodal** count: both classes ≥ 5 % of the tile, and the valley
   is < 0.5 × the smaller peak (`:43-59`). T = median of tile thresholds, **clamped
   to ≤ −15 dB**. If no tile is bimodal, T = −15 dB (`:62-91`; config
   `water_db_max: -15`, ruling **A2** `decisions.md:110`).
   **In practice both published runs used the fallback**: WA `threshold_db = -15.0`
   (metrics), Now `threshold_method: "fallback"`.
3. Open-water flood (`:115-133`):
   `(post_dB − pre_dB) < −3 dB` AND `post_dB < T` AND NOT `pre_dB < T`
   (pre-existing water excluded) AND NOT permanent water mask (if given) AND
   `HAND < 15 m` (`hand_range_m: [5, 15]`, config.yaml:26).
4. Receded water: the same call with pre and post swapped (`response_nisar.py:434`).
5. **Bright flood** (inundated vegetation / urban double bounce) is a separate,
   lower-confidence layer: `+3 dB` rise, not pre-water, `HAND < 5 m`
   (`flood_mask.py:136-152`). WA: 0.66 km². Now: 0 km².
6. **Minimum mapping unit 3 ha** (`mmu_ha`, config.yaml:37), applied as a
   connected-component pixel count `ceil(3 ha / pixel ha)` (`:105-112, 211`). At
   100 m (Now) that means ≥ 3 pixels. This follows DSWx-NI's stated detectable
   size (`.crew/reports/ml-sar.md:151`).

### a.5 Permanent-water masking

- The function accepts a `watermask` (`NISAR_WATERMASK`, which is JRC GSW + ESA
  WorldCover + OSM). **R2 never passes one**: `detect()` calls
  `fm.flood_mask(pre_p, post_p, …, hand_path=hand_path, bright=True)`
  (`response_nisar.py:431`). For WA, `02_flood_mask.py --watermask` is optional,
  and no record shows it was used for the 27.99 km² run. The only permanent-water
  control actually in force is **"dark in the pre image"** (step 3). JRC GSW is
  used in R1, not here.

### a.6 HAND gate: what it is, and its effect

- "HAND" here is a **DEM-only proxy**, `HAND_proxy = z − focalMin(z, r)`, not
  flow-routed HAND. `tools/make_hand90.py:1-20` uses NASADEM 3″ (3×3 block mean of
  1″) and r = `hand_proxy_radius_m` = **1500 m** (config.yaml:27), with the
  circular kernel approximated by 6 inscribed rectangles (~94 % of the disk).
- R2 prefers the 90 m file `hand_90m.tif` (sha256 `40bc5963e6fc`) and falls back
  to the legacy 0.01° (~1 km) `hand_m.tif` (`response_nisar.py:57-70`).
- **Effect on Now** (commit 3f11551; `nisar_meta.json`):

  | Stage | km² | people |
  |---|---|---|
  | before HAND gate | 31.62 | 840 |
  | removed by HAND ≤ 15 m (90 m proxy) | 29.28 | 758 |
  | **published (90 m HAND)** | **2.34** | **82** |
  | same pair with ~1 km HAND | 3.5 | 61 |

  The gate removes 93 % of the radar change area. The 90 m HAND did not restore
  area relative to 1 km. The commit's reading is that the candidates sit > 15 m
  above the 1.5 km local minimum, i.e. they are **likely off-floodplain false
  positives** (dark slopes, or agricultural/soil-moisture change, in an L-band HH
  scene).
- **Proxy quality vs MERIT Hydro `hnd`** (validation only, G2, `data/SCHEMA.md:422`):
  zone IoU of HAND < 5 m at 90 m, **r = 1000 m** — WA IoU 0.26 / Pearson r 0.87
  (n_px 2.27 M); RS IoU 0.42 / r 0.90 (n_px 4.61 M). At 1500 m (the radius actually
  served): WA 0.19, RS 0.34. The proxy flags far less low terrain than MERIT
  (WA 6.0 % vs 22.0 % of pixels < 5 m; RS 13.9 % vs 31.7 %). The best radius is
  reported but `hand_proxy_radius_m` was **not** changed (SCHEMA.md:426-427), so
  the served HAND uses the radius that validates worse.

### a.7 Polygonisation and labelling

- Now: label → `rasterio.features.shapes` (4-connected) → simplify 0.5 px →
  EPSG:4326, precision 1e-5° (`response_nisar.py:476-530`). Properties: `source,
  pre, post, track, frame, direction, acquired, status:"candidate",
  change:"new", flood_type: open-water|bright, confidence`.
- People per polygon: GPW count × (pixel area / GPW cell area) at each pixel
  centre, summed (`:455-464`). See (e).
- WA: `pipeline/tp/polygonize.py` (UTM area, EPSG:4326 output). **Naming
  caveat:** the product key there is `flood_confirmed` (`polygonize.py:24`), a
  holdover from contract E3. The public label is "candidates", but the internal
  key contradicts the hard rule.

### a.8 WA 2025 result

`data/metrics.json` `cases.wa_2025`:
- `flooded_km2` **27.987** (geodesic polygon area; commit 1665f70 restored this
  value after a 28.29 rasterisation-inflated figure), `flooded_km2_scope` "within
  NISAR swath", `swath_coverage_pct` **74.1** (the south-west of the AOI is outside
  the swath), so it is a **lower bound** (`README.md:268-270`).
- `water_km2` 304.1 (all post-image water, incl. permanent). Bright flood 0.66 km².
- Top clusters: Snohomish valley/Fobes Hill 10.61 km², Snohomish–Monroe 3.18,
  lower Snohomish 1.27, Skykomish at Sultan 1.05 km². The Skagit flats show little
  signal because they had receded by Dec 16 (`README.md:271-273`).
- `people_flooded` 1,150.9 (GPW, GEE path).

### a.9 Now runs

- Run 2026-11-15T11:39Z: 1 pair, 8 polygons, **2.34 km² new open water, 0
  receded, 0 bright, 82 people** (meta). R3 recomputes people on its 0.0025°
  grid and reports **43** for the same polygons (`summary.json
  sources.nisar-gcov.people_flooded`). The two sampling grids disagree by about
  2×, so quote one of them, labelled.
- One flood polygon is 0.03 km² holding 41 people: urban GPW density on a tiny
  polygon. That is plausible arithmetic but fragile.
- History: 12.06 km²/384 people (f91dcb5, before the HAND rework) → 3.5 km² (1 km
  HAND) → 2.34 km² (90 m HAND).

---

## (b) OPERA DSWx-S1 flood candidates (R1, Now)

Code: `pipeline/tp/response_dswx.py` (docstring `:1-53` is the method spec),
script `pipeline/scripts/13_response_dswx.py`.

### b.1 Input and classes

- `OPERA_L3_DSWX-S1_V1` (Sentinel-1 C-band, 30 m UTM tiles), via CMR/PO.DAAC,
  about 6 h latency (`response.md`).
- WTR classes: water = **{1 open water, 2 partial (unused by S1), 3 inundated
  vegetation}**, dry = {0}. 250 HAND-masked, 251 layover/shadow, 254 ocean, 255
  fill → unobserved (`:8-12, 67-68`).
- Each tile is averaged onto a fixed EPSG:4326 **0.0025° grid** (~280 × 230 m,
  4600 × 4000 px over bbox [−58, −34, −48, −22.5]). A coarse pixel is water when
  the observed water fraction is ≥ 0.5 (`:13-17, 256`).

### b.2 Change rule: a rolling baseline, not a pre/post pair

The brief describes "new water = post AND NOT pre". **That is the WA validation
rule (d), not R1.** R1 keeps a per-pixel **rolling water-frequency baseline**
(`:18-28, 285-294`):

- `cur` = most recent observation. The baseline `water_n/obs_n` counts all
  earlier observations. Tiles within 1 h are merged as the same pass. Counts are
  halved when `obs_n` reaches 60. Water at pixels that were candidates last run is
  not folded into the baseline, so a long flood cannot absorb itself.
- **Candidate** = latest observation is water AND it was acquired within
  **144 h** of the newest acquisition AND baseline ≥ **3 observations** AND
  baseline water frequency **< 20 %** (`params` in `dswx_meta.json`; defaults at
  `:661-663`).
- Bootstrap: 60 days, 2,295 granules (2026-09-16 → 11-15), 0 failures.
- Pixels with too little baseline → `unbaselined_water_km2` (19.2 km²), never
  candidates.
- `change`: "new" = not a candidate in the previous run, otherwise "persisting".
  "receded" = previous candidate now observed dry.

### b.3 Filters (`apply_context`, `:480-513`)

| Rule | Data | Effect | km² this run |
|---|---|---|---|
| Exclude permanent wetland/water | MCD12Q1.061 2025 LC_Type1 classes 11, 17, dilated 1 LC cell (`:346, 440-464`) | dropped | 222.3 |
| Outside Brazil | IBGE malhas v3 country outline | dropped | 856.5 (Iberá, Uruguay, Paraguay) |
| Cropland | LC 12, 14 (e.g. RS rice paddies) | "lower" | — |
| Seasonal/occasional water | JRC GSW v1.4 (1984–2021) cell mean: **occurrence ≥ 25 %** OR **seasonality ≥ 3 months** (`:348-349, 414-437`) | "lower" | 0 moved (16.9 km² of flagged pixels already lower/excluded) |
| No supporting rain | see b.4 | "lower" | 2.03 moved / 1.4 people |
| Min polygon area | 0.25 km² | below → `sub_mmu_km2` | 226.4 |

Raw candidates: 1,593.3 km² → all candidates 288.1 km² → **standard headline
127.7 km² / 491.8 people**; lower confidence 160.4 km² / 396 people; 382
features. Observed area this window: 875,708 km² (`coverage_pct` 70.0).

### b.4 Rain-support rule

- **ID note:** the brief calls this "L31". In `decisions.md`, **L31 is the SMAP
  gap fill** (`decisions.md:190`). The rain rule is "conductor-approved" in commit
  **637bc4a** and has no numbered decision. Cite the commit, or give it an ID.
- Rule (`:353-355, 358-411, 765`): a candidate becomes "lower" when
  `max(local 8-day IMERG Early total, max over a 50 km ellipse) < 10 mm` AND
  JRC occurrence = 0 (never water 1984–2021). The 50 km max is described as a
  **direction-agnostic superset of upstream, with no flow routing**. Cells with
  any missing day are unknown (+∞) and are never downgraded. The rule is skipped,
  with a reason, if fewer than 7 of 8 files exist or a file does not cover the
  domain.
- This run: window 2026-11-07..14, 8/8 files, domain median 18.9 mm. 2.03 km²
  moved. A local-only rule (no 50 km) would have moved 19.6 km² / 61 people
  (commit 637bc4a).
- **Transposed-IMERG correction (637bc4a):** the Now IMERG Early daily COGs from
  42f5eb6 were written lon-first on a lat-first transform. Their footprint read as
  about −22.5..−32.5 and did not cover the domain. They were rewritten (115 × 100
  over the full bbox) with `fetch.imerg.write_cog`, and the 3-day sums were
  recomputed. Spot check: 3-day sum ending 11-14 at (−49.385, −23.635) = 74.9 mm
  vs 71.8 mm from the independent TPI path. The bounds check at `:386-391` now
  refuses such files ("transposed or truncated IMERG COG").

### b.5 What R1 is not

There is no ground truth in the Now domain. The README states that DSWx-S1 can
flag smooth or bare surfaces as water, and that the confidence rules reduce but do
not remove this (`README.md` limitations). This run's 0 km² `new_km2` and
`n_granules 0` (no new granules since the 11-14 09:06Z acquisition) mean the
headline is **persisting** state from earlier passes. `latency_h` is null for that
reason.

---

## (c) OPERA DIST-ALERT-HLS landslide-scar candidates

Code: `pipeline/tp/response_dist.py` (`:1-24` spec), `pipeline/scripts/16_response_dist.py`.

- Product `OPERA_L3_DIST-ALERT-HLS_V1` (LP DAAC), 30 m MGRS tiles. For each tile
  the newest granule in [start, end] is read: `VEG-DIST-STATUS` + `VEG-DIST-DATE`.
- Pixel rule (`:138-144`):
  - STATUS ∈ **{5, 6, 8}**: provisional, confirmed or finished disturbance with
    **≥ 50 % vegetation-cover loss**. First single detections are dropped.
  - VEG-DIST-DATE (days since 2020-12-31) inside the window, i.e. **first
    detection** in the window.
  - **Slope ≥ 15°** from a 1″ NASADEM mask (`tools/make_steep_mask.py`, central
    differences in metres). The WA replay uses a slope > 15° mask derived from the
    T_slide COG (`16_response_dist.py:20-21`).
  - MCD12Q1 2025 LC not in {12, 14, 17} (cropland, cropland mosaic, water).
- Polygons: 8-connected per tile, kept between **3 px (0.0027 km²) and 1 km²**.
  Larger patches are treated as clear-cut/fire and excluded (`:39-44, 161`).
  Overlapping MGRS tiles are de-duplicated. Now runs are clipped to Brazil.
  People = GPW density at the centroid × area.

**RS 2024 validation** (window 2024-04-27..06-15; `metrics.json rs_2024.scar_*`;
`hit_rates`, `response_dist.py:320-344`):

| Variant | n | km² | inventory points ≤ 100 m | ≤ 500 m | candidates ≤ 500 m of a point |
|---|---|---|---|---|---|
| **default (published)**: {5,6,8}, ≥ 3 px | 2,849 | 20.02 | **17.45 %** | **46.13 %** | **55.07 %** |
| {5,6,8}, ≥ 1 px | 10,326 | 28.4 | 32.61 % | 70.53 % | 43.52 % |
| incl. < 50 % loss, ≥ 3 px | 11,403 | 82.1 | … | … | … |

- Reference: UFRGS/Cemaden inventory, n = 16,027 points (E8, `decisions.md:35`).
  Inventory points are undated.
- "Precision" here is a **proximity proxy**: the share of candidates within 500 m
  of any inventory point. It is not object-level precision. With 16 k points in
  the Serra Gaúcha, a 500 m buffer covers a lot of steep ground, so the chance
  level is not small and was not measured. A random-polygon baseline on the same
  steep, non-crop mask is the missing control.
- The sensitivity runs were measured before the 15 m publish compaction
  (`scar_sensitivity_note`).
- **WA 2025** (2025-12-05..2026-02-28): 176 candidates, 1.25 km², 13.8 people,
  **0 of 15** WGS points within 500 m. n = 15 cannot measure precision, and winter
  cloud and snow limit HLS looks (`README.md` limitations).
- **Now** (30-day window 2026-10-16..11-15, 147 tiles, latest granule 11-05):
  **685 candidates, 3.30 km², 89 people**. Of 2,917 km² of loss, 2,904 km² was
  flat (< 15°), 3.1 km² crop/water, 4,913 polygons were under 3 px, and 200 were
  outside Brazil. Caveat in the meta: outside a rain event, steep-slope vegetation
  loss is mostly **forestry harvest / fire**. DIST latency is about 1–2 weeks.

---

## (d) Flood-map validation vs OPERA DSWx (WA 2025)

Code: `tools/validation_metrics.py` (GEE catalog `OPERA/DSWX/L3_V1/S1` and
`/HLS`, L10b `decisions.md:102`), `tools/wa_opera_layer.py`. Definitions are in
`data/SCHEMA.md:407-416`.

- No OPERA granule exists on the NISAR post date 2025-12-16. The comparison uses
  the nearest DSWx days, **2025-12-15 and 12-17** (`validation_metrics.py:53-54`).
  S1 water classes are {1, 3}, cloud is 253 and nodata is 255 (`gee/opera.py:44-52`).
  Counting is done at a 30 m EE scale.

| Comparison | Reference | IoU | F1 | P | R | confusion (30 m px) |
|---|---|---|---|---|---|---|
| **Raw, all water** | every DSWx-S1 water px 12-15/17 | **0.0075** | 0.0149 | 0.198 | 0.0078 | TP 9,287 · FP 37,576 · FN 1.19 M |
| **Like-for-like new flood** | DSWx-S1 water 12-15/17 AND NOT water 12-01..07; cloud-free/observed on both dates | **0.0816** | **0.1509** | **0.1873** | **0.1263** | TP 8,777 · FP 38,086 · FN 60,704 |
| Optical, all water | DSWx-HLS 12-15/17, cloud-free px only (6 scenes) | 0.0133 | **0.026** | — | — | TP 8,137 · FP 34 · FN 603 k |

- Raw FN of 1.19 M px is **permanent/pre-existing water** (1.21 M pre-water px).
  The NISAR new-flood mask excludes that by design (`opera_fn_diagnosis`). The
  pre-water window used 31 granules on 12-02/03/05/07 (`.herdr/implement-wa-opera.md`).
- Reproduced independently by `wa_opera_layer.py --check-only` (exact) and in a
  local recount (ratios 0.993/0.997/1.004).
- **Pixel-area note for readers:** the 30 m EE lattice at 48° N is about 600 m²
  per pixel, not 900. So NISAR TP+FP = 46,863 px ≈ 28 km² (consistent with 27.99),
  and DSWx-S1 new flood TP+FN = 69,481 px ≈ 42 km².
- **Interpretation:** about 19 % of NISAR new-flood pixels are also DSWx-S1 new
  water, and NISAR catches about 13 % of DSWx-S1 new water. The two products differ
  in band (L vs C), date (12-16 vs 12-15/17, on a receding flood), resolution and
  algorithm. DSWx-S1 is not ground truth. Read this as **agreement between two
  candidate products**, not as accuracy.
- **Cloud facts** (why radar is the story):
  - WA: `cloud_pct` **92.89 %** of the AOI, from the DSWx-HLS cloud class (WTR 253)
    on 2025-12-15/17, 6 HLS scenes. Optical saw 7.1 %.
  - RS: `cloud_pct` **100.0 %** from HLS v2.0 Fmask (bits 1–4: cloud / adjacent /
    shadow / snow) over **20 HLSL30 + HLSS30 scenes on 2024-05-01, 05-02, 05-03**.
    A pixel counts as clear if it is clear in any scene, and none was
    (`rs_2024.cloud_pct_source`, `hls_scene_dates`).
- RS 2024 has **no radar flood map**: NISAR predates the event, DSWx-S1 has 0
  granules over RS for 2024-05-01..20, and the OPERA RTC-S1 route (2024-05-08/11)
  is blocked on Lucas accepting the ASF EULA (gaps-data G1). RS `flooded_km2`,
  `people_flooded`, `iou` and `f1` are null with reasons.

---

## (e) Impact

### e.1 People: GPW v4.11, 2020

- **Replay cases** (`gee/impact.py:50-59, 158-160, 210-220`): GEE
  `CIESIN/GPWv411/GPW_Population_Count`, the epoch nearest the event →
  `gpw_year` **2020** for both. Values are summed with `reduceRegion(sum)` at
  **scale 1000 m**.
  - `people_critical` = sum where event-day TPI > 0.6 (A8: "critical" means
    > 0.6 only, `decisions.md:116`). RS **1,488,833** (2024-05-03 critical day) and
    WA **229,782** (2025-12-12).
  - Totals: RS 5,298,955 and WA 3,431,240 (`population_total`).
  - `people_flooded` (WA only) = 1,150.9.
- **Now** (`data/now/static/gpw/gpw_popcount_2020_30sec.tif`, the **UN-adjusted**
  2020 count, sha-pinned in `now.yml:86`). R2 uses area-weighted pixel-centre
  sampling. R1/R3 sum GPW on the 0.0025° grid with centre-sampled masks
  (`all_touched=False`; `response_summary.py:21-28`).
  - Now `people_critical` 32,051, `people_exposed` 32,587.
- **Why ≤ 1 km:** GPW is a **30″ (~0.9 km) count grid**. It carries no information
  below that, so every sampler either works at ≤ 1 km or distributes each cell's
  count by area. Coarser reductions would merge cells and smear people into
  polygons they are not in. Sub-km flood polygons get **density × area**, which
  assumes uniform density within a cell. That is why a 0.03 km² urban polygon can
  hold 41 people.
- Caveat: the cases use the GEE non-UN-adjusted count, while Now uses UN-adjusted.
  The difference is small at state scale but is an inconsistency.

### e.2 VIIRS Black Marble night-light drop

`gee/impact.py:9-10, 51-57, 140-155, 210-226`. `NASA/VIIRS/002/VNP46A2`
`DNB_BRDF_Corrected_NTL`, masked to `Mandatory_Quality_Flag == 0`, at 500 m. The
value compares the mean of the **30 nights before** the event date with the mean
of the **7 nights after**, over the **affected area** (event-day TPI > 0.6,
plus the NISAR flood for WA). drop % = (before − after) / before.

- RS (event_date 2024-05-02): 2.586 → 1.730 nW·cm⁻²·sr⁻¹, **−33.1 %**.
- WA (2025-12-10): 0.746 → 0.418, **−43.9 %**.
- No seasonal/lunar or snow correction beyond BRDF/MQF, and no control area. The
  affected area is defined by our own index, which makes the drop
  self-referential. A WA December cloud/snow effect on the 7-night mean is
  plausible. Treat these as context, not as an outage estimate.

### e.3 Priority zones (R3)

`pipeline/tp/response_summary.py`. R1 (standard confidence) and R2 flood
polygons are unioned with the live **TPI Alto/Crítico** areas
(`geojson/critical_areas.geojson`, class ≥ 2). Areas are **geodesic**
(pyproj.Geod) after a union, so overlapping sources count once (overlap
0.0 km² this run).
- Municipalities come from **IBGE** malhas (UFs RS, SC, PR, SP, MS;
  `:59, 299-345`; a static-v1 copy is preferred).
- Ranking: **exposed people (desc), then exposed area, then IBGE code**
  (`:426-428`).
- Run 11:39Z: **69 municipalities**. #1 Florianópolis (TPI flood Crítico, 29,703
  people, 38.6 km²), #2 Santo Amaro da Imperatriz (landslide, 422), #3 Avaré-SP
  (DSWx 0.50 km², 269), #4 Viamão-RS (DSWx 7.28 km², 49), #5 São Miguel
  Arcanjo-SP (NISAR 2.28 km², 42).
- Headline: `flooded_km2` 129.8 (R1 127.5 + R2 2.34), lower confidence 160.3,
  `critical_km2` 257.8, `exposed_km2` 387.7.

### e.4 Alerts logic

Two distinct lists in `data/now/alerts.json`:
- **`active[]` (public map list)** (`live/alerts.py:131-150`): every current
  Alto/Crítico TPI area for today or D+1, clustered at about 3 km
  (`ALERT_CLUSTER_PX = 3` on the 0.01° grid, `:112`). It is not affected by
  upgrades, rate limits or quiet hours. Each item carries `first_issued_at`,
  `municipality`, `basis`, `obs_days`, `forecast_days`, `gfs_issued` and
  **`certainty`**.
  - **Certainty rule** (`live/run_now.py:245-248`): `observed` → high,
    `observed+forecast` → medium, `forecast` → low. Certainty is capped by the
    rain basis; "high" requires the trigger in observed data.
  - This run: 5 active (e.g. Florianópolis flood Crítico, 231.96 km²,
    observed to Nov 14 + GFS Nov 15 → medium), 0 notifications.
- **`observed[]`**: satellite candidates (DSWx-S1, NISAR, DIST), labelled
  "observations, not TPI alerts", with confidence standard or lower.
- **`notifications[]` + Telegram send** (`live/notify.py:1-26, 48-53, 302-328`):
  - Sent only on an **upgrade to class ≥ 2** that is above the chat's
    `last_class`.
  - At most **1 message / 6 h per chat**.
  - **Quiet hours 23:00–05:59 America/Sao_Paulo** (`live/config.py:53`), except
    Crítico.
  - A drop sends "risco reduzido", never during quiet hours.
  - Messages that are held keep `last_class`, so they retry on the next run.
  - Nothing is sent if the manifest is `alerts_paused` or `verified != true`.
- Text (`notify.py:158-193`): signed **"Tipping Point (não oficial)"**, says
  "sua região", states the hazard, the day, the basis (observed IMERG through dd/mm
  or "NOAA GFS issued …"), and the data dates. It always ends with **"Siga as
  orientações da Defesa Civil. SMS 40199."** It never speaks in Defesa Civil's
  voice. This fixed gaps-live G2.
- Live status: the bot needs `TELEGRAM_TOKEN`, which only Lucas can provide
  (gaps-live G3). Satellite candidates are not in the push logic; only TPI
  classes trigger sends.

---

## (f) SWOT river WSE series

`tools/wa_swot_series.py` (also used for RS) → `data/<case>/series/swot.json`.
Source: SWOT L2 HR RiverSP via PO.DAAC **Hydrocron**, fields `wse, wse_u,
reach_q`. Fill rows are dropped. Quality legend: 0 good / 1 suspect /
2 degraded / 3 bad.

| Case | Reaches listed | With data | Obs | Quality counts | Window |
|---|---|---|---|---|---|
| WA (Skagit/Suiattle SWORD 7831…) | 6 | **4** (011, 055 not in Hydrocron) | 46 | q1 = 28, q2 = 18 | 2025-11-01..2026-01-31 |
| RS (Jacuí / Guaíba, 6411…/6412…) | 15 | **13** (100045 missing; 300024 all fill) | 146 | q1 = 81, q2 = 65 | 2024-04-01..06-30 |

- **No observation has quality 0 (good)**. Everything is suspect or degraded.
- RS: 11 of 13 reaches peak on **2024-05-05/06**, with ranges of 6.8–17.5 m on the
  lower Jacuí. That is consistent with the documented Guaíba/Jacuí peak
  (event_dates peak 05-05). Reach 64110700015 has its maximum, 18.5 m, on
  **2024-04-04** (pre-event; other passes are ~0.5 m), which is an outlier.
- WA: reach 021 (tidal lower Skagit, typical ~1.5–3 m) shows a 29.5 m maximum on
  2026-01-02, which is an outlier. Reach 071 has a 2.8 m range peaking 12-14.
  Reach 141 (Suiattle) peaks 11-24, before the event.
- These are **context series only**. Uncertainties `wse_u` reach 0.48–0.72 m on
  some RS passes, there is no outlier screening beyond fill, and the revisit is
  about 21 days at a given reach. They should not be read as hydrographs.

---

## (g) INMET gauge check of IMERG (RS 2024)

`tools/imerg_vs_gauge.py`. Validation only (L19, `decisions.md:141`); never an
index input.
- IMERG **Final V07 daily** (`3B-DAY…V07B`) at the station's 0.1° cell, compared
  with INMET hourly gauges summed to **UTC** days. Window 2024-04-20..05-15.
  **7 stations, 182 station-days.**
- **Pearson r median 0.887** (range 0.60 Teutônia – 0.98 Porto Alegre),
  **bias median +5.5 %**, event-total ratio median **1.118**.
- `bias_pct` = window-total bias and `event_total_*` = event window. They differ
  by station (e.g. Rio Pardo bias +4.0 % but event total 385 → 488 mm; Canela
  −12.8 % but event totals equal at 562 mm). Teutônia's event total is 280 mm
  gauge vs 514 mm IMERG (likely gauge gaps during the storm, or point-vs-cell
  convective variance). Bento Gonçalves is −16.7 % (IMERG under-reads the
  wettest Serra station).
- Caveat in the data: "point gauge vs 0.1° cell". Note that this checks IMERG
  **Final**, while the Now job and the replay lead-time series use IMERG
  **Early**. Early is not validated here.

---

## Known limitations (for the science reviewer)

1. **NISAR threshold is the −15 dB fallback in both published runs.** The
   adaptive Otsu (A2) never fired. Method text should say "−15 dB (adaptive
   Otsu attempted; no bimodal tiles)".
2. **No permanent-water mask in R2** (and none on record for WA). Only
   "dark in the pre image" excludes standing water. `NISAR_WATERMASK` is wired
   in the function but not passed (`response_nisar.py:431`).
3. **HAND is a DEM-minimum proxy**, not drainage-routed HAND. It flags 3–4× less
   low terrain than MERIT. It is served at r = 1500 m though 1000 m validates
   better (WA IoU 0.19 vs 0.26). In Now it removes 93 % of the radar change
   (31.6 → 2.3 km²). The gate is doing most of the work, and it is not validated
   against floods.
4. **HV unused; bright flood only on HAND < 5 m.** L-band HH single-pol change
   is sensitive to soil-moisture and agricultural change. That is the likely
   origin of the 29 km² HAND-removed area in Now.
5. **WA post scene is 4–5 d after the crest** and covers 74.1 % of the AOI, so
   27.99 km² is a lower bound, and the data are BETA pre-calibration.
6. **Validation is product-vs-product.** IoU 0.082 / F1 0.151 against DSWx-S1
   (C-band, ±1 d), with no in-situ flood extent. The optical F1 0.026 is on 7 %
   observed area and is not comparable.
7. **R1 change rule is a rolling frequency baseline** (< 20 % water over ≥ 3
   prior observations), not pre/post. In an extended wet season it can drift. The
   rain rule has no flow routing (a 50 km isotropic max). The "L31" label in the
   brief is wrong: L31 is the SMAP gap fill.
8. **Scar "precision" is a 500 m proximity proxy with no chance baseline**.
   Hit rates at 100 m are low (17.5 %). Now candidates outside events are mostly
   harvest or fire. DIST latency is 1–2 weeks.
9. **People numbers depend on the sampler**: NISAR Now 82 (meta) vs 43 (R3). Cases
   use GEE GPW (non-UN-adjusted) at 1 km, Now uses UN-adjusted 30″. GPW 2020 is
   modelled from census data, not observed.
10. **Night-light drop has no control area** and is measured over our own
    Critical mask.
11. **SWOT: 0 good-quality observations**, obvious outliers kept (RS 04-04 18.5 m,
    WA 01-02 29.5 m).
12. **The INMET check validates IMERG Final, not the Early run used operationally.**
    n = 7 stations.
13. Internal product key `flood_confirmed` (`polygonize.py:24`) conflicts with
    the "candidates" rule. It is cosmetic but should be renamed.
14. RS 2024 has no flood extent at all, pending the ASF RTC-S1 EULA (Lucas).

---

**File:** `~/code/r2l2pj/.crew/science-review/03-observations-response.md`

**Summary (5 lines):**
1. NISAR flood candidates: HH-only repeat-pass change (Δ < −3 dB into < −15 dB water, not pre-water, HAND-proxy < 15 m, 3 ha MMU). Otsu fell back to −15 dB in both runs, and there is no permanent-water mask. WA = 27.99 km² within a 74.1 % swath; Now = 2.34 km² after HAND removed 29.3 of 31.6 km².
2. OPERA DSWx-S1 R1 uses a rolling < 20 % water-frequency baseline, not pre/post. Wetland/water is excluded, cropland, JRC-seasonal and no-rain (8 d IMERG < 10 mm within 50 km) are downgraded. Headline 127.7 km² / 492 people. The transposed IMERG COGs were fixed in 637bc4a.
3. DIST-ALERT scars (≥ 50 % loss, slope ≥ 15°, 3 px–1 km²): RS 2,849 candidates; 17.5 % / 46.1 % of 16,027 points within 100 / 500 m, and 55.1 % of candidates within 500 m, a proximity proxy with no chance baseline.
4. WA validation vs DSWx-S1: like-for-like IoU 0.082 / F1 0.151 (P 0.187, R 0.126), raw 0.0075. Optical is useless under cloud (WA 92.9 %, Dec 15/17; RS 100 %, 20 scenes May 1–3).
5. Impact: GPW 2020 at ≤ 1 km, VIIRS NTL −33 % RS / −44 % WA without controls, IBGE priority zones (69), alerts capped 1 per 6 h with quiet hours and certainty by rain basis. SWOT series are all suspect/degraded. INMET: r 0.89, +5.5 % (IMERG Final).
