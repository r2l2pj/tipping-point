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
