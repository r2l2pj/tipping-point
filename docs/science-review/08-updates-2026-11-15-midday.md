# Midday updates, 15 Nov 2026

This section adds the results that landed after sections 01–07 were written (about 09:30–12:00 BRT).
Every number is checked against `data/metrics.json` at data `origin/main` f2b7ff3 (L43 lift keys)
or against the file named next to it. Metric keys are under `cases.<case>.` unless written in full.
Where a number here differs from an earlier section, this section is newer.

Conventions used below:

- **Onset** = the L37 event onset. RS 2024: first deaths of the event (2 flood deaths, Paverama),
  2024-04-29 00:00 BRT = 03:00Z.
- **GFS 24 h** = the NOAA GFS 00Z run issued on 2024-04-28, which is 27 h before the onset. Frames
  depict dates up to and including the onset day, and we take the max over frames.
- **High+** = TPI > 0.4 (Alto or Crítico). **Critical** = TPI > 0.6.
- **Lift** (L43) = hit rate ÷ area share. The area share is the percentage of the scored domain
  (finite-TPI cells, geodesic km²) above the same cut. A lift of 1 means no better than a random
  location in that domain.

---

## 1. RS 2024: independent reference from Copernicus EMS EMSR720

Copernicus EMS Rapid Mapping activation **EMSR720** mapped the May 2024 Rio Grande do Sul floods
and landslides. It is a reference that is independent of our pipeline. **Scope caveat:** EMSR720
covers only five Taquari-valley AOIs (Guaporé, Encantado, Roca Sales, Das Antas Dam, Santa Tereza),
258.2 km² of scored domain in total. It does not map the Porto Alegre/Guaíba metro flood, so every
score below applies to the Taquari valley only, not to the whole case. Licence: free use with
attribution (© European Union, Copernicus Emergency Management Service).

### 1a. Flood extent on 2024-05-06 (observed TPI_flood vs EMS)

| Metric | Value | Key |
|---|---|---|
| IoU | 0.185 | `rs_2024.flood_ems_iou` |
| F1 | 0.31 (0.3123) | `rs_2024.flood_ems_f1` |
| Precision | 0.46 (0.4645) | `rs_2024.flood_ems_precision` |
| Recall | 0.24 (0.2352) | `rs_2024.flood_ems_recall` |
| TP / FP / FN cells | 1,066 / 1,229 / 3,466 | `rs_2024.flood_ems.headline.{tp,fp,fn}` |
| EMS flooded / TPI flagged / overlap | 32.0 / 16.2 / 7.5 km² | `rs_2024.flood_ems.headline.*_km2` |

Settings: observed band 20240506, High+ (`TPI_flood > 0.4`), cell-centre rasterization. The EMS
reference is the union of the "5-Flood" polygons (flooded area, flood trace and maximum flood
extent), with BLP permanent water removed.

What this means: TPI_flood is a susceptibility index on a ~90 m grid, not a flood map. The
precision of 0.46 says about half of the cells it flags were mapped as flooded. The recall of 0.24
says it misses most of the mapped water.

### 1b. Flood area flagged before the onset (with lift)

The denominator is the union of all EMS flood polygons over all dates and AOIs: 36.45 km², rasterized
cell-centre on the ~90 m grid.

| Source | High+ % of EMS flood area | Critical % | Area share High+ | Lift High+ | Key |
|---|---|---|---|---|---|
| Observed band 2024-04-29 (rain Apr 26–28) | 0.21 % | 0.0 % | – | – | `rs_2024.flood_ems.flagged_before_onset.observed_band_20240429` |
| GFS 24 h (issued 04-28, 27 h before) | 6.56 % | 0.10 % | 2.94 % | **2.23×** (Critical 1.25×) | `rs_2024.flood_ems_flagged_before_onset_{pct,area_share_pct,lift}` |
| GFS 48 h / 72 h (issued 04-27 / 04-26) | 0.0 % | 0.0 % | – | – | `rs_2024.flood_ems.flagged_before_onset.by_lead` |

What this means: the day-before forecast put about 2× more of the later-flooded area in High+ than
chance would. In absolute terms it is still small (6.6 %), and the runs issued 48 and 72 h before
the onset show nothing.

### 1c. EMS landslides (497 polygons, 6.37 km², undated)

