# gee/ — Earth Engine scripts

Paste each file into the GEE Code Editor (https://code.earthengine.google.com).
This repo is the source of truth — copy changes back here.

- Asset namespace: `projects/<GEE_PROJECT>/assets/tipping-point/...` (NOT legacy `users/` paths)
- Run order:
  1. `01` TPI index (RS) — exports the Drive COG **and** the asset
     `rs_2024_tpi_slide_series` that `08`/Telegram `/simular` read. Both exports are required.
  2. `05` P99 daily-rain climatology — heavy (~23 years of daily sums), one-time
     asset `p99_daily_rain_2001_2023`. Run before `06`.
  3. `06` ML feature table — needs `05`'s asset and `rs2024_initiation`
     (uploaded by `ml/labels.py`). Exports the features CSV plus the static and
     dynamic stack COGs.
  4. `02` lead time — needs `01`'s series and the `rs2024_initiation` asset.
  5. `07` TPI index (WA) — same script as `01` for the WA-2025 event.
  6. `08` simular table — needs `01`'s asset export; the CSV feeds
     `tools/build_simular.py` → `simular.json`.
- `03` RF susceptibility and `04` HAND validation are independent drafts.
- The climatologies in `01`/`05` are heavy: run once, export as assets, reference them after.
- AOIs/dates must stay in sync with `../events/*.json` — each event JSON has a
  `gee` block with the bbox + reference date (JS can't read files — keep the
  pasted values in sync by hand, comment above each one points to the event file).
