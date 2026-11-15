# 06 — Open questions and refinement agenda for the science/geology reviewer

Section 6 of the science review. Audience: the geoscientist (landslides, floods, remote sensing) who
will refine the method. Written 2026-11-15.

This section adds no new analysis. It collects every open question, limitation and suspected issue
raised in sections [01](01-product-architecture.md)–[05](05-validation-claims.md), removes duplicates,
and orders them by priority. Each item gives:

- **Q**: the question, in one line;
- **Why it matters**;
- **Now**: the current state, with a link to the section and the key number;
- **Suggested**: an experiment to run or a decision to make;
- **Effort**: **S** = up to half a day, mostly a decision or a re-score of existing outputs;
  **M** = 1–3 days, new runs on data the team already has; **L** = more than 3 days, or new data or
  methods.

All numbers are as of `tipping-point-data` `origin/main` 90b86c1 and the published `fallback` snapshot
of 2026-11-15 11:39Z, the same snapshot as sections 01–05. The independent ML overfit audit
(`.crew/research/overfit-audit.md`) **had not been written when this section was compiled**, so its
verdict is not reflected here (see E4).

---

## Top 10 to look at first

Ranked by how much the answer could change what we claim or serve, then by effort.

| # | Item | Question | Effort |
|---|---|---|---|
| 1 | [D2](#d2-null-baselines-for-every-hit-rate) | What do the hit rates (RS 85.9 % Critical, 36 % in the forecast, 55 % scar "precision") look like against a random-steep-terrain baseline? | S–M |
| 2 | [D3](#d3-false-alarm-rate-on-non-event-days) | How often does the same rule fire Critical on ordinary days? No false-alarm denominator exists. | M |
| 3 | [F2](#f2-is-critical-too-broad-to-be-useful) | Is "Critical" too broad, at 30–34 % of each AOI on the peak day? | M |
| 4 | [D1](#d1-which-onset-anchor-and-an-instant-or-a-window) | Should the "before the damage" anchor be hazard-specific, or a window? Moving it one day turns RS 36 % into 0 %. | S |
| 5 | [E5](#e5-the-live-map-serves-the-v1-model-and-the-leaked-v1-t_ml) | The live P(trigger) map is still v1 × the v1 T_ML that leaked RS. Keep it, swap it, or switch it off? | S |
| 6 | [F1](#f1-the-radar-confirms-tagline) | Keep the "Radar confirms" tagline, when NISAR vs Sentinel-1 gives F1 0.15 and there is no radar landslide evidence? | S |
| 7 | [B2](#b2-hand-proxy-radius-1000-m-vs-1500-m-or-real-hand) | HAND proxy radius: 1000 m validates better than the 1500 m we serve. Or should we use real flow-routed HAND? | S / M |
| 8 | [B4](#b4-nisar-water-threshold-15-db-otsu-never-fired) | NISAR flood threshold: adaptive Otsu never fired, so both published maps use the −15 dB fallback. Is −15 dB right for L-band HH? | S |
| 9 | [E3](#e3-t_ml-vs-the-1530-slope-rule) | The 15–30° slope rule beats the learned susceptibility (PR-AUC 0.829 vs 0.747). Should "where" be the slope rule? | S |
| 10 | [B1](#b1-pixel-local-rain-vs-upstream-routing) | Floods: the TPI uses pixel-local rain with no routing, and the WA river flood scored 0 % before onset. What is the right fix? | L |

Close behind these: [A1](#a1-rain-score-breakpoints-p90--p95--p99-and-the-hard-zero-below-p90)/[A2](#a2-class-cuts-02--04--06-and-uniform-weights)
(threshold calibration), [C3](#c3-smap-9-km-resolution-and-25-day-latency) (SMAP latency in the replay) and
[E6](#e6-petrópolis-was-scored-with-nan-forecast-rain) (the Petrópolis NaN bug, a quick fix).

---

## A. Landslide trigger physics and thresholds

### A1. Rain score breakpoints (p90 / p95 / p99) and the hard zero below p90

- **Q.** Are p90 → R 0, p95 → 0.5 and p99 → 1 the right anchors? Should R taper below p90 instead of
  dropping to exactly 0?
- **Why it matters.** R is a hard gate: below the pixel's p90, TPI = 0 whatever the soil and slope. The
  cube root also makes small exceedances count: with S = T = 1, rain just above p90 already gives
  Moderate, and 43 % of the way from p90 to p95 gives Critical.
- **Now.** Doc v4 defaults, never fitted. Median thresholds over the Now domain in mid-November are
  p90 44.7 / p95 64.7 / p99 111.2 mm of 3-day rain. p99 of a 483-sample distribution rests on about 5
  samples, and the samples are overlapping 3-day windows, so they are serially correlated. The code
  uses 2001–2023, but PLAN says 2001–2025. ([02 §2.1, §2.3, §11.8, §12 Q1](02-tpi-method.md))
- **Suggested.** Re-score the RS replay at the inventory points against a steep-terrain null (D2),
  sweeping (a) the breakpoints p85/p90/p95, (b) a linear taper from p75, (c) p99 replaced by a fitted
  GEV return level. Choose using lift over the null *and* the non-event false-alarm rate (D3), not hit
  rate alone. Also decide whether to extend the climatology to 2025.
- **Effort.** M

### A2. Class cuts 0.2 / 0.4 / 0.6 and uniform weights

- **Q.** Should the class cuts (and the uniform weights, ruling E6) be fitted to data, and how can that
  be done without leaking the evaluation?
- **Why it matters.** Critical is R·S·T > 0.216. Rain at p95 (R = 0.5) with S = 0.75 and a 24° slope is
  already Critical. This sets the 30–34 % Critical coverage in F2.
- **Now.** Defaults from the doc. On the held-out ML TEST events, the "TPI > 0.6" rule gets recall
  0.23–0.29 at high precision, and 24 h PR-AUC 0.455 against 0.622 for the XGBoost model. Changing the
  weights needs Lucas's approval (E6). ([02 §1, §11.5, §12 Q4](02-tpi-method.md);
  [04 §7.1](04-ml-models.md))
- **Suggested.** Fit the cuts leave-one-event-out on the ML TEST inventories, scoring the TPI rule as
  `rule_tpi` already does. Report the cut that gives a target Critical area fraction on non-event days
  (D3). Treat RS and WA as final checks only.
- **Effort.** M

### A3. The 3-day window: antecedent rain vs intensity

- **Q.** Is one fixed 3-day window right for landslides? Should a short-intensity term (1-day or
  sub-daily) be paired with a longer antecedent index (7–30 d)?
- **Why it matters.** The 3-day sum hides short convective bursts. Antecedent rain beyond 3 days enters
  only through S, and S adds little (A5).
- **Now.** Fixed 3 days for both hazards. In the ML model, relative 3-day rain carries the trigger
  (`rain_72h_rel` gain 0.362, then `rain_24h_rel` 0.136 and `rain_7d` 0.121). Landslide rate across
  rain_72h/p99 quintiles goes 2 → 5 → 10 → 18 → 38 %. ([02 §11.3, §11.7, §12 Q1](02-tpi-method.md);
  [04 §7.7](04-ml-models.md))
- **Suggested.** On the ML TEST inventories, compare R built from 1-day, 3-day and 7-day sums and from
  a 1-day-max × antecedent-index form (an intensity–duration style threshold). Keep the best per hazard.
- **Effort.** M

### A4. Convective events such as Petrópolis 2022

- **Q.** Should the product say plainly that it cannot see short convective storms, or add a sub-daily
  intensity term?
- **Why it matters.** Petrópolis was a ~3 h storm. GFS put about 38–40 mm in the cell, and 0.1° daily
  satellite rain also smooths it out. Southern Brazil has many such events.
- **Now.** Called a "forecast miss", but the published Petrópolis ML metrics are an artefact of NaN
  forecast rain (E6), so the miss has not actually been measured. ([02 §11.4](02-tpi-method.md);
  [04 §9.4](04-ml-models.md))
- **Suggested.** First fix E6 and re-score. Then decide: either document convective storms as out of
  scope, or test a max-3 h IMERG half-hourly intensity feature in Now (observed only, since GFS cannot
  resolve it).
- **Effort.** S (decision after E6) / L (intensity term)

### A5. What role should soil wetness play?

- **Q.** Should S be a 0.5–0.9 ramp on SMAP L4 root-zone wetness, an anomaly against SMAP's own
  climatology (as R is for rain), the surface layer, or a modifier rather than a gate?
- **Why it matters.** θ ≤ 0.5 zeroes the TPI. In WA, SMAP stayed low until the Dec 10 frame, which
  helped keep the flooded valleys at 0 %. In the ML data, soil adds almost nothing once rain is known.
- **Now.** Linear ramp 0.5 → 0.9 on root-zone wetness (0–100 cm), never calibrated for South Brazil
  soils. ML: soil permutation drop 0.002; within the top rain quintile, SMAP wetness quartiles give
  38 / 33 / 35 / 39 % landslide rate. But texture plus SMAP improves the horizon product (24 h GFS
  PR-AUC 0.622 vs 0.553). ([02 §3, §12 Q3](02-tpi-method.md); [04 §7.7](04-ml-models.md);
  [05 §3.2](05-validation-claims.md))
- **Suggested.** Ablate S in the RS and WA replays (S = 1, S = anomaly percentile, S from the surface
  layer) and compare lift and false alarms (D2, D3). Decide whether S should gate or only scale.
- **Effort.** S–M

### A6. Slope ramp 15–30° and terrain resolution in Now

- **Q.** Is 15–30° the right slope ramp for southern Brazil, and how should 30 m slope be aggregated to
  the 1 km Now grid?
- **Why it matters.** In Now, slope looks like a 1 km mean of 30 m slope (inferred, not recorded in
  code). Steep hollows are averaged with ridges and valley floors, so Now landslide TPI is lower and
  sparser than the 90 m replay. Today Now shows 0 km² landslide Critical.
- **Now.** Now slope p99 is 20.8°, and only 4.0 % of cells have T_slide > 0. There is no lithology,
  soil depth, curvature, contributing area or land cover in T (PLAN's "adjust by LHASA class and land
  use" was never implemented). The plain slope rule still beats the learned T_ML (E3).
  ([02 §4.1, §11.6](02-tpi-method.md); [01 §2.1](01-product-architecture.md))
- **Suggested.** Replace the 1 km mean with "fraction of 30 m cells steeper than 15°" or the p90 slope
  within the cell. Test 10–25° and 20–35° ramps against the RS inventory, using hard negatives (steep
  cells in the footprint that did not fail).
- **Effort.** M

### A7. R edge case when the percentiles are 0 (latent)

- **Q.** Guard the rain score for dry climates, where p95 = 0 gives R = 0.5 for zero rain?
- **Why it matters.** It does not occur in the current domains (minimum p90 6.8 mm), but it would after
  a global or arid-region expansion (L9).
- **Now.** Reproduced with a copy of `rain_score`. ([02 §2.1](02-tpi-method.md))
- **Suggested.** Add a minimum-rain floor (e.g. R = 0 when P3 is below a few mm) before any new domain.
- **Effort.** S

---

## B. Flood physics

### B1. Pixel-local rain vs upstream routing

- **Q.** What is the missing ingredient for river floods: upstream rain, travel time and routing, or
  snowmelt and rain-on-snow (Cascades)?
- **Why it matters.** TPI_flood multiplies local rain by local terrain. In WA the flooded valley floors
  had high T_flood (mean ≈ 0.96) but local rain stayed below p90, so the index scored the NISAR flood
  area **0 % at 24 h and 48 h and 0.14 % at 72 h** before onset. The 3-day window is also short for
  large basins.
- **Now.** L35 tested R_flood = max(R_local, R_upstream) over HydroBASINS lev-10. It raised High+
  coverage on Dec 10 from 41 % to 79 %, but onset-anchored warned % stayed 0, and false Critical rose
  32 % (117 → 309 km² on Dec 10). Not served. ([02 §11.1](02-tpi-method.md);
  [05 §3.2, Q5](05-validation-claims.md); [01 §7](01-product-architecture.md))
- **Suggested.** Test upstream rain *with a lag* (basin-area-scaled travel time) over a 5–7-day window.
  Check rain-on-snow for WA using SMAP/IMERG phase or a snow product. Benchmark against a routing model
  such as GFMS or GloFAS as a reference, not as an input. If no variant gets ahead of onset, document
  floods as a "where to look" layer rather than a forecast.
- **Effort.** L

### B2. HAND proxy radius 1000 m vs 1500 m, or real HAND

- **Q.** Switch the served radius to the validated best (1000 m), or replace the proxy with
  flow-routed HAND?
- **Why it matters.** The proxy is "height above the lowest point within r", not drainage-aware. It
  sets T_flood and also gates NISAR flood candidates (B5). It flags less than half as much low ground as
  MERIT Hydro, so T_flood under-flags.
- **Now.** Served r = 1500 m. HAND < 5 m IoU against MERIT: **RS 0.42 at 1000 m vs 0.34 at 1500 m;
  WA 0.26 vs 0.19**; Pearson r 0.85–0.90. Low-HAND share: proxy 13.9 % vs MERIT 31.7 % (RS), 6.0 % vs
  22.0 % (WA). PLAN said to adopt the best radius; that was never done.
  ([02 §4.3, §12 Q2](02-tpi-method.md); [03 a.6](03-observations-response.md);
  [05 §8.1, Q11](05-validation-claims.md))
- **Suggested.** Short term: move to 1000 m and re-run both replays (S). Better: compute HAND from flow
  routing on NASADEM (e.g. pysheds or WhiteboxTools), which keeps the NASA-only input rule (M). Using
  MERIT directly needs a ruling, because it is non-NASA and the rule allows only NOAA GFS as a non-NASA
  input.
- **Effort.** S / M

### B3. HAND ramp (< 5 m full, 15 m zero), 5° slope gate, and 1 km averaging in Now

- **Q.** Is the 5–15 m ramp right, and how should 90 m HAND be aggregated to the 1 km Now grid?
- **Why it matters.** Averaging HAND over a 1.1 km cell that mixes valley floor and hillslope pushes the
  cell above 15 m, so narrow floodplains drop out of Now.
- **Now.** Block mean of 12×12 cells of 90 m HAND: median HAND 20.0 → 29.7 m; cells with T_flood > 0
  fall from 36.0 % to 28.7 %. The 5° gate is hard-coded in `live/config.py` but read from config in
  `gee/tpi.py`. ([02 §4.2](02-tpi-method.md))
- **Suggested.** Aggregate with a low quantile (p10) or "fraction of cells below 5 m" instead of the
  mean. Fit the ramp against MERIT low-HAND zones and the WA NISAR flood mask.
- **Effort.** S–M

### B4. NISAR water threshold −15 dB (Otsu never fired)

- **Q.** Is −15 dB a sensible open-water threshold for NISAR L-band HH, and why did no tile pass the
  bimodality test?
- **Why it matters.** The method text says "adaptive Otsu", but both published maps (WA and Now) used
  the fixed fallback. The threshold sets every NISAR flood number.
- **Now.** Otsu on 512 px tiles, bimodal if both classes ≥ 5 % and valley < 0.5 × smaller peak, clamped
  to ≤ −15 dB (ruling A2). WA `threshold_db = -15.0`; Now `threshold_method: "fallback"`. Samples below
  −40 dB are treated as nodata after a −60 dB edge population pushed Otsu to −38 dB.
  ([03 a.4, limitation 1](03-observations-response.md))
- **Suggested.** Plot each scene's histogram over JRC permanent water and over dry land. Derive the
  threshold from those classes, or relax the bimodality test (larger tiles, tiles chosen around known
  water). Meanwhile, change the method text to "−15 dB (adaptive Otsu attempted; no bimodal tiles)".
- **Effort.** S

### B5. NISAR HH only, and the HAND gate removing 93 % of the change

- **Q.** Should the flood rule use HV (cross-pol) to separate open water from soil-moisture and
  agricultural change?
- **Why it matters.** L-band HH single-pol darkening responds to soil moisture and tillage as well as
  water. In Now, the HAND < 15 m gate removed **29.28 of 31.62 km²** of radar change, leaving 2.34 km².
  The gate does most of the work, and it has not been validated against floods.
- **Now.** Only HHHH is read; HVHV is downloaded for WA but not used. Bright (double-bounce) flood is a
  separate layer limited to HAND < 5 m (WA 0.66 km², Now 0). ([03 a.3, a.6, limitations 3–4](03-observations-response.md))
- **Suggested.** Inspect the 29 km² that the gate removed against DSWx-S1 and optical imagery. Test an
  HH-and-HV joint drop rule. Check whether bright flood should be allowed above 5 m HAND in urban
  areas.
- **Effort.** M

### B6. No permanent-water mask in the NISAR rule

- **Q.** Pass the permanent-water mask that the code already supports?
- **Why it matters.** Only "dark in the pre image" excludes standing water. A lake or reservoir that is
  wind-roughened in the pre scene and calm in the post scene would be mapped as new flood.
- **Now.** `NISAR_WATERMASK` (JRC GSW + ESA WorldCover + OSM) is wired into `flood_mask` but R2 never
  passes it. There is no record it was used for the WA 27.99 km². ([03 a.5, limitation 2](03-observations-response.md))
- **Suggested.** Pass a JRC GSW occurrence mask (as R1 already does) and re-run WA and Now. Report the
  change.
- **Effort.** S

### B7. OPERA DSWx-S1 (R1) candidate rules

- **Q.** Are the R1 rules sound for southern Brazil, especially rice paddies and long wet seasons?
- **Why it matters.** R1 is the largest observed-flood input in Now (127.7 km² standard, 288.1 km² all
  candidates), and there is no ground truth in the Now domain.
- **Now.** Rolling per-pixel water frequency (< 20 % over ≥ 3 earlier observations, latest observation
  within 144 h), not a pre/post pair; water fraction ≥ 0.5 on a 0.0025° grid; MCD12Q1 wetland and water
  excluded; cropland and JRC occurrence ≥ 25 % or seasonality ≥ 3 months downgraded; rain support
  (8-day IMERG Early < 10 mm locally and within a 50 km isotropic max) downgrades; 0.25 km² minimum.
  Known false positives: smooth or bare surfaces, rice. The rain rule has no decision ID (commit
  637bc4a). ([03 b, limitation 7](03-observations-response.md); [01 §2.1](01-product-architecture.md))
- **Suggested.** Review the parameters against a recent SC/PR flood that has DSWx-S1 coverage and a
  municipal report. Check baseline drift over a long wet season. Give the rain rule a decision ID.
- **Effort.** M

---

## C. Data and latency

### C1. IMERG Early (live) vs IMERG Final (climatology and ML training)

- **Q.** Should Early rain be quantile-mapped to Final before it is scored against a Final
  climatology?
- **Why it matters.** Any systematic Early-vs-Final bias in the upper tail moves R directly, and it
  shifts the ML `_rel` features (trained on Final via NASA POWER, served on Early).
- **Now.** No mapping. Early has not been checked against gauges. ([02 §2.3, §11.3](02-tpi-method.md);
  [04 §5 train/serve 1](04-ml-models.md))
- **Suggested.** Compare Early and Final 3-day sums above p90 over 2–3 years in the Now domain. If the
  bias is material, fit a per-cell or per-season quantile map.
- **Effort.** M

### C2. IMERG vs gauges: under-reading in the Serra

- **Q.** Does the percentile-based R cancel IMERG's under-reading at the Serra stations, or suppress the
  landslide signal there?
- **Why it matters.** The Serra stations are the ones that matter most for landslides.
- **Now.** IMERG **Final** vs 7 INMET gauges, 182 station-days: median r 0.887, bias +5.5 %. Bento
  Gonçalves −16.7 %, Canela −12.8 %. Teutônia's +19 % daily bias vs a 1.84 event-total ratio suggests
  missing gauge days. ([03 g](03-observations-response.md); [05 §8.2, Q7](05-validation-claims.md))
- **Suggested.** Extend to more INMET and Cemaden gauges and to IMERG **Early**. Screen gauge gaps.
  Test whether the bias is constant across percentiles (in which case R cancels it) or grows in the
  tail.
- **Effort.** M

### C3. SMAP 9 km resolution and 2–5-day latency

- **Q.** How should S handle SMAP's latency in Now, and how much does the replay overstate skill by
  ignoring it?
- **Why it matters.** Live S is days old and held constant over D..D+3. The hindcast replay uses SMAP
  with no latency, and the GFS forecast replay holds S at D−1, which gives 1–3 days of look-ahead that
  L28b did not address. At 9 km, S cannot separate a saturated hollow from a drained spur.
- **Now.** Live granule was **5 days old** on the 11:39Z run. Replay data are labelled "hindcast … not
  as-available". The as-available RS replay used a 3-day lag. "Latest" granule selection relies on CMR
  result order, which has not been verified. ([02 §3, §10 items 2 and 8, §11.2](02-tpi-method.md);
  [01 §2.1–2.2](01-product-architecture.md))
- **Suggested.** Re-run the GFS replay with S lagged by 3 and 5 days and report the change in warned %
  and lead time. Sort SMAP granules by time explicitly. Decide whether the live S should be advanced
  with IMERG (a simple bucket) between SMAP granules.
- **Effort.** S–M

### C4. GFS bias, no rescaling

- **Q.** Should GFS rain be bias-corrected (quantile-mapped) to IMERG before it enters R and the ML
  model?
- **Why it matters.** GFS rain is scored against IMERG Final percentiles as is. GFS drizzles and
  smooths extremes at 0.25°. The ML model only ever learned IMERG, then receives raw GFS. LHASA-F
  rescales GEOS-FP for exactly this reason.
- **Now.** RS 2024: 3-day AOI-mean forecast 141 mm vs 290 mm observed (ending 2024-05-02); about 60
  mm/day forecast vs 138 mm observed on May 1. ([02 §5, §11.4](02-tpi-method.md);
  [04 §5 train/serve 2](04-ml-models.md); [01 §2.2](01-product-architecture.md))
- **Suggested.** Fit a per-cell, per-season quantile map GFS → IMERG Final on the 2021–2025 archive,
  holding out the replay events. Re-score A1/A2 and the ML GFS TEST.
- **Effort.** M

### C5. Incomplete D−1 IMERG in the early Now runs

- **Q.** Add a completeness guard for the previous day's IMERG?
- **Why it matters.** IMERG Early has about 4 h latency, so the 00:17Z (and possibly 03:17Z) runs use a
  partial D−1, and that day enters two frames.
- **Now.** `fetch_daily` returns the half-hour count but `run_now` discards it. ([02 §6, §10 item 3](02-tpi-method.md))
- **Suggested.** Require 48 half-hours for D−1, or fall back to the previous run. Engineering, one line.
- **Effort.** S

### C6. One-day date-label offset between replay series

- **Q.** Align the date convention of the observed replay series with the GFS and Now frames?
- **Why it matters.** The observed band labelled X covers X−3..X−1; GFS and Now frames labelled X cover
  X−2..X. A timeline that mixes them compares windows offset by one day, which matters for lead times.
- **Now.** Documented in SCHEMA but not reconciled. ([02 §6.1](02-tpi-method.md))
- **Suggested.** Check every lead-time and warned-% calculation that mixes the two; relabel one series.
- **Effort.** S

### C7. Population: GPW variants and samplers

- **Q.** Use one GPW variant and one sampler everywhere?
- **Why it matters.** People counts differ by 2× for the same polygons depending on the sampler.
- **Now.** Replays use the GEE non-UN-adjusted GPW 2020 count at 1 km; Now uses the UN-adjusted 30″
  count. NISAR Now polygons: 82 people (R2 sampler) vs 43 (R3 grid). A 0.03 km² urban polygon holds 41
  people by density × area. ([03 e.1, a.9, limitation 9](03-observations-response.md))
- **Suggested.** Choose the UN-adjusted 30″ count and the area-weighted sampler for all products. Round
  people counts and state the method next to them.
- **Effort.** S

### C8. SWOT and night lights (context layers)

- **Q.** Keep SWOT and VIIRS night-light numbers as context only, or screen them first?
- **Why it matters.** Neither is validated, and both appear in the product.
- **Now.** SWOT: 0 good-quality observations (all suspect or degraded) and obvious outliers kept (RS
  2024-04-04 18.5 m; WA 2026-01-02 29.5 m). Night lights: −33 % RS, −44 % WA, with no control area, no
  snow or lunar screen, measured over our own Critical mask, split on the config event date rather than
  the onset; not in the Now job. ([03 e.2, f](03-observations-response.md);
  [05 §8.4, Q10](05-validation-claims.md))
- **Suggested.** Add outlier screening to SWOT. For night lights, add a control area and use the onset
  date; until then label it "indicative".
- **Effort.** S

---

## D. Validation design

### D1. Which onset anchor, and an instant or a window?

- **Q.** Should "before the damage" be anchored on a hazard-specific onset, and scored against a
  trigger window rather than a single instant?
- **Why it matters.** The RS anchor is the first deaths, which were from flooding (Paverama, Apr 29).
  The first landslide deaths were Apr 30. With the Apr 30 anchor the 24 h result flips from **36.45 %
  to 0 %**. The old May 1 anchor gave 88 %, which was mostly a nowcast of a storm already under way.
- **Now.** L37 anchors: RS 2024-04-29 03Z, WA 2025-12-08 08Z. Sensitivity rows are published. Neither
  inventory has per-landslide dates. Several keys were never re-anchored (`rs.lead_time_*`,
  `rs.warned_before_pct`, `rs.lead_time_asavail`, both `official_warning_delta_h`); RS −51.3 h has an
  undocumented reference instant. ([05 §2, §3.1, Q3, Q4](05-validation-claims.md))
- **Suggested.** Rule on one anchor per hazard. Score against the documented trigger window (RS Apr
  29–May 2) with explicit timing tolerance. Re-anchor or retire the stale keys.
- **Effort.** S

### D2. Null baselines for every hit rate

- **Q.** How do the hit rates compare with what random points on the same terrain would score?
- **Why it matters.** Without a baseline, "85.9 % of mapped landslides were Critical" may only mean "it
  rained hard on all steep ground". 3,224 km² of the RS AOI was slide-Critical that day.
- **Now.** No baseline is computed for any of: RS catalogue hit **85.86 %** Critical (May 2, observed
  rain); RS forecast **36.45 % / 1.87 %**; WA 66.7 % (10 of 15, Wilson 95 % CI about 42–85 %); DIST
  scar "precision" **55.07 %** of candidates within 500 m of an inventory point (with 16 k points, a
  500 m buffer covers much of the steep Serra). ([05 §6.1, §6.2, Q1](05-validation-claims.md);
  [03 c, limitation 8](03-observations-response.md))
- **Suggested.** For each day and run, compute the same share for random points on steep (≥ 15°),
  non-crop terrain in the AOI, and report lift or ROC (inventory vs random steep). For scars, draw
  random polygons of the same size distribution on the same steep, non-crop mask.
- **Effort.** S–M

### D3. False-alarm rate on non-event days

- **Q.** How often does the rule fire on days with no disaster?
- **Why it matters.** Every published number counts hits; none counts false alarms. The RS Apr 23 run
  put 40.6 % High+ and 10.6 % Critical on dates when the observed index was 0 %, a 3–5-day timing
  error. The AOI-wide "Critical anywhere" lead time warned on 2 of 7 (RS) and 5 of 8 (WA) issue days,
  and every WA warning came from landslide cells while the WA damage was flooding.
- **Now.** No false-alarm denominator exists. ([05 §3.1, §4 caveats, Q2](05-validation-claims.md))
- **Suggested.** Run the GFS replay rule over all 2021–2025 issue days for the RS and WA AOIs (or a
  random sample of 100+ days) and report the Critical-area distribution and how often "Critical
  anywhere" fires. Use it to set A1/A2.
- **Effort.** M

### D4. Single runs vs a run ensemble

- **Q.** Report a statistic over several runs instead of single GFS runs?
- **Why it matters.** The whole RS positive result rests on one run (Apr 28 00Z). The next run (Apr 29)
  shows 0 % for its frames up to the onset day. Run-to-run variance dominates.
- **Now.** By-issue tables per run. ([05 §3.1, Q2](05-validation-claims.md))
- **Suggested.** Report the mean (and spread) of warned % over all runs issued 24–96 h before onset,
  next to the single-run numbers.
- **Effort.** S

### D5. Small samples and inventory provenance

- **Q.** Which numbers rest on too few cases to quote, and can the WA inventory be dated?
- **Why it matters.** Small n makes single numbers fragile.
- **Now.** Forecast lead time rests on 7 (RS) and 8 (WA) scorable issue days. WA landslides: 15 WGS
  points, treated as undated, out of 279–750 statewide reports (selection undocumented). WA in the ML
  set: 8 points of 185 reports after dedupe. ([05 §1.2, Q12](05-validation-claims.md);
  [01 §7](01-product-architecture.md); [04 §2.3](04-ml-models.md))
- **Suggested.** Attach a CI to every rate. Document how the 15 points were selected. The WGS layer has
  an `event_date` field: use it for a real point-level lead time in WA. Confirm the WGS reuse licence.
- **Effort.** M

### D6. Event-weighted vs pooled metrics, and confidence intervals

- **Q.** Should results be reported per event (or event-weighted) with block-bootstrap intervals,
  rather than pooled?
- **Why it matters.** Pooled ML TEST is mostly three events: RS, Emilia and Helene are 86 % of the
  214,277 GFS rows. RS's 3,000 points sit in about 113 IMERG cells and share one storm. Maria and Kerala
  add 12,000 negative-only rows to the pooled GFS metrics.
- **Now.** Per-event 24 h PR-AUC for the served model ranges 0.23 (Rwanda) to 1.00 (Angra), median
  0.68; LOEO pooled 0.562–0.673. No confidence intervals. ([04 §7.3, §9.2, §9.3](04-ml-models.md))
- **Suggested.** Report event-macro-averaged metrics and a block bootstrap over events. Drop the
  negative-only Maria and Kerala rows from pooled GFS.
- **Effort.** S–M

### D7. Base rates set by design

- **Q.** Should metrics be quoted as lift over base rate, given that base rates come from label-window
  widths and case-control ratios?
- **Why it matters.** Horizon base rates are set by how many days each event's label window spans (GLC
  1 day, RS 4, Kerala 6), not by nature. Precision and accuracy depend on the case-control ratio and are
  not operational rates.
- **Now.** 24 h base rate VAL 0.039 vs TEST 0.135. ([04 §3.2, §7, §10](04-ml-models.md))
- **Suggested.** Always show base rate and lift next to PR-AUC; prefer ROC and recall where ratios
  differ. Feeds E4.
- **Effort.** S

### D8. NISAR vs DSWx-S1 comparison setup

- **Q.** Is the WA flood-map comparison set up correctly, and what drives the low recall?
- **Why it matters.** F1 0.151 is the only quantitative check of a radar flood map in the project.
- **Now.** Like-for-like: IoU 0.0816, F1 0.151, P 0.187, R 0.126. Grid is not stated (TP + FP = 46,863 px
  matches 27.99 km² only at ~24 m pixels). Unclear whether the 25.9 % of the AOI outside the swath was
  masked out of FN. DSWx-S1 dates are Dec 15/17 vs NISAR Dec 16, on a receding flood; the post pass is
  4–5 days after the Skagit crest; NISAR is BETA pre-calibration. This is agreement between two
  candidate products, not accuracy. ([03 d, limitation 6](03-observations-response.md);
  [05 §7, Q6](05-validation-claims.md))
- **Suggested.** State the grid, mask FN to the swath, and recompute. Test whether adding the bright
  layer or an HV rule (B5) changes recall.
- **Effort.** S

---

## E. ML specifics

### E1. Label quality and label windows

- **Q.** Are the training labels and the per-event trigger windows geologically sound?
- **Why it matters.** Labels set what "trigger" means. TRAIN/VAL positives are news-report dates; TEST
  inventories are undated and get fixed multi-day windows.
- **Now.** GLC/COOLR: 2,348 points, location ≤ 5 km, **95 % from 2015–2018**, report date not failure
  date. RS: every point dated 2024-05-01 with window Apr 29–May 2 (lenient: 4 positive issue days per
  point). Emilia and Kerala each had two rain episodes but one window. Maria may include failures
  outside the hurricane. ([04 §2, §8.4, §9.5](04-ml-models.md))
- **Suggested.** Review each TEST event's trigger window against event reports (the geologist's call).
  Consider splitting Emilia by episode where the inventory allows. Produce the TRAIN-vs-TEST
  feature-drift table that the EDA called for.
- **Effort.** M

### E2. SMAP read on the landslide day in daily training

- **Q.** Retrain the daily model with SMAP from the day before the landslide?
- **Why it matters.** In daily training rows, SMAP is the 10:30Z granule of the landslide day, which can
  already contain that day's rain (SMAP L4 is precipitation-forced). TEST and serving hold SMAP at the
  issue day, so there is no look-ahead at TEST, but the model may have learned a slightly optimistic
  soil signal.
- **Now.** Soil ablation is about 0 anyway. ([04 §5 train/serve 4](04-ml-models.md))
- **Suggested.** Retrain with SMAP at D−1 and compare.
- **Effort.** S

### E3. T_ML vs the 15–30° slope rule

- **Q.** Should the "where" factor be the plain slope rule rather than the learned susceptibility?
- **Why it matters.** The live product is P_trigger × T_ML. If the slope rule ranks better, the
  learned layer adds error.
- **Now.** On held-out Brazilian inventories (n = 5,796, base rate 0.25): **slope rule PR-AUC 0.829 vs
  T_ML v2 0.747**; T_ML wins only on ROC (by 0.01–0.02). T_ML trained on 73 positives and chosen on 20
  VAL positives; land cover carries 63 % of its gain. Random-land negatives are mostly lowland, so
  ROC ≈ 0.93 is easy. ([04 §7.6, §9.8](04-ml-models.md))
- **Suggested.** Decide to serve the slope rule as the "where" factor, or rebuild 3a with hard
  negatives (steep cells in the event footprint that did not fail) and re-test.
- **Effort.** S (decision) / M (rebuild)

### E4. The VAL vs TEST gap

- **Q.** Which split represents operational skill: VAL (routine GLC events) or TEST (major disasters)?
- **Why it matters.** It decides whether any ML skill claim is honest.
- **Now.** Raw 24 h PR-AUC VAL 0.129 vs TEST 0.622 ("~5×"). Lift is 3.3× vs 4.6×, so base rates explain
  about half. A real ranking gap remains: ROC 0.755 (VAL, observed rain) vs 0.941 (TEST, observed rain).
  Likely reasons: TEST storms are extreme (RS positives median 294 mm in 72 h vs 0.9 mm on negative
  dates), and random-date negatives are trivially separable from them (the climatology baseline scores
  0.97 on RS). The independent overfit audit had not reported. ([04 §10, §11](04-ml-models.md))
- **Suggested.** Read the audit when it lands. Add a TEST variant with hard negatives (wet days at the
  same points that did not fail). Expect operational skill nearer VAL than TEST.
- **Effort.** M

### E5. The live map serves the v1 model and the leaked v1 T_ML

- **Q.** Swap the live P(trigger) layer to v2, switch it off, or relabel it?
- **Why it matters.** The live P(trigger) frames come from the **v1** trigger × the **v1** T_ML, which
  was trained with RS 2024 points (the L32 leak). The published manifest still lists the dropped 14 d
  horizon and points at the v1 model card.
- **Now.** `now.yml` pins the v1 artefacts by sha256 from `static-v1`. The v2 meta has no
  `serve_combine` key, so dropping it in as-is would serve the trigger without × T_ML.
  ([04 §8.3](04-ml-models.md); [01 §2.1](01-product-architecture.md))
- **Suggested.** Decide together with E3: v2 trigger × slope rule, or v2 trigger × T_ML v2, with an
  explicit `serve_combine`. Remove 14 d.
- **Effort.** S

### E6. Petrópolis was scored with NaN forecast rain

- **Q.** Fix the GFS cache lookup and re-score Petrópolis?
- **Why it matters.** The published "Petrópolis forecast miss" (24 h PR-AUC 0.04; k0–k6 recall 1.00) is
  a missing-data artefact. Petrópolis points fall inside Angra's GFS box, so they read Angra's cache and
  get NaN on every issue day.
- **Now.** 100 % NaN GFS rain for Petrópolis rows; the correct cache exists (about 38 mm on day 0).
  Same class of bug as L33. ([04 §9.4](04-ml-models.md))
- **Suggested.** Choose the box per event, mask NaN forecast rows, re-score `evaluate`, `by_lead` and
  `combine`. Pooled effect small; per-event narrative changes.
- **Effort.** S

### E7. Forecast value, calibration and thresholds

- **Q.** Does GFS add anything at 24 h, and why do VAL thresholds give zero recall on RS before onset?
- **Why it matters.** "Would have warned" claims depend on the threshold.
- **Now.** At 24 h a no-forecast nowcast scores PR-AUC 0.618 vs 0.622 for the served model; the
  forecast helps from 48 h (+0.06) and at 7 d (+0.17). Horizon calibrators and thresholds were fit on
  observed IMERG (VAL is 2015–2018, before the GFS archive), so they never saw GFS error. RS at k1 ranks
  well (ROC 0.95) but recall is **0.00** at k = 0–2; Helene k1 recall is 0.94. VAL also serves early
  stopping, calibration and threshold. ([04 §6.1, §7.1, §7.4, §9.7](04-ml-models.md))
- **Suggested.** Calibrate on post-2021 GFS rows from a held-out set. Report recall at several fixed
  thresholds, not only the VAL max-F1. Consider the served-model choice (`direct_rain` wins VAL by
  0.009–0.024).
- **Effort.** M

### E8. Residual leakage and shift

- **Q.** Is there enough separation between TRAIN and TEST in space and reporting style?
- **Why it matters.** The leak filter removes GLC reports that may be a TEST event, but not earlier
  reports from the same region (e.g. older Serra do Mar slides stay in TRAIN for the Petrópolis, Angra
  and São Sebastião tests). The rel-rain p99 denominators are all-year, not seasonal; training terrain
  is 90 m while the Now grid is 1 km.
- **Now.** Judged mild for a trigger model with little terrain signal. ([04 §4, §5, §9.5](04-ml-models.md))
- **Suggested.** Run a region-held-out check (drop all GLC within 100 km of a TEST event, any year).
- **Effort.** S

---

## F. Product and claims

### F1. The "Radar confirms" tagline

- **Q.** Keep "Rain warns. Radar confirms.", or change it (e.g. "Radar sees through.")?
- **Why it matters.** The hard rule is that radar outputs are "candidates, never confirmed". NISAR vs
  Sentinel-1 is F1 0.151, and there is no radar landslide evidence at all (no GUNW pair crosses WA, and
  RS predates NISAR).
- **Now.** OPEN, Lucas's choice. "Confirm" wording also remains in summary.md, both READMEs, the
  original theme sentence, the `flood_warned` definition ("NISAR-confirmed") and the internal key
  `flood_confirmed`. ([05 §9.2 V5, §9.5 R7–R8, §10](05-validation-claims.md);
  [01 §1.1, §7](01-product-architecture.md); [03 a.7](03-observations-response.md))
- **Suggested.** Use "Radar sees through" (or similar); rename to "NISAR-mapped" everywhere; rename
  the internal key.
- **Effort.** S

### F2. Is "Critical" too broad to be useful?

- **Q.** Is the Critical class specific enough for a response product, and how should exposure numbers
  be presented?
- **Why it matters.** An index that labels a third of the region Critical will "contain" most damage by
  construction, and the people counts become very large.
- **Now.** Peak-day Critical: **RS 10,870.6 km² (≈ 34 % of the AOI; flood-Critical alone 7,647 km²),
  1,488,833 people; WA 4,127.5 km² (≈ 30 %), 229,782 people**, both 4 days after onset with observed
  rain. Separately, Now `summary.json` `critical_km2` means High + Critical (TPI > 0.4), contradicting
  ruling A8 (Critical = TPI > 0.6 only). ([05 §5, Q9](05-validation-claims.md);
  [02 §1](02-tpi-method.md); [01 §2.1, §7](01-product-architecture.md))
- **Suggested.** Decide the cut together with A2 and D3 (target a Critical area fraction on event days
  that is useful to civil defense). Report an area-to-damage ratio next to the people count. Rename the
  Now key to `high_or_above_km2`.
- **Effort.** M

### F3. What can honestly be claimed about prediction

- **Q.** Is the current claim set right: the index adds *where* and *who*, not an earlier warning?
- **Why it matters.** Official warnings came first in both replays (RS by 31.2 h, WA by 71.8 h).
  Prediction numbers are fragile to the anchor (D1) and rest on single runs (D4).
- **Now.** L38: no prediction number in the headline or voice-over; RS 36 % / 2 % appears only in the
  validation table, README and slide footnote. Two wordings about thresholds conflict ("not yet
  validated across many events" vs "calibrated on a single event"; neither is backed by a record, and
  the thresholds were never fitted). The ML card's headline stays held. ([05 §3.3, §9.3 S10–S11, §10](05-validation-claims.md);
  [01 §1.2, §7](01-product-architecture.md); [04 §12](04-ml-models.md))
- **Suggested.** Confirm the L38 framing. Use one threshold wording: "default thresholds from the
  design document, not yet fitted to data". Add "observed rain alone: 0.25 %" and "the Apr 23 run's
  early signal did not verify" to the footnote.
- **Effort.** S

### F4. Stale and misattributed public numbers

- **Q.** Clean up superseded numbers still visible in public surfaces?
- **Why it matters.** A reviewer who finds them will distrust the rest.
- **Now.** Data README still states 111 h, 84.6 h and 88.1 %; `metrics.json` still carries the withdrawn
  v1 T_ML AUCs (0.974 / 0.922 / 0.856) as flat keys; the WA inventory is credited to NASA GLC/COOLR
  instead of the Washington Geological Survey (app README, FORM §8); PLAN and README describe the
  climatology inconsistently. ([05 §9.5, §10](05-validation-claims.md); [02 §10 item 7](02-tpi-method.md))
- **Suggested.** Replace or delete the stale lines; move v1 keys under a `v1_withdrawn` node; credit
  WGS.
- **Effort.** S

### F5. Exposure, cloud and outage claims need their caveats on screen

- **Q.** Do the impact claims carry their definitions where they are shown?
- **Why it matters.** Each is true under a narrow definition that the headline omits.
- **Now.** "27.99 km²" is within a swath covering 74.1 % of the AOI (missing from the summary sentence).
  "229,782 people in critical areas" is the hindcast peak day. "Clouds hid 93 %" was measured Dec 15/17,
  after the storm; RS 100 % uses an any-scene-clear union, and `hls_observed_px` is undefined. Night-light
  drops have no control (C8). ([05 §9.1–9.2, §8.3, Q8](05-validation-claims.md))
- **Suggested.** Add the scope phrase to each headline; check whether HLS has any Dec 8–12 coverage to
  support "clouds hid the event".
- **Effort.** S

### F6. P(trigger) is a ranking index, not a probability

- **Q.** Does every surface present the ML layer as relative, never as a probability of failure?
- **Why it matters.** Case-control design sets the base rate (~0.15 daily); P_H composes design-rate
  probabilities, is recalibrated on VAL, and is then multiplied by T_ML.
- **Now.** The layer is labelled "relative likelihood … ranking index, not an absolute frequency",
  Brazil land only. ([04 §9.6](04-ml-models.md); [01 §7](01-product-architecture.md))
- **Suggested.** Keep the label; check the app legend and alert text use no "%" or "probability".
- **Effort.** S

### F7. Domain and scope of the Now products

- **Q.** Should the Now TPI be clipped to RS + SC + PR (or Brazil), as R1 and P(trigger) are?
- **Why it matters.** Class areas sum to about 1.05 M km² against about 0.58 M km² for the three states,
  so TPI is computed over neighbouring states and countries. R4 scar candidates outside rain events are
  mostly forestry harvest or fire, and DIST latency is 1–2 weeks.
- **Now.** TPI not clipped; R1 and P(trigger) clipped to the IBGE Brazil outline. Now R4: 685
  candidates, 3.30 km². ([01 §2.1](01-product-architecture.md); [03 c](03-observations-response.md))
- **Suggested.** Clip TPI areas and alerts to the served region. Show R4 only when a rain trigger
  occurred in the window.
- **Effort.** S

---

## Index of items by source section

| Section | Items drawing on it |
|---|---|
| [01 — Product & architecture](01-product-architecture.md) | A6, B1, B7, C3, C4, D5, E5, F1, F2, F3, F6, F7 |
| [02 — TPI method](02-tpi-method.md) | A1–A7, B1–B3, C1, C3–C6, F2, F4 |
| [03 — Observations & response](03-observations-response.md) | B2, B4–B7, C2, C7, C8, D2, D8, F1, F7 |
| [04 — Machine learning](04-ml-models.md) | A2–A5, C1, C4, D5–D7, E1–E8, F3, F6 |
| [05 — Validation & claims](05-validation-claims.md) | A5, B1, B2, C2, C8, D1–D5, D8, F1–F5 |
