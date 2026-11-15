# L35 upstream-rain flood R — re-score result (lead-analysis, 2026-11-15 ~10:00 BRT)

R_flood_new = max(R_local, R_up(basin)), R_up = rain_score of the 3-day rain averaged over the
full HydroBASINS lev-10 upstream area vs that basin-mean series' own 2001-2023 climatology
(IMERG V07 Final daily; GEE IMERG_V07 == GES DISC GPM_3IMERGDF on 537 overlap days, max abs diff
0.000 mm). GFS frames: observed days < issue + GFS 00Z run (L28b). Code: tools/upstream_rescore.py
(this branch), cubes: tools/upstream_rain*.py (main c10eb11 branch l35-rain), basins: l35-basins.

## WA 2025 (reference: NISAR new-flood area, 27.99 km² in swath)
| | current | upstream |
|---|---|---|
| observed Alto+ share of flooded area, Dec 9 / 10 / 11 | 0.01 / 41.0 / 89.0 % | 34.1 / 79.4 / 97.0 % |
| observed Crítico share, Dec 10 / 12 | 8.97 / 70.2 % | 39.8 / 91.9 % |
| onset-anchored (Dec 8 00 PST) warned%, GFS 24/48/72 h + observed | 0 / 0 / 0.14, 0 | unchanged (0) |
| best issued GFS before King County anchor | 0.14 % | 36.3 % (12-04 run, frames 12-05..07 — dates the observed index stayed 0: timing error) |
| Crítico km²·days, whole window | 3618.7 | 4800.3 (+33 %) |
| FALSE Crítico (in swath, not flooded, not pre-water) km²·days | 2609.4 | 3432.2 (+32 %) |
| false Crítico on Dec 10 | 117.3 km² | 308.6 km² |
| Alto+ precision vs NISAR flood (window) | 2.4 % | 2.7 % |

## RS 2024 (no flood reference map)
Crítico km²·days 47,870 -> 50,983 (+6.5 %), almost all on May 6-7 (+1,345 / +1,735 km², Guaíba
recession: upstream rain keeps the valley Crítico). Days May 1-5 differ by -0.5 % = reconstruction
error of R_local/S bilinear onto the 90 m grid (R_new >= R_local by construction), so treat
differences < ~1 % as noise.

## Verdict (L35 / L36e rule)
Improves WA flooded-area coverage on the event days, but does NOT improve the onset-anchored
warned% and inflates false Crítico by ~32 % -> NOT served in Now; current formula stays.
Pitch only as a post-event improvement tested on the same event: "adding upstream-basin rain, 79 %
of the NISAR-confirmed flood area was High/Critical on Dec 10 (vs 41 %), at the cost of ~1/3 more
Critical area elsewhere".
