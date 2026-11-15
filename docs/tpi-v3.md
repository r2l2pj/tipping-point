# TPI v3 candidate: ablation, class cuts, NISAR weight calibration (lead-ml, 2026-11-15; nothing served)

From the external review (`.crew/review/10-correcoes-revisao-externa.md`), items M2–M6, plus option 1, where NISAR calibrates the flood weights. **Nothing here is served.** The index stays v2 (uniform weights, cuts 0.4 / 0.6).

| | |
|---|---|
| **Inputs** | lead-analysis `~/.cache/lead-analysis/v3/<case>/{frames,components}.npz` (tool `tools/tpi_v3_frames.py`) |
| **Scoring tools** | `tools/tpi_v3_ablation.py`, `tools/m4_cut_calibration.py`, `tools/calibrate_weights_nisar.py` (Jhonata Vicente de Oliveira), `tools/nisar_weight_calibration.py` |
| **Numbers** | `ml/models/{tpi_v3_ablation,m4_cuts_v2_v3,tpi_weights_nisar,nisar_weight_calibration}.json` |

## Metrics

- **High+** = TPI > 0.4; **Critical** = TPI > 0.6.
- **Hit** = share of the truth flagged.
- **Area** = share of the domain flagged.
- **Lift** = hit ÷ area.
- **M1 ratio** = TPI hit ÷ the hit of a terrain-only map flagging the same area (see `docs/lift-vs-terrain.md`). Above 1 means rain and soil add to terrain.
- **event** = the observed replay, max over the event window. **gfs24** = the GFS run issued ≥ 24 h before onset.
- **AP / AUC** (in the weight calibration) score how well the index *ranks* cells, with no cut involved.

**Truth per case.**

| Case | Truth | Domain |
|---|---|---|
| RS 2024 flood | Copernicus EMS flood extent | 5 Taquari AOIs |
| WA 2025 flood | NISAR flood candidates | NISAR raster extent |
| Uruguaiana | 7 NISAR new-water polygons (17–18 cells of 0.01°) | the corridor box −57.25…−56.45, −29.85…−29.05 |
| RS slide | 16,027 inventory points | case grid |
| WA slide | 15 mapped slides | case grid |

The Uruguaiana corridor box is small; it is used here as the NISAR reference area.

**Reproduction.**

- Composing v2 from the components reproduces the served COGs exactly (max error 0.0002, 0 NaN mismatches).
- v3 is reproduced within 0.0002, with 249–308 NaN-mismatch cells out of 4.6 M in RS (wetland edges). Uruguaiana v2 and v3 are reproduced exactly.

## 1. Ablation: one change at a time vs v2 (Critical: area / hit / lift / M1 ratio)

The changes:

- **M2:** liquid-phase IMERG plus a liquid climatology.
- **M3:** R grows past P99 on a log2 scale and caps at 4×P99.
- **M5:** HAND radius 1000 m and GSW 10–50 % wetland set to NaN (flood only).
- **M6:** the S ramp starts at 0.3.

| Case · frames | v2 | M2 | M3 | M5 | M6 | v3 (all) |
|---|---|---|---|---|---|---|
| RS flood · event | 2.9 / 14.9 / 5.2 / 1.00 | = v2 | = v2 | **4.3 / 21.1 / 4.9 / 1.00** | = v2 | 4.3 / 21.1 / 4.9 / 1.00 |
| WA flood · event | 6.2 / 73.6 / 11.8 / 0.78 | 6.3 / 73.9 / 11.8 / 0.78 | **4.6 / 45.0 / 9.7 / 0.51** | 8.0 / 72.6 / 9.0 / 0.77 | **7.9 / 98.1 / 12.5 / 1.01** | 9.0 / 96.0 / 10.6 / 1.01 |
| RS slide · event | 10.8 / 85.2 / 7.9 / 1.00 | = v2 | 10.7 / 85.1 / 8.0 / 1.00 | = v2 | = v2 | 10.7 / 85.1 / 8.0 / 1.00 |
| RS slide · gfs24 | 0.11 / 0.18 / 1.6 / 0.08 | = v2 | **0.02 / 0.01 / 0.7 / 0.03** | = v2 | 0.36 / 0.83 / 2.3 / 0.11 | 0.14 / 0.12 / 0.85 / 0.04 |
| WA slide (n = 15) · event | 22.6 / 60 / 2.65 / 1.00 | = v2 | 21.4 / 60 / 2.8 / 1.29 | = v2 | 22.9 / 60 / 2.6 / 1.00 | 21.8 / 60 / 2.8 / 1.29 |
| WA slide · gfs24, **High+** | 3.8 / 0 / 0 / 0 | **7.3 / 20 / 2.7 / 1.75** | 3.2 / 0 / 0 / 0 | = v2 | 4.4 / 6.7 / 1.5 / 0.98 | 7.3 / 20 / 2.7 / 1.75 |
| Uruguaiana flood · Sep 30 (18 cells) | 12.3 / 83 / 6.8 / 6.2 | = v2 (no M2 there) | 8.4 / 72 / 8.6 / 7.8 | **7.2 / 61 / 8.4 / 9.6** | 15.6 / 89 / 5.7 / 5.2 | 7.7 / 61 / 7.9 / 9.0 |
| Flood · gfs24 (RS, WA) | ≤ 0.01 % area, 0 hit | same | same | same | same | same |

