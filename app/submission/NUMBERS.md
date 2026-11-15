# NUMBERS — every spoken or written number and its key

Internal sheet (external review, `docs/science-review/10-correcoes-revisao-externa.md`, C1). Every number we say or write — project page, FORM, slides, deck, READMEs, video captions and overlays, live-demo script, gallery blurb — has a row here with the key it comes from. Keys are in `data/metrics.json` under `cases.<case>.` unless the row names another file. Values checked against `origin/main` debfab2 (2026-11-15). A number with no key does not ship.

Where: **P** = `project-page.md` (FORM fields 2 and 5 paste from it), **S** = `slides-outline.md` (slide n), **K** = deck (`.crew/deck/deck.html` → `slides.pdf`), **R** = root `README.md`, **A** = `app/README.md`, **V** = video v7 (`captions-v7.srt`, `captions-v7.pt-BR.srt`, overlays in `.crew/video/build-v7.sh`), **L** = live demo (`.crew/DEMO-1600.md`), **B** = `.crew/gallery/blurb.md`.

## Uruguaiana, Uruguay River, Oct 2026 (main case, `rs_uruguai_2026`)

| Number as said / written | Key (value) | Where |
|---|---|---|
| Region turned Critical on **Sep 30** | `critical_km2_date` (2026-09-30) | P summary, S3, K, V, L, B |
| **13,129 km²** Critical on Sep 30 | `critical_km2` (13128.97) | P table, S3 |
| **6,121 km²** of it on the low floodplain (HAND ≤ 5 m) | `tpi_replay_robustness.unscaled_floodplain_critical_km2.2026-09-30` (6121) | P table, S3 |
| Flood stage **Oct 5** (8.50 m), above it **Oct 5–21** and **Oct 29–31**, crest **10.02 m** on **Oct 16** | `forecast_proof.truth.flood_stage_m` (8.5), `forecast_proof.truth.uruguaiana_77150000.crossings` ([2026-10-05, 2026-10-21], [2026-10-29, 2026-10-31]), `….crest` ([2026-10-16, 10.02]) | P, S2, K, L |
| **Five days** before flood stage | Oct 5 − `critical_km2_date`; same value as `forecast_proof_uruguaiana_p99` (5) | P, S3, K, V, L, B |
| NOAA forecast from **Sep 27**, **8 days** ahead | `tpi_flood_corridor_first_critical_gfs.issue` (2026-09-27; frame 2026-09-29) → Oct 5 | P, S3, K, V, L, B |
| Near-real-time rain, scaled to the final product (**÷1.34**): floodplain still Critical Sep 30, **3,541 km²** | `tpi_replay_robustness.early_to_final_scaling`, `….early_to_final_scaled_floodplain_critical_km2.2026-09-30` (3541) | P, S3, K, L |
| **15** Critical days Sep 15 → Oct 31; **3** small false alarms **Sep 20–22** (**182 / 256 / 127 km²**) | `tpi_replay_robustness.specificity.critical_days_count` (15), `….critical_days_without_flood_stage_within_10d` (3), `….false_alarm_days` | P, S3, K, L, B |
| Critical area only **1.34×** concentrated on the floodplain ("right days, not yet the right streets") | `tpi_replay_robustness.specificity.lift` (1.34) | P, S3, K, L |
| 6-hourly: first > 1,000 km² Critical **Sep 29, 03:00 BRT**; frames share **66 of 72** rain hours | `tpi_replay_6h.first_critical_over_1000_km2` (2026-09-29T06:00Z), `tpi_replay_6h.note` | P table, R |
| **54%** of the NISAR flood candidates on the floodplain, which is **28%** of the land (**1.92×**) | `forecast_proof.where.share_of_flooded_candidate_area_flagged` (0.538), `….flagged_share_of_aoi` (0.281), `….lift_vs_random` (1.92) | P, S3, K, L |
| About **98,000** people on the low floodplain | `forecast_proof.where.people_in_flagged_corridor_gpw2020` (97755) | P, S3, K, V, L, B |
| Teammate's NISAR polygons: **7**, **1.8 km²**, about **28** people | `flood_source_note` (7 polygons), `flooded_km2` (1.817), `people_flooded` (28) | P, S3, S4, K |
| Our stricter check: no gated flood area; darkening **1.76 km²** along the river, **15.7 km²** over fields | `nisar_reproduction`, `radar_darkening_corridor_km2` (1.762), `radar_darkening_fields_km2` (15.732) | P table |
| NISAR every **12 days**, track **010D**: Sep 18 / Sep 30 / Oct 12 / Oct 24 / Nov 5 | `nisar_dates`, `nisar_pair.track` | P, S4, K, L |
| Swipe **Sep 30 → Oct 12** | `nisar_headline_pair` | S2, S4, L |
| Case box lon −58.0..−55.3, lat −30.6..−28.2 | `tpi_replay_robustness.aoi` | P table |

