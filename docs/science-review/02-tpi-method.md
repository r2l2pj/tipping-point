# 02 — The Tipping Point Index (TPI): the method as the code runs it today

**Scope.** This section describes the TPI as the code computes it, not as the plan describes it. Where the
plan (`docs/PLAN.md`, "Modo 1" and "Nível 1/2") and the code disagree, the code is reported and the
difference is flagged.

**Code snapshot.** `tipping-point-data` at `origin/main` **90b86c1** (2026-11-15 10:30 BRT). The local
`main` checkout is 136 commits behind, so every `path:line` below refers to `origin/main`. Scripts under
`.herdr/` are committed one-off runners that produced published replay data. Decision IDs (D/E/A/L) refer
to `.crew/plans/decisions.md`.

**Short version.** The TPI is a per-pixel weighted geometric mean of three scores, each between 0 and 1.
R scores 3-day rain against that pixel's own seasonal p90/p95/p99. S rescales SMAP L4 root-zone wetness
linearly from 0.5 to 0.9. T is static terrain: slope for landslides and a HAND proxy for floods. The
whole index is local to each pixel. It has no routing, no rainfall intensity below 3 days, no soil or
lithology, and no land cover. Its thresholds are the planning document's defaults and have never been
fitted to data.

---

## 1. Formula, weights, classes

```
TPI_h = R^wR · S^wS · T_h^wT          h ∈ {slide, flood}
(wR, wS, wT) = (0.3333, 0.3333, 0.3334)   config.yaml:29  thresholds.tpi_weights
```

- Implementation: `live/tpi.py:61-77` (Now), `gee/tpi.py:145-148` (replay in GEE), and
  `gee/tpi.py:262-317` (replay recombined locally at 90 m). Uniform weights are ruling **E6**. Changing
  the weights needs Lucas's approval.
- Any factor equal to 0 makes TPI = 0. R is effectively a **hard gate**: below the pixel's p90 there is
  no risk, whatever the soil and terrain.
- NaN in any input propagates to TPI. The class raster treats NaN as class 0 (`live/tpi.py:86`), but
  every published area and PNG excludes non-finite pixels through a separate `valid` mask
  (`live/run_now.py:401-409, 444-466`).

**Classes** (`config.yaml:28`, `live/tpi.py:80-87`): the class is the number of cut points strictly
exceeded (`v > cut`). A8 and commit 7999142 confirm that Critical is strictly above 0.6.

| Class | TPI | Label (app) | Equivalent condition on R·S·T (uniform weights) |
|---|---|---|---|
| 0 | ≤ 0.2 | Baixo / Low (transparent in PNGs) | R·S·T ≤ 0.008 |
| 1 | 0.2 – 0.4 | Moderado | 0.008 < R·S·T ≤ 0.064 |
| 2 | 0.4 – 0.6 | Alto / High | 0.064 < R·S·T ≤ 0.216 |
| 3 | > 0.6 | Crítico / Critical | R·S·T > 0.216 |

**What the cube root does in practice.** With S = T = 1, rain just above p90 gives R = 0.01 and
TPI = 0.215, which is already Moderate. R = 0.064 (about 13 % of the way from p90 to p95) gives High.
R ≥ 0.216 (about 43 % of the way from p90 to p95) gives Critical. In the other direction, rain at p95
(R = 0.5) with S = 0.75 and T_slide = 0.6 (slope 24°) gives TPI 0.61, which is Critical. The geometric
mean compresses less than a plain product would (PLAN §Modo 1). The flip side is that **modest
exceedances on steep, wet pixels classify as Critical**. This interaction between the class cuts and
the cube root is where calibration matters most.

**Naming inconsistency (Now).** In `data/now/summary.json`, `critical_km2` is **High + Critical**
(class ≥ 2) (`live/run_now.py:281-282, 455-464`). Its definition string says "TPI >= 0.4", but the code
cut is strictly greater than 0.4. Ruling A8 says "critical" means TPI > 0.6 only. The replay
`metrics.json` follows A8. The Now summary does not.

---

## 2. R — rain score (identical for landslide and flood)

### 2.1 Mapping (`live/tpi.py:32-42`, `gee/tpi.py:128-142`, same `.where()` chain)

```
R = 0                                   P3 < p90
R = 0.5·(P3 − p90)/(p95 − p90)          p90 ≤ P3 < p95
R = 0.5 + 0.5·(P3 − p95)/(p99 − p95)    p95 ≤ P3 < p99
R = 1                                   P3 ≥ p99         (then clamp to [0,1])
```

- Below p90 the score is exactly 0, with no taper. This is why "dry" Now runs are almost entirely Low
  (PLAN: "Se não chover, o mapa fica quase todo Baixo").
- Each percentile is the pixel's own, for the same time of year. R is therefore a **relative anomaly**.
  The same 3-day total of 100 mm can score 1 in one pixel and 0 in a wetter one.
