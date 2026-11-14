# U8a — case publisher / bundle (`tp.bundle.publish_case`)

Branch `unit/u8a`. Includes the mid-task L4 delta: all outputs under
`data/<case-id>/` (`rs_2024`, `wa_2025`), `_index.json` pinned to the
`{"layers":[{id,date,png,cog,bounds,forecast}]}` shape for U8b.

## Files (write scope kept)

- `pipeline/tp/bundle.py` (new) — `publish_case(event_id, dry_run=False,
  data_dir=None, events_path=None) -> dict` summary. Per call:
  - `png/<layer-id>_<YYYYMMDD>.png`, EPSG:3857, for every source COG found:
    - `<case>_tpi_{slide,flood}_*.tif` series COGs, one PNG per band
      (band name `YYYYMMDD` -> date; unnamed bands -> skip-record).
    - `<case>_tpi_forecast_d{1,2,3}_<AAAAMMDD>.tif` -> `tpi_{slide,flood}_d<k>`
      layers, `forecast: true`, depicting issue date + k. Hazard per band:
      'flood'/'slide' in band name wins; single unnamed band = slide, two
      unnamed bands = slide+flood.
    - single-date layers `hillshade`, `nisar_pre`, `nisar_post`,
      `night_lights_pre`, `night_lights_post`, `t_ml`, `opera_validation`
      (glob per layer; date from filename `AAAAMMDD`, else reference_date).
      `t_ml`/class grids via `live.tpi.classify` + `live.render`
      (`warp_to_3857` + `classes_png_bytes`, honouring the COG's CRS, not
      just 4326); continuous rasters via a gray 2–98% stretch PNG.
    - sidecar `png/_index.json` = `{"layers":[{id,date,png,cog,bounds,forecast}]}`
      (pinned keys; `bounds`/`cog` null-able) for U8b's manifest builder.
  - `geojson/permanent_water.geojson` from `*_water_nisar_*.tif`;
    `geojson/flood_confirmed.geojson` from `*_flood_nisar_*.tif` only when
    it exists; `geojson/landslide_candidates.geojson` from
    `*_landslide_candidates_*.tif`/`*_hls_scar_*.tif` (values 1+2 = candidate
    + high-confidence, every feature stamped `status: "candidate"` — never
    'confirmed'). Via `tp.polygonize.mask_to_geojson`.
  - `geojson/priority_zones.geojson`: connected blobs (scipy label,
    `MIN_BLOB_PIXELS`) of event-day TPI classes >= 2, slide -> hazard
    'landslide', flood -> 'flood' when its series exists. Ranked by max
    TPI; props `{rank,name,tpi,class,hazard,area_km2,people}` — name =
    nearest `places.geojson` feature, class = EN label by index
    (Low|Moderate|High|Critical), people = `*_pop_*`/`*_gpw_*` raster sum
    or null + reason.
  - `series/imerg_smap_daily.json` — `{event,reference_date,rain_p95_mm,
    tpi_threshold:0.6,days:[{date,offset,kind,imerg_mm,smap,tpi,river_m}]}`
    from `series/<case>_rain_soil_*.json`, TPI area-max per day across
    slide/flood bands, `river_m` from `series/<case>_swot_*.json`
    (time_str+wse records). kind='observed' offset<=0 else 'forecast'
    (perfect forecast). Nulls recorded under summary `nulls`.
  - refreshes `data/events.json` `layers[]` at runtime (NOT committed).
  - every missing source -> `skipped[]` record, never a crash; `dry_run`
    fills `planned[]` and writes nothing.
- `tools/publish_case.py` (new) — thin CLI `--event [--dry-run]`, prints
  the summary JSON. No creds needed.
- `pipeline/tests/test_bundle.py` (new) — 7 tests.
- `data/rs_2024/geojson/places.geojson` (new, 6 RS towns from gee/08),
  `data/wa_2025/geojson/places.geojson` (new, 8 Skagit/Snohomish towns).

`data/events.json` is NOT committed (runtime refresh only).

## Commands + results

- `uv sync` — ok.
- `uv run pytest pipeline/tests live/tests ml/tests -q` — **66 passed**.
- `uv run ruff check .` — **All checks passed**.
- `uv run python tools/publish_case.py --event wa-2025-12 --dry-run` —
  prints planned writes (`wa_2025/png/_index.json`, `events.json`) +
  skip-records for every absent source; writes nothing, no creds.
- `python -c json.load` on both places.geojson — valid, 6 + 8 features.
- `git status` — only scope files; `data/events.json` untouched.

## Notes for U8b

- `_index.json` entries carry exactly `{id,date,png,cog,bounds,forecast}`.
- Summary `skipped[]`/`nulls[]` map to manifest `products` null + reason.
- `bands_by_date`/summary `layers` available for the per-case manifest node.
