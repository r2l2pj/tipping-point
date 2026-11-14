# implement — U13a (ruling E4): boundaries + Zenodo inventory + RS catalog

## Changed

- `pipeline/fetch/__init__.py` — new package; docstring marks all products
  REFERENCE-ONLY footer-credited layers (never TPI inputs).
- `pipeline/fetch/boundaries.py` — `municipal_boundaries(event) -> Path` writes
  `data/<case_id>/geojson/municipal_boundaries.geojson`. BR → IBGE malhas v3
  (`/api/v3/malhas/estados/RS?intrarregiao=municipio`, GeoJSON) joined with the
  localidades API for names; US → TIGERweb Places_CouSub MapServer BAS-2026
  layers 8/11/12 (county subdivisions, incorporated places, CDPs) queried with
  the AOI envelope, then re-filtered client-side with shapely. Feature
  properties: `{name, admin: municipio|place|county_subdivision, source:
  IBGE|US Census TIGER, license, url}`. Any source failure → empty
  FeatureCollection with top-level `fetch_error`; never crashes silently.
- `pipeline/fetch/inventory.py` — `download_inventories(raw_dir) ->
  {name: [Path]}` pulls EVERY file (all sidecars) of the two CC-BY-4.0 Zenodo
  records via `https://zenodo.org/api/records/<id>`; idempotent (skips existing
  files); unreachable/failed inventory → empty/partial list, never raises.
  `inventory_catalog(event) -> Path` reads the two POINT shapefiles
  (UFRGS `Landslides_Initiation_RS_Brazil_2024`, Cemaden
  `GEO_RS_CEMADEN_WGS84_PONTOS_MM`), reprojects to 4326, filters to the event
  AOI, and to the event window when a date column exists (none does — both
  files carry only Id/X/Y), writing
  `data/rs_2024/geojson/landslide_catalog.geojson` with properties
  `{source, license: CC-BY-4.0, doi, event_date}` ('catalog' entries — no
  'confirmed' wording anywhere).
- `pipeline/tests/test_fetch_boundaries.py` — offline: AOI-intersection filter
  on synthetic features incl. edge-touch and null geometry; BR write path with
  mocked `_get_json`; unreachable → fetch_error stub; TIGERweb layer→admin map.
- `pipeline/tests/test_fetch_inventory.py` — offline: `catalog_filter` AOI +
  window on a temp .shp (and the no-date-column path); full sidecar list
  download on a fake Zenodo payload (monkeypatched `requests.get`); unreachable
  record → `[]`; end-to-end catalog from a planted point shp.

## Generated (real run, network available)

- `data/rs_2024/geojson/municipal_boundaries.geojson` — **138** municipios
  (IBGE), 84 KB.
- `data/wa_2025/geojson/municipal_boundaries.geojson` — **260** features
  (53 county_subdivision + 207 place, US Census TIGER), 8.7 MB.
- `data/rs_2024/geojson/landslide_catalog.geojson` — **16027** catalog
  points (8741 UFRGS + 7286 Cemaden), 3.6 MB; 0 outside the AOI;
  `event_date: null` (no date fields exist in either inventory).
- `data/raw/inventory/{ufrgs,cemaden}/` — 14 + 14 files (all sidecars;
  gitignored). No `fetch_error` stubs — all three sources reachable.

## Commands run

1. `uv sync` (mise uv 0.13.8) — env OK, no new deps (requests/geopandas/
   shapely/pandas only).
2. Endpoint probes (curl): Zenodo 15618462 + 13225473 file lists and license
   `cc-by-4.0`; IBGE malhas `intrarregiao=municipio` → 497 RS polys (note:
   `/malhas/estados/RS/municipios` 404s — correct shape is the `intrarregiao`
   query param); TIGERweb BAS-2026 layers 8/11/12 bbox query → 53/84/123 hits.
3. `.venv/bin/python -m pytest pipeline/tests -q` → **31 passed** (20
   pre-existing + 11 new). Plan said 46 — that count was the union across
   sibling unit worktrees on the main line; this branch only carries these 31.
4. `.venv/bin/ruff check pipeline/` → clean (this repo lints with a near-ALL
   ruleset — BLE001/S112/I001 enforced).
5. Real fetch via `municipal_boundaries()`/`inventory_catalog()` on
   `tp.events.load_event` — counts above; all output geojson re-parsed with
   json+shapely, required properties asserted on every feature.

## Import paths

`pipeline` is a namespace package: both `pipeline.fetch.boundaries` (repo root
on sys.path) and `fetch.boundaries` (pipeline/ on sys.path, used by the tests)
resolve; both modules use the installed `tp.events` for data-dir resolution.

## Scope update L4 (per-case data folders)

Outputs moved from `data/<event_id>/` to per-case dirs — `data/rs_2024/` and
`data/wa_2025/` (`case = "_".join(event_id.split("-")[:2])`, same rule as
`tp.events.artifact_stem`). Code updated in `municipal_boundaries` and
`inventory_catalog`; files `git mv`'d then regenerated at the new paths —
same counts (138 / 260 / 16027), pytest 31 green, ruff clean, geojson
re-parsed. WA's landslide_catalog is the sibling GLC worker's job.
