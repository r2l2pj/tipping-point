# Date and rain-window conventions

Every product below works on **UTC calendar days**. A "day D" is the UTC date.
The frame or band *labelled* X names the **end** of its 3-day rain window, but
which three days that window contains differs between products — the one-day
offset between the observed series and the forecast/Now frames is the single
most error-prone convention in this repo (science review §6.1).

| Product | Frame labelled X covers | Verified |
|---|---|---|
| Replay **observed** TPI series (`<case>_tpi_{slide,flood}_*.tif` bands `YYYYMMDD`; r_series / s_series the same) | days **X−3, X−2, X−1** — `[X − 3 d, X)`, day X **excluded** | `gee/tpi.py:60-62` (`_accum`: `filterDate(end.advance(-days,'day'), end)`), `gee/tpi.py:159` (`rain3 = _accum(_imerg(), t, 3)` where `t` is the band date), documented in `data/SCHEMA.md:210-215` |
| Replay **GFS issued** frames (L22/L28b; `<case>_tpi_gfs_<issue>.tif`, horizons d1..d3 depicting D+1..D+3) | days **X−2, X−1, X** inclusive — observed IMERG for days < issue D, GFS D 00Z for days ≥ D | `pipeline/tp/forecast_align.py:24-29` (`window_days` returns `target−2..target`) and `forecast_align.py:32-56` (`issue_rain3` raises on observed ≥ D or forecast < D) |
| **Now** frames (today = D+0, d1..d3 = D+1..D+3; `data/now/cog/{slide,flood}_*.tif`) | days **X−2, X−1, X** inclusive — IMERG Early for days < today, NOAA GFS for days ≥ today | `live/tpi.py:112-120` (`frame_rain3` sums `day−2, day−1, day`), `live/run_now.py:366-396` (series keyed by date, `d < today` → observed, `d >= today` → GFS) |
| **Now alerts** (`data/now/alerts.json`) | alerts fire on class upgrades in the **today** and **d1** frames only, so the alert's rain window is that frame's X−2..X | `live/alerts.py:56-59` (`"when": "today" | "d1"`), `live/alerts.py:66-73` (`worst = max(cls_today, cls_d1)`), `live/run_now.py:522-525` (`day = today` or `today+1`); certainty is capped by the frame's `basis` (`live/run_now.py:245-248, 523-532`) |
| **ML labels** (positive-cell `t_utc`, `ml/labels.py`) | `t_utc` = **end** of the cell's maximum-24 h IMERG window inside `label_window` 2024-04-27..2024-05-06 | `ml/labels.py:79-98` (`assign_peak24h_times`: rolling-24 h argmax → window end), `ml/config.py:30` (`EVENT_WINDOW`), `config.yaml:10` (`label_window`) |

Supporting conventions:

- **GFS COG bands**: band `d<j>` of `<case>_tpi_gfs_<issue>.tif` is forecast mm
  over calendar day **D+j−1** (j = 1..4; issue D 00Z) — `tools/regen_gfs_replay.py:7,50`,
  `pipeline/tp/forecast_align.py:19-21`. Day totals are the four trailing-6-h
  APCP buckets covering that UTC day (`live/fetch.py:614-621`).
- **Anchors**: all metric timestamps are stored as UTC. Local midnights named
  in sources convert as BRT = UTC−3 and PST = UTC−8. The L37 event onsets:
  RS 2024 = **2024-04-29T03:00Z** (Apr 29 00:00 BRT, first deaths), WA 2025 =
  **2025-12-08T08:00Z** (Dec 8 00:00 PST). A **date-only** anchor is interpreted
  as **noon local** (`ml/lead_time.py:208-215` `damage_time`, e.g. the old
  MetSul "2024-04-27" → 2024-04-27T15:00Z) — date-level confidence, so the
  onset keys now carry `*_anchor_utc` explicitly.
- **Lead-time keys**: `lead_hours = anchor − first Critical (≥ 0.6) crossing`
  of an 8-connected ≥ 4 px cluster in the TPI_slide series (`ml/lead_time.py:102-130`,
  `.herdr/lead_time_run.py`, `.herdr/asavail_run.py`). Since L37 the anchor is
  the onset (see `anchor_utc` / `lead_time_anchor_utc`); pre-onset anchors are
  kept under `withdrawn.damage_anchor`.
- **`official_warning_delta_h`** = `official_warning_at` − `first_critical_time`
  (first AOI pixel ≥ 0.6 in the *slide* series): how long before the index's
  first Critical signal the published official warning was issued. Both
  instants are stored (`official_warning_delta_warning_utc` /
  `official_warning_delta_reference_utc`).