- **Latent edge case (found in this review, not triggered in the current domains).** If p95 = 0, then
  P3 = 0 falls in the "≥ p95" branch and gives R = 0.5. If p99 = 0 as well, zero rain gives R = 1.
  I reproduced this with a copy of `rain_score`. In the South Brazil climatology file the minima are
  p90 6.8 mm, p95 12.8 mm and p99 28.6 mm, so the case never occurs today. It would matter for a global
  release (L9) in arid regions or dry seasons.
- For reference, median thresholds over the Now domain on doy 320 (mid-November) are
  **p90 44.7 mm, p95 64.7 mm, p99 111.2 mm** of 3-day rain (read from `clim_imerg_0p1.tif`).

### 2.2 Rain inputs by mode

| Mode | Observed rain | Forecast rain | 3-day window for the frame dated X |
|---|---|---|---|
| **Now** (3-hourly) | IMERG **Early** V07. Daily `GPM_3IMERGDE` for days older than 48 h; half-hourly `GPM_3IMERGHHE` (×0.5 mm per step) for the last 48 h (`pipeline/fetch/imerg.py:60-64, 428-471`). 8-day lookback (`live/config.py:29`) | NOAA GFS 0.25° APCP (§5) | Days X−2, X−1, X **inclusive**, each day taken once (`live/tpi.py:112-120`) |
| **Replay, observed series** (published TPI series, RS 2024 / WA 2025) | GEE `NASA/GPM_L3/IMERG_V07`, half-hourly, sum × 0.5. This is IMERG **Final**: it equals GES DISC `GPM_3IMERGDF` on 537 overlap days with max abs diff 0.000 mm (`.herdr/l35-result.md`) | — | `[X − 3 d, X)`, i.e. days X−3..X−1, **day X excluded** (`gee/tpi.py:60-62, 159`; `data/SCHEMA.md:210-215`) |
| **Replay, GFS forecast frames** (L22/L28/L28b) | IMERG Early through `fetch_daily` (same fetcher as Now) for days ≤ D−1 (`.herdr/gfs_replay_run.py:79-89, 240`) | GFS D 00Z run, days D..D+3 | Days X−2..X inclusive, observed if before D and forecast otherwise (`pipeline/tp/forecast_align.py:24-56`) |
| **Replay, as-available** (A5, RS only) | IMERG Early (`.herdr/asavail_run.py:6-9`) | — | as published in `lead_time_asavail.json` |
| **Perfect forecast** (legacy upper bound, A4) | the observed TPI band of D+k reused as the "forecast" (`pipeline/tp/forecast.py:40-89`) | — | — |

### 2.3 Climatology (percentiles)

Both climatologies share these settings: `config.yaml:41-46` gives 2001–2023, 3-day windows, and
window ends at doy ± 30 d in 3-day steps, which is **21 offsets × 23 years = 483 samples**. Each sample
is the sum over `[end − 3 d, end)`.

| | Now (`live/build_clim.py`, L30-clim, since 8b76d69) | Replay (`gee/tpi.py:71-112`) |
|---|---|---|
| Source | IMERG **Final** daily V07 (`GPM_3IMERGDF`) through GES DISC OPeNDAP (`live/build_clim.py:52-53`) | GEE IMERG_V07 (Final), half-hourly |
| Doy | every doy 1..366 on the leap-year calendar; band = (doy−1)·3 + {p90, p95, p99} (`live/build_clim.py:1-15, 127-130`) | **one** image centred on the case `event_date`, used for **every day** of the window (`gee/tpi.py:106`) |
| Percentile estimator | `np.nanpercentile`, linear (`live/build_clim.py:266-277`) | `ee.Reducer.percentile([90, 95, 99])` |
| Grid | IMERG native 0.1°, Now bbox padded by 1 cell, stored rounded to 0.1 mm (`live/build_clim.py:282-288`) | exported at 10 km (`CLIM_SCALE_M`, `gee/tpi.py:30`) |
| Regrid | linear interpolation to the 0.01° grid, the same as the IMERG obs (`live/static_fetch.py:113-138`) | GEE samples at the 1 km series scale, then bilinear to 90 m (§6) |
| Gates | lat axis monotonic, covers domain, and **3 reference points vs an independent IMERG-Final computation within max(1 mm, 3 %)**; the run is unverified unless `clim_source == imerg-final-0p1` (`live/static_fetch.py:224-260`, `live/run_now.py:499-504`). Live value: worst ratio 0.027 of tolerance | none |

**Product mismatch (limitation).** In Now, IMERG **Early** rain is scored against an IMERG **Final**
climatology. The gauge check (G6: IMERG Final vs 7 INMET gauges, RS 2024) found r = 0.887 and bias
+5.5 %. Early has not been validated against gauges. Any systematic Early-vs-Final bias in the
upper tail moves R directly.

**Window convention offset (small).** In Now, the frame dated D covers rain through the **end** of day D.
The climatology for doy(D) covers windows ending at D **00Z**, which is one day earlier. Because the
climatology samples ±30 days, the seasonal effect is negligible. I record it only for completeness.

---

## 3. S — soil score (identical for landslide and flood)