The polygons were photo-interpreted from optical imagery acquired 2024-05-06..05-15 (Pléiades Neo,
GeoEye-1). The imagery shows the scars but not when each slope failed, so every polygon is scored
against the onset. We score with GFS 24 h High+ inside the EMS AOIs, where the High+ area share is
26.72 % (69.0 of 258.2 km²).

| Scoring | Hit | Lift High+ | Critical hit / lift | Key |
|---|---|---|---|---|
| **Centroid cell** (cell at the slide centre) | **56.94 %** | **2.13×** | 7.04 % / 2.93× | `rs_2024.landslide_ems_warned_gfs_24h_centroid_{pct,lift}` |
| Any touched cell | 81.09 % | 3.03× | 20.72 % / 8.63× | `rs_2024.landslide_ems_warned_gfs_24h_{pct,lift}` |
| Observed band 04-29 (any cell) | 31.99 % | – | 0.0 % | `rs_2024.landslide_ems_warned_observed_pct` |
| GFS 48 h / 72 h | 0.0 % | – | 0.0 % | `rs_2024.landslide_ems_warned_gfs_{48,72}h_pct` |

Caveats:

- "Any cell" is more permissive than the area share it is compared with, so its lift is inflated
  (`prediction_lift.rows.rs_ems_landslide_any_cell.note`). **Use the centroid row (2.1×) as the
  honest lift.**
- About a quarter of the EMS AOI area is High+, so in this steep valley a large share of the slides
  would be hit by chance.

### 1d. Scar candidates (OPERA DIST-ALERT) vs EMS landslides, against a random baseline

There are 150 scar candidates inside the EMS AOIs (of 2,849 in total), covering 1.25 km². The random
baseline places the same number of shapes on steep terrain (T_slide > 0, slope ≥ 15°) inside the
same AOIs, with sizes drawn from the candidate areas. It is repeated over 200 seeds, and the 95 %
interval is the 2.5–97.5 percentile range over seeds.

| Radius | Hit (EMS slides with a candidate within r) | Random baseline mean [95 %] | **Lift** | Precision (lift) |
|---|---|---|---|---|
| 100 m | 27.36 % | 8.33 % [5.23–11.28] | **3.28×** | 68.67 % (3.75×) |
| 500 m | 54.53 % | 48.95 % [42.86–55.56] | **1.11×** | 80.67 % (1.13×) |

Keys: `rs_2024.scar_ems_{hit,precision}_{100,500}m_pct`, `rs_2024.scar_ems_lift_{100,500}m` and
`rs_2024.scar_ems_baseline`.

What this means: at 100 m the candidates are clearly better than random steep terrain. At 500 m the
observed value falls inside the random interval, so the 500 m hit rate that earlier sections quoted
is mostly a terrain effect and should not be cited as skill. Scars remain **candidates**.

---

## 2. RS 2024: prediction on the 16,027-point landslide inventory, now with lift (L43)

| Item | Value | Key |
|---|---|---|
| GFS 24 h High+ area share of the case grid | 4.16 % (1,329 of 31,972 km²) | `prediction_lift.rows.rs_inventory_slide.area` |
| Inventory points in High+ | 36.45 % of 16,027 | `slide_warned.forecast_gfs.alto_or_critico_pct` |
| **Lift High+** | **8.76×** | `slide_warned_gfs_24h_lift` |
| Critical: area share / hit / lift | 0.29 % / 1.87 % / **6.45×** | `slide_warned_gfs_24h_critical_lift` |
| GFS 48 h / 72 h runs | 0 % / 0 % | `slide_warned.by_lead` |
| Observed band 04-28 (no forecast) | 0.25 % | `slide_warned.by_lead.24.observed` |

The forecast flagged 4 % of the case area as High+, and that 4 % held 36 % of the mapped landslides.
Each inventory point takes the ~90 m cell that contains it, with no any-cell inflation.

Sensitivity checks:

- **SMAP latency** (`slide_warned_smap_latency.rows`). Live SMAP L4 soil moisture is 2–5 days old
  when a forecast is issued. Using soil wetness from 3, 4 or 5 days before the issue day, instead of
  1 day before, gives 35.41 / 35.35 / 34.25 % High+, compared with 36.45 % in the published replay.
  Critical goes from 1.87 % to 1.44 / 1.40 / 1.19 %. **The result holds under realistic latency
  (34–35 %).**
