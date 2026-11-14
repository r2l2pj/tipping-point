# U11b — MODIS NRT flood fetch + compare (RS supplement)

Branch `unit/u11b`. Files touched (all new):

- `pipeline/tp/modis_nrt.py` — earthaccess search/download of the MCDWD
  product, AOI clip, water classification, and `compare_water()` metrics.
- `pipeline/scripts/11_modis_nrt.py` — CLI: `--event` (required), `--ref`
  (optional, falls back to the event's `water_nisar` COG when present),
  `--start`/`--end` (default = event window), `--dry-run` (prints CMR
  params, no creds).
- `pipeline/tests/test_modis_nrt.py` — 12 tests.
- `.herdr/implement-u11b.md` — this report.

## Decisions

- **short_name tolerance**: the plan default `MCDWD_L3_FWD_NRT` is NOT a
  real CMR name. Verified real names (Earthdata/LANCE, user guide Rev F):
  `MCDWD_L3_F1C_NRT` (1-day GeoTIFF, cloud-shadow screened — preferred),
  `_F1_`, `_F2_`, `_F3_` GeoTIFFs, and `MCDWD_L3_NRT` (4-band HDF bundle).
  `search()` keeps FWD as the requested default then tries each verified
  name, returning the first collection with granules; `search_params()`
  exposes the candidate list (what `--dry-run` prints).
- **Lazy earthaccess**: imported inside `search()`/`fetch_nrt()` only;
  `earthaccess.login()` (EDL env vars / `~/.netrc`) runs at call time.
  `--dry-run` never imports it.
- **Crop**: `clip_to_aoi()` clips GeoTIFFs to the events/*.json
  `aoi.bbox` (EPSG:4326) via `rio.clip_box`; unreadable/non-TIFF files
  (the HDF bundle) pass through unclipped.
- **MCDWD classes** (user guide): 0 no water, 1 surface water,
  2 recurring flood, 3 flood; >=250 = fill/flag, excluded from the
  denominator (`classify()`).
- **`compare_water(nrt_mask, ref_raster)`**: reprojects the NRT mask to
  the ref grid (`reproject_match`, nearest), emits flat keys
  `modis_nrt_agreement_pct`, `modis_nrt_iou`, `modis_nrt_f1`,
  `modis_nrt_water_px`, `modis_nrt_ref_water_px`, `modis_nrt_valid_px`,
  `modis_nrt_scene_date` (parsed from `AYYYYDOY` in the filename),
  `modis_nrt_granule`, `modis_nrt_reason`. Any missing/unreadable input
  -> nulls + a `modis_nrt_reason` string; never raises. Scene date and
  granule are still populated when only the reference is missing.
- The script prints `{"event": ..., **metrics}` as JSON to stdout for a
  later metrics merge; `pick_scene()` chooses the downloaded scene
  nearest the event `reference_date`.

## Commands run

- `uv sync` (mise shim broken — used
  `$HOME/.local/share/mise/installs/uv/0.13.8/uv-x86_64-unknown-linux-gnu/uv`)
  — env already in sync.
- `uv run pytest pipeline/tests live/tests ml/tests -q` — **58 passed**
  (46 baseline + 12 new; 9 pre-existing warnings).
- `uv run ruff check live/ ml/ pipeline/` — **all checks passed**
  (ruff 0.17.5 defaults flag blind `except`; used targeted
  `# noqa: BLE001` with justification, matching `ml/evaluate.py`).
- `uv run python pipeline/scripts/11_modis_nrt.py --event wa-2025-12 --dry-run`
  — prints short-name candidates + bbox + event window; same for
  `rs-2024-05` with `--start/--end` overrides.

No new dependencies. Not tested against live CMR/EDL (no creds in this
env); the network path is exercised only via unit-level seams.