- **`first_critical_at`** (flat key) is a *different* first-critical: the first
  **date** whose slide-union-flood daily max TPI > 0.6
  (`pipeline/tp/bundle.py:456-457, 756-759`). See `first_critical_at_basis`.

The one-day offset in practice: the observed band "2024-05-02" covers Apr 29–
May 1; the GFS frame depicting "2024-05-02" covers Apr 30–May 2. Comparisons
that mix observed bands with forecast frames under the same label compare
windows shifted by one day.

## Known limitations

- **People counts (one method, S03a).** Every people number is GPW v4.11 2020 population count (the UNADJUSTED count, not the UN-adjusted one) on its **native 30″ grid**, each cell weighted by the fraction of its area covered by the mapped area (exact polygon ∩ cell intersection; unions de-duplicate overlaps). Now: R1 DSWx-S1 and R2 NISAR polygons, R3 summary, priority zones and the Now `summary.json` `people_*` (merged from R3) all use it (`tp.response_summary.people_geom`, `people_method`). **Exception, not yet unified:** the replay cases (`people_critical` in metrics/series/GFS layer stats, WA `people_flooded`, `scar_people`) use GPW resampled by Earth Engine to ~1 km (0.00898°), which undercounts by ~11–13 %: RS 2024-05-02 Crítico area holds 1.49 M people on that grid vs 1.68 M on the native grid (2024-04-30: 1.17 M vs 1.30 M). Treat case people as a lower bound; re-run on the native grid after submission.
- **HAND radius served is 1500 m; 1000 m validates better.** The served
  `thresholds.hand_proxy_radius_m` is 1500 m (`config.yaml:27`), but the MERIT
  reference check scores 1000 m best (IoU 0.42 RS / 0.26 WA vs 0.34 / 0.19 at
  1500 m — `metrics.json cases.*.hand_validation_by_radius`,
  `hand_best_radius_m: 1000`). Switching radius is a post-submission choice:
  it changes TPI_flood everywhere, not just the validation numbers.
- **NISAR flood has no permanent-water mask — only "dark in pre".**
  `compute_flood` excludes prior water via `~water_before` (`pre_db < thr`)
  (`pipeline/tp/flood_mask.py:124-133`); the optional `watermask` input
  (`pipeline/scripts/02_flood_mask.py:20-21`, default `None`) was never
  supplied, so no independent permanent-water layer was applied.
- **HV unused.** Only the co-pol term (HHHH, else VVVV) is read
  (`pipeline/tp/response_nisar.py:6,39` `CO_POL_TERMS`); the `hv_drop_db`
  cross-pol vote (`config.yaml:35`) has no consumer in the produced layers.
- **Otsu never fired — the fixed −15 dB was used.** `wa_2025.threshold_db`
  is −15.0, the `water_db_max` fallback (`pipeline/tp/flood_mask.py:25,62-91`:
  `adaptive_threshold` returns "fallback" when no tile is bimodal;
  `.herdr/implement-r2-nisar.md:61,72-75`).
- **Scar precision has no random baseline.** `scar_precision_500m_pct`
  (55.07 % RS, 0 % WA) counts candidate patches near inventory points, with
  no null model of chance co-location (science review §6.2/Q1).
- **SWOT outliers kept.** `tools/wa_swot_series.py:69-98` drops only
  fill/no-data and out-of-window rows; every other reach sample is kept
  verbatim — no outlier screening.
- **The INMET gauge check used IMERG Final, not Early**
  (`tools/imerg_vs_gauge.py:1,49-50`, `GPM_3IMERGDF`). Now and the as-available
  replay run on IMERG **Early**, which was never validated against gauges.
- **SMAP latency.** The GFS replay holds S at D−1 (`.herdr/gfs_replay_run.py:379-385`)
  — 1–3 d of look-ahead vs real L4 latency — while live S is 2–5 d old
  (`live/fetch.py:198-229` takes the newest CMR granule; the 2026-11-15 run's
  `smap_age_days` was 5). The as-available replay models this with
  `SMAP_LAG_DAYS = 3` (`.herdr/asavail_run.py:60`).
- **Early Now runs may read a partly-posted D−1 IMERG Early.** The cron fires
  at `:17` every 3 h (`.github/workflows/now.yml:12`), IMERG Early has ~4 h
  latency, and `run_now` discards the per-day half-hour count
  (`live/fetch.py:77-80`), so the 00:17Z/03:17Z runs can ingest an incomplete
  D−1 into two frames. A `steps`-count guard (48 expected) is pending after
  submission (science review §6).