- **Onset anchor** (`slide_warned.sensitivity`, anchor `first_landslide_deaths`, 2024-04-30 03Z).
  If the anchor moves one day later to the first landslide deaths, the run 24 h before it (issued
  04-29) flags **0 %**. The 04-28 run (36.45 %) then becomes the 48 h run.
- **Robustness verdict.** The number depends on a single forecast run and is fragile to a one-day
  shift of the anchor. That is why L38 keeps it out of the headline and the voice-over.
- **Earliest Critical issue.** An earlier run (issued 04-23, 147 h before) flagged 40.63 % High+ and
  10.61 % Critical (`slide_warned.best_issued_gfs_before_anchor`). The runs in between (04-24..04-27)
  flagged 0 %, so this is not a sustained warning.

---

## 3. WA 2025: NISAR time series and the double-bounce hypothesis

### 3a. Three-pass HH time series (`data/wa_2025/nisar_timeseries.json`)

All three passes share track 172 D / frame 064, so the acquisition geometry is identical. Values are
the mean HH backscatter in dB over pixels valid on all three dates.

| Area | Nov 10 (ref) | Dec 4 (pre) | Dec 16 (post) | Range across passes |
|---|---|---|---|---|
| NISAR flood candidates (27.9 km², n = 7,758 px) | −9.72 | −9.68 | **−16.20** | −6.5 dB post vs ref |
| Pre-existing water | −13.56 | −14.78 | −16.14 | −2.6 dB post vs ref |
| Upland forest control | −2.20 | −5.11 | −4.42 | 2.9 dB |
| Marysville built-up control | −4.98 | −6.42 | −6.16 | 1.4 dB |
| Skagit farmland control (not flooded) | −5.20 | −5.99 | −6.08 | 0.9 dB |

Caveats from the file:

- **Selection.** The candidates were defined by the Dec 4 → Dec 16 darkening, so their drop on Dec 16
  is partly true by construction. The independent check is that Nov 10 and Dec 4 agree (−9.7 dB):
  the pixels were stable before the event, and the darkening is new.
- **Calibration.** The passes come from different BETA processing versions (004 / 006 / 007), and
  absolute dB can carry offsets of about 1–3 dB between passes. The controls drift 0.9–2.9 dB, so
  only changes larger than about 3 dB should be read.
- The NISAR swath covers about 74 % of the WA AOI.
- Status: **candidates, never confirmed.**

### 3b. Hypothesis test (`data/wa_2025/nisar_hypothesis.json`)

The hypothesis comes from L42b. HH (co-pol) darkening finds flooding in open fields. Flooded towns
and forest are expected to BRIGHTEN through double bounce, so a darkening detector would miss them.

The test compares the NISAR candidates with OPERA new-flood pixels on the OPERA ~30 m lattice, with
land cover from MODIS MCD12Q1 2025 IGBP (500 m). The file's verdict is `supported`. Our reading is
**supported, with one nuance.**

| Check | Result |
|---|---|
| Misses enriched in urban+forest | 44.8 % of the 60,936 missed OPERA flood pixels are urban or forest, against 5.2 % of the 8,718 detected pixels: **8.56× enrichment** |
| Recall by land cover | **open 24.7 %** (8,231 / 33,311), **urban 3.4 %** (166 / 4,838), **forest 1.3 %** (290 / 22,903) |
| Mean HH change Dec 4 → Dec 16, flooded pixels | open −2.72 dB (n = 31,662), urban −0.66 dB (n = 2,348), forest −0.18 dB (n = 16,753). Unflooded controls drift +0.2 to +0.4 dB |
| **Literal brightening (> +3 dB)** | **No.** At most 0.5 % of flooded urban or forest pixels rise by more than 3 dB. The +3 dB "bright-flood" layer recovers 7 of the 60,936 misses (0.01 %) |

What this means:

- The nuance: double bounce **cancels** the specular darkening. Flooded towns and trees stay near
  their already-bright baseline instead of getting brighter. So a darkening-only threshold misses
  the places where people live, and a "brighter than before" gate does not recover them.
- Closing the gap would need HV or texture information, or detection that is aware of land cover.

Caveats from the file:

- The HAND ≥ 15 m gate, the permanent-water exclusion and the 3 ha minimum mapping unit also cause
  misses. The land-cover split isolates the double-bounce component only approximately.
- The NISAR products are BETA.
- Land cover is 500 m data resampled with nearest neighbour to 30 m.

