# NISAR in the index (L61; lead-analysis, lead-ml, lead-nisar-wa, lead-nisar-uru; 2026-11-15)

**NISAR plays three roles: it *raises* the flood index, it *scores* it, and an experiment tests it for *soil*.** NISAR water is always a **candidate** (provisional GCOV, thresholded), never "confirmed". A cell with no pass does not mean zero risk.

## 1. NISAR water raises the flood index (served; lead-analysis)

| | |
|---|---|
| **Code** | `live/nisar_water.py`, `live/now_nisar_tpi.py` |
| **Now layer** | `tpi_flood_nisar_d0..d3` |
| **Replay layer** | `tpi_flood_nisar` |

**The water mask.**

- **Signal:** NISAR HH < −18 dB, outside permanent water.
- **Source:** decoded from the fixed −25..0 dB frames (one stretch for HH and HV, every case and Now).
- **Pass rule:** only passes from the 12 days up to the map day count, never a later pass. Where passes overlap, the most recent wins.

**The combined index.** `tpi_flood_nisar = max(TPI_flood, 0.8 × water)`.

- Observed water lifts a cell to Critical (0.8 > 0.6).
- Everywhere else the normal TPI stands.

## 2. nisar_agreement: does the TPI flag the ground NISAR sees under water?

**Reference.** The single-pass NISAR water mask above.

**Scored only on cells a pass observed.** The `observed` band or array is the denominator. Unobserved cells are not "dry".

**What is compared.**

| Frame set | Definition |
|---|---|
| **observed** | The flood TPI of the map day (pass day). |
| **lead_Ld** | The flood TPI forecast issued L = 1, 2, 3 days before the map day, valid on the map day. |

**Metrics.** For High+ (> 0.4) and Critical (> 0.6):

- `hit_pct`: share of NISAR-water cells the TPI flagged;
- `area_pct`: share of observed cells flagged;
- `lift` = hit / area;
- `ratio_vs_terrain` (cases only): the M1 ratio vs a terrain-only map of the same area. Above 1 means rain and soil add to terrain (`docs/lift-vs-terrain.md`).

Next to these: **`outside_floodplain_pct`**, the share of NISAR-water cells with HAND > 5 m. Rice paddies, irrigation and reservoirs look like radar water but are not floods.

### Who scores what

