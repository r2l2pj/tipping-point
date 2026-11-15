# RS-EMS implementation report (t19j → t195)

## What changed

**New files**
- `tools/rs_ems_validation.py` — `build` (EMS zips → cleaned geojson + meta) and
  `score` (rasterize + confusion metrics + flagged-before-onset + `--write`
  merge into `data/metrics.json`).
- `pipeline/tests/test_rs_ems_validation.py` — hermetic tests on a synthetic
  4×4 grid (rasterize center vs area-weighted, known-value confusion,
  end-to-end score incl. flagged-before-onset, merge touches rs_2024 only).
- `data/rs_2024/geojson/flood_ems.geojson` — EMS flood union per
  AOI+product+image date, EPSG:4326, props aoi/product/image_date_utc/sensor/
  source/status/notations/permanent_water_excluded/area_km2. 0.71 MB
  (simplified at 20 m tolerance — raw dissolve was 7.9 MB).
- `data/rs_2024/geojson/flood_ems_aoi.geojson` — the five EMS AOI extents.
- `data/rs_2024/geojson/landslide_ems.geojson` — EMS `6-Mass Movement`
  polygons (per lead note: reported, not scored).
- `data/rs_2024/flood_ems_meta.json` — activation/AOIs/products (versions,
  image dates, sensors, download URLs, stats), licence, coverage limit.

**Modified**
- `data/metrics.json` — `cases.rs_2024` gained `flood_ems_iou`, `_f1`,
  `_precision`, `_recall`, `_flooded_km2`, `_flagged_before_onset_pct`,
  `_flagged_before_onset_critical_pct`, `flood_ems` (object), `flood_ems_method`.
  `flooded_km2` / `flood_warned_pct` untouched (both still null).
- `data/SCHEMA.md` — new `flood_ems*` section under the warned/lead-time block.

## Activation + products

- **EMSR720** "Flood in Rio Grande Do Sul State, Brazil"; GLIDE
  FL-2024-000063-BRA; event 2024-05-02, activated 2024-05-03T09:04Z, activator
  DG ECHO. Only EMS rapid-mapping activation for RS (checked EMSR700–EMSR740).
- Found via the **documented public dashboard API**
  `…/backend/dashboard-api/public-activations/?code=EMSR720` — no bot
  protection hit; product zips downloaded through the API's own
  `downloadPath` links (signed S3 redirect).
- 5 AOIs, all intersecting our rs_2024 AOI: **AOI01 Guapore, AOI02 Encantado,
  AOI03 Roca Sales, AOI04 Das Antas Dam, AOI05 Santa Tereza** (Taquari valley,
  ~51.6–51.9 W / 29.0–29.3 S). 15 DEL/GRA product zips + 5 BLP zips under
  `~/.cache/lead-analysis/rs-ems/raw/<aoi>/<pkg>/`.
- Image dates: **2024-05-04** (DEL SAR: COSMO-SkyMed, RADARSAT-2 — AOI01/02/05),
  **05-05** (PAZ — AOI02/03/05), **05-06** (Pleiades Neo GRA — all five),
  **05-07** (GeoEye-1 — AOI01), **05-08** (PNEO — AOI04), **05-15**
  (WorldView-3 — AOI05). AOI04's DEL_PRODUCT had no downloadPath (nothing
  published); its GRA products carry the flood trace.
- Layers used: `observedEventA` + `maximumFloodExtentA`, `event_type`
  '5-Flood' (notations Flooded area / Flood trace / Maximum flood extent);
  BLP `hydrographyA` permanent/reference water subtracted
  (`permanent_water_excluded: true` on every feature).

## Headline numbers (`score --write`)

- **Headline date = 2024-05-06** (all five AOIs mapped that day, optical GRA).
  Alto+ vs observed band 20240506: **IoU 0.185, F1 0.3123, precision 0.4645,
  recall 0.2352**; EMS flooded **32.04 km²** (rasterized), TPI Alto+
  16.23 km², overlap 7.54 km², domain 258.2 km².
- Same date, GFS issued 05-05: IoU 0.2065 / F1 0.3423.
- SAR dates score lower (05-04: IoU 0.116, 4.19 km²; 05-05: 0.159, 4.79 km²);
  05-07 (AOI01 only) IoU 0.252; 05-08/05-15 have no observed band (series ends
  05-07) and 05-15 has no GFS run either → metrics null, km² reported.
- **Flagged before onset** (onset 2024-04-29 03:00Z, denominator = full EMS
  flood footprint 36.45 km²): observed band dated 2024-04-29 → **0.21 % Alto+**;
  GFS issued 04-28 → **6.56 % Alto+ / 0.10 % Crítico**; issued 04-27 and 04-26
  → 0 %. Published: `flood_ems_flagged_before_onset_pct` = 6.56,
  `_critical_pct` = 0.1.

## Commands run

- `curl` against `https://mapping.emergency.copernicus.eu/backend/dashboard-api/public-activations/?code=EMSR*` (list + per-AOI metadata), and the zips' `downloadPath` URLs.
- `python tools/rs_ems_validation.py build --simplify-m 20`
- `python tools/rs_ems_validation.py score --write`
- `pytest -q pipeline/tests live/tests gee/tests` → **853 passed**
- `ruff check` + `ruff format` on both new files → clean
- All python invocations under `systemd-run --user --scope -p MemoryMax=2G`,
  venv `~/code/r2l2pj/tipping-point-data/.venv/bin/python`, `PYTHONPATH=.:pipeline`.
- Commit `1f05a07` on branch `rs-ems` (not pushed).

## Caveats

- **Coverage:** EMSR720 mapped only the five Taquari-valley AOIs — Porto
  Alegre, Guaíba, Canoas, Eldorado do Sul, São Leopoldo (the metro flood
  epicentre) were never covered by any EMS activation. Every score is
  Taquari-valley-only; stated in `flood_ems_method`, per-row `scope`, and
  SCHEMA.md.
- EMS polygons are imagery-mapped extent at a moment — miss flooding under
  cloud/vegetation; GRA 'Flood trace' marks earlier water no longer present;
  DEL_MONIT 'Maximum flood extent' is cumulative across the two monitoring
  images.
- Date convention: EMS image date E pairs with observed band E (rain
  E−3..E−1) and GFS frame depicting E (rain E−2..E) — the one-day offset in
  docs/conventions-dates.md applies.
- Landslides: 497 EMS '6-Mass Movement' polygons → 8 dissolved groups,
  ~6.4 km² total (AOI04 largest, 3.2 km²) in `landslide_ems.geojson` —
  reported only, not scored.
- `flood_ems_flooded_km2` (32.04 km², headline date) is the rasterized
  cell-area on the ~90 m grid; polygon geodesic areas are in the geojson props.