---

## 4. Live Southern Brazil NISAR: first pair rejected, status `no_usable_pair`

Files: `data/now/response/nisar_meta.json` (generated 14:44Z) and
`data/now/response/nisar_timeseries.json` (generated 14:40Z).

**The first live pair was rejected.** The published `flood_nisar.geojson` pair (Oct 25 → Nov 6, 2026,
track 024 D frame 101) failed on four counts:

1. It mixed acquisition modes: the pre image was mode 2005 F with 25 looks and the post image was
   mode 4005 P with 100 looks, so the two are not comparable.
2. Its water threshold fell back to −15 dB because the per-scene Otsu calibration failed.
3. Every candidate polygon lay within 2 km of the partial swath's valid-data edge and of the east
   edge of the domain bbox.
4. All candidates were in São Miguel Arcanjo (SP), outside the RS/SC/PR scope.

**New R2 rules** (`guards` in `nisar_timeseries.json`; commits 9456cc8 and f22427d):

- The pre and post images must have the same looks and the same acquisition mode. Mixed-mode pairs
  are rejected from the granule IDs before anything is read.
- Only per-scene Otsu thresholds are accepted. The −15 dB fallback is not flood evidence, and a pair
  that needs it is rejected.
- Components within 2 km of the scene edge or the bbox edge are dropped.
- Only polygons in RS, SC and PR are kept. The output status is one of `no_usable_pair`,
  `no_new_flooding` or `candidates`.

**Four-pass same-mode series** (all mode 4005_DHDH_A). The table shows the mean HH in dB over the
candidate strip. The pair dates are 024 D / 101; the other two are context passes on other tracks.

| Pass | Oct 25 (024 D, pre) | Oct 29 (074 A, context) | Nov 1 (125 D, context) | Nov 6 (024 D, post) |
|---|---|---|---|---|
| Candidate strip | inside the 2 km edge exclusion (n = 1 px) | −5.6 | −5.1 | inside the edge exclusion (n = 0) |
| Open-field control (55 km²) | −8.8 | −6.6 | −6.3 | −9.1 |

What this means: on the two mid-swath context passes the strip reads as ordinary land (−5.6 and
−5.1 dB). The "new water" call came from the edge of the data, not from a flood signal. The
context passes have different incidence angles, so their dB levels show only whether the strip was
water-dark mid-swath; they cannot be compared pixel by pixel with the pair.

**Current status:** the same-mode rerun picks the comparable 4005 pair, but Otsu fails on the
partial swath, so the run reports **`status: no_usable_pair`**. All flood fields are null with
reasons in `_null_reasons`. The newest acquisition available is 2026-11-06 21:16Z, and the search
covered 102 granules over a 60-day window. This is the honest live state: there are no NISAR flood
candidates for Southern Brazil this pass.

---

## 5. People-affected audit

Source: `~/code/r2l2pj/.crew/review/people-audit.md`, an independent recount at code f22427d.

- **The Now counts are correct.** The auditor recounted every Now priority zone independently and
  matched each one within about 4 %. For example, Florianópolis has 29,306 published people against
  29,344 recounted, in 38.6 km², about 760 people/km² against the census 796/km².
- **The raster matches the census.** Against the IBGE 2022 census the raster is within −4.6 % to
  +13 %: Florianópolis −4.6 %, Canoas +0.2 %, Porto Alegre +9.5 %.
- **Why the Now counts look low.** Most of today's flood polygons lie on farmland or wetland at 1–15
  people/km². The priority-zone geometry is drawn as the whole municipality while the count covers
  only the exposed part, which is a display issue.
- **GPW is the unadjusted count.** The raster is GPW v4.11 Population Count 2020 at 30″, not the
  UN-adjusted product. The "UN-adjusted" label in the code, README and SCHEMA is wrong; the
  difference is under 1 % for Brazil.
- **Replay zone bug (B1, real, published).** `pipeline/tp/bundle.py` `_pop_sum` adds the whole ~1 km
  cell of every cell whose centre falls inside a zone, and it does not mask the −3.4e38 nodata.
  As a result:
  - Small zones get a whole cell or nothing. Viamão shows 3,811 people where about 74–104 is right,
    and most zones show 0.
  - Three WA zones carry about **−1e40** people.
  - The fix is to reuse the exact Now method (`people_geom`) and then rebuild the bundles.
