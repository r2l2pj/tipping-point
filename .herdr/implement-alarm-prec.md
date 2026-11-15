# Alarm precision of the GFS forecast (t195)

Unit = municipality x GFS 00Z issue day. ALARM = any ~90 m TPI_slide cell
(centre inside the municipality polygon) > 0.6 Crítico / > 0.4 Alto+, max
over `d1..d3 _slide` bands. HIT = municipality holds >= 1 mapped landslide
AND the depicted window overlaps the trigger window (undated inventories;
rs 2024-04-29..05-02, wa 2025-12-08..12-12). Producer `tools/alarm_precision.py`;
merged into `data/metrics.json` (`alarm_*` flat keys + `cases.<c>.alarm_precision`
object); schema in `data/SCHEMA.md`.

| case | reference | window | cut | n alarms | hits | false alarms | precision % | baseline % | lift | POD<onset % | lead h med (IQR) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| rs_2024 | catalog | depict_d1_d3 | critical | 971 | 327 | 644 | 33.68 | 24.93 | 1.35 | 100.00 | 3.0 (3.0..27.0) |
| rs_2024 | catalog | depict_d1_d3 | alto_plus | 1093 | 343 | 750 | 31.38 | 24.93 | 1.26 | 100.00 | 3.0 (3.0..27.0) |
| rs_2024 | catalog | depict_d0_d2 | critical | 971 | 413 | 558 | 42.53 | 24.93 | 1.71 | 100.00 | 3.0 (3.0..27.0) |
| rs_2024 | catalog | depict_d0_d2 | alto_plus | 1093 | 429 | 664 | 39.25 | 24.93 | 1.57 | 100.00 | 3.0 (3.0..27.0) |
| rs_2024 | ems | depict_d1_d3 | critical | 971 | 35 | 936 | 3.60 | 2.90 | 1.24 | 100.00 | 3.0 (3.0..27.0) |
| rs_2024 | ems | depict_d1_d3 | alto_plus | 1093 | 40 | 1053 | 3.66 | 2.90 | 1.26 | 100.00 | 15.0 (3.0..27.0) |
| rs_2024 | ems | depict_d0_d2 | critical | 971 | 45 | 926 | 4.63 | 2.90 | 1.60 | 100.00 | 3.0 (3.0..27.0) |
| rs_2024 | ems | depict_d0_d2 | alto_plus | 1093 | 50 | 1043 | 4.57 | 2.90 | 1.58 | 100.00 | 15.0 (3.0..27.0) |
| wa_2025 | catalog | depict_d1_d3 | critical | 66 | 13 | 53 | 19.70 | 10.00 | 1.97 | 100.00 | 56.0 (20.0..68.0) |
| wa_2025 | catalog | depict_d1_d3 | alto_plus | 84 | 14 | 70 | 16.67 | 10.00 | 1.67 | 100.00 | 44.0 (26.0..62.0) |
| wa_2025 | catalog | depict_d0_d2 | critical | 66 | 11 | 55 | 16.67 | 10.00 | 1.67 | 100.00 | 32.0 (8.0..56.0) |
| wa_2025 | catalog | depict_d0_d2 | alto_plus | 84 | 13 | 71 | 15.48 | 10.00 | 1.55 | 100.00 | 32.0 (14.0..50.0) |

## Notes

- Universe: rs_2024 = 138 IBGE municipios (AOI-intersecting,
  `geojson/municipal_boundaries.geojson`) x 15 issue days = 2,070 units;
  wa_2025 = 7 TIGER counties (Island, Jefferson, King, Kitsap, Pierce,
  Skagit, Snohomish) x 20 issue days = 140 units.
- WA counties are NOT in the published `municipal_boundaries.geojson` (it
  holds `place` + `county_subdivision` features only); fetched from TIGERweb
  State_County/MapServer/1 (BAS 2026, STATE='53', AOI-intersect) and cached
  at `~/.cache/lead-analysis/alarm-prec/wa_counties.geojson`.
- Slide municipalities: rs catalog 86/138, rs ems 10/138 (EMSR720 covers the
  Taquari AOIs only), wa catalog 2/7 (Skagit 5 pts, Snohomish 10 pts).
- Hit units: rs 6 overlapping issue days (04-26..05-01), wa 7 (12-05..12-11)
  under the primary depict-D+1..D+3 window.
- POD = 100 % everywhere: every slide municipality was already alarmed by a
  run issued before the onset. Precision still beats the random baseline by
  lift 1.24-1.97 — the alarm is geographically informative but permissive.
- rs lead median 3.0 h is dominated by the 04-29 00Z run (issued 3 h before
  the onset); pre-onset-only IQR 3..27 h. wa lead median 56 h.
- `depict_d0_d2` variant: same bands re-read under the forecast_rain COG
  `d<j>` = D+j-1 convention; identical alarm masks, hit-overlap test shifts
  one day earlier (rs overlap issues become 04-27..05-02).
- Flat `alarm_*` keys mirror `catalog` x `depict_d1_d3`; POD/lead flat keys
  use the critical cut.
