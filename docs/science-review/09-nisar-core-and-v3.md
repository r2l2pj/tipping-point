# 09 — Replay v2, NISAR core, the Uruguay River case, ML v3

*Science review for the team geoscientist. Written 2026-11-15 13:30–15:00 BRT by lead-analysis.
Every number below is a key in `data/metrics.json` on data main, or a named file. Rulings
L44–L50 are in `.crew/plans/decisions.md`.*

**Conventions** — same as 05: High = "Alto" (TPI > 0.4), Critical = "Crítico" (> 0.6);
`*_pct` 0–100; IoU/F1/AUC 0–1; onset anchors per L37 (RS 2024-04-29 03:00Z, WA 2025-12-08 08:00Z).
NISAR outputs are **candidates**, never confirmed.

---

## 1. Replay v2 is the served replay (L44, L46)

**What changed.** The TPI formula is unchanged: TPI = (R·S·T)^(1/3). Only the inputs changed.

- **R.** v1 computed R in Earth Engine at about 1 km. IMERG 0.1° and the percentiles were
  nearest-resampled there (`gee/tpi.py` has no `.resample('bilinear')`), which baked 10 km steps
  into `r_series`. v2 computes R on the 90 m grid. The 3-day sums use IMERG **Final**, the same
  product as the climatology. Both the sums and the p90/p95/p99 per 0.1° cell are bilinear-
  interpolated before `rain_score` (`tools/replay_v2.py`).
- **Permanent water** is now nodata. That is JRC GSW occurrence ≥ 50 % or MCD12Q1 IGBP 17, and it
  removes the TPI painted over Guaíba and Lagoa dos Patos.

Since ~13:30 BRT the canonical COGs, PNGs, critical areas, priority zones, series and every metric
are v2. v1 survives in `cases.<c>.replay_v2.before_after` and in git history.

| Metric | v1 | **served v2** |
|---|---|---|
| RS landslides High+ in the GFS run issued 27 h before onset | 36.45 % | **5.77 %** |
| … Critical | 1.87 % | 0.18 % |
| RS case area flagged High+ by that run | 4.16 % | 2.13 % |
| Lift (hit rate ÷ area share) | 8.76× | **2.71×** |
| EMS-mapped landslides (497, Taquari) High+ 24 h pre-onset | 81.09 % | 0.0 % |
| EMS flood IoU / F1 (Taquari AOIs, 2024-05-06) | 0.185 / 0.312 | 0.197 / 0.329 |
| Alarm precision, Critical, municipality × GFS run (RS) | 33.7 % (lift 1.35) | 34.8 % (lift 1.40) |
| RS Critical km² (peak day) | 10,871 | 9,175 |
| RS people in Critical (peak, native 30″ GPW) | 1,681,783 | 1,627,862 |
| WA flood area High+ 24/48/72 h pre-onset | 0 / 0 / 0.14 % | 0 / 0 / 0 % |
| WA alarm precision, Critical | 19.7 % (lift 1.97) | 20.3 % (lift 2.03) |
| WA people in Critical (peak, native) | 254,263 | 225,583 |
| AOI lead time, Critical anywhere (RS / WA median) | 87 h / 80 h | unchanged |

**Why the RS headline dropped from 36 % to 6 %.** The obvious suspect was a few 10 km blocks, and
it is not that:

- **Not blocks.** The 5,842 v1-warned slides sat in 81 of the 113 IMERG 0.1° cells that hold the
  inventory, and only 7 of those cells were fully warned.
- **Not the climatology.** The two climatologies agree: p90 at the warned slides is 41.5 mm in v1
  and 41.0 mm in v2.
- **The observed rain.** v1's GFS frames took 2024-04-27 from IMERG **Early** (GPM_3IMERGDE, as
  available in real time) and scored it against a **Final**-based climatology. Over the Serra on
  that day Early was **1.34× Final**: box mean 26.0 vs 19.4 mm, max 75.9 vs 57.5 mm (GES DISC V07B).
- **Effect at the slides.** With Final, the 3-day rain for the Apr 29 frame is about 34 mm, below
  p90, so R = 0 at most slides. With Early it reaches about p90.

So v1 is the "as-available" view and mixes products; v2 is the consistent-product view. **L46: lead
with v2.** v1 appears only as the sensitivity in `docs/replay-v2.md`.

**Live Now has the same mix (known bias).** Now scores IMERG Early against the IMERG-Final
climatology (`live/build_clim.py`), so Now R is probably inflated. This is documented in the README
"Declared limitations". The fix after submission is a Final-consistent comparison, or an Early-based
climatology.