- **RS replay `people_critical` is about 11 % low (B2).** The published 1,488,833
  (`rs_2024.people_critical`) is computed on the case COG. That COG is GPW resampled by nearest
  neighbour to 0.00898° without area rescaling. On the native grid the same Critical mask
  (10,870.6 km², 2024-05-03) holds **1,679,659 (≈ 1.68 M)**. WA's 229,782 is about 14 % low by the
  same mechanism (about 267 k).
  - The right comparison is the 6.09 M people in the 2° × 1.5° case box, not RS's 11 M.
- **Latent issue (B3).** The Now fallback grid would read about 30 % low if the response step
  failed. Today `response_summary` overwrites those keys.
- **Status: fix in progress.** Until the bundles are rebuilt, cite the replay people numbers as
  "about 1.68 M (native-grid recount)", and do not cite per-zone replay people.

---

## 6. Early-warning ML (pre-onset only)

Source: `~/.cache/lead-ml/out3/early_warning.md` (local run output, 11:42 BRT; code
`ml/ml/local/early_warning.py`).

**Design:**

- Only forecasts issued strictly before each event's L37 onset are used (k ≥ 1 day). Rows issued on
  or after the onset ("rain already falling") are dropped, so the shortest horizon is 48 h.
- There are three kinds of negatives:
  - `same_season`: the same points on other dates within ±45 days of the onset's day of year;
  - `pre_event`: the same points earlier in the run-up;
  - `elsewhere`: points 5–50 km away on the same issue days.
- `ew_direct` is the v2 direct XGBoost refit without in-event rows.
- Calibration and the VAL-F1 thresholds are refit on pre-onset VAL rows for every model.
- The source is the NOAA GFS 00Z runs.

**48 h, TEST, GFS, pooled.** There are 20,581 positives and 357,776 negatives (244,862 pre_event,
100,464 elsewhere, 12,450 same_season), a base rate of about 5.4 %.

| Model | Pooled acc | P | R | F1 | Pooled PR-AUC / ROC | VAL PR-AUC |
|---|---|---|---|---|---|---|
| `ew_direct` (pre-onset XGBoost) | 0.895 | 0.277 | 0.388 | **0.323** | 0.252 / 0.788 | 0.144 |
| `direct_rain` (v2 direct XGBoost) | 0.876 | 0.231 | 0.395 | 0.292 | 0.253 / 0.780 | 0.139 |
| `xgb_rain_soil` (served v2) | 0.913 | 0.245 | 0.165 | 0.197 | 0.181 / 0.822 | 0.134 |
| **`fc_max72_rel`** (1 feature: max 72 h forecast rain ÷ local 72 h P99, untrained) | 0.853 | 0.186 | 0.381 | 0.250 | 0.215 / 0.781 | 0.132 |
| `fc_max24_rel` (max daily forecast rain ÷ local daily P99, untrained) | 0.867 | 0.217 | 0.403 | 0.282 | 0.232 / 0.771 | 0.128 |
| **`rule_tpi`** (TPI > 0.6) | 0.914 | 0.112 | 0.047 | 0.066 | 0.086 / 0.601 | 0.053 |

What this means:

- Accuracy is dominated by the 94.6 % negatives, so read P, R and F1 instead.
- The best trained model beats the one-feature rain/P99 rules only modestly: F1 0.32 against
  0.25–0.28. This is consistent with the audit in 07, which found the ML is essentially a
  rain-exceedance detector.
- The TPI > 0.6 rule is a poor pre-onset alarm: recall is 4.7 %.

**Event spread.** The table gives 48 h GFS per-event recall at the VAL-F1 threshold.

| Model | Angra 22 | Emilia 23 | Helene 24 | Himachal 23 | Petrópolis 22 | **RS 24** | Rwanda 23 | São Sebastião 23 | WA 25 |
|---|---|---|---|---|---|---|---|---|---|
| ew_direct | 0.97 | 0.49 | 0.47 | 0.60 | **0.00** | **0.00** | 0.38 | 0.50 | 0.88 |
| fc_max72_rel | 0.97 | 0.50 | 0.43 | 0.52 | **0.00** | **0.00** | 0.78 | 0.50 | 1.00 |
| rule_tpi | 0.57 | 0.07 | 0.00 | 0.02 | **0.00** | **0.00** | 0.09 | 0.23 | 0.13 |