| Domain | Owner | Code | Output |
|---|---|---|---|
| WA 2025 | lead-nisar-wa | `tools/nisar_agreement.py` (from lead-ml's draft) | `metrics.json` `cases.wa_2025.nisar_agreement` + flat keys |
| Uruguaiana 2026 | lead-nisar-uru | `tools/nisar_pass_agreement.py` (same Scorer) | `metrics.json` `cases.rs_uruguai_2026.nisar_agreement` |
| **Now (live)** | **lead-ml** | `tools/now_nisar_agreement.py` (reuses `agreement_day`) | `data/now/nisar_agreement.json`, every Now run |

### The Now agreement

**When it runs.** A best-effort `now.yml` step right after "Now history".

**Inputs.**

- **Mask:** `nisar_tpi/water.npz` inside the job; otherwise the `water` / `observed` bands of `cog/tpi_flood_nisar_today.tif`.
- **TPI:** today's TPI from `cog/tpi_flood_today.tif`.
- **Forecast leads:** the per-run archive `history/<run>/cog/tpi_flood_d<k>.tif`, whose band description is the target date. For each lead L it uses the latest archived run issued on pass_date − L whose `d<L>` frame targets the pass day.

**Stated limits** (written into the JSON):

- **Pass age.** Today's TPI is compared with a pass up to 12 days old; `pass_age_days` is reported.
- **Leads.** They become non-null only for passes after the archive started (2026-11-15). A 3-day lead needs about 4 days of history (lead-ml asked for 32 kept runs).
- **No terrain ratio.** Now has no T_flood grid in its outputs, so there is no M1 ratio.
- **Pending.** With no usable pass or mask, the file says `pending`, with the reasons.

**Status at writing.** No Now pass had been scored yet. The previous Now pair (024D_101, Oct 25 → Nov 6) was rejected because its water threshold fell back to the clamp. The first live mask comes from the Now run after main `de3a094` / `a487850`.

## 3. The river's dance: NISAR water follows the river (Uruguaiana; Jhonatan, landed by lead-ml)

| | |
|---|---|
| **Code** | `tools/nisar_river_dance.py` |
| **Data** | `data/rs_uruguai_2026/series/river_dance.json` |
| **Images** | `app/docs/img/dance_strip.png` (9 passes), `dance_slide.png` (6), `dance_chart.png` |

**Setup.**

- **Passes:** 9 NISAR HH passes, Sep 18 – Nov 5 2026, from mixed tracks 010D and 082D.
- **Area:** a 30 × 30 km UTM 21S box at Uruguaiana.
- **Water:** HH < −15 dB. The per-scene Otsu threshold fell outside [−20, −13] dB on all 9 passes, so the −15 dB fallback was used every time.
- **New water:** water minus the persistent river and minus any pixel that is water in ≥ 7 of 9 passes. Counted inside a 3 km river corridor, on pixels valid in every pass, with a 3 ha minimum mapping unit.

**Result against the ANA Uruguaiana gauge (77150000), daily median stage on the pass day:**

| Subset | Pearson r | n |
|---|---|---|
| All passes | **0.918** | 9 |
| Track 010D only | 0.911 | 5 |
| Track 082D only | 0.954 | 4 |
| Floodplain only (HAND ≤ 5 m) | **0.966** | 9 |

**Fields and rice do not drive r.**

- Outside the HAND ≤ 5 m floodplain lies 46–52 % of the new water on passes above flood stage, and 61–85 % on low-water passes.
- Yet the floodplain-only new water tracks the gauge *better* (r = 0.966).

## 4. EXPERIMENTAL: NISAR SME2 in the soil term (not served, not published; lead-ml)

| | |
|---|---|
| **Code** | `tools/sme2_s_blend.py` |
| **Numbers** | `ml/models/sme2_s_blend.json` |
| **Layers** | staged in `~/.cache/lead-ml/l61/<case>/` only |

**The blend.**

- **Input:** NISAR L3 SME2 (provisional, ~0–5 cm, 200 m), averaged onto the case grid.
- **Quantile mapping:** SME2 is mapped onto the distribution of the SMAP-based S over the same swath that day. This removes SME2's absolute bias and keeps only its spatial ranking.
- **Blend:** `S_blend = 0.5·S_SMAP + 0.5·S_SME2-mapped` inside the swath (31–35 % of each case grid); S_SMAP elsewhere.
- **Index:** `TPI_flood_sme2 = cbrt(R·S_blend·T)`.

| Case · day | Spearman (SME2 vs SMAP S) | Mean abs. TPI change in swath | Critical cells v2 → blend |
|---|---|---|---|
| WA · Dec 4 | −0.54 | 0.000 | 0 → 0 |
| WA · Dec 16 | −0.43 | 0.000 | 0 → 0 |
| Uruguaiana · Sep 30 | 0.07 | 0.005 | 3,583 → 3,319 |
| Uruguaiana · Oct 12 | −0.34 | 0.000 | 0 → 0 |

**Honest note.**

- **SME2's pattern disagrees with SMAP's.** The rank correlation is negative on 3 of 4 days.
- **Where it could matter, it barely changes the index.** On 3 of 4 days R or T is zero, so the index doesn't move. On the one rainy day (Uruguaiana, Sep 30), the blend removes 7 % of the Critical cells.
- **No skill test was possible.** No NISAR water mask exists for those days, and SME2 is provisional, with only a minority of pixels flagged "recommended".
- **Decision: do not serve.** Revisit when SME2 V1 (non-provisional) and more passes exist.