**SMAP latency sensitivity** (served v1 numbers, B3-6). Holding S 3–5 days old at issue time moved
the RS 24 h hit rate by at most −2 points (36.45 → 34.25 %). Re-run it on v2 before quoting it.

---

## 2. NISAR as the analytic core — WA 2025 (L42, L45)

- **HH 3-date series** (`data/wa_2025/nisar_timeseries.json`; GCOV BETA, track 172 D frame 064,
  identical geometry). On the flood candidates, HH reads −9.7 dB on Nov 10, −9.7 dB on Dec 4 and
  −16.2 dB on Dec 16. Controls drift by at most 2.9 dB.
  - The candidates were *selected* by the Dec 4 → Dec 16 darkening, so the Dec 16 drop is partly
    there by construction. The independent check is that Nov 10 equals Dec 4: the pixels were stable
    before the event.
  - BETA product counters differ (004/006/007), so read only changes larger than about 3 dB.
- **HV next to HH** (`nisar_polarization.json`).
  - Inside the HH candidates, HV falls from −16.3 to −21.0 dB.
  - An HV-only rule (post < −23 dB and drop ≥ 3 dB; p05 of the HV histogram, since HV has no
    bimodal water mode) finds only 0.047 km², IoU 0 with HH. HV adds no independent open-water
    detections here.
  - 108.6 km² brightened by ≥ 3 dB in HH, but only 1.8 % of that lies within 500 m of a flood
    candidate. Mostly not flood-adjacent.
- **Double-bounce hypothesis — supported** (`nisar_hypothesis.json`).
  - Of the 60,936 OPERA-confirmed new-flood pixels that our HH detector missed, 45 % are urban or
    forest, against 5 % of the pixels it caught (8.6× enriched).
  - Mean HH change: flooded open land −2.72 dB, flooded urban −0.66 dB, flooded forest −0.18 dB.
  - Flooded urban and forest do *not* brighten by 3 dB. Double bounce cancels the specular
    darkening, so these pixels stay near their bright baseline.
  - Closing the gap needs HV, texture or land-cover-aware detection, not the bright layer.
- **GUNW coherence change (W-coh)** — see §2.1.
- **SME2 soil moisture (W-sme2).** Cut at 12:50 to free memory for the Uruguay stack. Not done.
  It stays the cheapest NISAR-in-the-index next step (`.crew/research/nisar-core-options.md`).

### 2.1 Coherence loss (W-coh, `nisar_coherence.json`, metrics `wa.nisar_coh_*`)

- **Data.** NISAR L2 GUNW Beta, track 172 D frames 064/065. The reference pair is Nov 22 → Dec 4,
  the event pair Dec 4 → Dec 16. Coherence is reprojected to the 60 m grid (EPSG:32610).
- **Rule.** Coherence-loss candidates need coh_ref ≥ 0.4 and Δcoh ≥ 0.3 (config
  `coherence_ref_min` / `coherence_drop`), with water and high-elevation snow excluded.
- **Result.** 1,877 km² of loss (309.6 km² on slopes ≥ 15°), in 4,362 candidate polygons.
- **Overlap with the NISAR flood candidates: 0 %.** The flood pixels are water-masked. Overlap with
  OPERA new water is 8 km².
- **Landslides.** 10 of 15 WGS points lie within 100 m of a loss polygon, but a random placement
  scores 60.9 %. At 500 m it is 15/15 against a 100 % baseline. **No landslide-specific skill:** this
  is broad winter storm decorrelation (rain, snow, forest) of L-band over 12 days.
- **Use.** Published as `nisar_extra` layers (`nisar_coherence_loss`, `nisar_coherence_event`)
  labelled Beta and candidates. Do not use it as landslide evidence.
- **Bugs fixed during the work.** A WarpedVRT 4326→32610 path returned 97 % false water, and a
  nearest-reproject denormal leaked through. Both are fixed and reported in
  `.herdr/implement-nisar-coh.md`.

---

## 3. The Uruguay River case `rs_uruguai_2026` (L47–L50)

**Truth timeline** (ANA telemetry, gauges 77150000 Uruguaiana and 75900000 Itaqui; flood stage
8.50 m):

- Uruguaiana is above flood stage on **Oct 5–21**, crests at **10.02 m on Oct 16**, is above again
  on **Oct 29–31**, and nears the red-alert level at 8.52 m on Nov 12.
