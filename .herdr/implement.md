# implement: alarm precision (t195)

## What changed

- `tools/alarm_precision.py` (new) — municipality x GFS-issue-day alarm
  precision. Reuses `tools/warned_before.py` (`ANCHORS`, `_day`, `_nanmax`,
  ALTO/CRIT) by import (`sys.path` insert, same convention as
  `tools/rs_ems_validation.py`). Rasterizes municipal polygons onto the TPI
  grid (cell-centre rule), nanmax over `d1..d3 _slide` bands per issue day,
  `np.maximum.reduceat` per municipality; slides->municipality via STRtree
  `intersects`. Scores per case x reference x depicted-window variant x cut;
  `--write` merges into `data/metrics.json` (json load/modify/dump +
  `os.replace`, like `warned_before.merge`).
- `pipeline/tests/test_alarm_precision.py` (new) — 14 hermetic tests: issue
  discovery, band picking, depicted windows (both variants), overlap edges,
  lead_h sign, `summarize` precision/baseline/lift/POD/lead on synthetic
  units, municipality rasterization partition, STRtree slide assignment.
- `data/metrics.json` — merged `cases.<c>.alarm_precision_*` flat keys +
  `cases.<c>.alarm_precision` object (per reference, per variant, per cut,
  `by_issue_day` table, definitions, dating assumption, flat-key map).
- `data/SCHEMA.md` — new "Alarm precision — `alarm_*`" section.
- `.herdr/implement-alarm-prec.md` — headline table + notes.

## Decisions / findings

- WA `municipal_boundaries.geojson` holds `place`+`county_subdivision`
  features, NOT counties. Fetched WA counties from TIGERweb
  `State_County/MapServer/1` (BAS 2026, STATE='53', AOI-intersect, same
  query pattern as `pipeline/fetch/boundaries.py`), cached at
  `~/.cache/lead-analysis/alarm-prec/wa_counties.geojson` -> 7 counties
  (Island, Jefferson, King, Kitsap, Pierce, Skagit, Snohomish).
- TPI bands `d1..d3` depict D+1..D+3 (warned_before `gfs_frames`); the
  `depict_d0_d2` variant re-reads them under the forecast_rain `d<j>`=D+j-1
  convention (same arrays, hit test only).
- Flat keys mirror `references.catalog.variants.depict_d1_d3`; POD/lead flat
  keys use the critical cut.
- All municipalities have >= 1 TPI cell (no zero-cell units).

## Commands run

- `curl TIGERweb State_County/MapServer?f=json` + layer-1 query STATE='53'
  AOI-envelope -> wa_counties.geojson (7 counties)
- `python tools/alarm_precision.py` then `--write` (systemd-run, 2G cap,
  PYTHONPATH=.:pipeline)
- `pytest -q pipeline/tests/test_alarm_precision.py` -> 14 passed
- `pytest -q pipeline/tests live/tests gee/tests` -> 893 passed, 0 failed

## Headline (primary window depict D+1..D+3, catalog reference)

| case | cut | alarms | hits | precision % | baseline % | lift | POD<onset % | lead h med (IQR) |
|---|---|---|---|---|---|---|---|---|
| rs_2024 | critical | 971 | 327 | 33.68 | 24.93 | 1.35 | 100 | 3.0 (3-27) |
| rs_2024 | alto+ | 1093 | 343 | 31.38 | 24.93 | 1.26 | 100 | 3.0 (3-27) |
| rs_2024 ems | critical | 971 | 35 | 3.60 | 2.90 | 1.24 | 100 | 3.0 (3-27) |
| wa_2025 | critical | 66 | 13 | 19.70 | 10.00 | 1.97 | 100 | 56.0 (20-68) |
| wa_2025 | alto+ | 84 | 14 | 16.67 | 10.00 | 1.67 | 100 | 44.0 (26-62) |

Committed on branch `alarm-prec` (not pushed).
