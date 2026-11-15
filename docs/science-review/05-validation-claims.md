# 05 — Validation of the index on the replay cases, and what we claim publicly

*Science review for the team geoscientist. Written 2026-11-15 ~10:45 BRT. Read-only audit.*

**Sources of truth used here**

| Source | Version |
|---|---|
| `tipping-point-data/data/metrics.json` | `origin/main` **90b86c1** (2026-11-15 10:30 BRT). The local `main` checkout (4f9e295) is stale, so all keys were read from `origin/main`. |
| `data/SCHEMA.md`, `README.md`, `config.yaml`, `events/*.json`, `.herdr/l35-result.md` | same commit |
| Submission drafts | `tipping-point-app-wt/submission`, branch `submission-main` **61a78a4** (`submission/project-page.md`, `FORM.md`, `demo-script.md`, `slides-outline.md`, `captions.srt`, `REVIEW.md`, `COMPLIANCE.md`, `summary.md`) and the app `README.md` at that commit |
| Decisions | `.crew/plans/decisions.md` (E7, E8, A1, A2, L10b, L18, L19, L22, L28/L28b, L30, L32–L38) |
| Research | `.crew/research/event-onsets.md` (L37), `r-warnrs.md`, `r-warnwa.md`, `r-wainv.md`; `.crew/review/trace-numbers.md` |

**Conventions**
- Keys are written `rs.<key>` / `wa.<key>`, meaning `cases.rs_2024.<key>` / `cases.wa_2025.<key>` in `metrics.json`.
- Classes: **High** = "Alto", TPI > 0.4. **Critical** = "Crítico", TPI > 0.6.
- `*_pct` keys are 0–100. IoU, F1 and AUC values are 0–1.
- "Hindcast" means archived IMERG V07 and SMAP L4 with no latency modelled (README, "Replay-case caveat"). "Forecast as issued" means the L22/L28b NOAA GFS replay: observed IMERG through D−1, the GFS 00Z run of D, SMAP held at D−1.

---

## 1. The two replay cases

### 1.1 Rio Grande do Sul, April–May 2024 (`rs_2024`, event id `rs-2024-05`)

| Item | Value | Source |
|---|---|---|
| AOI | bbox W −52.5, S −30.5, E −50.5, N −29.0 (EPSG:4326). That is ≈ 32,200 km² by spherical-area calculation, covering the Serra Gaúcha, the Taquari valley and the Porto Alegre metro area. | `config.yaml cases.rs_2024.aoi`; area computed here |
| Reference date | 2024-05-02 (config `event_date`). The events file has `start_date` 2024-04-27. | `config.yaml`, `events/rs-2024-05.json` |
| Replay window | 2024-04-22 → 2024-05-07 (D−10 … D+5) | `events/rs-2024-05.json gee.window` |
| ML label window | 2024-04-27 → 2024-05-06 | `config.yaml label_window` |
| Landslide inventory | UFRGS/Cemaden, **n = 16,027 points**. Points are imagery-mapped and **undated**. The inventory papers (Andrades-Filho et al. 2025; Egas et al. 2025) give only "late April–May 2024". It is used for validation only and is never an input (E8). | `rs.slide_warned_n`, `rs.landslide_hit.n_points`, `rs._null_reasons.point_lead_times`, event-onsets.md |
| Radar | **None.** The event predates the NISAR launch (Jul 2025). OPERA DSWx-S1 has no granules for 2024-05-01..20 (checked in CMR). An OPERA RTC-S1 route exists for 2024-05-08/11 but is waiting on an Earthdata EULA. | `rs._null_reasons.flooded_km2`, `.flood_warned_pct` |
| Flood reference map | None, so `rs.flooded_km2`, `rs.iou`, `rs.f1` and `rs.flood_warned_pct` are all **null with a reason** | `rs._null_reasons` |
| Population in AOI | 5,298,955 (GPW v4.11, 2020 epoch, 30″) | `rs.population_total` |

### 1.2 Western Washington atmospheric rivers, December 2025 (`wa_2025`, event id `wa-2025-12`)

| Item | Value | Source |
|---|---|---|
| AOI | bbox W −122.6, S 47.0, E −121.5, N 48.5. That is ≈ 13,700 km², covering the Snohomish, Skykomish, Snoqualmie and Skagit lowlands and the Cascade foothills. | `config.yaml cases.wa_2025.aoi` |
| Reference date | 2025-12-10 (config). The events file has `start_date` 2025-12-08. | `config.yaml`, `events/wa-2025-12.json` |
| Replay window | 2025-11-30 → 2025-12-20 (D−10 … D+10) | `events/wa-2025-12.json gee.window` |
| NISAR | L2 GCOV, track **172D**, frame **064**, `NISAR_L2_GCOV_BETA_V1`. These are pre-calibration BETA products, so only relative change on the same frame is used. Scenes: 2025-11-10 (no-event reference), **2025-12-04 (pre)**, **2025-12-16 (post)**, all at 03:18 UTC. | README "NISAR products", events file |
| Swath coverage | The post scene covers **74.1 %** of the AOI; the SW is outside the swath. `flooded_km2` is therefore a **lower bound within the swath**. | `wa.swath_coverage_pct`, `wa.flooded_km2_scope` |
| Flood detector | Per-scene adaptive Otsu on co-pol GCOV backscatter change. −15 dB is used only as fallback/clamp. Other steps: median 3×3 filter, −3 dB drop, HAND < 15 m gate, permanent water removed, 3 ha minimum mapping unit. The detector misses double bounce (bright flooding); a separate bright-flood layer holds 0.66 km². | README "Method", `wa.threshold_db`, `wa.flood_bright_km2`, A2 |
| Landslide inventory | **15 points** from the Washington Geological Survey (WA DNR) "December 2025 Atmospheric River Landslides" Clearinghouse. They are treated as undated. NASA GLC/COOLR has **no** in-window WA records; its last WA record is 2018-05-30. | `wa.landslide_hit.n_points`, `wa._null_reasons.lead_time_*` ("WGS landslide inventory has 15 points … undated"), `r-wainv.md` |
| Population in AOI | 3,431,240 | `wa.population_total` |

---

## 2. Onsets: what "before the damage" is measured from (L37)

### 2.1 The documented onsets

| Case | Anchor in use (`*_warned_anchor_utc`) | Meaning | Source (`*_warned_anchor_source`) | Confidence (event-onsets.md) |
|---|---|---|---|---|
| RS 2024 | **2024-04-29 00:00 BRT = 03:00Z** | First deaths of the event: 2 flood deaths at Paverama on Apr 29. Heavy rain began Apr 27 and stalled from Apr 29. First **landslide** deaths came on Apr 30 (Itaara, Silveira Martins). Serra slides likely peaked May 1–2, but they are undated. | MetSul laudo (metsul-laudo_V4_assinado.pdf); Jornal do Comércio 2024-04-29; SGB (Guimarães et al., rigeo 25165) | high for Apr 27 and Apr 29; medium for the Apr 30 landslides |
| WA 2025 | **2025-12-08 00:00 PST = 08:00Z** | Onset day: heavy rain began Dec 8. Skagit County EOC fully activated at 21:00 PST. Damage and evacuations followed on Dec 9–10, and river records on Dec 11–12. | WA EMD SEOC sitrep 25-4783 (2025-12-10) | medium (no dated first landslide) |