- Per-event PR-AUC ranges from about 0.03 (Petrópolis) to about 0.5–0.7 (Angra, Himachal).
- The event-weighted F1 over 8 events is 0.31 for `ew_direct` and 0.25 for `fc_max72_rel`.

**Events missed by every model:**

- **RS 2024 and Petrópolis 2022 are missed by every model at 48 h and 72 h** (recall 0.000 on GFS).
- The cause is not diagnosed yet. For RS this is consistent with §2, where the forecast signal sits
  in a single run (issued 27 h before the onset) and the earlier runs are at 0 %.
- At 7 d, RS recall rises to 0.59–0.71 for the rain models (Petrópolis 0.33–0.83).

**Caveats:**

- This is a local run on the v2 (random/block) split, with case-control negatives.
- The precision values depend on the base rate and are not operational alarm precision.
- Under a perfect forecast (observed IMERG) all models improve, which is an upper bound. Even then,
  Petrópolis stays at 0 recall at 48 h.
- ML v3 (L41) is the fix for the split. See §8.

---

## 7. Decisions L37–L43 (summary)

Source: `~/code/r2l2pj/.crew/plans/decisions.md`.

| ID | Time (BRT) | Decision |
|---|---|---|
| L37 | 09:35 | "Before the damage" is measured from the documented **start** of the event, not the peak. Onsets per event, with sources. RS = first deaths 2024-04-29 (the old fixed 05-01 date is retired). Warned %, lead times and the ML TEST label window are recomputed from the onset. |
| L38 | 10:05 | After re-anchoring, the RS number is 36 % of 16,027 landslides High+ in the GFS run 27 h before the first deaths (Critical 2 %; the 48/72 h runs 0 %), and WA is 0 % at 24/48 h. The pitch spine is **response** with NISAR. Prediction is a supporting line with no number in the headline or voice-over, because it flips to 0 % with a one-day anchor shift. "88 % before May 1" is retired. |
| L39 | 10:20 | Production moves to the team VPS; Vercel is no longer the target. |
| L40 | 10:55 | Overfit audit (section 07): no leakage, but the TEST headline overstates operational skill, and the ML ≈ the rain/P99 rule. Honest ML statement: per-event ROC median 0.93, pooled PR-AUC 0.62 (bootstrap 0.40–0.79), VAL 0.13 at a base rate of 0.04. Operationally that is about 4–5 alarm-days per site-year, of which at most 2–10 % hit a catalogued slide. The live map switches to v2. |
| L41 | 11:00 / 11:10 | ML v3 uses a **curated event-level split**: TEST = RS 2024, Helene 2024, Petrópolis 2022 (+ WA); VAL = São Sebastião 2023, Rwanda 2023, Himachal 2023; TRAIN = Maria, Kerala, Emilia, Angra + filtered GLC. Negatives are the same sites at the onset day of year ±45 days, in the event year plus 2 non-event years. Target: VAL precision ≥ 0.30, reported with its base rate, never tuned on TEST, and it must beat the rain/P99 rule. |
| L42 | 11:15 | Re-centre on NISAR, per the challenge's "the core must be NISAR and surface change". The tagline becomes "Rain warns. Radar sees through." Add a testable hypothesis (§3b) and a "future of NISAR" section, publish WA HH+HV with the 3-date series (§3a), and show the live Southern Brazil pass (§4). |
| L43 | 11:55 | ML stays an honest experiment. The focus is forecast-driven prediction tied to NISAR: every risk zone shows the next expected NISAR pass ("where and when to look"). Every pre-onset hit rate is reported with **lift over the area share flagged**, never as a bare hit rate (§1–2). |

---

## 8. Still running at the time of writing

- **ML v3 on the curated split (L41).** Training and evaluation are in progress, and no numbers are
  published yet. Until they are, the ML results in 04, 07 and §6 stand.
- **Alarm precision per municipality.** This turns the per-site-year alarm-day estimate (L40) into
  "of the days we would have alerted municipality X, how many had a catalogued slide or flood". It
  is not published yet.
- **Replay people fix (§5, B1/B2).** The fix computes the replay zones and `people_critical` with the
  native-grid exact method and then rebuilds the rs_2024 and wa_2025 bundles. Until then, the
  published `rs_2024.people_critical` = 1,488,833 is known to be about 11 % low (≈ 1.68 M), and the
  per-zone replay people are unreliable.
