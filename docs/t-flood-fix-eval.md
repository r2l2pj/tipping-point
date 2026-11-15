# T_flood fix — EVALUATION ONLY (lead-ml, 2026-11-15; nothing served changes)

A change to the terrain term is a TPI formula change, so it needs Lucas's OK. This file only measures it.

- Tool: `tools/t_flood_eval.py`.
- Numbers: `ml/models/t_flood_eval.json`.

**The three T_flood variants:**

| variant | definition |
|---|---|
| **live** (served) | T = 1 at HAND ≤ 5 m, ramp to 0 at 15 m; T = 0 where slope > 5° (the case's published `t_flood` COG) |
| **control** | the live formula on the same 90 m terrain as the candidate (isolates *data* from *formula*) |
| **candidate** | T = clip(1 − HAND / 30 m); T = 0 where the **90 m** slope > 5° |

**How TPI is scored:**

- TPI_flood = R^⅓ · S^⅓ · T^⅓, and only T changes.
- R and S are each case's own published series. Rebuilding with the live T reproduces the published RS frames (mean abs error 0-0.01).
- Scores use the max TPI over the event days.
- High+ = TPI > 0.4; Critical = TPI > 0.6.
- lift = (share of truth flagged High+) ÷ (share of the area flagged High+).

**90 m terrain sources:**

- RS / Uruguaiana: NASADEM slope (ML static90) + static-v1 `hand_90m`.
- WA: MERIT Hydro `hnd` + NASADEM slope via a one-off GEE download.

## Results

| case · truth | variant | ROC-AUC | truth flagged High+ / Critical | area flagged High+ | lift |
|---|---|---|---|---|---|
| **RS 2024** · Copernicus EMS flood, 5 Taquari AOIs (Apr 29–May 5) | live | 0.61 | 25 % / 17 % | 6.8 % | 3.7 |
| | control | 0.64 | 31 % / 23 % | 8.2 % | 3.8 |
| | **candidate** | **0.85** | **79 % / 77 %** | 19.7 % | 4.0 |
| **WA 2025** · NISAR flood candidates, in the NISAR extent (Dec 8–12) | **live** | **0.93** | 97 % / 68 % | 10.6 % | **9.2** |
| | control | 0.85 | 98 % / 70 % | 21.9 % | 4.5 |
| | candidate | 0.83 | 98 % / 58 % | 26.7 % | 3.7 |
| **Uruguaiana** · 7 NISAR flood polygons, Sep 30 frame | live | 0.94 | 94 % / 88 % | 30.1 % | 3.1 |
| | control | 0.97 | 94 % / 88 % | 22.5 % | **4.2** |
| | candidate | 0.96 | **100 % / 94 %** | 36.2 % | 2.8 |

Cost side:

| measure | live | control | candidate |
|---|---|---|---|
| RS case-wide High+ area | 8,621 km² | 8,764 km² | **11,986 km² (+39 %)** |
| RS case-wide Critical area | 7,972 km² | — | 10,949 km² |
| T = 0 share, RS AOIs | 93 % | — | 80 % |
| T = 0 share, WA extent | 87 % | — | 68 % |
| Uruguaiana: days with ≥ 5 % of the region at High+ (Sep 28 – Oct 20, local-rain replay) | 9 | 9 | 9 |

`flood_warned` (GFS forecast frames) was **not** re-scored, because forecast R is not stored per frame.

## Reading

- **RS (deep Taquari valley floods):** the gain is real and it comes from the **HAND range**, not the slope data. The control barely moves (0.61 → 0.64), while the 0–30 m ramp lifts ROC to 0.85 and the Critical coverage of the EMS flood from 17 % to 77 %. The flooded ground there sits at a median HAND of 16 m, beyond the live 15 m cut-off.
- **WA (shallow river floodplain):** the **served T is best**. Moving to 90 m MERIT/NASADEM terrain alone drops ROC 0.93 → 0.85, and the wider ramp drops it a little further (0.83), flagging 2.5× the area. The WA case's original terrain is finer and better than the 90 m substitutes.
- **Uruguaiana:** close to neutral. The candidate catches all 7 polygons at High+ but flags more of the region (lift 3.1 → 2.8). The 90 m live formula (control) has the best lift (4.2). False-alarm days are unchanged (9 in all three).
- **The cost is consistent:** the candidate flags more area everywhere (RS +39 % High+ km², WA ×2.5).

**Recommendation for Lucas:**

- Do **not** switch T_flood globally. The candidate helps deep-valley extreme floods (RS) and hurts shallow floodplains (WA).
- If anything changes, the defensible option is a HAND range that depends on the event type or valley depth, for example 0–30 m only where the valley relief is large. That needs its own test against more events before serving.
- Keep the served T for the submission.
