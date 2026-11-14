# Data contract — `data/` (doc v4)

Everything the app reads comes from this directory. Pipelines write it; the app never computes.

CRS rules (v4):
- **COGs and GeoJSON: EPSG:4326** (COGs may also keep the NISAR product's native CRS).
- **PNGs for MapLibre image sources: EPSG:3857.** A 4326 PNG stretched over
  lon/lat corner coordinates misaligns at these latitudes; `live/render.py`
  warps class grids to Web Mercator before encoding, and each manifest layer
  carries `bounds` = the 3857 image extent in lon/lat `[w, s, e, n]`.

## Layout

```
data/
├── events.json                  # index the app loads first
├── manifest.json                # the contract the app reads (see below)
└── <event-id>/                  # one dir per case (e.g. wa-2025-12, rs-2024-05)
    ├── cog/                     # rasters, clipped to AOI, keep each <= ~20 MB
    ├── png/                     # pre-rendered EPSG:3857 PNGs (one per layer/day)
    ├── geojson/                 # polygons/points, EPSG:4326
    ├── series/                  # time series JSON for charts
    └── metrics.json             # validation + impact numbers (flat keys)
```

The live bundle `now/` is overwritten every 3 h by `live/run_now.py` and holds
the same kinds of files plus `summary.json`, `alerts.json`, `prev_classes.npz`
and its own `manifest.json` (the v4 `now` node).

## File naming

`<caso>_<camada>_<AAAAMMDD>.tif` — e.g. `rs_2024_tpi_slide_20240502.tif`.
Same rule for `geojson/` and `series/` files; PNG layers are
`<camada>_<AAAAMMDD>.png` (or `<camada>_d<k>.png` for now/ forecast days).
Filenames are stable: the app must never have to glob.

## manifest.json (root)

```json
{ "updated_at": "2026-11-15T09:00:00Z",
  "cases": {
    "rs_2024": {
      "mode": "replay",
      "bounds": [-52.5, -30.5, -50.5, -29.0],
      "layers": [
        { "id": "tpi_slide", "date": "2024-05-02",
          "png": "rs_2024/png/tpi_slide_20240502.png",
          "cog": "rs_2024/cog/rs_2024_tpi_slide_series.tif",
          "legend": ["Baixo", "Moderado", "Alto", "Crítico"],
          "bounds": [-52.6, -30.6, -50.4, -28.9] }
      ],
      "metrics": "rs_2024/metrics.json",
      "data_dates": { "imerg": "2024-05-02", "smap": "2024-04-30" }
    },
    "now": {
      "mode": "live",
      "bounds": [-58.0, -34.0, -48.0, -22.5],
      "data_dates": { "imerg": "...", "smap": "...", "geos_fp_issued": "..." }
    }
  } }
```

Layer fields: `id`, `date`, `png` (EPSG:3857 render for the image source),
`cog` (download/reproducibility; may be null), `legend` (tpi palette order
Baixo/Moderado/Alto/Crítico), `bounds` (PNG extent in lon/lat, for the image
source corners). Layers may add `forecast: true` — the app must render
forecast days differently (never like observed).

## now/manifest.json

The `now` node, written LAST by every `live/run_now.py` run (the app never
reads a half-updated set):

```json
{ "schema": "tipping-point/now@2",
  "mode": "live", "updated_at": "...", "bounds": [w, s, e, n],
  "data_dates": { "imerg": "...", "smap": "...", "geos_fp_issued": "..." },
  "layers": [ { "id": "tpi_slide_d0", "date": "YYYY-MM-DD",
                "png": "png/tpi_slide_d0.png", "cog": null,
                "legend": [...], "bounds": [w, s, e, n], "forecast": false } ],
  "products": { "critical_areas": "geojson/critical_areas.geojson",
                "summary": "summary.json", "alerts": "alerts.json" },
  "nisar_passes": [] }
```

`now/summary.json`: `generated_at`, `critical_km2` {slide, flood, any},
`people_critical` (a.k.a. `people_exposed`), `forecast_source` (`geos-fp` /
`perfect-forecast`), `classes_km2` per label, `data_dates`.

## metrics.json (per event, flat keys)

```json
{ "event": "wa-2025-12",
  "iou": 0.0, "f1": 0.0, "cloud_pct": 0.0,
  "landslide_hit_pct": 0.0, "hand_validation": 0.0,
  "people_critical": 0, "people_flooded": 0, "flooded_km2": 0.0,
  "night_light_drop_pct": 0.0,
  "lead_time_mean_h": 0.0, "lead_time_median_h": 0.0, "warned_before_pct": 0.0,
  "rf_auc": 0.0, "rule_auc": 0.0, "feature_importance": {},
  "forecast_source": "geos-fp" }
```

Written by: `04_validation.py` (iou, f1, cloud_pct), `gee/09_impact.js`
(people_critical, people_flooded, flooded_km2, night_light_drop_pct — merge
its CSV printout), `gee/04_hand_vs_merit.js` (hand_validation), landslide
confirmation (landslide_hit_pct), `ml/lead_time.py` (lead_time_*,
warned_before_pct), `ml/evaluate.py` (rf_auc = learned-model AUC, rule_auc =
manual slope rule, feature_importance; XGBoost stands in for the doc's
in-GEE Random Forest — see ml/README.md), level-2 forecast runs
(forecast_source). Detailed sub-results may nest under extra sections
(e.g. `landslide_ml`); the keys above stay flat.

## Conventions

- Nodata: rasters use a written nodata value; GeoJSON omits empty geometries.
- `data/raw/` is gitignored — never commit downloads; commit only clipped outputs.
