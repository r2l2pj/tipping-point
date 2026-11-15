# SMAP soil-wetness downscaling test — negative result (lead-ml, 2026-11-15)

**Question.** Inside each 9 km cell, the SMAP L4 root-zone wetness S can be redistributed by HAND, wetter where the ground is low, while keeping the cell mean. Does that locate flooded ground better than plain bilinear S?

**Answer: no. Downscaling is not adopted.**

- Tool: `tools/smap_downscale_test.py`.
- Numbers: `ml/models/smap_downscale_test.json`.
- Reference: Copernicus EMS EMSR720 flood extent in the 5 Taquari-valley AOIs (images 2024-05-04..15).
- Grid: 90 m HAND, 34,389 pixels, 14 % of them flooded.
- SMAP dates: 2024-05-01 and 2024-05-03.

| score (ROC-AUC / PR-AUC), May 3 | value |
|---|---|
| −HAND alone | **0.925 / 0.48** |
| HAND-only ramp `T_hand30 = clip(1 − HAND/30 m)` | 0.917 / 0.48 |
| bilinear S × T_hand30 | 0.917 / 0.47 |
| block (nearest-cell) S × T_hand30 | 0.917 / 0.48 |
| **HAND-downscaled S × T_hand30** | **0.916 / 0.47** (no gain) |
| bilinear S alone | 0.36 / 0.12 (worse than random) |
| HAND-downscaled S alone | 0.93 / 0.53 (this is HAND's own skill, carried into S) |

Why it fails:

1. **SMAP is saturated during the event.** Bilinear S runs 0.95-0.98 across the AOIs, so S has nothing left to discriminate. Downscaling only moves HAND into S, and the terrain term already has HAND.
2. **The weights collapse.** With `w = exp(−HAND / 5 m)` and valley relief of 100-360 m, the downscaled S becomes binary (p5/p50/p95 = 0 / 0 / 1) after clipping to [0, 1]. Clipping also breaks the mass conservation.

## Side finding for the TPI method (needs the conductor's approval before any change)

On these Taquari floods, the **live `T_flood` scores ROC 0.52** (98.8 % of the pixels are 0), against 0.92 for a HAND-only ramp. There are two causes:

- **The slope gate** (`slope > 5° → T_flood = 0`) uses the 0.01° (~1 km) static slope. In narrow valleys, 95 % of the floor pixels sit in 1 km cells whose mean slope is > 5°.
- **The HAND range** of 5-15 m is too tight for this event: the median HAND of flooded pixels is 16.1 m.

Suggested test (not applied; changing the TPI formula needs the conductor's approval): gate the slope on the 90 m slope, or drop the gate where HAND < 5 m, and widen the HAND range to about 0-30 m for extreme events. Then re-score the RS/WA flood_warned %.
