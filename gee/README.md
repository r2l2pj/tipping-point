# gee/ — Earth Engine

Earth Engine work for Tipping Point. Two forms live side by side:

- `*.js` — original Code Editor scripts (paste into
  https://code.earthengine.google.com; this repo is the source of truth —
  copy changes back).
- `*.py` — Python ports / native modules, run as `uv run python -m gee.<name>`
  with credentials from env (`GEE_PROJECT` + `GCP_SA_KEY` or
  `GOOGLE_APPLICATION_CREDENTIALS`; `GEE_SERVICE_ACCOUNT_KEY` is a fallback).

## Asset namespace

All derived rasters live under one root (single source of truth — the app
server reads them via geotiff.js/ee range reads; local `png`/`cog` files are
fallback):

```
projects/<GEE_PROJECT>/assets/tipping-point/
├── static/<domain>/            # domain = south_brazil | wa_2025
│   ├── clim_rain               # ImageCollection, `doy` property per image,
│   │                           #   bands p90|p95|p99 — int16 mm×10 (÷10 on read)
│   ├── slope                   # degrees
│   └── hand                    # metres
├── <case>/                     # rs_2024, wa_2025
│   ├── clim_rain               # ~25 doys covering the case event window,
│   │                           #   clipped to the case AOI
│   ├── tpi_slide_series, tpi_flood_series, r_series, s_series
│   │                           # multiband, one band per day named YYYYMMDD
│   ├── t_slide, t_flood        # single-band static terrain scores
│   ├── landslide_candidates_hls
│   └── ...                     # further ingested rasters — names in
│                               #   data/<case>/assets.json
└── now/
    ├── imerg_early, smap_l4, geosfp                            # input collections
    └── tpi_slide_d0..d3, tpi_flood_d0..d3, r_d0..d3, s_d0..d3  # single-day images
```

The `static/sul_brasil/clim_rain` collection serves the live job over the
`domains.now` bbox (`config.yaml` / `tp.config.now_bounds()`): a rolling
~75-doy window — same `doy` property and `p90|p95|p99` bands, int16 mm×10.

## Stamp contract

Every derived image carries the properties:

- `data_date` — `YYYY-MM-DD`, the day the image depicts (band date for
  series).
- `updated_at` — ISO-8601 write stamp; readers check freshness without the
  manifest. A consumer keying on a non-null `updated_at` never reads a
  half-updated set (same rule as `data/SCHEMA.md`).

## Python entry points

| Module | Purpose | Dry flags |
| --- | --- | --- |
| `python -m gee.tpi --case <id>` | Replay TPI_slide/TPI_flood multiband series + R/S series + T rasters + per-case climatology asset | `--dry-run` (window + planned exports, no `ee.Initialize`), `--tasks` (export dicts) |
| `python -m gee.hand --case <id>` | HAND-proxy validation vs MERIT (reference only); merges `hand_validation` | `--dry-run` / `--tasks` |
| `python -m gee.impact --case <id>` | people_critical/flooded, flooded_km2, VIIRS night-light drop + COGs | `--dry-run` / `--tasks` |
| `python -m gee.opera --case <id>` | NISAR water vs OPERA DSWx: iou/f1/cloud_pct into metrics.json | `--dry-run` / `--tasks` |
| `python -m gee.hls_scar --case <id>` | HLS dNBR landslide-scar candidates | `--dry-run` / `--tasks` |
| `python -m gee.now_tpi [--project P]` | Live now/ TPI assets (called via `tools/now_tpi.py` by `live/jobs.py now`) | `--dry-run` |
| `python -m gee.check_series <cog> --case <id>` | Sanity-check an exported TPI series COG | — |

`--dry-run`/`--tasks` never call `ee.Initialize` — they work without
credentials. `make all` wires these in dependency order and skips them with a
`SKIP <step>: missing …` line when `GEE_PROJECT`/key env vars are absent.

Related non-module entry points: `tools/ingest_assets.py --case <id>`
(file → asset ingestion into the namespace above), `tools/now_tpi.py`
(thin wrapper over `gee.now_tpi`).

## JS scripts — run order (Code Editor)

1. `01` TPI index (RS) — exports the Drive COG **and** the
   `tpi_slide_series` asset that `08`/Telegram `/simular` read. Both required.
2. `09` impact — needs `01`'s asset (and `07`'s for WA); for
   people_flooded/flooded_km2 also upload `flood_nisar` and set
   `HAS_NISAR_FLOOD=true`.
3. `05` P99 daily-rain climatology — heavy, one-time asset. Run before `06`.
4. `06` ML feature table — needs `05` + `rs2024_initiation` (`ml/labels.py`).
5. `02` lead time — needs `01`'s series + `rs2024_initiation`.
6. `07` TPI index (WA).
7. `08` simular table → CSV feeds `tools/build_simular.py`.
8. `03` RF susceptibility, `04` HAND validation — independent drafts.

AOIs/dates must stay in sync with `../events/*.json` — JS can't read files;
keep the pasted values in sync by hand.