- Itaqui is above flood stage on Oct 5–20 and crests at 9.93 m on Oct 16.
- At Itaqui, 29 people were displaced on Oct 8.

**NISAR availability** (`.crew/research/uruguai-nisar-granules.md`).

- Track **010 D frame 103**: Aug 13, Aug 25, Sep 6, Sep 18, **Sep 30, Oct 12**, Oct 24, Nov 5.
  Dates up to Oct 21 are in `NISAR_L2_GCOV_PROVISIONAL_V1`; from Oct 24 they are in `_V1`.
- Other tracks: 082 D (Sep 23 / Oct 5 / 17 / 29), 060 A, 132 A.
- The swath covers Uruguaiana but not Itaqui (track 010 D).
- **Correction:** our first search used only `_V1` and wrongly called the teammate's Sep 30 → Oct 12
  pair "likely Sentinel-1". It is NISAR provisional GCOV, and it is relabelled.

**What is published.**

- **v0.** The teammate's 010 D Sep 30 → Oct 12 pair: 7 curated polygons, geometries repaired, 1.817
  km² geodesic and 28 people (native GPW), all in a 44 km² Uruguaiana check box. Labelled NISAR
  (GCOV provisional) candidates, with the caveat that some may be rice irrigation.
- **082 D HH/HV frames** (Sep 23, Oct 5, Oct 17, Oct 29) as `nisar_frame` context.
  - The R2 guard **rejected all three 082 D pairs**: the per-scene threshold landed on the −15 dB
    clamp.
  - Field darkening was 202 of 212 km² darkened on Oct 5, with about 40 % of polygons flagged as
    irrigation.
  - October is rice planting in western RS, so this is the paddy-flooding signature. **No flood
    candidates are published from it.**
- **010 D stack** (Sep 18 → Nov 5, HH+HV) and gated corridor-only candidates: from the uruguai-nisar
  worker. A flood layer counts only change in the Uruguay/Ibicuí floodplain (HAND ≤ 5 m), with rice
  and agricultural land excluded. Field darkening goes to a separate "possible rice irrigation" note
  and never into people counts. *Status at 15:00: see the README of the case node / metrics
  `nisar_flooded_km2*`, `radar_darkening_*`.*

**R2 guard, final form** (`tp.response_nisar`). A pair is rejected when:

- it mixes acquisition modes or looks (checked from the granule ids before any read);
- its threshold falls back, **or** an "adaptive" threshold sits at the −15 dB clamp (the 082 D
  case);
- or, for individual components rather than whole pairs, a component lies within 2 km of the scene
  or bbox edge.

Live Now additionally keeps only RS/SC/PR land. The live Southern Brazil pair (024 D) stays
`no_usable_pair`. The 8 São Paulo polygons are withdrawn.

**Forecast proof — retrospective upstream test** (lead-ml, `tools/uruguai_forecast_proof.py`;
`metrics.rs_uruguai_2026.forecast_proof*`, `series/forecast_proof.json`).

- **Signal:** basin-mean 3-day rain over the Uruguay basin upstream of Uruguaiana (HydroBASINS lev07,
  192,578 km²). It combines IMERG Early (observed) with the NOAA GFS 00Z run.
- **Result:** the signal exceeded the season's **p99 on the Sep 30 issue**. That is **5 days before
  Uruguaiana reached flood stage** (Oct 5) and 8 days before the Itaqui displacement. The second
  rise passed p95 on Oct 23, 6 days before Oct 29.
- **Negative controls:** the GFS forecast alone flagged neither rise, and local rain at Uruguaiana
  never reached its own p99.
- **Not served.** This is an evaluation, not a served product: the upstream-basin term (L35) is not
  in the live Now index or the replay TPI. It uses IMERG Early against a POWER/IMERG-Final
  climatology, the same product mix as §1.
- **Pitch wording:** "tested retrospectively", never "the live index uses upstream rain".

**TPI replay: cut (conductor, 13:55).** Local rain index not replayed for this case; local rain at Uruguaiana never reached its own P99 — the flood was driven by upstream-basin rain (see forecast_proof). The replay was
cut because of OOM risk during the recording. When it is done after submission it must use
`clim_imerg_0p1.tif`, not the legacy (scrambled) `doy_*.tif` files.

---

## 4. ML v3 and lead time 0–3 d (lead-ml; `.crew/research/model-metrics.md`, data main b25cb05)