**Sensitivity anchors published alongside** (`*_warned.sensitivity[]`, `forecast_lead_time_onset.sensitivity[]`):
- RS:
  - 2024-04-30 03Z: first landslide deaths.
  - 2024-05-01 00Z: the previous fixed dating, L36a, now superseded.
- WA:
  - 2025-12-09 00:35Z: King County Flood Warning Center opening. This was the previous "damage" anchor. `r-warnwa.md` confirms it marks the Center opening, not a separately issued Phase-2 alert.

**Official warnings, for context** (`*.official_warning_at`):

| Case | Official warning | Notes |
|---|---|---|
| RS | **INMET 2024-04-26 13:50 BRT (16:50Z)** | Earliest notice with an exact timestamp. INMET also issued a date-only forecast notice on 2024-04-20, which is the earliest dated official notice. |
| WA | **NWS Seattle Flood Watch 2025-12-06 00:10Z** (VTEC O.NEW.KSEW.FA.A.0012) | `r-warnwa.md` |

Both official warnings precede both onsets.

### 2.2 Why we moved from peak anchors to onset anchors

- Neither inventory has per-landslide timestamps. Before L37 the RS "warned" metric was scored against a fixed **2024-05-01 00Z**, close to the peak (`config event_date` 2024-05-02).
- **The look-ahead problem.** Under L28b, a GFS run issued at D 00Z includes **observed IMERG for every day ≤ D−1**. The "24 h before May 1" run (issued 2024-04-30 00Z) had therefore already ingested three days of observed storm rain (Apr 27–29).
- **Evidence from the observed curve** (`rs.slide_warned.by_date_observed`, % of the 16,027 points High or above / Critical):

| Day | High+ | Critical |
|---|---|---|
| Apr 28 | 0.25 % | 0 % |
| Apr 29 | 3.66 % | 0 % |
| Apr 30 | **88.83 %** | 73.46 % |
| May 1 | 90.8 % | 84.1 % |

- **Conclusion.** The 88.13 % "forecast" for May 1 (`rs.slide_warned.sensitivity[fixed_dating_may1].by_lead.24.gfs`) was mostly a **nowcast of a storm already under way**, not a forecast made before the damage. Lucas ruled (L37, 09:35 BRT) that "before the damage" is measured from the **start** of the event (first documented damage), with a cited source per event. The 88 % headline was put on hold (L37), then retired (L38).
- **Side effects of the move:**
  - The older `forecast_lead_time` block (RS anchor 2024-04-27 15Z MetSul; WA 2025-12-09 00:35Z King County) is kept in `metrics.json` under its own anchor. Only the `*_onset` blocks are quotable.
  - The **as-available nowcast lead-time keys** were **not** re-anchored. These are `rs.lead_time_*`, `rs.warned_before_pct`, `rs.lead_time_asavail`, and also `rs.official_warning_delta_h` −51.3 h. They still refer to the MetSul anchor 2024-04-27 or to undocumented reference instants. See Q4.

---

## 3. "Warned before": the published metrics

**Definition** (`*.slide_warned.definition`, `*_by_lead_definition`, SCHEMA "Did the alert come before?"):
- Each damage location takes the ~90 m TPI cell that contains it.
- A location counts as warned if that cell is **High or above** (and separately, Critical) in the frames that were available before the onset.
- **GFS route:** the NOAA GFS 00Z run issued on the UTC date of (onset − h), so at least h hours before the onset. Its d1..d3 frames depicting dates ≤ the onset day are used, taking the max over frames.
- **Observed route:** the observed (hindcast) band dated onset − h/24 d, with latency ignored. It is therefore an upper bound on what an observed-only system could have known.
- Cells with no TPI value are kept in the denominator (`no_tpi_pct`).

> Inconsistency: SCHEMA says High is "≥ 0.4", but the `definition` strings and the app class cut say "> 0.4". The values were unchanged by commit 7128755, but the text should agree.

### 3.1 RS 2024: landslides (`rs.slide_warned*`, n = 16,027 inventory points, onset 2024-04-29 03Z)

| Run / band | Issued | Hours before onset | High+ (% of points) | Critical (% of points) | Key |
|---|---|---|---|---|---|
| **GFS "24 h"** | 2024-04-28 00Z | **27 h** | **36.45 %** (≈ 5,842 pts) | **1.87 %** (≈ 300 pts) | `rs.slide_warned_gfs_24h_pct`, `_critical_pct`; `= rs.slide_warned_pct` (headline basis) |
| GFS "48 h" | 2024-04-27 00Z | 51 h | **0 %** | 0 % | `rs.slide_warned_gfs_48h_pct` |
| GFS "72 h" | 2024-04-26 00Z | 75 h | **0 %** | 0 % | `rs.slide_warned_gfs_72h_pct` |
| Observed (hindcast), band Apr 28 | — | 27 h | **0.25 %** | 0 % | `rs.slide_warned_observed_pct` |
| Earliest issued run with any signal | 2024-04-23 00Z | 147 h | 40.63 % | 10.61 % | `rs.slide_warned.best_issued_gfs_before_anchor` |
| Runs issued Apr 24 / Apr 25 | — | 123 / 99 h | 0 % | 0 % | `rs.slide_warned.by_issue_gfs` |

`no_tpi_pct` is 0.77 % (GFS) and 0.75 % (observed). These points are kept in the denominator.

**Sensitivity to the anchor** (`rs.slide_warned.sensitivity[]`):

| Anchor | GFS 24 h | GFS 48 h | GFS 72 h | Observed 24 h |
|---|---|---|---|---|
| **2024-04-29 03Z (onset, in use)** | **36.45 / 1.87** | 0 / 0 | 0 / 0 | 0.25 / 0 |
| 2024-04-30 03Z (first landslide deaths) | **0 / 0** (run of Apr 29) | 36.45 / 1.87 (run of Apr 28) | 0 / 0 | 3.66 / 0 |
| 2024-05-01 00Z (old fixed dating, **retired**) | **88.13 / 69.07** (run of Apr 30) | 0.27 / 0.01 | 70.89 / 22.01 | 88.83 / 73.46 |

(Each cell: High+ % / Critical %.)

**How to read this**
- The whole positive result rests on **one GFS run** (Apr 28 00Z). Its d1 frame (3-day sum ending Apr 29: observed Apr 27 + GFS Apr 28–29) pushed 36 % of mapped landslide locations into High, but only 1.9 % into Critical.
- The Apr 29 run, issued closer to the event, shows **0 %** for its d1 frame (ending Apr 30). Its frames ending ≤ the onset day showed nothing. **Run-to-run variance dominates.**
- The **Apr 23 run** put 40.6 % High+ and 10.6 % Critical on frames depicting Apr 24–26. The observed index on those dates was **0 %** (`by_date_observed`). That run is a **timing error, a false alarm 3–5 days early**, not skill. The same pattern shows up in WA under L35 (§3.2).
- On the observed side, mapped landslide locations went from 0.25 % High+ (Apr 28) to 3.66 % (Apr 29) to **88.8 %** (Apr 30). The index followed the storm closely but did not lead it.
- README "Declared limitations" adds that GFS **under-forecast the RS peak by about half**: 3-day AOI-mean 141 mm forecast vs 290 mm observed, ending 2024-05-02.

