# A5 — as-available lead-time TPI series (rs_2024)

## What I did

New driver `.herdr/asavail_run.py` (run: `PYTHONPATH=.:pipeline .venv/bin/python .herdr/asavail_run.py`,
`.env` sourced; `GCP_SA_KEY` pointed at the SA JSON). It rebuilds the daily
2024-04-22..2024-05-07 TPI series using only inputs that existed at each day D:

- **R**: IMERG Early via `pipeline/fetch/imerg.py fetch_daily` over
  [-52.5,-30.5,-50.5,-29.0], 2024-04-20..05-07 (daily GPM_3IMERGDE +
  half-hourly GPM_3IMERGHHE, streamed via earthaccess + EARTHDATA_TOKEN).
  3-day accumulation normalized by the same p90/p95/p99 climatology basis as
  the published series (GEE `climatology()` — static historic basis, valid
  at every D), `rain_score` ported to numpy.
- **S**: `NASA/SMAP/SPL4SMGP/008` from GEE, valid-date shifted 3 days —
  S(D) = mean of granules valid on D-3 (`filterDate(D-3, D-2)`),
  approximating the last L4 granule published by D (~2-3 d latency).
  Documented assumption.
- **T**: static — existing `rs_2024_t_slide/t_flood_20240502.tif` reused.
- Combined on the 90 m NASADEM grid via the existing
  `gee.tpi._combine_case_tpi` (R/S upsampled, geomean, rule 3).

## Outputs written

- `data/rs_2024/cog/rs_2024_tpi_asavail_20240502.tif` — TPI_slide, 16 bands YYYYMMDD, 90 m
- `data/rs_2024/cog/rs_2024_tpi_asavail_flood_20240502.tif` — TPI_flood (needed for `tpi_flood_max`)
- `data/rs_2024/series/asavail_tpi_daily.json` — `{case, basis, days:[{date,tpi_slide_max,tpi_flood_max,first_critical_bool}]}`
- `data/rs_2024/lead_time_asavail.json` — same flat keys as `lead_time_metrics.json` (cluster-lead vs damage_anchor)
- `intermediate/asavail/` — R/S/clim/SMAP intermediate COGs

## Numbers (the honest 'warned X hours earlier')

| metric | published hindcast | as-available |
|---|---|---|
| first Critical (>=0.6) | 2024-04-28 | **2024-04-27** |
| event_lead_h vs anchor (2024-04-27 local noon) | -9.0 h | **+15.0 h** |
| lead_time_median_h (clusters) | -57.0 | -33.0 |
| warned_before_pct | 0.0 | 2.87 |
| n_clusters | 5678 | 5609 |
| official_warning_delta_h | -51.3 | -27.3 |

First Critical crossing comes **24 h earlier** in the as-available series and
lands 15 h *before* the date-level damage anchor (hindcast missed it by 9 h).
Most clusters still cross after the early anchor (median lead -33 h).

Series ramp: max TPI 0 until 04-25, 0.23/0.28 on 04-26, first critical 04-27,
saturating ~1.0 from 05-02.

## Notes / incidents

- `load_module()` on `.herdr/lead_time_run.py` executes both cases at import;
  the first run regenerated `data/rs_2024/lead_time_metrics.json` (histogram
  counts drifted by a few px). Restored via `git checkout`; the script now
  inlines the small helpers instead. No other stray writes.
- Other `data/` modifications in `git status` predate this run (shared checkout).
- No GEE batch exports started (`assets=False`); only interactive `getDownloadURL` pulls.