- **v2 (served `xgb_rain_soil`).**
  - Setup: NOAA GFS 00Z, held-out TEST events, rows with a finite forecast only (n 202,277).
  - PR-AUC / ROC: 24 h 0.679 / 0.907, 48 h 0.702 / 0.894, 72 h 0.705 / 0.865, 7 d 0.858 / 0.884
    (7 d is low skill). The TPI > 0.6 rule scores 0.469 / 0.728 at 24 h.
  - VAL 24 h PR-AUC is 0.13 against a base rate of 0.04. Per-event median PR-AUC is 0.68; the
    minimum is Petrópolis at 0.04, ROC 0.23, which is a genuine GFS miss.
  - TEST is extreme storms, so it overstates everyday skill. ML ≈ a rain / local-P99 rule.
- **Early warning** (issued ≥ 1 d before the documented onset): 48 h PR-AUC ML 0.25 (ROC 0.79), the
  one-feature rain/P99 rule 0.23, TPI 0.09. RS 2024 and Petrópolis have recall 0 before onset for
  every model.
- **v3** (curated event split, season negatives, base rate 0.5–2.4 %): **not served**. On GFS TEST
  the rain/P99 rule beats or ties it: PR-AUC 48 h 0.17 vs 0.24, 72 h 0.22 vs 0.33, 7 d 0.55 vs 0.56.
- **Lead 0–3 d** (target = onset, GFS issued d−k, case-control base rate 0.32):

  | Model | PR-AUC 0 / 1 / 2 / 3 d |
  |---|---|
  | ML | 0.73 / 0.73 / 0.56 / 0.63 |
  | Rain rule | 0.78 / 0.75 / 0.59 / 0.57 |
  | TPI | 0.53 / 0.49 / 0.34 / 0.34 |
  | Random | 0.32 |

  - Recall: ML 0.65 → 0.48 from 0 d to 3 d; TPI High+ 0.27 → 0.16.
  - TPI precision is higher at 0 d: 0.87 vs ML 0.68.
  - RS 2024 and Petrópolis are missed by every model at every lead.
- **Early vs Final for RS 2024** (same rows): ML recall goes 0.01 → 0.14 at 0 d and 0.01 → 0.25 at
  1 d with Early rain; 2–3 d is unchanged. This is the same product effect as §1, seen from the ML
  side.
- **T_ML v2** (no RS in training): PR-AUC 0.747 / ROC 0.929, against the slope rule's 0.829 / 0.918.
  The slope rule is better on PR-AUC.
- **Never say:** "ML predicted RS 2024", "ML beats a simple rain rule", or any v1 GFS number
  (superseded, L33).

---

## 5. Other published-number fixes today (for the record)

| Fix | Outcome |
|---|---|
| **People** | GPW v4.11 2020 **unadjusted** count on its native 30″ grid, exact polygon-cell fractions, everywhere. RS people in Critical 1.49 M → 1.68 M at v1 (1.63 M at v2). WA `people_flooded` 1,151 → 949. Replay zones are fixed (Viamão 3,811 → 74; WA −1e40 zones finite). The ~30 %-low Now and GEE grids are never published. |
| **A8** | Now `critical_km2` = Critical only, `alert_km2` = High+. Stats cover Brazilian land only. Now TPI is masked on permanent water. |
| **Copernicus EMS EMSR720 (RS 2024)** | Flood reference for 5 Taquari AOIs. Porto Alegre and Guaíba are not covered. 497 landslide polygons. DIST scars have a 3.3× lift over a random steep-terrain baseline at 100 m and 1.1× at 500 m (served v1 scores; re-run with v2 TPI where TPI is involved). |
| **HAND validation** | Served radius 1500 m: IoU 0.34 RS, 0.19 WA. Best radius 1000 m: 0.42 / 0.26. The radius choice is post-submission because it changes the TPI. |

## 6. Open (post-submission)

1. A Now Early-vs-Final consistent climatology.
2. Replay people for the GFS layers is fixed, but `scar_people` still uses centroid density.
3. The `_compact_geojson` MultiPoint path should be audited. The catalogs are fine: one MultiPoint
   holding 16,027 / 15 points.
4. NISAR SME2 into S (cut today), and coherence with a snow/forest-aware threshold.
5. TWI/HAND downscaling of S, tested against EMS and NISAR, kept only if it beats bilinear.
6. 082 D / 010 D paddy-versus-river separation with MapBiomas.
7. The Uruguay TPI replay (cut today) with `clim_imerg_0p1`; with IMERG Final once it is released.
