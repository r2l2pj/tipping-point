# M1 terrain-only baseline and A3 elevation check (lead-ml, 2026-11-15; no formula change)

From the external review (`.crew/review/10-correcoes-revisao-externa.md`), items §M1 and A3.

| | |
|---|---|
| **Tools** | `tools/lift_vs_terrain.py`, `tools/a3_wa_elevation.py` |
| **Numbers** | `ml/models/lift_vs_terrain.json`, `ml/models/a3_wa_elevation.json` |
| **Tests** | `pipeline/tests/test_lift_vs_terrain.py` |

## M1: is the TPI better than terrain alone?

**The question.** The TPI multiplies rain (R), soil wetness (S) and terrain (T). A high lift could come from T alone. So each TPI lift is compared with a **terrain-only map that flags the same number of cells**: the cells with the highest T.

**The measure.** `ratio = TPI hit rate ÷ terrain-only hit rate`.

- A ratio above 1 means rain and soil improve on terrain alone for *where*.
- A ratio of 1 or below means terrain alone points just as well.

**Method.**

- The function is the review's §M1 function, with one fix: ties in T are averaged.
- T has flat stretches (many cells at exactly 1), so the cut-off cell is often tied with thousands of others. The §M1 stable argsort picks the tied cells in raster order (north first), which is arbitrary.
- `lift_terrain` therefore uses the expected hit under a random choice among tied cells. The raw §M1 value is kept as `lift_terrain_stable`.
- A reference cell with no TPI value counts as a miss, as in the published lift.

**Results.** High+ is TPI > 0.4 and Critical is TPI > 0.6.

| Row | Cut | Area flagged | TPI hit | Terrain-only hit (same area) | TPI lift | Terrain lift | **Ratio** |
|---|---|---|---|---|---|---|---|
| RS slide: GFS run ≥ 24 h before onset, 16,027 inventory points | High+ | 2.13 % | 5.8 % | 39.9 % | 2.71 | 18.7 | **0.15** |
| | Critical | 0.11 % | 0.18 % | 2.4 % | 1.60 | 20.9 | **0.08** |
| RS flood: GFS run ≥ 24 h before onset, vs EMS (5 AOIs) | both | 0 % | — | — | — | — | **undefined** (the run flags no cell) |
| RS flood: observed replay, max Apr 29–May 5, vs EMS | High+ | 4.3 % | 22.6 % | 22.6 % | 5.23 | 5.23 | **1.00** |
| | Critical | 2.9 % | 14.9 % | 14.9 % | 5.21 | 5.21 | **1.00** |
| WA flood: GFS run ≥ 24 h before onset, vs NISAR candidates | High+ | 0.16 % | 0 % | 3.4 % | 0 | 21.3 | **0** |
| | Critical | 0.01 % | 0 % | 0.2 % | 0 | 21.3 | **0** |
| WA flood: observed replay, max Dec 8–12, NISAR extent | High+ | 9.2 % | 98.8 % | 98.3 % | 10.8 | 10.7 | **1.01** |
| | Critical | 6.2 % | 73.6 % | 94.5 % | 11.8 | 15.2 | **0.78** |
| Uruguaiana corridor: observed replay Sep 30, 7 NISAR polygons (18 cells of 0.01°) | High+ | 30.1 % | 88.9 % | 32.9 % | 2.95 | 1.09 | **2.7** |
| | Critical | 12.3 % | 83.3 % | 13.4 % | 6.80 | 1.09 | **6.2** |

**Reading.**

- **RS landslides.** Terrain alone (slope 15–30°) finds the inventory points 7 to 13 times better than the TPI's forecast flag of the same size. The GFS rain picked the wrong part of the state for slides. The published 2.71 lift is real, but **it is not evidence that rain and soil help locate slides.**
- **RS and WA floods during the event.** Ratio ≈ 1. At the event peak, R and S saturate across the flooded areas, so the TPI's flag *is* the terrain's top cells. **"Where" comes from the HAND term.**
- **WA Critical, ratio 0.78.** Rain and soil move part of the Critical area away from the NISAR water.
- **Uruguaiana.** **The only case where rain and soil add to terrain** (ratio 2.7 / 6.2). The corridor's flat floodplain gives T = 1 on more cells than are flagged, and rain picks the right ones. The reference is small: 18 cells.
- **Forecast flood rows (24 h GFS run).** Undefined (RS) or 0 (WA). The forecast run flags almost no flood area.

## A3: is the WA Critical slide area high in the Cascades (snow)?

**Data.** NASADEM_HGT.001, six 1-arc-second tiles downloaded locally (no GEE), averaged onto the 90 m TPI grid. Area of TPI_slide > 0.6:

| Day | Critical km² | 0–500 m | 500–1,000 m | 1,000–1,500 m | > 1,500 m | **Share ≥ 1,000 m** | Median elevation |
|---|---|---|---|---|---|---|---|
| 2025-12-10 | 2,151 | 238 | 1,037 | 827 | 49 | **40.7 %** | 919 m |
| 2025-12-11 | 2,649 | 409 | 1,274 | 916 | 49 | 36.4 % | 876 m |
| 2025-12-12 | 2,709 | 442 | 1,296 | 923 | 49 | 35.9 % | 870 m |