### 3.2 WA 2025: floods (`wa.flood_warned*`, denominator = NISAR new-flood area, onset 2025-12-08 08Z)

Denominator: **27.92 km²** of NISAR flood pixels on the ~12 m flood raster (`wa.flood_warned.confirmed_flood_km2`). The canonical polygon total is 27.99 km² (`wa.flood_warned_denominator_label`).

| Run / band | Issued | Hours before onset | High+ (% of area) | Critical (% of area) | Key |
|---|---|---|---|---|---|
| GFS "24 h" | 2025-12-07 00Z | 32 h | **0 %** | 0 % | `wa.flood_warned_gfs_24h_pct` (`= wa.flood_warned_pct`) |
| GFS "48 h" | 2025-12-06 00Z | 56 h | **0 %** | 0 % | `wa.flood_warned_gfs_48h_pct` |
| GFS "72 h" | 2025-12-05 00Z | 80 h | **0.14 %** (≈ 0.04 km²) | 0 % | `wa.flood_warned_gfs_72h_pct` |
| Observed, all bands Nov 30 → Dec 8 | — | — | 0 % | 0 % | `wa.flood_warned_observed_*`, `by_date_observed` |
| Runs Dec 3 / Dec 4 | — | 128 / 104 h | 0.14 % | 0.14 % | `wa.flood_warned.by_issue_gfs` |

Observed curve on the flooded pixels after onset (`by_date_observed`, High+ / Critical):

| Day | High+ | Critical |
|---|---|---|
| Dec 9 | 0.01 % | 0 % |
| **Dec 10** | **41.0 %** | 8.97 % |
| Dec 11 | 89.0 % | 41.7 % |
| **Dec 12** | **98.9 %** | 70.2 % |
| Dec 14 onward | ≈ 0 % | — |

Sensitivity to the King County anchor (Dec 9 00:35Z): 0.14 % High+ at 24/48/72 h, 0 % Critical (`wa.flood_warned.sensitivity[0]`).

**Published explanation** (`wa.flood_warned.explanation`)
- TPI_flood is **pixel-local**. On the flooded valley floors T_flood is high (HAND term, mean ≈ 0.96). However, local 3-day rain stayed below the pixel's own P90 (R = 0), and SMAP wetness stayed low until the Dec 10 frame.
- The Snohomish/Skykomish/Skagit floods were driven by **rain upstream in the Cascades**, which a pixel-local R cannot see.
- So the index rose on the flooded floors only **after** the first damage.

**L35 upstream-rain experiment** (`.herdr/l35-result.md`; re-score only, nothing published or served)
- Variant tested: R_flood = max(R_local, R_upstream), with R_upstream computed over the HydroBASINS lev-10 upstream basin.
- What it improved: the High+ share of the flooded area on Dec 10 rose from 41 % to 79 %.
- What it did not improve: **the onset-anchored warned % stayed 0**.
- What it cost: false Critical km²·days rose **+32 %**. False Critical on Dec 10 went from 117 to 309 km².
- Its best "early" run (Dec 4) hit 36 % on dates when the observed index was 0, which is again a timing error.
- **Not served** (L35/L36e). It may be pitched only as a post-event improvement tested on the same event.

### 3.3 What the submission says about "warned before"

- RS: the 36 % / 1.87 % / 0 % / 0 % / 0.25 % set with its anchor sensitivity appears **only** in the project-page validation table, the app README and the slide-5 footnote (L38 refinement). It is **never** in the headline or the VO.
- WA: "0 % (24 h, 48 h) · 0.14 % High, 0 % Critical (72 h)", with the upstream-rain explanation, in the table, the Limitations section, the app README and slide 5.
- "Official warnings came before the index in both replays":
  - This is true for **index values at the damage locations**: INMET Apr 26 16:50Z precedes the Apr 28 run's positive signal; NWS Dec 6 00:10Z precedes any High value on WA flooded pixels.
  - It is **not** true for the *anywhere-in-AOI* Critical signal (§4). The WA Dec 3 run flagged slide-Critical somewhere in the AOI three days before the NWS Flood Watch.
  - The sentence is correct as worded ("the index adds where and who, not an earlier warning"). Keep the AOI-wide number out of copy so the two do not get mixed.

---

## 4. "Critical anywhere in the region": the AOI-wide lead time (not at the damage sites)

**Definition** (`*.forecast_lead_time_onset.definition`)
- An issue day (GFS 00Z run before the onset) "warns" if **any pixel anywhere in the AOI** has TPI (slide **or** flood) > 0.6 in a frame depicting a date ≤ the onset day.
- lead_h = onset − issue 00Z.
- This is a lenient any-pixel test with tiny denominators (README "Declared limitations").

| Case | Warned / issue days | Earliest | Median lead | IQR | Hazard that triggered | Key |
|---|---|---|---|---|---|---|
| RS (onset Apr 29 03Z) | **2 of 7** (runs of Apr 23 and Apr 28) | 147 h | 87 h (n = 2, so the median is just the mean of 147 and 27) | 57–117 h | slide, both | `rs.forecast_lead_time_onset` |
| WA (onset Dec 8 08Z) | **5 of 8** (Dec 3–7) | 128 h | **80 h** | 56–104 h | **slide, all five** | `wa.forecast_lead_time_onset` |

Sensitivities:
- RS:
  - Apr 30 anchor: 4 of 8, median 63 h.
  - May 1 anchor: 5 of 8, median 72 h.
- WA:
  - King County anchor: 6 of 9, median 84.6 h, earliest 144.6 h. **This is the origin of the retired "85 h".**

**Caveats for the reviewer**
- **(a)** In WA every warning came from landslide cells somewhere in a 13,700 km² AOI. None came from the flooded valleys, and the WA damage being validated is flood.
- **(b)** In RS, one of the two warning runs is the Apr 23 timing error (§3.1).
- **(c)** No false-alarm denominator exists. We have not measured how often "Critical anywhere" fires on non-event days in the climatology.

**Copy status:** these numbers are **not used in any public copy** (REVIEW.md:13; L38).

---

## 5. Critical area and people (hindcast, peak day, observed rain)

**Definition** (`tp.bundle._critical_stats`)
- `critical_km2` is the geodesic area of pixels with TPI > 0.6 for slide ∪ flood on the series **peak day**.
- `people_critical` is the GPW v4.11 2020 count under that union, via coverage-fraction reprojection to the ~1 km GPW grid.
- `test_metrics_consistency.py` asserts that the scalars equal the series peak row. It was added after an old-run value of 430.1 km² clobbered RS.
- Slide and flood areas are disjoint: T_flood is zeroed above 5° slope, while T_slide needs ≥ 15°. So union = sum.
- The docstring of `gee/impact.py` still says "TPI_slide > 0.6" only. It is stale; the bundle definition is authoritative.

| Case | Peak day | Critical total | Slide | Flood | Share of AOI | People | Share of AOI pop. | Keys |
|---|---|---|---|---|---|---|---|---|
| RS | **2024-05-03** | **10,870.6 km²** | 3,223.8 km² | 7,646.8 km² | ≈ 34 % of ≈ 32,200 km² | **1,488,833** | 28.1 % of 5,298,955 | `rs.critical_km2`, `_date`, `critical_slide_km2`, `critical_flood_km2`, `people_critical` |
| WA | **2025-12-12** | **4,127.5 km²** | 2,662.7 km² | 1,464.7 km² | ≈ 30 % of ≈ 13,700 km² | **229,782** | 6.7 % of 3,431,240 | `wa.*` same keys |