"= v2" means the change to v2 is smaller than 0.1 percentage point.

**Reading.**

- **Every v3 flood gain comes from M5 (RS) and M6 (WA).**
  - M6 lifts WA Critical coverage of the NISAR water from 74 % to 98 % for +27 % area, and moves the ratio from 0.78 to 1.01.
  - M5 widens RS coverage, with the ratio unchanged at 1.00.
- **M3 hurts.** It halves WA Critical coverage (74 % → 45 %, ratio 0.51) and the RS slide forecast (Critical lift 1.6 → 0.7).
- **M2 does nothing to floods.** For WA slides it gives the only forecast hits: 3 of 15 slides at High+ with ratio 1.75. That is 3 points.
- **At the event peak, the RS ratio stays 1.00 under every change.** R and S saturate, so the ranking is terrain's.
- **Uruguaiana: M5's wetland mask hides real new water.** 5 of the 18 NISAR polygon cells lie on GSW 10–50 % wetland. M5 and v3 set those to NaN, so Critical coverage drops from 83 % to 61 %. The ratio rises (6.2 → 9.6) only because the flagged area shrinks. M6 widens coverage (89 %) for more area; M3 shrinks both. With 18 cells, one cell is 5.6 points.

## 2. M4: class cuts calibrated on one case, evaluated on the other, per variant

**Rule.** On the calibration case, take the lowest cut that flags ≤ 2 % of the domain **and** has M1 ratio > 1. Apply that cut unchanged to the other case, both directions (RS⇄WA), for both hazards and both frame sets.

| Variant | Splits that pass on the calibration case | Same cut on the held-out case |
|---|---|---|
| v2, M5, M6 | none (M5: flood gfs24 cut 0.32 on 0.06 % of EMS, degenerate) | — / WA hit 0 |
| M2 | slide gfs24, calibrated on WA (n = 15): cut 0.58 | RS ratio 0.13 (fails) |
| M3 | flood event, calibrated on RS: cut 0.84 (ratio 1.001) | WA hit 1.5 %, ratio 0.13 (fails) |
| v3 | flood gfs24, calibrated on RS: cut 0.30 (1 % of EMS) | WA hit 0 (fails) |

Flood cuts applied to Uruguaiana: the area-only cuts from RS or WA catch 0–28 % of the polygon cells for every variant. The exception is M6 with the RS cut (0.74): 72 % at 6.7 % area. That cut did not meet the ratio rule on RS.

**M4 is not met for any variant: no calibrated cut transfers.** Keep the cuts 0.4 / 0.6.

## 3. NISAR calibrates the flood weights (option 1)

Jhonata's `tools/calibrate_weights_nisar.py` is the method of record.

- **Index:** TPI_flood = R^wR · S^wS · T^wT, with weights summing to 1, a 0.05 step and each weight ≥ 0.1 (120 triples plus uniform).
- **Frames:** max over the event days.
- **Scores:** threshold-free AP / AUC on the domain cells.
- **Out-of-sample design:** leave one NISAR case out (WA ⇄ Uruguaiana), plus RS-EMS as an independent check.

**Serve rule:** beat uniform on both NISAR cases **and** win out of sample both ways **and** keep RS-EMS AP within 5 %.