**Cross-check.** The review's longitude bands reproduce: 18.9 / 758.0 / 1,373.7 km² against the review's 18.7 / 752.9 / 1,364.4.

**Verdict: A3 is not confirmed by the review's own rule (> 50 % above 1,000 m).**

- The "high Cascades" longitude band is mostly mid-slope valley terrain: **59 % of the Critical area sits below 1,000 m.**
- Snow may still matter for the 41 % above 1,000 m. A phase split (M2) is still worth testing.
- **"Most of the Critical area is snow-covered high terrain" should not be cited.**

## M4: class cuts calibrated on one case, evaluated on the other (v2 vs v3 candidate)

**Tool and data.**

- Tool: `tools/m4_cut_calibration.py`.
- Numbers: `ml/models/m4_cuts.json` (served COGs) and `ml/models/m4_cuts_v2_v3.json` (lead-analysis's `~/.cache/lead-analysis/v3/<case>/frames.npz`).
- **The v2 rebuild in frames.npz reproduces the served COG numbers exactly**, so v2 and v3 are compared on identical frames, grids and references.

**The v3 candidate** combines M2, M3, M5 and M6:

- **M2:** liquid-phase IMERG.
- **M3:** R grows past P99 on a log2 scale and caps at 4×P99, because the review's 2×P99 cap still saturated RS.
- **M5:** T_flood from HAND with a 1000 m radius; GSW 10–50 % wetland set to NaN.
- **M6:** the S ramp starts at 0.3 instead of 0.5.

**Rule for the Critical cut (§M4).** On the calibration case only, take the lowest cut (0.30–0.99, step 0.01) that flags at most 2 % of the domain **and** has M1 ratio > 1. Then apply that cut unchanged to the other case. Flood cuts are also applied to the Uruguaiana corridor.

**Frame sets.**

- **event:** the observed replay, max over the event window.
- **gfs24:** the GFS run issued ≥ 24 h before onset.

### Current cuts (High+ 0.4 / Critical 0.6), v2 → v3, Critical

| Case · frames | Area flagged | Truth hit | Lift | M1 ratio |
|---|---|---|---|---|
| RS slide · event | 10.7 % → 10.7 % | 85 % → 85 % | 7.9 → 8.0 | 1.00 → 1.00 |
| RS slide · gfs24 | 0.11 % → 0.14 % | 0.18 % → 0.12 % | 1.6 → 0.85 | 0.08 → 0.04 |
| RS flood (EMS) · event | 2.9 % → 4.3 % | 14.9 % → 21.1 % | 5.2 → 4.9 | 1.00 → 1.00 |
| WA slide (15 points) · event | 22.6 % → 21.8 % | 60 % → 60 % | 2.65 → 2.76 | 1.00 → 1.29 |
| WA slide · gfs24, **High+** | 3.8 % → 7.3 % | 0 → 20 % (3 of 15) | 0 → 2.7 | 0 → 1.75 |
| WA flood (NISAR) · event | 6.2 % → 9.0 % | 74 % → 96 % | 11.8 → 10.6 | 0.78 → 1.01 |
| Flood · gfs24 (RS and WA) | ≤ 0.1 % | 0 | — | — |

### Cross-case calibration result

**No split passes.** On the calibration case, no cut meets both rules, either for v2 or for v3, in any frame set.

- **The one exception is degenerate:** v3 flood gfs24 calibrated on RS gives cut 0.30, with 0.18 % area and ratio 1.02 from 1 % of EMS cells. On WA the same cut hits 0.
- **Area-only cuts (≤ 2 % area, ignoring the ratio) do not transfer.**
  - v3 slide event: RS→WA 0.97 gives a WA ratio of 3.25, but it catches only 2 of 15 slides. WA→RS 0.98 gives a ratio of 1.00.
  - v3 flood event: RS→WA 0.93 catches 0 % of the NISAR water. WA→RS 0.80 has ratio 1.00.
- **The R·S·T product scale gives the same ranking** (product cut = geometric-mean cut³, so 0.6 ↔ 0.216 and 0.97 ↔ 0.91). The conclusion is identical.

**Why.** Even with M3, the event-peak rain and soil terms are near 1 almost everywhere:

| | Domain-mean R | Domain-mean S |
|---|---|---|
| RS, May 2–3 | 0.95–0.98 | 0.98–0.99 |

So inside the flagged area, the TPI still ranks cells like T. Raising or lowering a cut cannot add information the formula does not carry.

**Reading for v3.**

- **Gains (flood):** v3 widens flood coverage in both cases (WA NISAR 74 % → 96 % at Critical, RS EMS 15 % → 21 %) for about 45–50 % more area. It moves the WA Critical ratio from 0.78 to 1.0.
- **Gains (WA slides):** v3 helps slightly (ratio 1.29 at the event; 3 of 15 slides at High+ on gfs24), but n = 15.
- **Loss:** v3 is worse for the RS slide forecast. On gfs24 at Critical, the lift drops from 1.6 to 0.85.
- **§M4 acceptance:** not met. A new class-cut table cannot be adopted on this evidence.