Other impact keys:
- People under the NISAR flood mask (WA): **1,151** (`wa.people_flooded`). This is GPW 2020 at 30″, so it is very coarse for a 28 km² mask.
- RS: no NISAR mask, so `rs.people_flooded` is null.

**Caveats**
- These are **post-onset hindcast** numbers: RS 4 days after onset, WA 4 days after onset. They describe exposure during the event, not a forecast.
- A third of each AOI is Critical on the peak day, and RS flood-Critical alone is 7,647 km². At that size the class has **low spatial specificity**: an index that labels a third of the region Critical will "contain" most damage almost by construction (see §6.1).
- The RS May 2 row has 10,569.8 km² and 1,487,866 people (REVIEW.md:93). Only the May 3 peak row is quoted.

---

## 6. Landslide validation after the fact

### 6.1 Catalogue hit rate: TPI_slide at inventory points (observed rain, not a forecast)

**Definition:** `landslide_hit_pct` is the share of inventory points whose 90 m TPI_slide cell is ≥ 0.6 on the headline day (`*.landslide_hit.definition`).

| Case | Day | Critical hit | Median TPI | High+ (≥ 0.4) | n | Key |
|---|---|---|---|---|---|---|
| RS | 2024-04-30 | 73.1 % | 0.707 | 89.6 % | 16,027 | `rs.landslide_hit.per_day` |
| RS | 2024-05-01 | 84.9 % | 0.871 | 91.4 % | 16,027 | same |
| RS | **2024-05-02 (headline)** | **85.86 %** | 0.898 | 91.7 % | 16,027 | `rs.landslide_hit_pct`, `_date` |
| WA | Dec 9 / 10 / 11 | 13.3 / 40.0 / 66.7 % | 0.24 / 0.47 / 0.66 | — | 15 | `wa.landslide_hit.per_day` |
| WA | **2025-12-12 (headline)** | **66.7 % (10 of 15)** | 0.665 | — | 15 | `wa.landslide_hit_pct` |

**Caveats**
- These are **after the fact**: observed rain, peak day, 1–4 days after the onset. The 86–88 % RS figures are **agreement during the event, not prediction**. 85.86 % is the Critical hit; the High+ share at the inventory points is 88.8–91.1 % on Apr 30–May 3 (`slide_warned.by_date_observed`).
- **No null baseline.** At 90 m scale (NASADEM native), the RS note adds that at 1 km the same series stays below 0.6 because slope flattens.
  - The relevant null is the Critical share of **steep (≥ 15°) terrain** in the AOI on that day. With 3,224 km² of slide-Critical, a random steep point may already have a high hit chance.
  - Neither key reports this baseline. See Q1.
- WA n = 15 is tiny. The Wilson 95 % CI on 10/15 is ≈ 42–85 %. The points are WGS reports assumed to be from the storms, not a verified inventory (`r-wainv.md`).

### 6.2 Post-event scar candidates (OPERA DIST-ALERT-HLS) vs inventories

**Method** (`*.scar_method`)
- VEG-DIST-STATUS ∈ {5, 6, 8} (≥ 50 % vegetation loss), first detection in the window.
- Slope ≥ 15° (NASADEM).
- Not cropland or water (MCD12Q1).
- Patch size 3 px … 1 km².
- Results are "candidates, never confirmed".

| Case | Window | Candidates | Area | Inventory within 100 m / 500 m of a candidate | Candidates within 500 m of inventory (precision proxy) |
|---|---|---|---|---|---|
| RS | 2024-04-27 → 06-15 | **2,849** | **20.02 km²** | 17.45 % / **46.13 %** of 16,027 | **55.07 %** |
| WA | 2025-12-05 → 2026-02-28 | 176 | 1.25 km² | 0 % / 0 % of 15 | 0 % |

RS sensitivity (`rs.scar_sensitivity`): relaxing the filters trades precision for recall.
- With ≥ 1 px: 500 m hit 70.5 %, precision 43.5 %.
- Including < 50 % loss: hit 81.7 %, precision 41.0 %.
- The non-default variants were measured before the 15 m publish compaction (`scar_sensitivity_note`).

People in candidate areas: RS 522, WA 14 (`*.scar_people`).

---

## 7. Flood-map validation: NISAR vs OPERA (WA only)

**Like-for-like headline** (`wa.validation_method`)
- Comparison: NISAR new-flood mask vs **OPERA DSWx-S1** new flood. New flood = water on 2025-12-15/17 **and not** water on 2025-12-01..07, cloud-free on both dates (S1 is unaffected by cloud).
- The DSWx-S1 reference is **Sentinel-1 radar (C-band), not optical**. trace-numbers.md issue #1 fixed earlier copy that called it optical.

| Metric | Value | Key |
|---|---|---|
| F1 | **0.151** | `wa.f1` |
| Precision | **0.187** | `wa.precision` |
| Recall | **0.126** | `wa.recall` |
| IoU | **0.082** | `wa.iou` |
| Confusion (px) | TP 8,777 · FP 38,086 · FN 60,704 | `wa.confusion` |

**Other comparisons** (published but not headline)
- All-water vs every DSWx-S1 water pixel: IoU 0.0075, F1 0.015, P 0.198, R 0.008 (`wa.*_raw`).
  - The FN of 1.19 M px is permanent or pre-existing water (1.21 M px), which the new-flood mask removes by design (`wa.opera_fn_diagnosis`).
- Optical OPERA DSWx-HLS all-water, cloud-free pixels only: F1 **0.026**, IoU 0.013 (`wa.hls_*`).
  - `hls_method` says this is "not comparable to iou/f1": 92.9 % cloud, so the reference is mostly unobserved.

**Context**
- NISAR mapped flood **27.99 km²** within the swath. Total water was 304.1 km².
- Largest clusters (`wa.flood_top_clusters`):
  - Snohomish valley at Fobes Hill: 10.61 km²
  - Snohomish–Monroe: 3.18 km²
  - Lower Snohomish: 1.27 km²
  - Skykomish at Sultan: 1.05 km² and 0.91 km²
- The **Skagit had already receded** before the Dec 16 pass; it peaked Dec 11–12 (README "Declared limitations").

**Interpretation for the reviewer**
- Low recall is expected and documented:
  - co-pol darkening only, with no double-bounce in urban or forest areas;
  - BETA calibration;
  - one-day offset between NISAR and DSWx-S1 (Dec 16 vs Dec 15/17);
  - a different sensor, band and resolution.
- With F1 0.15, the NISAR map is **an independent candidate layer that partly agrees with Sentinel-1**, not a validated flood extent. Copy correctly says "candidates".
- The comparison grid is not stated in the keys. TP + FP = 46,863 px matches 27.99 km² only at ~24 m pixels. DSWx-S1 is 30 m and the flood raster is ~12 m (`flood_warned_denominator_label`). See Q6.
- "NISAR swath ~ AOI" in `validation_method` is ambiguous. It is unclear whether the 26 % of the AOI outside the swath was masked out of FN.

