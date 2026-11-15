# b3-leadtime — lead-time key consistency + docs/conventions-dates.md

Worktree `b3-leadtime`, branch `b3-leadtime`. Report for t195 (lead-analysis).

## How each key was computed (anchors, task A)

- `data/<case>/lead_time_metrics.json` and `lead_time_asavail.json` were written
  by `.herdr/lead_time_run.py` and `.herdr/asavail_run.py::lead_time_json`.
  `cluster_lead_times` (ml/lead_time.py:102-130): `lead_hours = anchor −
  first >=0.6 crossing` of each 8-connected >=4 px cluster in the TPI_slide
  COG. The anchor was the events-file `damage_anchor`; `damage_time`
  (ml/lead_time.py:208-215) turns a **date-only** anchor into **noon local**
  → the old RS anchor "2024-04-27" (MetSul) = **2024-04-27T15:00Z**.
- `first_critical_time` = earliest slide-cube frame with any pixel >= 0.6
  (`event_first_crossing`, lead_time_run.py:41-44) — RS observed
  2024-04-28T00Z, RS as-available 2024-04-27T00Z, WA 2025-12-09T00Z.
- `event_lead_h` = anchor − `first_critical_time` (lead_time_run.py:90-91).
- `official_warning_delta_h` = events-file `official_warning.at` −
  `first_critical_time` (lead_time_run.py:53-59,92). For RS that warning
  instant was the **Canoas municipal notice 2024-04-25T20:40:20Z** — not the
  INMET 2024-04-26T16:50Z published as `official_warning_at`: −51.33 h came
  from an undocumented Canoas-vs-Apr-28 pair. For WA both keys already agreed
  (NWS Dec 6 00:10Z − Dec 9 00Z = −71.83 h).
- `first_critical_at` (flat, `pipeline/tp/bundle.py:456-457,756-759`) = first
  date whose slide-union-flood per-day max > 0.6 — a *different* definition
  from `first_critical_time`. Published values were stale: RS said
  "2024-05-02" while the current series already has max 0.705 on Apr 28;
  WA said "2025-12-09" while the union max was 0.618 on Nov 30.

## What changed (files)

- `data/rs_2024/lead_time_metrics.json`, `data/rs_2024/lead_time_asavail.json`,
  `data/wa_2025/lead_time_metrics.json`, `data/metrics.json`
  (load/modify/dump only; metrics.json indent=2 ensure_ascii=False, only the
  keys below):
  - All cluster lead-time stats **recomputed against the L37 onset**
    (`anchor_utc`/`lead_time_anchor_utc` + `anchor_basis`/`lead_time_anchor_basis`
    = "onset" + `anchor_source`/`lead_time_anchor_source`). Old anchors kept
    under `withdrawn.damage_anchor`.
  - `damage_anchor` updated to the onset (basis "onset").
  - `official_warning_delta_h` redefined consistently across both cases as
    `official_warning_at − first_critical_time`, with the two instants stored
    (`official_warning_delta_warning_utc`, `official_warning_delta_reference_utc`,
    `official_warning_delta_basis`).
  - `first_critical_at` corrected to definition-true values +
    `first_critical_at_basis`.
  - wa: `lead_time_asavail` added as `null` +
    `_null_reasons.lead_time_asavail` (never produced; not re-anchored).
  - `lead_time_mean_h` recomputed from the new cluster leads (exact —
    crossings are daily 00Z frames vs a fixed anchor).

## Before → after

| Key | Before | After |
|---|---|---|
| rs.lead_time_median_h / iqr / mean | −57 / [−57,−57] / −61.5 vs MetSul Apr 27 15Z | −21 / [−21,−21] / −25.52 vs onset Apr 29 03Z |
| rs.warned_before_pct / warned_24h_pct | 0.0 / 0.0 | 0.740 / 0.282 (42 / 16 of 5,678 clusters) |
| rs.event_lead_h | −9.0 | +27.0 |
| rs.official_warning_delta_h | −51.33 (Canoas − Apr 28, undocumented) | −31.167 (INMET official_warning_at − first_critical_time Apr 28 00Z) |
| rs.first_critical_at | 2024-05-02 (stale) | 2024-04-28 |
| rs.lead_time_asavail.* (file + metrics.json copy) | anchor MetSul; median −33; warned 2.87 %; event_lead +15; delta −27.33; copy stale (n 5609 vs file 5612) | onset; median +3.0; warned 76.60 % (4,299/5,612); warned_24h 4.597 %; event_lead +51; delta −7.167; synced |
| wa.event_lead_h | 0.583 vs King County | −16.0 vs onset |
| wa.official_warning_delta_h | −71.833 | unchanged (already official_warning_at − first_critical_time) + instant fields |
| wa.first_critical_at | 2025-12-09 (stale) | 2025-11-30 |
| wa.lead_time_asavail | absent | null + reason |
| wa lead_time_* cluster stats | null (inventory undated, mode event_first_critical_only) | unchanged |

Recompute was feasible in-repo (COGs + band names + onset), so the null +
`withdrawn` path was not needed; only `damage_anchor` provenance uses
`withdrawn`.

## Task C

- New `docs/conventions-dates.md`: per-product rain-window conventions with
  file:line verification (observed series `[X−3d, X)` at gee/tpi.py:60-62,159
  + SCHEMA:210-215; GFS replay and Now frames X−2..X at
  forecast_align.py:24-56 / live/tpi.py:112-120 + run_now.py:366-396; Now
  alerts on today/d1 at live/alerts.py:56-73 + run_now.py:245-248,522-532;
  ML label t_utc = end of max-24 h window at ml/labels.py:79-98), GFS band
  d<j>=D+j−1, noon-local date-only convention, and the listed Known
  limitations (HAND 1500 vs 1000, no permanent-water mask, HV unused, Otsu
  fallback −15 dB, no scar null baseline, SWOT outliers kept, INMET check on
  IMERG Final, SMAP latency D−1/2–5 d, D−1 completeness at 00:17Z/03:17Z).
- README.md: one added paragraph linking to it (lines 13-15).

## Commands run

- `systemd-run --user --scope -p MemoryMax=2G ~/.cache/lead-analysis/b3-leadtime/reanchor.py`
  (PYTHONPATH=.:pipeline, shared venv) → recomputed cluster leads vs onset on
  both RS COGs; verified first_critical_at per-day union maxima.
- `systemd-run … apply_keys.py` → applied the json edits above.
- `systemd-run … python -m pytest -q pipeline/tests live/tests gee/tests`
  → **848 passed** in 34 s.

## Notes for the lead

- The observed slide COG drifted since `lead_time_metrics.json` was last
  produced (per-frame cluster counts moved slightly: Apr 28 21→16,
  Apr 30 4530→4540, May 1 1070→1065; n still 5,678). New stats reflect the
  current COG.
- WA `first_critical_at` 2025-11-30 = a marginal pre-event Critical pixel
  (union max 0.618) — definition-true, pre-onset.
- README "Lead-time counts" paragraph still cites the MetSul anchor numbers
  (science review R4) — left as is; flagged stale by the review already.
