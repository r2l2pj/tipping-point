# B4 — NISAR polygon table assets in GEE + OPERA catalog ids (L10/L10b)

Worktree `tipping-point-data-wt/b4`, branch `unit/b4`, commit eed51a9
(base e1d8f18 main).

## Changed

- `gee/opera.py` — rewritten (ported from unit/operacat, adapted to main):
  - Catalog ids are now the EE catalog collections `OPERA/DSWX/L3_V1/HLS`
    and `OPERA/DSWX/L3_V1/S1` (ruling L10b) — the Earthdata short names
    `OPERA_L3_DSWX-HLS_V1`/`OPERA_L3_DSWX-S1_V1` are no longer used as EE ids
    (they remain only as the `opera_dswx` label via `tp.validation.opera_product`).
  - Band `WTR_Water_classification` for both products; water classes HLS
    {1,2}, S1 {1,3} (S1 class 3 = inundated vegetation); cloud 253, nodata
    255. `cloud_pct` always comes from the HLS collection (S1 is radar).
  - NISAR input: prefers the `flood_nisar*` **table** asset (FeatureCollection)
    from `data/<case>/assets.json` (manifest rows with kind table, or legacy
    flat map entries containing "flood"/"polygon"), rasterized in GEE via
    `ee.Image.constant(0).byte().paint(ee.FeatureCollection(id), 1)` — never
    `ee.Image()` on the permanent-water mask or on `local-only` image rows
    (which cannot exist under L10; raster fallback retained only behind a
    real `ee.data.getAsset` existence check).
  - Reads the ingest manifest schema (`{"assets": [{name, gee_id, kind,
    status}]}`) as well as the legacy flat name→path map.
  - `confirm_catalog_schema()` verifies `WTR_Water_classification` exists in
    both catalog collections after EE init (getInfo on `bandNames()` only).
  - Empty catalog window → `ee.Algorithms.If` empty-image guard; null reasons
    for iou/f1/cloud_pct when coverage is absent.
  - Kept main's guarded `gee.common` import + `_common_ready()` so `--dry-run`
    works on branches without U4; `_asset_root()` prints `<GEE_PROJECT>` when
    the env is unset.
- `gee/impact.py`:
  - `_flood_mask` now rasterizes the flood polygon FeatureCollection
    (`Image.paint`) instead of `ee.Image(flood_nisar)` on a raster that
    cannot exist under L10.
  - New `_assets_map()` reads both the ingest manifest and the legacy flat
    map; `_asset()` (tpi_slide_series etc.) and `_flood_asset()` use it.
    `_flood_asset` prefers manifest table rows whose name contains "flood",
    then any mapped "flood" name, then the convention path.
  - Null reason text now says "run 06_polygonize.py and
    tools/ingest_assets.py first" (was "`earthengine upload` the COG").
- `gee/tests/test_opera.py` — replaced with the operacat suite (recording
  Fake `ee`, monkeypatched `opera.ee`), plus a B4-specific test asserting a
  `local-only` manifest image row never reaches `ee.Image()`.

Not changed (out of scope): `pipeline/scripts/04_validation.py` still accepts
`--auto` (Earthdata fetch) — L10b says "keep the code"; `tools/ingest_assets.py`
already ingests `{flood,water}_nisar_<date>` geojson as table assets.

## Commands run + results

- `uv run --python 3.12 python -m pytest gee/tests pipeline/tests -x -q`
  → **453 passed** (worktree `.venv`; uv shim broken by mise, used
  `~/.local/share/mise/installs/uv/0.13.8/.../uv` directly).
- `uv run --python 3.12 ruff check .` → clean (after wrapping an implicit
  f-string concat in `plan()` per ruff's unsafe-fix hint).
- `python -m gee.opera --case wa_2025 --dry-run` → prints catalog ids
  `OPERA/DSWX/L3_V1/S1` + `…/HLS`, band `WTR_Water_classification`, water
  `(1, 3)`, cloud 253/nodata 255, flood-polygons FeatureCollection — no EE
  calls.

## Evidence notes

- No real GEE calls anywhere (fake-ee tests only); no `.env` touched;
  nothing pushed.
- Commit: `eed51a95536940d5459a0dd9a85b8e893d39eaad` on `unit/b4`
  (3 files: gee/opera.py, gee/impact.py, gee/tests/test_opera.py).