RS has no flood-map validation (§1.1).

---

## 8. Ancillary checks

### 8.1 HAND proxy vs MERIT Hydro (`*.hand_validation*`; MERIT is reference only)

**Method** (`hand_validation_definition`)
- Proxy = NASADEM z − min(z) within radius r, on a common 90 m grid over the case AOI.
- Score = IoU of the HAND < 5 m zones, plus Pearson r over all pixels.

| Case | r = 1000 m (best) | r = 1500 m (**the radius the index actually uses**, `config thresholds.hand_proxy_radius_m`) | r = 3000 m | Low-HAND share: proxy vs MERIT (1000 m) | n px |
|---|---|---|---|---|---|
| RS | IoU **0.42**, r 0.90 | IoU **0.34**, r 0.86 | 0.24 / 0.77 | 13.9 % vs 31.7 % | 4,607,942 |
| WA | IoU **0.26**, r 0.87 | IoU **0.19**, r 0.85 | 0.11 / 0.79 | 6.0 % vs 22.0 % | 2,274,437 |

**Caveats**
- The proxy marks **less than half** of MERIT's low-HAND area at every radius, so flood T is conservative in extent.
- The served index uses 1500 m. Copy that quotes "IoU 0.42" (if any) should quote 0.34 for RS and 0.19 for WA at the served radius. SCHEMA says `hand_validation_radius_m` is "reported only, `thresholds.hand_proxy_radius_m` is NOT rewritten".
- **Retired "HAND 98 %":** before commit b4aacf9 (G2), `wa.hand_validation` was 0.975, an ambiguous percent-vs-fraction value that the app rendered as "98 %". The underlying radius IoUs were 0.0098 / 0.0094 / 0.0076, so the figure meant nothing. The G2 rebuild on one common grid replaced it (SCHEMA §hand_validation).

### 8.2 IMERG vs rain gauges (RS; L19; validation only, `rs.imerg_vs_gauge`)

**Setup**
- Product: GPM_3IMERGDF V07 daily Final, at the cells of 7 INMET automatic stations.
- Window: 2024-04-20..05-15; **182 station-days** (7 × 26).

**Results**
- Median Pearson r **0.887**.
- Median bias **+5.5 %**.
- Median event-total ratio (Apr 27–May 6) **1.12**.

| Station | r | Bias | Event total gauge → IMERG |
|---|---|---|---|
| A801 Porto Alegre JB | 0.977 | +5.5 % | 382.6 → 425.3 mm |
| B807 Belém Novo | 0.979 | +7.6 % | — |
| A840 Bento Gonçalves (Serra) | 0.919 | −16.7 % | 647.2 → 571.2 mm |
| A879 Canela (Serra) | 0.887 | −12.8 % | 562.0 → 561.8 mm |
| A882 Teutônia | **0.600** | +19.0 % | 280.0 → **514.1** mm, ratio 1.84 |

**Caveats**
- The comparison is a point gauge vs a 0.1° cell.
- IMERG **under-reads the two Serra stations by 13–17 %**. These are the landslide stations that matter most for TPI_slide.
- Teutônia's +19 % daily bias is inconsistent with its 1.84 event-total ratio. This suggests missing gauge days (see Q7).

### 8.3 Cloud cover (why radar)

| Case | Cloud | Basis | Key |
|---|---|---|---|
| WA | **92.9 %** | OPERA DSWx-HLS cloud class (WTR 253) on **2025-12-15/17**, over the AOI. That is post-storm, the same week as the NISAR post pass (Dec 16), not the Dec 8–12 storm days. | `wa.cloud_pct`, `wa.cloud_pct_source` |
| RS | **100 %** (cloud-free 0 %) | HLS v2.0 Fmask (L30 + S30), cloud/adjacent/shadow/snow bits; **20 scenes, 2024-05-01..03**. A pixel counts as clear if clear in **any** scene. | `rs.cloud_pct`, `rs.hls_scenes`, `rs.hls_scene_dates` |

Notes:
- "93 %" in the headline and VO is 92.89 % rounded. The complement, "optical saw 7 %", is used on slide 5.
- `rs.hls_observed_px` = 535,467 coexists with 0 % clear, so it presumably counts footprint pixels rather than clear ones. The key needs a definition (Q8).

### 8.4 Night-light drop (VIIRS Black Marble VNP46A2, `DNB_BRDF_Corrected_NTL`)

**Definition** (`gee/impact.py`): mean of the 7 nights after the event vs the 30 nights before, inside the affected area (Critical ∪ flood).

| Case | Drop | Before → after (band units, nW·cm⁻²·sr⁻¹ as stored) | Key |
|---|---|---|---|
| WA | **43.9 %** | 0.746 → 0.418 | `wa.night_light_drop_pct`, `ntl_mean_*` |
| RS | **33.1 %** | 2.586 → 1.730 | `rs.night_light_drop_pct` |

**Caveats**
- The mask is the Critical area: largely rural slopes and floodplain with very low radiance (WA mean 0.75), so small absolute changes become large percentages.
- Cloud residuals, snow (WA, December) and lunar or BRDF artefacts are not separated from outages.
- The "event" date for the 7/30-night split comes from config `event_date` (RS 05-02, WA 12-10), not the L37 onset.
- No control-area drop is reported.
- Present this as indicative only; it should not be read as a validated outage measure.

---

## 9. Every public claim, mapped to its data key and status

Statuses:
- **OK**: matches the key, with the needed caveat present.
- **OK-c**: matches, but a caveat should be added or tightened.
- **FIX**: wrong or misattributed.
- **STALE**: superseded number still visible in a public surface.
- **OPEN**: wording decision pending.

### 9.1 Headline and summary (project page form field 2 = `project-page.md` "High-Level Project Summary"; FORM §2)

| # | Claim (verbatim or close) | Key(s) = value | Status / note |
|---|---|---|---|
| H1 | **"clouds hid 93% of the area from optical satellites; the NISAR Dec 4 → Dec 16 pass pair mapped 27.99 km² of flood candidates through them"** (headline, L38) | `wa.cloud_pct` 92.89; `wa.flooded_km2` 27.987, `flooded_km2_scope` "within NISAR swath"; NISAR pre 2025-12-04, post 12-16 | **OK-c**: the summary sentence omits "within the swath (74 % of the area)". The validation table carries it, but the headline does not. The cloud figure is from Dec 15/17 DSWx-HLS, the same week as the post pass, so "through them" is fair. |
| H2 | "229,782 people were in critical areas" | `wa.people_critical` 229782, `critical_km2_date` 2025-12-12 | **OK-c**: no date or definition in the summary. It is the observed-rain hindcast peak day (4 days after onset), union of slide + flood TPI > 0.6. |
| H3 | "NISAR passes over Southern Brazil every 12 days … L-band sees through cloud and darkness" | mission facts (external) | OK (external) |
| H4 | "a live view refreshes every 3 hours" | now.yml cron `17 */3 * * *` (trace-numbers) | OK |
| H5 | "two past disasters replay day by day with the forecast as it was issued" | L22/L28b; `*.forecast_source` "noaa-gfs" | OK. D+4 onward frames are perfect-forecast upper bounds, labelled as such (demo-script production notes). |

