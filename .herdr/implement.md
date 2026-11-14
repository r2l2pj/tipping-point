# L22 — REAL GFS-forecast replay frames

## What changed

- `.herdr/gfs_replay_run.py` (new, one-off runner — same pattern as
  `asavail_run.py`). Per issue day D, horizons k=1..3, hazards slide/flood:
  `TPI_fcst(D+k) = R_fc^wR · S_held(D)^wS · T^wT` (config.yaml
  `tpi_weights` [0.3333,0.3333,0.3334]), combined **on the 90 m T grid**
  like `gee.tpi._combine_case_tpi` (R/S resampled UP with bilinear
  reproject; terrain never flattened).
  - **R_fc**: `live.tpi.projected_rain3` — observed IMERG daily mm for
    days ≤ D (tail of the 3-day window) + GFS `forecast_rain/<case>_gfs_<D>.tif`
    bands d1..dk (native 0.25°, reprojected onto the 0.1° IMERG grid) —
    then `live.tpi.rain_score` vs the case rain3 climatology p90/p95/p99.
    Observed daily mm comes from `pipeline.fetch.imerg.fetch_daily`
    (IMERG Early, Earthdata token from `.env`) — the same basis the
    as-available run used; raw granules are streamed and deleted.
    Climatology: rs_2024 reuses `intermediate/asavail/clim_rain3.tif`;
    wa_2025 had no local clim COG so it was downloaded once via
    `gee_tpi.climatology(..., assets=False)` + `tp.fallback._write_cog`
    into `intermediate/forecast_replay/wa_2025_clim_rain3.tif`.
  - **S_held**: published `s_series` band for day D (held across all 3
    horizons; the s-series is ~1 km — resampled up to 90 m, same as
    `_combine_case_tpi` does for coarse inputs).
  - **T**: static `t_slide`/`t_flood` COGs.
- Outputs per issue day D (37 issue days total):
  - `data/<case>/cog/<case>_tpi_gfs_<YYYYMMDD>.tif` — 6-band float32 COG,
    bands `d1_slide,d1_flood,d2_slide,d2_flood,d3_slide,d3_flood`.
  - `data/<case>/png/tpi_{slide,flood}_gfs_d<k>_<D+k YYYYMMDD>.png` —
    rendered via `tp.bundle._class_png_3857` + `live.tpi.classify`
    (classes 0.2/0.4/0.6, class 0 transparent).
  - `data/<case>/png/_index.json` — appended (existing entries preserved):
    `{id: tpi_{haz}_gfs_d<k>, date: D+k, issued: D, png, cog, bounds,
    forecast: true, forecast_source: "noaa-gfs", kind: "forecast"}`.
  - `data/<case>/forecast_lead_time.json`.

## Judgment calls / deviations

- R_fc is the piecewise `rain_score` (0/0.5/1.0 anchors at p90/p95/p99),
  not a literal `mm/clim` ratio — that's what the published R series is
  and what the geomean needs ([0,1]).
- PNG filenames carry the **depicted** date (D+k), matching the existing
  `tpi_*_d<k>` perfect-forecast convention; `issued` is on the index entry.
- "Damage cluster / AOI max": implemented as AOI max over
  max(TPI_slide, TPI_flood) (no dated landslide inventory exists in
  either case). An issue day "warns" if some horizon's AOI max > 0.6 for
  a depicted date ≤ anchor date. `lead_h` = anchor − issue 00Z.
- `wa_2025` forecast_rain covers all 21 issue days (runs.json 21/21 ok).

## Commands run + results

```
set -a; . ./.env; set +a
PYTHONPATH=.:pipeline .venv/bin/python .herdr/gfs_replay_run.py
```

- rs_2024: IMERG daily 2024-04-19..05-08 (20 days, 0.1° grid 15×20);
  16 COGs `rs_2024_tpi_gfs_20240422..0507.tif`, 96 PNGs, +96 index layers
  (index now 136 entries → 522→? lines grew as expected, valid JSON).
- wa_2025: IMERG daily 2025-11-27..12-21 (25 days, 15×11); GEE clim
  download ok; 21 COGs `wa_2025_tpi_gfs_20251130..1220.tif`, 126 PNGs,
  +126 index layers.
- Spot checks: rs issue 2024-04-30 d1/d2 → TPI max 1.0 (GFS had the
  deluge); issue 2024-04-24 all-zero (dry forecast). wa issue 2025-12-05
  d1..d3 slide max 0.29→0.67→0.73 (ramping AR). PNGs RGBA, correct size.
- Harmless `NotGeoreferencedWarning` from `classes_png_bytes` PNG writer.

## Headline metric

| case | issue days | warned | earliest critical issue | would_have_warned_h | lead median [IQR] |
|---|---|---|---|---|---|
| rs_2024 | 16 | 2 | 2024-04-22 | **135.0 h** | 123 h [117, 129] |
| wa_2025 | 21 | 6 | 2025-12-03 | **144.6 h** | 84.6 h [54.6, 114.6] |

(anchor: rs 2024-04-27T15Z date-level; wa 2025-12-09T00:35Z timestamped.)