```
S = clip((θ − 0.5)/(0.9 − 0.5), 0, 1)     θ = SMAP L4 sm_rootzone_wetness   config.yaml:24
```
(`live/tpi.py:45-47`, `gee/tpi.py:162`)

- **Variable.** `sm_rootzone_wetness` from `SPL4SMGP` **v008** (`gee/common.py:30-31`,
  `live/fetch.py:179, 211, 220`). This is root-zone (0–100 cm) wetness, a degree of saturation from 0 to
  1, from the L4 land-model assimilation. It is not surface soil moisture. θ ≤ 0.5 gives S = 0, which
  zeroes the TPI. θ ≥ 0.9 gives S = 1.
- **Now.** One granule only: the CMR search over the last 10 days takes `results[-1]`
  (`live/fetch.py:198-229`). That is the 3-hourly field at the granule time, not a daily mean. The
  published run of 2026-11-15 used `SMAP_L4_SM_gph_20261110T223000`, **5 days old**
  (`summary.json data_dates.smap_age_days = 5`). The same S is **held for D+0..D+3**
  (`live/run_now.py:379-387`). This is the planning document's "level 2: S constant" (PLAN Nível 2).
  - *Robustness note.* `fetch_smap_l4` assumes CMR returns granules in time order. The P(trigger) path
    instead sorts by native-id (`live/fetch.py:156-165`). I have not verified whether the CMR order
    explains the 5-day age.
- **Replay, observed series.** The mean of every SMAP granule in the 24 h before the band time,
  `[t − 1 d, t)` (`gee/tpi.py:65-68`). There is no latency: these are hindcast inputs (commit 56d015e
  labels them `data_basis` "hindcast").
- **Replay, GFS frames.** S is held at the published `s_series` band for **D−1**
  (`.herdr/gfs_replay_run.py:14-15, 379-385`), i.e. the SMAP mean over D−2 00Z..D−1 00Z. SMAP L4
  real-time latency is about 2–3 days by the project's own assumption (`.herdr/asavail_run.py:60`) and
  5 days was observed live. The forecast replay therefore gives S **1–3 days of look-ahead**. This was
  not raised in L28b, which dealt only with rain.
- **As-available replay (RS).** Uses granules valid on D−3 (`SMAP_LAG_DAYS = 3`,
  `.herdr/asavail_run.py:11-15, 60`).
- **Regrid (Now).** SMAP 9 km EASE-2 points are interpolated to 0.01° with `griddata` (linear, then
  nearest fill) (`live/grid.py:26-40`). A destination pixel whose *nearest* SMAP source is a gap is
  set back to NaN (`live/run_now.py:346-352`).
- **L31 coastal/lagoon fill (Now only).** A NaN pixel takes the nearest *valid* SMAP value if that
  value is within **9 km** (one SMAP cell) **and** the pixel is land, defined as inside the IBGE Brazil
  outline and not MCD12Q1 IGBP class 17 (`live/run_now.py:182-242, 353-359`). Pixels farther away
  stay no-data. Each run publishes `filled_km2` and `nodata_km2`. The live run shows
  `filled_km2 = 0.0` and `nodata_km2 = 202,008` (mostly ocean and areas outside Brazil). Replays are
  **not** filled: RS has 4.3 km² and WA 8.0 km² of no-data land, which changes peak numbers by
  < 0.05 % (README "Declared limitations").

---

## 4. T — terrain scores (the only factor that differs by hazard)

### 4.1 Landslide: `T_slide`
```
T_slide = clip((β − 15°)/(30° − 15°), 0, 1)       β = NASADEM slope, config.yaml:25
```
(`live/tpi.py:50-52`, `gee/tpi.py:115-120`)

- **Replay.** `ee.Terrain.slope(NASA/NASADEM_HGT/001)` on the 1″ (~30 m) DEM, exported at **90 m**
  (`gee/tpi.py:31, 364-367`). The TPI is combined on that 90 m grid (doc rule 3: coarse R and S are
  upsampled, terrain is never flattened).
- **Now.** `static-v1 terrain/slope_deg.tif` on the **0.01° (~1.1 km)** grid (`live/static_fetch.py:197-218`).
  It was exported once from the GEE asset `static/south_brazil/slope` (computed at 30 m,
  `live/build_static.py:48, 291-295`). I infer, but the code does not record, that GEE served it from
  the asset's mean pyramid, which would make it effectively a **~1 km average of 30 m slope**. Live check:
  slope p99 = 20.8°, and only **4.0 % of cells** have T_slide > 0 (`manifest.checks`). At 1 km, steep
  hollows are averaged with ridges and valley floors, so Now landslide TPI is structurally lower and
  sparser than the 90 m replay.
- **Planned refinements not implemented.** PLAN's table says to "adjust by LHASA class and land use".
  The code uses no lithology, land cover, curvature or aspect in T. Those live only in the separate
  ML T_ML layer (3a), which is not part of the TPI.