### 9.2 Video VO and on-screen text (`captions.srt`, `demo-script.md`, L38 53-word VO)

| # | Claim | Key(s) | Status |
|---|---|---|---|
| V1 | "In Washington, clouds hid 93% of the area from optical satellites; NISAR radar mapped flood candidates through them." | `wa.cloud_pct` 92.89 | OK. No km² is spoken. |
| V2 | Lower third: "NISAR radar mapped 28 km² of flood candidates through them"; overlay "Flood candidates · 27.99 km² within the NISAR swath" | `wa.flooded_km2` 27.987 | OK (the overlay carries the scope) |
| V3 | "Civil Defense sees who is affected, ranked, live across southern Brazil every 3 hours." Overlay "229,782 people in critical areas (WA, Dec 12, 2025)"; Impact panel night lights −44 % | `wa.people_critical`; `wa.night_light_drop_pct` 43.92 | OK-c: the night-light caveat (§8.4) is not on screen. |
| V4 | "Tipping Point maps where NASA rain, soil and slope data tip toward failure." (RS replay, no number) | — | OK: no forecast number on screen (L38 refinement) |
| V5 | **"Rain warns. Radar confirms."** (cue 5, title and close cards, slide 1, summary.md tagline) | — | **OPEN**: Lucas's choice is pending (demo-script.md:15; MORNING.md:18). Reviewers (win-odds-claude, win-odds-gpt) read "confirms" next to "candidates" and F1 0.15 as an overclaim. The data cannot support "confirms" (§7). Recommend "Radar sees through." |

### 9.3 Slides (`slides-outline.md`; slide 5 = "Did it work?")