| Weights (R, S, T) | WA AP / AUC | Uruguaiana AP / AUC | RS-EMS AP / AUC |
|---|---|---|---|
| uniform (⅓, ⅓, ⅓), v2 | 0.039 / 0.947 | 0.010 / 0.828 | 0.301 / 0.622 |
| terrain only | **0.073 / 0.966** | 0.003 / 0.583 (≈ chance) | 0.301 / 0.622 |
| fit on WA → (0.1, 0.1, 0.8) | 0.064 / 0.964 (in-sample) | **0.009 / 0.754 (held out: ×0.90, worse)** | 0.301 / 0.622 |
| fit on Uruguaiana → (0.3, 0.1, 0.6) | **0.061 / 0.964 (held out: ×1.58)** | 0.031 / 0.881 (in-sample) | 0.301 / 0.622 |
| best pooled = (0.3, 0.1, 0.6) | 0.061 / 0.964 | 0.031 / 0.881 | 0.301 / 0.622 |

**Recommendation: `serve: false`, keep uniform.**

| Check | Result |
|---|---|
| beats uniform on both NISAR cases | true |
| wins out of sample both ways | **false**: the WA fit loses on Uruguaiana |
| keeps RS-EMS AP within 5 % | true |

Class level at the fixed cuts (Critical area / hit / lift / M1 ratio):

| | WA | Uruguaiana | RS-EMS |
|---|---|---|---|
| uniform | 5.9 / 68 / 11.6 / 0.74 | 38.2 / 88 / 2.3 / 2.0 | 2.9 / 16.7 / 5.9 / 1.00 |
| (0.3, 0.1, 0.6) | 6.8 / 95 / 14.0 / 1.01 | 45.6 / 88 / 1.9 / 1.67 | 1.9 / 11.1 / 5.8 / 1.00 |

**Reading.**

- **The only out-of-sample win is fit on Uruguaiana, tested on WA.** That fit rests on 17 reference cells. A T-heavy triple (0.3, 0.1, 0.6) helps WA mostly by **moving toward terrain**, and on WA terrain alone ranks the NISAR water best of all (AP 0.073).
- **The opposite direction fails.** In Uruguaiana, rain carries the signal and terrain alone is at chance.
- **The two NISAR cases want different things,** so no single weight triple is supported.
- **RS-EMS AP is identical for every triple.** At the RS peak, R and S are saturated, so any weights give the same ranking.

**Supplementary check (`tools/nisar_weight_calibration.py`, lead-ml).**

- **Setup:** the same idea with fixed-cut fitting (argmax Critical hit % − area %), weights summing to 3 on a 0.25 grid (91 triples, including zeros), and WA on its NISAR pair window Dec 4–16. The Uruguaiana darkening change map is added as a second truth.
- **Result:** fixed-cut fits pick rain-heavy weights (wR = 2.5). They "beat v2" on held-out cases mainly because, with R ≈ 1, they lower the effective Critical threshold to "any T > 0". So they enlarge the Critical class rather than rank better: RS Critical becomes exactly v2's High+, with ratio 1.00.
- **Darkening truth:** a fit on the Uruguaiana darkening map (Oct 24 → Nov 5, an ungated change map) loses badly on WA (Critical hit 70 % → 16 %).
- **v3 R** (liquid + extended; Uruguaiana: extended only), with uniform weights:
  - WA Critical hit 74 % → 45 % (ratio 0.51).
  - RS unchanged.
  - Uruguaiana Sep 30 – Oct 12: area 38 % → 27 %, hit 83 % → 72 %, ratio 2.0 → 2.5.
  - Fixed-cut fits with v3 R (WA → (2.5, 0, 0.5); Uruguaiana → (2.25, 0, 0.75)) again "beat v2" on the held-out cases by shifting the effective threshold. They drop S entirely (wS = 0).
- **Conclusion:** this confirms the threshold-free verdict. No weight triple should be served.

## Verdict

- **Keep v2 weights and cuts.**
  - None of M2–M6 alone or together, no recalibrated cut (M4) and no NISAR-fitted weight triple passes its out-of-sample check.
  - Most apparent gains move the index toward terrain or enlarge the flagged area.
- **Post-submission candidates worth more data:**
  - **M6 (S ramp from 0.3):** the WA flood gain with ratio > 1.
  - **M5 (HAND 1000):** RS flood coverage.
  - Both need more flood events scored against independent maps before they are served.
