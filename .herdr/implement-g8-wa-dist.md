# G8 — WA 2025 landslide scar CANDIDATES from OPERA DIST-ALERT-HLS

## Result

`--case wa_2025` run over bbox [-122.6, 47.0, -121.5, 48.5]
(data/events.json wa-2025-12 = config.yaml cases.wa_2025.aoi), window
2025-12-05 .. 2026-02-28 (the storm plus the first cloud-free looks,
~12 weeks — lead-analysis narrowed an earlier 2025-12-05..2026-11-05 run).

- 158 granules acquired in window, 4 MGRS tiles, all readable (0 failed):
  T10TET, T10TFT, T10UEU, T10UFU (newest granule acquired <=2026-02-28
  read per tile; first-detection dates windowed to the same range).
- **176 candidates, 1.246 km², people = 13.8** (all 176 got people +
  municipality). First detections: Dec 2025: 32 (0.272 km²),
  Jan 2026: 108 (0.793 km²), Feb 2026: 36 (0.181 km²).
- Excluded: 23.85 km² in-window >=50 % vegetation loss total; 21.17 km²
  dropped by the steep gate (the mask only covers the AOI, so out-of-AOI
  loss counts as "flat"); 0.003 km² cropland/water; 1216 polygons < 3 px.
- Inventory check vs the 15 undated WGS points: **hit 0 % at 100 m and
  500 m; precision proxy 0 %**. Nearest candidate is 1.4–3.5 km from each
  point — genuinely no detection, not a CRS bug (candidate centroids
  verified inside the AOI, municipality joins resolve). Heavy December
  cloud + the >=50 %-cover-anomaly threshold plausibly missed the
  catalogued slides; candidates are never confirmed.
- metrics.json cases.wa_2025 got scar_source/window/candidates_n/
  candidates_km2/people/hit_100m/hit_500m/precision_500m/inventory_n/method;
  no null scar keys, so no _null_reasons entries were needed for them.

## Gates (the Brazil statics don't cover WA — manifests confirm bbox
[-58,-34,-48,-22.5], so WA versions were built into gitignored
`static/cache/wa/`)

- **steep**: `static/cache/wa/steep15_wa.tif` — derived from
  `data/wa_2025/cog/wa_2025_t_slide_20251210.tif` (uint8: 1 where T_slide>0,
  0 finite, 255 nodata). Equivalence: T_slide =
  `slope.unitScale(15, 30).clamp(0, 1)` (gee/tpi.py), so T_slide>0 <=>
  slope>15 deg on GEE NASADEM, exported at 90 m. Difference vs the 1"
  rs_2024 mask: strictly `>` rather than `>=` 15 deg (measure-zero) and 90 m
  source grid. Chosen over a fresh NASADEM pull per the task's allowance.
  644,664 steep px inside the AOI.
- **land cover**: `static/cache/wa/lc_type1_2025.tif` — MCD12Q1.061 2025
  tile h09v04 (fully covers the AOI; single MODIS tile suffices),
  downloaded from LP DAAC with EARTHDATA_TOKEN and converted with
  `tools/convert_mcd12q1.py --bbox` (new flag; default unchanged =
  domains.now). pyhdf ran in a uv ephemeral env (`--no-project`, py3.12 —
  cp315 has no wheel); no project dependency added.
- **people**: `data/wa_2025/cog/wa_2025_pop_gpw_20251210.tif` (GPW count,
  ~1 km, EPSG:4326). `attach_people_and_municipality` already converts
  count -> density -> people; its `v < 0` guard skips the -FLT_MAX fill.
- **municipality**: `data/wa_2025/geojson/municipal_boundaries.geojson`
  already exists — US Census TIGER county subdivisions (260 features).
  `name` only; `code`/`uf` stay absent (IBGE fields don't apply).

## Changed files

- `pipeline/scripts/16_response_dist.py`: added `wa_2025` to CASE_WINDOWS
  (2025-12-05..2026-11-05), CASE_GATES + CASE_STEEP per-case maps, CLI
  flags now optional with per-case fallback, `_utm_epsg(bounds)` so
  hit_rates uses UTM 10N (32610) for WA / 22S for RS, docstring updated.
- `tools/convert_mcd12q1.py`: `--bbox W S E N` arg.
- `data/wa_2025/geojson/landslide_candidates.geojson`: new (508 KB, compact
  pass same rules as rs_2024).
- `data/metrics.json`: scar_* keys under cases.wa_2025.
- `data/manifest.json`: rebuilt via tools/build_manifest.py —
  products.landslide_candidates set, wa_2025 _null_reasons cleared.
- `data/wa_2025/candidates_status.json`: now points at the produced
  geojson, records window/tiles and the 0/15 inventory overlap.
- `data/SCHEMA.md`: wa_2025 replay bullet under "Landslide scar candidates".
- `README.md`: one row — OPERA DIST-ALERT-HLS product/role.

## Commands

```sh
# steep mask (T_slide>0 -> uint8 1/0/255)
PYTHONPATH=.:pipeline $SHARED_VENV/bin/python - <<'EOF'  # writes static/cache/wa/steep15_wa.tif
# MCD12Q1 h09v04 HDF4 -> LC_Type1 COG
$UV run --no-project --python 3.12 --with pyhdf --with rasterio --with numpy --with pyyaml \
  python tools/convert_mcd12q1.py --src static/cache/wa/mcd12q1 \
  --out static/cache/wa/lc_type1_2025.tif --bbox -122.62 46.98 -121.48 48.52
# the run (EARTHDATA_TOKEN exported from shared .env, never printed)
PYTHONPATH=.:pipeline $SHARED_VENV/bin/python pipeline/scripts/16_response_dist.py --case wa_2025
PYTHONPATH=.:pipeline $SHARED_VENV/bin/python tools/build_manifest.py
PYTHONPATH=$PWD:$PWD/pipeline $SHARED_VENV/bin/python -m pytest -q pipeline/tests live/tests ml/tests
  -> 749 passed, 0 failed
```

Note: the prescribed `uv run --no-sync pytest` creates a bare `.venv`
(no deps, `uv sync` forbidden) so tests ran via the shared checkout's
venv interpreter with PYTHONPATH pointed at this worktree — same pattern
prior implement agents used here (read-only use of the shared checkout;
nothing in it modified). `ruff check` flags 2 pre-existing RUF100 on
`# noqa: E402` comments — present on HEAD too, left as-is.

DIST granule reads worked via the LP DAAC 303 -> pre-signed URL path;
no tile errors.