### 4.2 Flood: `T_flood`
```
H      = z − min_{‖x′−x‖ ≤ r} z(x′)        z = NASADEM, r = 1500 m  (config.yaml:27)
T_flood = clip(1 − (H − 5)/(15 − 5), 0, 1) · 𝟙[β ≤ 5°]          config.yaml:26, :38
```
(`live/tpi.py:55-58`, `gee/tpi.py:115-125`, `live/config.py:46-51`)

- H = 5 m or less gives T_flood = 1. H = 15 m or more gives 0. Slope **strictly greater than 5°**
  gives 0. Note that `live/config.py:51` hard-codes 5.0, while `gee/tpi.py:33` reads
  `slope_flood_max_deg`.
- **The proxy is not HAND.** It is "height above the lowest point within 1.5 km". It does not know
  where the drainage is, which way it flows, or whether the local minimum lies in a different basin.
  On wide floodplains (wider than about 3 km) the minimum may sit on the same flat surface, which is
  correct. On terraces next to an incised channel it underestimates flood exposure. Next to a ridge
  saddle it overestimates.
- **Replay.** Computed in GEE on 30 m NASADEM with `focalMin(radius=1500 m)`, exported at 90 m.
- **Now.** `hand_90m.tif` is computed at 3″ (NASADEM 1″ averaged 3×3, then a disc minimum
  approximated by 6 inscribed rectangles covering about 94 % of the disc, `tools/make_hand90.py:1-20`).
  It is then **averaged over 12×12 blocks** onto the 0.01° grid (`live/static_fetch.py:165-194, 209-213`,
  commit 513172b). The slope gate uses the ~1 km slope. Averaging HAND over a 1.1 km cell that mixes
  valley floor and hillslope pushes the cell mean above 15 m, so narrow floodplains drop out. Measured
  effect: median HAND 20.0 → 29.7 m, and cells with T_flood > 0 fall from 36.0 % → **28.7 %**
  (commit 513172b).

### 4.3 HAND-proxy validation against MERIT Hydro `hnd` (reference only, G2, commit b4aacf9)

Method: IoU of the "HAND < 5 m" zones and Pearson r over pixels, on one common 90 m grid over the case
AOI (`gee/hand.py`; `metrics.json cases.*.hand_validation_*`).

| Case | Radius | IoU (H<5 m) | r | % px proxy < 5 m | % px MERIT < 5 m |
|---|---|---|---|---|---|
| WA 2025 | **1000 m (best)** | **0.26** | **0.87** | 6.0 | 22.0 |
| WA 2025 | 1500 m (**served**) | 0.19 | 0.85 | 4.3 | 22.0 |
| WA 2025 | 3000 m | 0.11 | — | — | 22.0 |
| RS 2024 | **1000 m (best)** | **0.42** | **0.90** | 13.9 | 31.7 |
| RS 2024 | 1500 m (**served**) | 0.34 | 0.86 | 11.2 | 31.7 |
| RS 2024 | 3000 m | 0.24 | — | — | 31.7 |

Readings for the geoscientist:
1. The served radius (**1500 m**, `config.yaml:27`) is **not** the validated best (1000 m). PLAN Modo 3
   step 6 says to "update hand_proxy_radius_m with the best". That update was never made.
2. The correlation is high but the IoU is low. The proxy ranks terrain well but marks only a third to
   a quarter as much "HAND < 5 m" area as MERIT. With the 5–15 m ramp, the proxy will **under-flag**
   low-lying ground. The fix could be a radius change, a different ramp, or real HAND from flow routing.

### 4.4 Landslide vs flood, side by side

| | TPI_slide | TPI_flood |
|---|---|---|
| R | same 3-day pixel-local R | same 3-day pixel-local R (no upstream term; L35 tested and not served, §9) |
| S | same SMAP root-zone wetness | same |
| T | slope 15→30° ramp | HAND proxy 5→15 m inverted ramp, zeroed above 5° slope |
| Spatial support of T | replay 90 m; Now ~1 km slope | replay 90 m; Now 90 m HAND averaged to ~1 km |
| Validation reference | inventory points (RS 16,027; WA 15 WGS) | NISAR flood polygons (WA), none for RS |

---

## 5. GFS forecast-rain handling (L25, L28, L28b, L30)

- **Source.** The public NOAA AWS bucket `noaa-gfs-bdp-pds`, file `gfs.tHHz.pgrb2.0p25.fFFF`. The code
  finds the APCP message by byte range from the `.idx` file and decodes it with **rasterio/GDAL GRIB**,
  with no cfgrib/eccodes (L28) (`live/fetch.py:434-514`).
- **Bucket selection.** Only the **trailing 6-h** APCP message (`(fh−6)-fh hour acc fcst`) is read. The
  "0-N day acc" total is ignored (`live/fetch.py:473-488`). The day total is the **sum of the four 6-h
  buckets that cover that UTC day**. A bucket ending at D+1 00:00 belongs to day D (`_gfs_day_label`,
  `live/fetch.py:577-581`). For a 00Z run issued on D this gives fh 6–24 for day D, 30–48 for D+1,
  54–72 for D+2 and 78–96 for D+3. The code requires exactly 4 buckets per day
  (`live/fetch.py:614-621`).