## Washington, Dec 2025 (`wa_2025`)

| Number | Key (value) | Where |
|---|---|---|
| **27.99 km²** (spoken "28 km²") NISAR flood candidates | `flooded_km2` (27.987) | P, S5, K, L, B |
| Post pass covers **74%** of the area | `swath_coverage_pct` (74.1) | P table |
| Clouds hid **93%** (92.9%); optical saw **7%** | `cloud_pct` (92.89); 7% = 100 − `cloud_pct` | P, S5, K, L, B |
| F1 **0.15** · precision **0.19** · recall **0.13** (13%) · IoU **0.08** | `f1` (0.151), `precision` (0.187), `recall` (0.126), `iou` (0.082) | P, A |
| About **950** people inside the candidates | `people_flooded` (949) | P, S5, S6, K |
| **225,583** people in Critical areas, Dec 12 | `people_critical` (225583), `critical_km2_date` (2025-12-12) | P, S6, K |
| Night-light drop **44%** | `night_light_drop_pct` (43.92) | P, S6, K |
| **66.7%** of **15** inventory points Critical on the peak day | `landslide_hit_pct` (66.667), `scar_inventory_n` (15), `landslide_hit_date` | P, R |
| WA flood **0%** at 24 / 48 / 72 h | `flood_warned_gfs_{24,48,72}h_pct` (0 / 0 / 0) | P, S5, L |
| Onset **Dec 8, 2025 00:00 PST** | `forecast_lead_time_onset.anchor_local` | P |
| GFS run issued **Dec 3** → Critical landslide risk **Dec 6** | `forecast_lead_time_onset.earliest_critical_issue` (2025-12-03); frame `tpi_slide_gfs_d3_20251206` | P, S5, K |
| Alarms **20.3%** (**14 of 69**) vs **10.0%** → **2.0×** | `alarm_precision_critical_pct` (20.29), `_n_hits` (14), `_n_alarms` (69), `alarm_baseline_pct` (10.0), `alarm_lift` (2.03) | P |
| Scar candidates **176**, **1.25 km²**, none within 500 m of the 15 points | `scar_candidates_n` (176), `scar_candidates_km2` (1.246), `scar_hit_500m_pct` (0.0) | R |
| 3-pass series **−9.7 → −9.7 → −16.2 dB** (drop ~**6.5 dB**); controls within **2.9 dB** | `data/wa_2025/nisar_timeseries.json` → `areas.flood_candidates.hh_db` (−9.718, −9.676, −16.197), `areas.control_*` | P, S5, K |
| Double bounce: **60,936** missed pixels, **45%** urban/forest vs **5%** caught (**8.6×**); **−2.72 / −0.66 / −0.18 dB**; recall **24.7% / 3.4% / 1.3%** | `data/wa_2025/nisar_hypothesis.json` → `missed_composition._total_n`, `enrichment.*_share` (0.448 / 0.052), `enrichment.enrichment_missed_vs_detected` (8.56), `delta_db.{open,urban,forest}.flooded.hh.mean_db`, `recall_by_group.*.recall` | P, S5, K |

## Rio Grande do Sul, 2024 (`rs_2024`, served replay v2)

