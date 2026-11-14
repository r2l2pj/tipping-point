# U2 — contract docs (lead-analysis)

## What changed

`data/SCHEMA.md` rewritten (draft, uncommitted — awaiting cross-repo sign-off
before commit, per task and lead-integration note). It is now the single
source of truth for the app data contract. All must-include items and all 7
pinned items from lead-integration's sign-off review are in:

- Root `data/manifest.json` keyed by canonical ids `rs_2024` / `wa_2025`
  (each node carries `event_id` = `rs-2024-05` / `wa-2025-12`); directories
  keep event ids; every asset path explicit; `now` node sourced from
  `data/now/manifest.json` (schema `tipping-point/now@2`).
- Per-case node `{event_id, mode, bounds, layers[], metrics, data_dates,
  products, forecast_source}`; layer fields `{id,date,png,cog,legend,bounds,
  forecast?}`.
- Layer ids: `tpi_slide`, `tpi_flood`, `hillshade`, `nisar_pre`, `nisar_post`,
  `opera_validation`, `night_lights_pre`, `night_lights_post`, `t_ml`;
  `*_d<k>` forecast layers carry `forecast: true`.
- Products map: `municipal_boundaries`, `landslide_catalog`, `places`,
  `priority_zones`, `permanent_water`, `flood_confirmed`,
  `landslide_candidates` — path or `null` + `_null_reasons` convention.
- `series/imerg_smap_daily.json` shape pinned
  (`{event,reference_date,rain_p95_mm,tpi_threshold,days[{date,offset,kind,
  imerg_mm,smap,tpi,river_m}]}`); dated swot series file documented.
- Stable app filenames (series/geojson/png rules + dated
  `<caso>_<camada>_<AAAAMMDD>` stems); "app never globs / never derives
  paths" stated up front.
- `priority_zones` props `{rank,name,tpi,class,hazard,area_km2,people}`;
  landslides always "candidates", never "confirmed".
- `metrics.json`: exact flat v4 key set incl. `forecast_source`
  (`perfect-forecast` replay / `geos-fp` now), `_null_reasons`,
  `collections` (dataset ids/versions).
- `data/now/` layout (moved from repo-root): manifest written LAST,
  `updated_at` null/absent mid-run; `summary.json` pinned (generated_at,
  critical_km2{slide,flood,any}, people_critical + people_exposed alias,
  classes_km2 per label, data_dates, forecast_source geos-fp);
  `alerts.json` pinned as `live/alerts.py` emits
  (candidates[{class:int,label,centroid:[lon,lat],pixels:int,hold:bool}]).
- `data/events.json` stays `tipping-point/events-index@1` with hyphen event
  ids = manifest `event_id` (not the underscore case keys).
- Legend arrays pinned pt-BR `["Baixo","Moderado","Alto","Crítico"]` index
  0–3 (live/config.py LABELS); app maps by index.
- `simular.json` flagged as an app-side fixture (tools/build_simular.py,
  Telegram /simular demo), NOT a contract product.
- Writer-of-each-file table; CRS rules (COG/GeoJSON 4326 w/ NISAR native
  allowed; PNG 3857 + lon/lat bounds); nodata conventions;
  `data/raw/` gitignored.

## Commands run + results

- `ls`, `find data`, reads of `data/SCHEMA.md` (old), `live/run_now.py`,
  `live/publish.py`, `live/alerts.py`, `live/config.py`,
  `pipeline/tp/events.py`, `pipeline/scripts/*.py`, `events/*.json`,
  `.gitignore`, `.crew/plans/{data,decisions}.md`,
  `tipping-point-app/src/lib/{data,types,contract}.ts` — context gathering.
- `python3` fence count on new file: 20 (even). `Page N of 50` count: 0.
- Proof greps on `data/SCHEMA.md` (counts):

```
data/now/            10
imerg_smap_daily      4
_null_reasons         4
collections           4
rs-2024-05           19
products              8
forecast_source       7
```

## Sign-off change (lead-integration)

`_null_reasons` hoisted OUT of the `products` map to case-node level
(sibling of `products`); `products` values are strictly `string | null`.
Committed `data/SCHEMA.md` + this report only (no push).

## Scope / notes

- Touched only `data/SCHEMA.md` + this report. Committed only on lead
  instruction after cross-repo sign-off (not pushed); no deps, no creds.
- Interpretive choice: asset paths in the root manifest are data-root-relative
  (`rs-2024-05/png/...`); paths inside `data/now/manifest.json` are relative
  to `data/now/` — matches `live/run_now.py` output and app
  `resolveAsset()` (handles both forms without doubling).