| # | Claim | Key(s) | Status |
|---|---|---|---|
| S1 | Slide 5 headline = H1 | as H1 | OK-c (as H1) |
| S2 | Slide 5 prediction line: "36% of the 16,027 mapped landslides … High or Critical … 27 hours before the first deaths (Critical: 2%)" | `rs.slide_warned_gfs_24h_pct` 36.45, `_critical_pct` 1.87, `slide_warned_n` 16027, anchor 2024-04-29T03Z | OK |
| S3 | **Slide-5 footnote** (required): "Earlier runs (issued 51 and 75 hours before) showed 0%. Onset = first deaths, Apr 29, 2024 00:00 BRT. Anchor-sensitive: with onset Apr 30 the 24 h run shows 0%; the earlier May 1 anchor gave 88%. n = 16,027 UFRGS/Cemaden inventory points." | `rs.slide_warned_gfs_48h_pct` 0, `_72h_pct` 0; `rs.slide_warned.sensitivity[first_landslide_deaths].by_lead.24.gfs` 0; `[fixed_dating_may1].by_lead.24.gfs` 88.13 | OK. Suggest adding "observed rain alone: 0.25 %" and "the Apr 23 run's early signal did not verify". |
| S4 | WA: "none of the NISAR-flooded area was High … 24 h or 48 h before onset (Dec 8, 2025); 0.14% in the run issued 72 h before … rivers flood from rain upstream" | `wa.flood_warned_gfs_{24,48,72}h_pct` 0 / 0 / 0.14; `_critical` 0; `explanation` | OK |
| S5 | "Official warnings came before the index in both replays" | `*.official_warning_at` (INMET 2024-04-26 16:50Z; NWS 2025-12-06 00:10Z) | OK at the damage locations. Do not pair it with the AOI-wide lead (§3.3). |
| S6 | "Optical saw only 7% … in WA (92.9% cloud) and 0% in RS (100% cloud)" | `wa.cloud_pct`, `rs.cloud_pct` 100, `rs.hls_scenes` 20 | OK-c: WA is Dec 15/17, post-storm |
| S7 | Slide 2: "478 municipalities, 185 deaths, 581,638 displaced at the peak" (Gov RS "SOS Rio Grande do Sul", updated Aug 19, 2025) | not in data (L36d one-source rule) | OK (external, single official source). Check that `.crew/deck/slides.pdf` was regenerated: trace-numbers #6 found the old 446/497 figures on p2. |
| S8 | Slide 6: "229,782 + 44 % (WA, Dec 12); 1,488,833 + 33 % (RS, peak May 3)" | `*.people_critical`, `*.night_light_drop_pct`, `rs.critical_km2_date` 2024-05-03 | OK-c (hindcast peak day; NTL caveat) |
| S9 | Slide 6: "GFS under-forecast the RS peak by about half" | README limitations: 141 vs 290 mm, 3-day ending 2024-05-02 | OK (README fact, not a metrics key) |
| S10 | Slide 6: "thresholds not yet validated across many events" vs app README "Thresholds were calibrated on a single event" | config `thresholds` (no calibration record) | **FIX** (trace-numbers #7): pick one wording. Neither is backed by a key. |
| S11 | Slide 3: ML trigger sentence "PR-AUC [X] vs [Y] … base rate [B]" | `data/ml/ml_card_v2.json headline_status` = "held: independent overfit audit pending" | **OPEN**: must stay without numbers. Brackets must not ship. |

### 9.4 Project page validation table (`project-page.md` "Validation")

| # | Row | Key(s) = value | Status |
|---|---|---|---|
| T1 | WA NISAR flood candidates 27.99 km² within swath; 74 % coverage; BETA | `wa.flooded_km2`, `swath_coverage_pct` 74.1 | OK |
| T2 | WA NISAR vs OPERA DSWx-S1 new flood: F1 0.15 · P 0.19 · R 0.13 · IoU 0.08 | `wa.f1` 0.1509, `precision` 0.1873, `recall` 0.1263, `iou` 0.0816 | OK. The label was fixed to Sentinel-1 radar (trace #1). |
| T3 | WA NISAR vs optical HLS water F1 0.026 | `wa.hls_f1` 0.0263 | OK |
| T4 | Cloud WA 92.9 % (DSWx-HLS Dec 15/17, after the storm) · RS 100 % (20 HLS scenes) | `wa.cloud_pct`, `rs.cloud_pct`, `rs.hls_scenes` | OK |
| T5 | RS scar candidates 2,849 / 20.0 km²; 46 % within 500 m; 55 % of candidates within 500 m | `rs.scar_candidates_n`, `_km2`, `scar_hit_500m_pct` 46.13, `scar_precision_500m_pct` 55.07 | OK |
| T6 | WA landslide catalog hits 66.7 % of 15 points; undated; scored Dec 12, after the damage, "not a forecast" | `wa.landslide_hit_pct` 66.667, `landslide_hit_date` 2025-12-12 | OK. Trace #2 was fixed. |
| T7 | People in Critical areas WA 229,782 (Dec 12) · RS 1,488,833 (May 3); "Critical cells (TPI > 0.6)" | `*.people_critical` | OK. The operator was fixed (trace #8). |
| T8 | Night-light drop WA 44 % · RS 33 % | `*.night_light_drop_pct` | OK-c (§8.4) |
| T9 | RS forecast-as-issued: 36 % / Critical 2 %; earlier runs 0 %; observed 0.25 %; anchor sensitivity (Apr 30 → 0 %; May 1 → 88.13 %); "One forecast run carries the result" | `rs.slide_warned*` (§3.1) | OK. This is the most carefully caveated row. |
| T10 | WA forecast-as-issued: 0 % (24/48 h) · 0.14 % High, 0 % Critical (72 h); local rain only | `wa.flood_warned*` | OK |
| T11 | ML trigger `[pending v2 re-score — L33]` | `ml_card_v2.headline_status` held | OPEN: the bracket must not ship. Either drop the row or say "withheld pending an independent overfit audit". |
| — | **Missing rows the reviewer may want**: RS catalogue hit 85.9 % (after the fact), IMERG vs gauge r 0.89 / +5.5 %, HAND proxy IoU | `rs.landslide_hit_pct`, `rs.imerg_vs_gauge`, `*.hand_validation_iou` | Not claimed publicly. If added, label the RS hit "during the event, observed rain". Quote HAND at the served 1500 m radius. |

### 9.5 Other public surfaces (public repos)

| # | Surface | Claim | Status |
|---|---|---|---|
| R1 | App README `## Validation` (submission-main) and FORM §8 | "Washington 2025: **NASA Global Landslide Catalog / COOLR**, 15 points" / "NASA GLC/COOLR: WA validation" | **FIX (attribution)**. The 15 WA points are **Washington Geological Survey** Clearinghouse reports (`wa._null_reasons`, `r-wainv.md`). GLC/COOLR has no in-window WA records. The WGS reuse licence is unconfirmed (`r-wainv.md`). Credit WGS and drop the GLC attribution for WA. |
| R2 | **Data README (`tipping-point-data` main, public)**, "Forecast lead time" paragraph | "rs_2024 **111 h** … wa_2025 **144.6 h** (median **84.6 h**, IQR 54.6–114.6, 6 of 8)" | **STALE**: these are pre-L37 anchors (§4). Replace them with the onset block (RS 2 of 7; WA 5 of 8, median 80 h, slide-driven, AOI-wide) or delete. |
| R3 | Data README "Declared limitations", "Did the alert come before?" | "RS 2024 — **88.1 %** of the 16,027 … 24-48 h before **2024-05-01** (observed 88.8 % / 73.5 %)"; WA "before the **first damage report**" | **STALE**: retired by L37/L38. Replace with §3.1/§3.2 numbers and the onset anchors. |
| R4 | Data README "Lead-time counts (observed replay)" | RS 0 of 5,678 clusters warned, median −57 h vs MetSul 2024-04-27 15Z | OK-c: a pre-L37 anchor that is not re-anchored. Label it "old anchor" or re-anchor it (Q4). |
| R5 | Data README "ML scope": "there is **no `t_ml.tif` and no T_ML this weekend**" | metrics `rs.xgb_model` "xgb_terrain_landcover", `rs.xgb_auc` 0.974 | **STALE / contradictory** (ML section, flagged for 06). |
| R6 | `metrics.json` itself: `rs.xgb_auc` 0.974, `rs.rf_auc` 0.922, `rs.rule_auc` 0.856, `rs.landslide_ml` | v1 T_ML values withdrawn by L32 (RS leak) | **STALE in data**: still published flat keys. The app must not render them (trace #3). Null them with a reason, or move them under a `v1_withdrawn` node. |
| R7 | `metrics.json` `wa.flood_warned.definition`, data README title "NISAR confirms", app README "radar confirms flood only" | "NISAR-**confirmed** new-flood area" | OK-c: wording conflicts with the "candidates" rule. Prefer "NISAR-mapped". |
| R8 | `summary.md` (alternative summary, not primary) | "NISAR GCOV passes … **confirm** flooded area through clouds and darkness" | OK-c / OPEN: only if used. Same "confirm" issue. |

---

## 10. Withdrawn and retired claims, and why

| Claim (as it was) | Where it came from | Why withdrawn | Decision | Still visible? |
|---|---|---|---|---|
| **"Tipping Point would have warned 85 h earlier"** (WA median 84.6 h, IQR 54.6–114.6, 6 of 8; RS 111 h, 1 of 4) | `*.forecast_lead_time` (old anchors: King County opening Dec 9 00:35Z; MetSul Apr 27 15Z). STATE.md:42 "in submission". | (1) **Any-pixel-in-AOI** rule, not at the damage. (2) Every WA "warning" came from slide cells while the WA damage was flood. (3) n = 8 and n = 4 issue days. (4) The anchor was not the onset. (5) No false-alarm rate. win-odds-claude #9 called it a "Validity liability". | L35 (WA median retired), L37, L38 | **Data README (R2)**. Not in judge-facing copy (trace-numbers: "85 h / 111 h … appear nowhere"). |
| **"88 % of the 16,027 mapped landslides were High/Critical 24 h before May 1, 2024"** (88.13 %, Critical 69.07 %; 48 h 0.27 %, 72 h 70.89 %) | `rs.slide_warned.sensitivity[fixed_dating_may1]` | May 1 is **after** the onset (first deaths Apr 29, landslide deaths Apr 30). The Apr 30 run already contained 3 days of observed storm IMERG (§2.2). It flips to 0 % with a 1-day anchor shift. | L36a, then on hold (L37), then retired (L38) | **Data README (R3)**. In copy it appears only as the sensitivity figure in the footnote and the table, which is correct. |
| **"when × where" score** (24 h PR-AUC 0.717 vs 0.574) and every T_ML-multiplied number (ROC 0.974 vs 0.856; raw 0.548 → 0.156) | ML v1 combine | **Leak**: RS 2024 inventory points trained T_ML (3a), and RS was also a 3b TEST event. | L32 | `metrics.json` flat keys (R6). Not in copy. |
| **v1 GFS ML numbers** (24 h xgb PR-AUC 0.752 vs RF 0.733 vs TPI rule 0.613; base 0.154; by-lead) | ML v1 | 40 % of RS same-point negatives had **no GFS run** (pre-2021 dates became NaN, which made them easy negatives). v2 re-scores only issue days with a run, and its headline is still **held** for an overfit audit (`ml_card_v2.headline_status`). | L33 | `data/ml/ml_card.json` (v1) still exists. MORNING.md:61 is internal and labelled. |
| **"% warned first"** (RS 2.9 % of n = 5,609 clusters crossed Critical before the anchor, as-available) | `rs.lead_time_asavail.warned_before_pct` 2.87 | Dropped by Lucas (REVIEW.md:75). The replacement sentence "the index marked the area as critical before the damage" was also retired (L36/L38). Official warnings came first. | Lucas 2026-11-15; L36; L38 | No |
| **"…flagged the area [X] hours before the damage" / "Flagged [X] hours before the damage"** | L18 wording template | Same reasons as the 85 h row | L35 → L36 → L38 | No |
| **"confirms"** as a claim about the flood map or landslides ("radar confirms where the ground actually moved or flooded") | Theme sentence (doc), summary.md, READMEs, tagline | NISAR map vs Sentinel-1 is F1 0.15. Landslides have no radar evidence at all (no GUNW pair spans WA; RS predates NISAR). Rules: floods are "candidates", landslides are "candidates, never confirmed". | AGENTS.md hard rule; demo-script honesty rules; win-odds | **Partly**. Removed from the project page claims. **Still in the tagline "Radar confirms" (OPEN, V5)**, in summary.md, both READMEs and the `flood_warned` definition (R7, R8). |
| **"Radar overlaps at F1 0.15 on the 7 % optical could see"** | earlier VO / slide 5 | Mislabelled the reference: it is DSWx-**S1** radar, not optical, and not limited to the 7 % | trace-numbers #1; L38 VO | No |
| **HAND "98 %"** | `wa.hand_validation` 0.975, ambiguous percent-vs-fraction, rendered as 98 % | Meaningless. The real IoU was ≈ 0.01 on mismatched grids. The G2 common-grid rebuild gives IoU 0.26 (WA) / 0.42 (RS) at 1000 m. | G2 (b4aacf9); SCHEMA | No |
| RS "critical 430.1 km²" | old-run clobber | Concurrent-merge clobbering; the true peak is 10,870.6 km² | test_metrics_consistency.py | No (never quoted, REVIEW.md:93) |
| RS facts "446 of 497 municipalities", "R$88.9 bn", "worst floods in state history" | early slide 2 | No single official source | L36d | Possibly in `.crew/deck/slides.pdf` p2 if not regenerated (S7) |
| "first damage in Canoas" (E7 anchor) | MetSul article 2024-04-27 | The article does not name Canoas as the first damage (`r-warnrs.md`) | superseded by L37 onset | `rs.lead_time_asavail.damage_anchor` still says "MetSul … Canoas" (R4) |

---

## 11. Open scientific questions for the reviewer

1. **Null baselines for every hit rate.**
   - What share of steep (≥ 15°) AOI terrain was Critical on 2024-05-02? If it is close to 86 %, the catalogue hit rate carries no information beyond "it rained everywhere on steep ground".
   - The same question applies to WA (n = 15) and to the 36 % forecast hit. What share of all steep cells was High+ in the Apr 28 run?
   - Proposed metric: lift or ROC of TPI at inventory points vs random steep points, per day.
2. **Is the RS 36 % skill or timing luck?**
   - Only one of three runs (Apr 28) shows it. The next run (Apr 29) shows 0 % at the onset day. The earliest run (Apr 23) "warned" 40 % for Apr 24–26, which did not verify.
   - Should we report a **run-ensemble** statistic (e.g. the mean over all runs issued 24–96 h before) instead of single runs, plus the false-alarm rate of the same rule on non-event windows?
3. **Is "first deaths (flood, Paverama)" the right onset for a landslide metric?**
   - The landslide-specific onset (Apr 30) flips the 24 h result to 0 %.
   - Should the anchor be hazard-specific, and should we score against a trigger **window** (Apr 29–May 2, per event-onsets.md) rather than a single instant, given that the inventory is undated?
4. **Keys not re-anchored to the onset.**
   - `rs.lead_time_*`, `rs.warned_before_pct`, `rs.lead_time_asavail` (MetSul 2024-04-27) and both `official_warning_delta_h` values still use older references.
   - `rs.official_warning_delta_h` = −51.3 h does not match `rs.first_critical_at` = 2024-05-02 (that would be ≈ −127 h from INMET Apr 26 16:50Z). Its reference instant is undocumented.
   - WA is self-consistent: NWS Dec 6 00:10Z vs first Critical Dec 9 00Z gives −71.8 h.
5. **Pixel-local flood R.**
   - L35 shows that upstream-basin rain fixes coverage during the event (41 → 79 % on Dec 10) but not the onset warning, and it costs +32 % false Critical.
   - Is the missing ingredient routing or travel time (GFMS-style), or antecedent snowmelt and rain-on-snow in the Cascades, which IMERG + SMAP root-zone do not capture?
6. **NISAR vs DSWx-S1 comparison grid.**
   - The keys do not state the grid. TP + FP = 46,863 px matches 27.99 km² only at ~24 m pixels.
   - Was the out-of-swath 26 % masked from FN?
   - Did the 1-day S1 offset (Dec 15/17 vs Dec 16) and C-band vs L-band penetration under vegetation drive most of the FN?
   - Would adding the bright-flood (double-bounce) layer (0.66 km²) or cross-pol change recall materially?
7. **IMERG under-reads the Serra.** Bento Gonçalves −16.7 % and Canela −12.8 %. Teutônia's +19 % daily bias vs 1.84 event ratio points to missing gauge days. Does the R score's per-pixel climatology (also IMERG) cancel this bias, as it should for a percentile-based R, or does it suppress the Serra signal?
8. **Cloud metrics.**
   - WA 92.9 % is measured on post-storm days (Dec 15/17). Is there any DSWx-HLS or HLS coverage for Dec 8–12 to support "clouds hid the event"?
   - RS uses an any-scene-clear union, so 100 % is a strong statement. What does `hls_observed_px` (535,467) count?
9. **Specificity of "Critical".** About 30–34 % of each AOI is Critical on the peak day. Is the class cut (0.6) too low for a response product, or should exposure numbers (1.49 M, 230 k people) be reported against a High/Critical area-to-damage ratio so that readers see the precision cost?
10. **Night-light drop.** No control area, no cloud or snow screen reported, and the split uses config event dates rather than onsets. Is a 33–44 % drop in a very-low-radiance rural mask distinguishable from noise or snow cover?
11. **HAND proxy at the served radius.** At 1500 m the IoU is 0.34 (RS) and 0.19 (WA), and the proxy marks less than half of MERIT's low-HAND area. Should the index switch to 1000 m (best IoU) or to MERIT HAND directly (it is not NASA, but is it allowed as an input?), given that T_flood ≈ 0.96 on the WA flooded pixels anyway?
12. **Inventory provenance.** The WA set is 15 WGS points out of 279–750 statewide reports. How were the 15 selected (AOI clip, geometry type, date field)? The WGS FeatureServer has an `event_date` field: could dated records allow a real point-level lead time for WA?

---

**Output:** `~/code/r2l2pj/.crew/science-review/05-validation-claims.md`

**Summary (5 lines)**
1. Onset anchors (L37: RS 2024-04-29 03Z first deaths; WA 2025-12-08 08Z) replaced peak anchors, because the May-1 "forecast" already held 3 days of observed storm rain. That retired 88.13 %, and the old AOI-wide 85 h / 111 h leads.
2. RS forecast-as-issued result: 36.45 % High+ / 1.87 % Critical of 16,027 points from one GFS run (Apr 28, 27 h ahead). The 48/72 h runs and the Apr 29 run give 0 %, observed gives 0.25 %. WA flooded area is 0/0/0.14 % because R is pixel-local, and L35 upstream rain did not fix it.
3. Validation during and after the event is decent but has no baselines: RS catalogue hit 85.9 % Critical (observed rain), NISAR vs DSWx-S1 F1 0.15, HAND IoU 0.42/0.26 (0.34/0.19 at the served radius), IMERG vs gauge r 0.89, bias +5.5 %.
4. The submission copy matches the keys. Needed fixes: the WA inventory is credited to NASA GLC/COOLR instead of WGS (app README, FORM §8); the public data README still states 111 h, 84.6 h and 88.1 %; `metrics.json` still carries the withdrawn v1 T_ML AUCs; the "Radar confirms" tagline is still open.
5. Key open questions: null baselines for every hit rate, run-to-run variance vs skill, a hazard-specific onset (Apr 30 flips RS to 0 %), the comparison grid and FN masking for NISAR vs S1, and whether "Critical" (30–34 % of each AOI) is specific enough to report exposure.