- **Run choice (Now).** The D 00Z run is preferred. Otherwise the newest earlier cycle (18/12/06/00Z of
  previous days) whose index reaches the last needed fh. A cycle *later* than D 00Z is never used,
  because it cannot cover all of day D (`live/fetch.py:542-574`). The 00Z run is posted around 03:30Z
  (`.crew/research/model-metrics.md`), and the cron runs at :17 every 3 h (`now.yml:12`). The 00:17Z and
  03:17Z runs may therefore use the previous day's 18Z cycle, and the `gfs_issued` label says so.
- **Replay.** Always the D 00Z run (`tools/regen_gfs_replay.py:48-78`). Every COG is verified against
  independently GDAL-sampled GRIB buckets: 37 issue days (RS 16, WA 21) × 24 cells,
  **0 mismatches, max |diff| 0** (`data/gfs_replay_verification.json`).
- **Grid.** Native **0.25°** NCEP cell centres taken from the GDAL geotransform (the "L28 georeference
  truth"), with longitudes wrapped to −180..180 (`live/fetch.py:491-514`). If a 0.25° file lacks an
  hour, a 0.50° fallback is interpolated onto the 0.25° grid (`live/fetch.py:529-537`).
- **How it reaches the TPI grid.** Now: linear interpolation to 0.01° (`live/run_now.py:360-362`).
  GFS replay frames: bilinear onto the IMERG daily 0.1° grid, R is computed there, then R is bilinear
  onto the 90 m T grid (`.herdr/gfs_replay_run.py:135-232`).
- **No bias correction.** GFS daily totals are scored against **IMERG Final** percentiles as they are.
  GFS's own rain distribution (drizzle bias, smoothed extremes at 0.25°) is not quantile-mapped to IMERG.

---

## 6. Time alignment: observed vs forecast frames (L28b)

**Rule L28b** (decisions.md, L28 block): at issue time D 00Z, observed IMERG covers only days through
**D−1**. GFS run D 00Z covers **D, D+1, D+2, D+3**. Frame "D+k" is the 3-day accumulation **ending on
day D+k**, and each calendar day is counted once.

```
frame D+0 : IMERG(D−2) + IMERG(D−1) + GFS(D)        basis "observed+forecast"
frame D+1 : IMERG(D−1) + GFS(D) + GFS(D+1)          basis "observed+forecast"
frame D+2 : GFS(D) + GFS(D+1) + GFS(D+2)            basis "forecast"
frame D+3 : GFS(D+1) + GFS(D+2) + GFS(D+3)          basis "forecast"
```
- Now: `live/run_now.py:364-396` (series keyed by date: IMERG for d < today, GFS for d ≥ today;
  `tpi.frame_rain3`). The run stops if any day is missing.
- Replay: `pipeline/tp/forecast_align.py:32-56`. It raises an error if an observed day ≥ D (look-ahead)
  or a forecast day < D appears. S is held at D−1 (§3).
- **Honesty labels.** Every Now layer carries `basis`, `obs_days`, `forecast_days`, `gfs_issued` and
  `basis_label` (`live/run_now.py:251-271, 414-423`). Even "today" (D+0) is `forecast: true,
  partly_forecast: true`, because day D itself comes from GFS. Alert certainty is capped by basis:
  observed → high, observed+forecast → medium, forecast → low (`live/run_now.py:245-248`).
- **D−1 completeness is not checked (open).** `fetch_daily` returns the half-hour count per day, but
  `run_now` discards it (`live/fetch.py:77-80`). IMERG Early has about 4 h latency, so the 00:17Z run,
  and possibly the 03:17Z run, sees an incomplete D−1, and that D−1 enters two frames. A one-line guard
  using `steps` (48 expected) would fix it.

### 6.1 Date-label offset between the observed replay series and the forecast and Now frames (found in this review)

| Product | Frame labelled X covers |
|---|---|
| Replay observed TPI series (GEE) | days **X−3, X−2, X−1** (`[X−3d, X)`; `data/SCHEMA.md:210-215`) |
| Replay GFS frames, Now frames | days **X−2, X−1, X** |

So the observed band labelled "2024-05-02" covers Apr 29–May 1. The GFS frame labelled "2024-05-02"
covers Apr 30–May 2. Neither convention is wrong, and SCHEMA documents the observed one. But a timeline
that puts both under the same date compares windows **offset by one day**. Lead-time and warned-%
calculations that mix the two should be checked against this offset. `tools/warned_before.py:19-23`
documents its own convention ("band date D = rain ending D 00Z").

---

## 7. Grids, resolutions, resampling, CRS

| Quantity | Native | Now TPI grid (0.01° ≈ 1.1 km, 1150 × 1000 px, EPSG:4326) | Replay TPI grid (90 m, EPSG:4326) |
|---|---|---|---|
| IMERG (Early / Final) | 0.1° | linear interpolation (`live/grid.py:43-49`) | GEE samples at 1 km for R, then bilinear to 90 m (`gee/tpi.py:284-301`) |
| Climatology p90/95/99 | 0.1° (Now file) / 10 km export (replay) | linear interpolation | 10 km → 1 km (GEE) → bilinear 90 m |
| GFS APCP | 0.25° | linear interpolation | bilinear → 0.1° → R → bilinear 90 m |
| SMAP L4 | 9 km EASE-2 | `griddata` linear + nearest fill, gap mask, L31 fill | 1 km export, then bilinear 90 m |
| Slope | NASADEM 1″ | ~1 km aggregate (static-v1, see §4.1) | 30 m slope sampled at 90 m |
| HAND proxy | NASADEM 1″ / 3″ | 3″ proxy, 12×12 block mean | 30 m proxy sampled at 90 m |
| Outputs | — | class PNG **EPSG:3857** (nearest warp, `live/render.py:95-120`); float TPI COGs D+0/D+1 EPSG:4326; GeoJSON 4326 | 3857 PNGs, multiband COGs 4326 |

Implication: in the replay, R and S are smooth fields (≥ 1 km, bilinear) multiplied by 90 m terrain, so
spatial detail comes **only from T**. In Now, T is also ~1 km.

---

## 8. Execution modes

| Mode | Code | Inputs | Cadence and notes |
|---|---|---|---|
| **Now (Modo Agora)** | `live/run_now.py --local`, run by `live/jobs.py now` with `NOW_LOCAL=1` (`.github/workflows/now.yml:126-137`) | IMERG Early + SMAP L4 (latest) + GFS (D 00Z or earlier) + static-v1 slope/HAND + 0.1° Final climatology | cron `17 */3 * * *`, plus a watchdog workflow and a local systemd timer as backup. Frames D+0..D+3. No GEE at runtime (L25). Publishes to the GitHub `fallback` branch, then Neon (L26) |
| **Replay, observed** (RS 2024 window 04-22..05-07; WA 2025 11-30..12-20) | `gee/tpi.py run`: R, S series in GEE at 1 km; local 90 m combination | GEE IMERG_V07 (Final), SMAP 24 h mean, case climatology centred on event_date | Hindcast, not latency-aware (`data_basis`). These are the published `tpi_{slide,flood}` series |
| **Replay, as-available** (RS) | `.herdr/asavail_run.py` | IMERG Early; SMAP lagged 3 d; same case climatology | Gives `lead_time_asavail` (median −33 h, event_lead +15 h) |
| **Replay, GFS forecast** (L22/L28/L28b) | `tools/regen_gfs_replay.py` → `.herdr/gfs_replay_run.py` | IMERG Early ≤ D−1 + GFS D 00Z + S(D−1) + 90 m T | 15 RS and 20 WA issue days (the first day skipped for lack of S at D−1); 3 horizons × 2 hazards |
| **Perfect forecast** | `pipeline/tp/forecast.py` | observed TPI of D+k | labelled upper bound only (A4, README) |

**Run gates (Now).** If any check fails, `verified = false` and alerts are paused
(`live/run_now.py:285-296, 494-510`, `live/static_fetch.py:148-162`):
- slope p99 in [12°, 45°];
- T_slide > 0 on at least 1 % of cells;
- climatology geolocation (§2.3) and `clim_source == imerg-final-0p1`;
- no pixel with R = 0 and TPI > 0;
- daily IMERG COGs re-read at 3 source-cell centres;
- municipalities loaded.

The 11:39Z run on 2026-11-15 passed all of them.

---

## 9. Bugs found and fixed overnight (2026-11-14 19:00 → 2026-11-15 03:15 BRT) that touch TPI numbers

| # | Bug | Where | Effect on numbers | Fix |
|---|---|---|---|---|
| 1 | **GFS double count (~3.3×).** GEE `NOAA/GFS0P25` `total_precipitation_surface` is a trailing 1–6 h accumulation that resets every 6 h; summing every hourly image counted rain about 3.3 times | replay GFS fetcher (GEE) | forecast rain ~3.3× too high. Caught 5 min after the fetcher landed (456527c 19:14 → fix 19:19) and before the first L22 frames (f450b72, 19:59) | 086ffda: sum only fh % 6 == 0 buckets, fh > 0 (`pipeline/fetch/gfs.py:131-155`) |
| 2 | **GFS one-row (0.25°) shift.** GEE's GFS0P25 registration is one row south of the NCEP grid. 18d47e2 "fixed" a north shift to match GEE, which L28 later called a conductor error | replay GFS COGs | every forecast cell mislocated ~28 km N–S. Lead time before → after the GRIB rebuild: RS **135 h → 111 h** (earliest issue 04-22 → 04-23, n_warned 2/16 → 1); WA unchanged at 144.6 h (median 84.6 h, n = 6). Part of this change also comes from #7 | L28: 37 COGs regenerated from NOAA GRIB via GDAL, 0 mismatches (6b39ebb, 0dc5b58) |
| 3 | **IMERG first-day double count (2×).** CMR's end-inclusive temporal search also returned the daily Early granule for the first half-hourly day, so that day was summed twice | `pipeline/fetch/imerg.py:440-448` (Now and every replay that uses `fetch_daily`) | Now: the D−2 day doubled in the D+0 frame (checked point: 59.65 vs 29.825 mm). GFS replay: only the D+1 frame of the next issue day (RS issue 05-06 Critical slide 133.8 → 0.5 km², flood 3984.6 → 799.3; WA issue 12-19 slide 1921.7 → 1010.7, flood 190.9 → 11.8), all after the anchors, so lead time was byte-identical. RS as-available: 2024-05-07 slide max 0.970 → 0.716 | 26174eb, reruns 41dbf77 and 325fe24; Now COGs re-fetched 6b27e87; alert baselines reset (v2 cache key) |
| 4 | **Scrambled 0.01° legacy climatology** (per-doy COGs held "two squashed domain copies") | Now `clim/doy_NNN.tif` | percentiles at the wrong places: up to **19.6 mm** off the independent reference at the check points. R in Now was wrong at pixel level | 8b76d69 (new 0.1° IMERG-Final file; remapped legacy p90 vs new r = 0.995), 3d83e37 (gates), 87654d5 (legacy can never back a verified run) |
| 5 | **Doy off-by-one.** `tm_yday` in a non-leap year read the previous day's climatology after February | Now climatology lookup | climatology of day D−1 used for D. Expected small because of the ±30 d sampling; not quantified | 8b76d69: `leap_doy` (`live/static_fetch.py:85-87`) |
| 6 | **Stale slope files.** Committed `terrain/*.tif` had slope p99 7.6° against 20.8° in static-v1; the precomputed `t_slide.tif` was ~0 | Now terrain | **landslide TPI ≈ 0 in every Now run** before the fix | 548471b: T derived only from static-v1 slope/HAND, `terrain_checks` gate |
| 7 | **L28b misalignment.** (a) Replay: old frames used IMERG through **D** plus GFS from D+1, which counted day D as observed (look-ahead) and dropped D+3's last day. (b) GFS buckets were labelled with the end date + 1, so fh 6–24 became "D+1" (6dae74f). (c) Now treated the last IMERG day as "today": once the first half-hours of D posted (~05Z), the window became D−2 + D−1 + ~1 h of D | replay frames, Now | (c) **TPI collapsed to 0 everywhere** in run 43682317667 (05:47Z) and day D was never covered. (a) feeds the lead-time change in #2 | 6dae74f, 6b39ebb, bb6ae9d |
| 8 | **Transposed IMERG COGs.** Lon-first data on a lat-first transform | `data/now/cog/imerg_early/*.tif` (legacy 42f5eb6) | **not a TPI input** (TPI uses the in-memory array). It corrupted R1's rain-support rule and the published rain COGs | 637bc4a rewrote them; 26174eb added the `imerg_cogs_match_source` gate |
| 9 | Now T_flood from 0.01° HAND (HAND averaged at coarse scale) | Now terrain | T_flood > 0 on 36.0 % of cells (0.01° file) vs 28.7 % with the 90 m block mean; r = 0.966 between them | 513172b (a method change rather than a bug) |

The Now runs published before about 06:00Z (03:00 BRT) on 2026-11-15 include some combination of
#3–#7 and must not be quoted. The 05:47Z run passed every gate but still had the #7c collapse, because
no gate catches an all-zero map. The first run lead-analysis checked independently and passed was
05:56Z. Replays were re-audited after #3 and #4
(STATE.md: "Replays clean").

---

## 10. Open items found during this review (not fixed)

1. **Observed vs forecast date-label offset** of one day (§6.1).
2. **S look-ahead in the GFS forecast replay.** S(D−1) is used without SMAP latency (§3).
3. **D−1 IMERG completeness** is not checked in the early Now runs (§6).
4. **Served HAND radius 1500 m** vs the validated best of 1000 m (§4.3).
5. **`critical_km2` in Now** means High + Critical, contradicting A8 (§1).
6. **R edge case** when percentiles are 0. This is latent and matters only for a global release (§2.1).
7. **Stale docs.** The README "Domains and the rain climatology" still describes an int16 GEE
   ImageCollection. PLAN says the series runs to 2001–2025, but the code uses 2001–2023. README
   lead-time numbers predate the L37 onset re-anchoring (`metrics.json forecast_lead_time_onset`: RS
   147 h earliest, n 2/7; WA 128 h, median 80 h, n 5/8).
8. **SMAP "latest" selection** relies on CMR result order (§3).

---

## 11. Known limitations (science)

1. **Rain is pixel-local and nothing is routed.** TPI_flood cannot see a river flood driven by rain
   upstream. WA 2025 shows it: 0 % of the NISAR flood area was High+ in the GFS runs 24 h or 48 h
   before onset, and 0.14 % at 72 h (`metrics.json flood_warned_*`). The valley floors had high T_flood
   but no local extreme rain. **L35 test** (`.herdr/l35-result.md`): R_flood = max(R_local, R_upstream)
   over HydroBASINS lev-10 upstream areas. It raised WA High+ coverage of the flood on Dec 10 from
   41 % to 79 %, but onset-anchored warned % stayed 0, Critical km²·days rose 33 %, and **false**
   Critical rose 32 %. It is **not served** (L36e). It helped once flooding had started, not ahead of it.
2. **SMAP L4 is 9 km and late.** S is a modelled root-zone field at about 9 km, 2–5 days old in Now
   (5 days in the 2026-11-15 run). It is held constant over D..D+3. At 9 km it cannot separate a
   saturated hollow from a drained spur. Coastal and lagoon cells are masked by SMAP, and filled only
   within 9 km on land (L31).
3. **IMERG Early in Now.** Early has about 4 h latency and no monthly gauge adjustment. It is scored
   against a **Final** climatology (§2.3). The 3-day window also hides intensity: short convective
   bursts barely register.
4. **GFS underestimates convective and extreme rain.** RS 2024: the 3-day GFS forecast was 141 mm
   against 290 mm observed AOI mean ending 2024-05-02 (README). Petrópolis 2022, a ~3 h convective
   storm, is a forecast miss: GFS daily max ≈ 40 mm in the cell, and 0.1° satellite rain also misses it.
   All models score near the base rate there (`.crew/research/model-metrics.md`). GFS is not
   bias-corrected to IMERG (§5).
5. **Thresholds are not calibrated, to Brazil or anywhere.** S 0.5–0.9, slope 15–30°, HAND 5–15 m,
   slope gate 5°, class cuts 0.2/0.4/0.6, the p90/p95/p99 breakpoints and uniform weights are all the
   planning document's defaults. Only R adapts to the local climate. On the held-out ML TEST events the
   "TPI > 0.6" rule pooled PR-AUC is 0.455 at 24 h (GFS), against 0.622 for the served XGBoost. Those
   TEST numbers are still under audit (`.crew/research/model-metrics.md`).
6. **Terrain is crude.** Slope only for landslides: no lithology, soil depth, land cover, curvature or
   contributing area. For floods, a radius-minimum proxy rather than a hydrologically conditioned HAND,
   with IoU 0.19–0.34 at the served radius. In Now, both are smoothed to about 1 km (§4).
7. **The 3-day window is fixed for both hazards.** It does not cover antecedent rain beyond 3 days,
   except through S, nor the longer accumulation that large-basin floods need.
8. **One climatology per replay case.** The percentiles are centred on `event_date` for every day of
   the 16- to 21-day window, which is acceptable given the ±30 d sampling. They cover 23 years
   (2001–2023), so p99 of a 483-sample distribution rests on about 5 samples.
9. **The same resolution mismatch everywhere.** R and S are ≥ 1 km and smooth, while T is 90 m in
   replay. Spatial patterns inside a rain or soil cell are entirely terrain, so "where" is good and
   "how much" is coarse.
10. **NaN as no-data.** Areas outside SMAP coverage are no-data, not Low. Replays are not L31-filled.

---

## 12. Questions for the science lead

1. Should the R breakpoints (p90 → 0, p95 → 0.5, p99 → 1) have a taper below p90? Should the 3-day
   window differ by hazard, for example 1-day intensity for slides and 5–7-day or upstream totals for
   floods?
2. Should the flood T use real HAND (MERIT, or HAND from flow routing on NASADEM) instead of the
   radius proxy? Failing that, should the radius move to 1000 m and the 5–15 m ramp be revisited given
   the proxy's under-flagging?
3. Is a 0.5–0.9 root-zone-wetness ramp sensible for South Brazil soils? Should S use the surface layer,
   or an anomaly against SMAP's own climatology as R does?
4. Should the class cuts be fitted against the RS inventory or the ML TEST events, and if so how,
   without leaking the evaluation?

---

**Sources read.** `tipping-point-data@origin/main 90b86c1`: `live/{tpi,run_now,fetch,static_fetch,build_clim,grid,config,render}.py`,
`gee/{tpi,common,lead_time,01_tpi_index.js}`, `pipeline/fetch/{imerg,gfs}.py`,
`pipeline/tp/{forecast,forecast_align}.py`, `tools/{regen_gfs_replay,gfs_series,make_hand90,make_steep_mask,warned_before}.py`,
`.herdr/{gfs_replay_run,asavail_run}.py`, `.herdr/l35-result.md`, `config.yaml`, `data/SCHEMA.md`,
`data/metrics.json`, `data/gfs_replay_verification.json`, `README.md`, `docs/PLAN.md`, `now.yml`;
`origin/fallback:data/now/{summary,manifest}.json` (run 2026-11-15T11:39Z); git log of commits 086ffda,
247778c, 18d47e2, 26174eb, 8b76d69, 3d83e37, 87654d5, 548471b, 637bc4a, bb6ae9d, 6dae74f, 513172b,
7555383, 0dc5b58, 41dbf77, 325fe24, f450b72, b4aacf9, 4ce4d12; `.crew/plans/decisions.md`,
`.crew/STATE.md`, `.crew/research/model-metrics.md`.