| Number | Key (value) | Where |
|---|---|---|
| Peak **May 3, 2024**: **9,175 km²** Critical, **1,627,862** people (deck: **1.63 million**) | `critical_km2_date` (2024-05-03), `critical_km2` (9174.578), `people_critical` (1627862) | P table, S6, K, A |
| **5.8%** of **16,027** landslides High+ in the run issued **27 h** before the first deaths | `slide_warned_gfs_24h_pct` (5.77), `slide_warned_n` (16027), `slide_warned_gfs_24h_issue` (2024-04-28 00Z) vs `slide_warned_anchor_utc` (2024-04-29T03:00Z) | P, S5, K, R, A, L |
| That run flagged **2.1%** of the area → **2.7×** | `slide_warned_gfs_24h_area_share_pct` (2.13), `slide_warned_gfs_24h_lift` (2.71) | P, S5, K, R, A, L |
| Critical: **0.18%** of slides in **0.11%** of the area → **1.6×** | `slide_warned_gfs_24h_critical_pct` (0.18), `…_critical_area_share_pct` (0.11), `…_critical_lift` (1.64) | P, R, A |
| Runs issued **51 h** and **75 h** before: **0%** | `slide_warned_gfs_{48,72}h_pct` (0, 0), `…_{48,72}h_issue` (Apr 27, Apr 26) | P, S5, K, R, A |
| Observed band 24 h before onset: **0.59%** | `slide_warned_observed_24h_pct` (0.59) | R |
| Onset = first deaths, **Apr 29, 2024 00:00 BRT** (MetSul) | `slide_warned.anchor_local`, `slide_warned_anchor_source` | P, R, A |
| v1 number **36%**, IMERG Early **1.34×** wetter (retired, sensitivity only) | `docs/replay-v2.md` (as-available sensitivity) | P, R, A |
| EU emergency maps flood: IoU **0.20** · F1 **0.33** · precision **0.75** · recall **0.21**; **32 km²** mapped | `flood_ems_iou` (0.1967), `flood_ems_f1` (0.3288), `flood_ems_precision` (0.7478), `flood_ems_recall` (0.2107), `flood_ems_flooded_km2` (32.04) | P, S5, K, A |
| Before onset: **0%** of that flood flagged | `flood_ems_flagged_before_onset_pct` (0.0) | P |
| EU emergency maps slides: **0%** of **497** | `landslide_ems_warned_gfs_24h_pct` (0.0), `landslide_ems_n` (497) | P, A |
| Scar candidates **2,849**, **20.0 km²**; **17.5%** of inventory within 100 m | `scar_candidates_n` (2849), `scar_candidates_km2` (20.02), `scar_hit_100m_pct` (17.45) | P, A |
| Scar candidates hit **3.3×** more mapped slides than chance at **100 m**; only **1.1×** at 500 m | `scar_ems_lift_100m` (3.28), `scar_ems_hit_100m_pct` (27.36), `scar_ems_lift_500m` (1.11) | P, A |
| Alarms **34.8%** (**330 of 948**) vs **24.9%** → **1.4×** | `alarm_precision_critical_pct` (34.81), `_n_hits` (330), `_n_alarms` (948), `alarm_baseline_pct` (24.93), `alarm_lift` (1.4) | P |
| Night-light drop **33%** | `night_light_drop_pct` (33.08) | P, S6, K |
| Optical **100%** cloud, **20** HLS scenes | `cloud_pct` (100.0), `hls_scenes` (20) | P |
| HAND ≤ **15 m** vs floods ~**16 m** above the river; ROC **0.61 / 0.85**, WA **0.93 → 0.83** | `docs/t-flood-fix-eval.md` (not in metrics.json) | R, A |

## Pending (L61, NISAR agreement — fill ~21:00, then grep for `[TBD` = 0)

| Slot | Meaning | Key (to confirm) | Used in |
|---|---|---|---|
| `[TBD uy_high]` | % of NISAR water pixels (Uruguaiana pass) at High+ in the index before the pass | `[TBD key]` | R A P S K |
| `[TBD uy_crit]` | same, Critical | `[TBD key]` | R A P |
| `[TBD uy_area]` | % of the land the pre-pass index flagged High+ (chance) | `[TBD key]` | R A P |
| `[TBD uy_lift]` | uy_high ÷ uy_area | `[TBD key]` | R A P S K |
| `[TBD uy_pass]` | the NISAR pass scored (date) | `[TBD key]` | R A S |
| `[TBD uy_lead]` | forecast lead used for the headline (e.g. "1 day") | `[TBD key]` | R A P S K |
| `[TBD uy_m1]` | uy lift ÷ terrain-only lift (M1 ratio) | `[TBD key]` | R A |
| `[TBD uy_outside_fp]` | % of NISAR water off the low floodplain (HAND > 5 m) — rice caveat | `nisar_outside_floodplain_pct` (per layer) | R A P S |
| `[TBD wa_high]` / `[TBD wa_lift]` | Washington, same | `[TBD key]` | R A P S K |
| `[TBD now_status]` | Now: latest NISAR pass used, or "no water seen yet" | `now/summary.json` `[TBD key]` | P |
| block | `nisar_agreement` in metrics.json (lead-analysis, ~21:00); sub-keys filled by hand per row | — | R A |

### L61 'What we learned about NISAR' (verified by lead-analysis)

| Number | Key / source |
|---|---|
| WA Otsu never fired, fixed −15 dB | `cases.wa_2025.threshold_db` = −15.0 |
| UY pairs at the −15 dB clamp: 010D Sep 30→Oct 12, Oct 24→Nov 5; 082D Sep 23→Oct 5/17/29 | `cases.rs_uruguai_2026.nisar_reproduction`, `.nisar_pairs[0]`, `.nisar_082d` |
| clamped mask covers 99.3% of the 7 teammate polygons (1.82 km²), IoU 0.014 | `cases.rs_uruguai_2026.nisar_reproduction` |
| 202 of 212 km² field darkening, Oct 5 (082D) | `cases.rs_uruguai_2026.nisar_082d` |
| 1.76 / 15.7 km² ungated darkening, Oct 24→Nov 5 | `cases.rs_uruguai_2026.radar_darkening_corridor_km2` 1.762, `radar_darkening_fields_km2` 15.732, `radar_darkening_pair` (NOT the png note's 5.62/11.88, older definition) |
| 45% vs 5%, 8.6× (8.562) | `data/wa_2025/nisar_hypothesis.json` result_paragraph |
| SME2 0.23→0.25→0.23 vs SMAP 0.24→0.32→0.41; 68–75% recommended | notes of `nisar_sme2_<date>_010D` in `data/rs_uruguai_2026/png/_index.json` |

## Other numbers (sources outside metrics.json)

| Number | Source | Where |
|---|---|---|
| Terrain baseline: terrain alone finds RS slides **7–13×** better than the 24 h GFS flag; terrain-only lift **18.7×** vs **2.71×**; ratio **0.15** High+ / **0.08** Critical; floods ratio **≈1** (RS **1.00**, WA **1.01**); Uruguaiana ratio **2.7** High+ / **6.2** Critical, **18** cells | `ml/models/lift_vs_terrain.json` → `rs_inventory_slide.{alto_plus,critico}.ratio` (0.145 / 0.076), `rs_ems_flood_event.*.ratio` (1.0), `wa_nisar_flood_event.alto_plus.ratio` (1.006), `uru_nisar_flood_sep30.{alto_plus,critico}.ratio` (2.7 / 6.221); `docs/lift-vs-terrain.md` | P, S5, S6, K, R, A, L |
| WA Critical slide area **about 40%** above 1,000 m on Dec 10, median **919 m** (never "most") | `ml/models/a3_wa_elevation.json` → `days.20251210.critico.above_1000m_pct` (40.7), `….median_elev_m` (919) | P, S6, K, R, A |
| ML: ROC **0.93** median, **9** held-out disasters, ~**4–5** alarm-days per site per year; **11** disaster events | `docs/science-review/07-overfit-audit.md` | P, S3, S6, K, R, A |
| Peak saturation: High above ~**16°**, Critical above ~**18°**; P**99** | review 10 §A1 (`docs/science-review/10-correcoes-revisao-externa.md`) | P, S6, R, A |
| Live first NISAR pair (Oct 25 → Nov 6, 2026) rejected: strip **2.35 km²**, HH **−5.6 / −5.1 dB** | `data/now/response/nisar_timeseries.json`; `docs/science-review/08-updates-2026-11-15-midday.md` | P table |
| RS 2024 context: **478** municipalities, **185** deaths, **581,638** displaced | Government of Rio Grande do Sul, SOS Rio Grande do Sul (updated Aug 19, 2025) | S2, K |
| Red alerts Itaqui / Uruguaiana **Jul 26–29, 2026** | Defesa Civil RS | S2, K |
| GFS under-forecast the RS 2024 peak by about half: 3-day **141 mm** forecast vs **290 mm** observed (AOI mean ending 2024-05-02) | `data/rs_2024/forecast_rain/*.json` vs the IMERG series (README Declared limitations, lead-analysis) | R |
| Physical constants: L-band **24 cm**, C-band **5.6 cm**, revisit **12 days**, NISAR soil moisture ~**200 m**, SMAP ~**9 km**, IMERG/SMAP ~**10 km**, GFS **0.25°**, live refresh **3 h**, MMU **3 ha**, **3 dB** drop, **−15 dB** fallback | NISAR Science Users' Handbook; product specs; `config.yaml` | P, S, K, A |

## Retired — never say

36% / 8.8× (v1 RS forecast) as a headline · "46% of landslides within 500 m" · "175 km² Critical three days before" · "55,000 people exposed" · "three days before the peak" · 1,681,783 / 254,263 / 1,488,833 / 229,782 people · 1.85 km² (on-grid area; use 1.8 km² = `flooded_km2`) · "GFS under-forecast the RS peak by about half" on slides/deck (the README keeps it with its source numbers, row above) · "people are lower bounds (~12% low)" (old resample; counts are now native-grid) · "most of the WA Critical area is high/snow terrain" (A3 not confirmed: about 40%) · the 2.7× RS lift as evidence that rain locates slides.

## Grep proof (run after any copy change)

```sh
cd ~/code/r2l2pj/tipping-point-data
grep -rnE "46 ?%|(^|[^,0-9])175 km|55[ ,.]?000|55 mil|three days before the peak|36\.45|8\.8×|1,681,783|254,263|1,488,833|229,782|1\.85 km|about half|12% low|Radar confirms" \
  README.md app/README.md app/submission/{project-page,FORM,slides-outline,demo-script,storyboard}.md app/submission/captions-v7*.srt \
  ../.crew/{DEMO-1600.md,gallery/blurb.md,deck/deck.html,video/build-v7.sh}
# expected: only the disclosed v1 sensitivity (36% / 36.45 / 8.8×) in P / R / A the README's sourced "about half", and demo-script's note that "Radar confirms" is retired
```
