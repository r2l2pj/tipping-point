# U5 — gee/impact.py + gee/hand.py (port of 09_impact.js + 04_hand_vs_merit.js)

## Changed

- `gee/impact.py` (new) — `python -m gee.impact --case <id> [--dry-run|--tasks] [--metrics-path]`.
  - GPWv4 nearest epoch via sort on abs(date diff in years) → `gpw_year`;
    `people_critical` = `population_count` sum inside event-day
    `TPI_slide > 0.6` (cut = `threshold("tpi_classes")[-1]`) at scale 1000.
  - `night_light_drop_pct` = VNP46A2 `DNB_BRDF_Corrected_NTL` masked by
    `Mandatory_Quality_Flag == 0`, mean of 7 nights after vs 30 before, scale 500.
  - `people_flooded`/`flooded_km2`: wa_2025 only, gated on
    `ee.data.getAsset(<flood asset>)`; gate fail or non-wa →
    null + reason (`rs_2024`: "no NISAR coverage — event predates launch").
    When the gate opens, the light mask becomes critical∪flood (as in the JS).
  - L7: EE asset paths = `projects/<GEE_PROJECT>/assets/tipping-point/
    <case>/<name>` (`tpi_slide_series`, `flood_nisar`), and `_asset()` prefers
    `data/<case>/assets.json` (name→path map, e.g. `water_nisar`/`flood_nisar`
    keys) when the file exists — convention path otherwise.
  - L8: `collections` also records the concrete GEE asset ids actually read
    (`tpi_slide_series` always; `flood_nisar` only when the gate opened).
  - Pulls the pre/post NTL means locally as COGs — `img.getDownloadURL(
    {region, scale:500, format:"GEO_TIFF"})` → `requests.get` → unzip →
    `rasterio.shutil.copy(driver="COG")` (same mechanism as
    `live/build_static.py::_download_band`); files land at
    `data/<case>/cog/<caso>_night_lights_{pre,post}_<AAAAMMDD>.tif` via
    `tp.events.artifact_stem`. Replaced `Export.image.toDrive` (staged files
    in Drive, nothing fetched them — review blocker).
  - Merges via `tp.metrics.merge_metrics(case_id, ...)` — per L4 the helper
    resolves the arg; collections record GPW id+epoch and the VNP46A2 id.
- `gee/hand.py` (new) — `python -m gee.hand --case <id> [--dry-run|--tasks]`.
  - HAND proxy = NASADEM elevation − `focalMin(radius, 'meters')` for radii
    1000/1500/3000; per radius Pearson r on `sample(scale=90, numPixels=5000,
    seed=1)` vs MERIT `hnd` + IoU of the HAND<5 m zone (`And`/`Or` reduceRegion
    sum, scale 90). `hand_validation{radius:{r,iou}}` + `hand_best_radius_m` =
    argmax(r+iou) merged via `merge_metrics`; collections NASADEM + MERIT ids.
- `gee/tests/test_impact_hand.py` (new) — 10 tests, no ee/creds needed.
  - `sys.modules['ee']` is replaced with a chainable call recorder before the
    modules import (executes `.map()` lambdas so inner calls log too), then the
    original entry is restored so other test files see real ee. `gee.common` is
    stubbed in sys.modules only when U4's module is absent (it is on this
    branch); run-path names (`init`, `case_cfg`, `aoi_geom`, `event_dates`,
    `ASSET_ROOT`, `IMG_NASADEM`, `BAND_DEM`) are monkeypatched in a fixture so
    tests pass against the real module too.
  - Asserts: GPW sort-by-abs-year-diff + `.first()`, 30/7-night `advance`
    calls, `MQF==0`, scales 500/1000, the two COG export stems, focalMin radii
    {1000,1500,3000}, best-radius pick, null+reason merge when the flood gate
    fails (and opens when `getAsset` succeeds), assets.json preference +
    convention fallback, the getDownloadURL pull writing real COGs
    (LAYOUT=COG tag; the COG driver is write-only so rasterio reports
    GTiff on read), dry-run prints with no `init`.
- L4 applied: all `data/<event-id>/` literals → `data/<case>/`; merge target
  printed as `data/metrics.json[<case>]`, arg passed as the case key.

## Commands + results

- `$UV sync` — ok.
- `$UV run python -m pytest gee/tests pipeline/tests -q` — **44 passed**.
- `$UV run ruff check .` — **clean** (one `noqa: BLE001` on the flood-asset
  gate — any lookup failure intentionally closes the gate).
- `$UV run python -m gee.impact --case wa_2025 --dry-run` — prints computes +
  2 exports (`wa_2025_night_lights_{pre,post}_20251210.tif`), no EE init.
- `$UV run python -m gee.hand --case rs_2024 --dry-run` — prints the
  1000/1500/3000 m plan.

## Assumptions / notes for the lead

- `gee.common` absent on this branch: `from gee.common import ...` is wrapped
  in `except ModuleNotFoundError (name=='gee.common')` → names become None and
  only `--dry-run` works until U4 lands; `run()`/`--tasks` raise a clear error.
- Assumed signatures `case_cfg(case_id)`, `aoi_geom(case_id)`,
  `event_dates(case_id) -> [YYYY-MM-DD]`, and `ASSET_ROOT` =
  `projects/<GEE_PROJECT>/assets/tipping-point` (JS asset-namespace convention).
  If U4's differ, only these call sites need adjusting.
- L7 `<name>` under `tipping-point/<case>/` uses the short layer key
  (`flood_nisar`, `tpi_slide_series`), matching the assets.json key examples;
  flag if producers instead keep the `<case>_` prefix inside the subpath —
  `assets.json` resolves either way, the convention fallback assumes short
  names.
- `--tasks` = execute all GEE-side work (computes + the COG pull) and print,
  skipping the metrics merge; default = execute + merge + pull. hand.py has
  no pull step, so `--tasks` = computes only.
- `hand_best_radius_m` is an extra flat key beyond the pinned SCHEMA list
  (brief-mandated); `hand_validation` nests {radius:{r,iou}}.
- The JS `Export.table` CSV side-artifact was dropped — Python merges
  metrics.json directly.

## Post-rebase fix (cases[<cid>] metrics shape)

- `merge_metrics` now returns `{"cases": {<cid>: {...}}}`. Test reads moved to
  `cases[<cid>]`, including `_null_reasons`, `collections` and `hand_validation`.
- `impact` resolves `common.ASSET_ROOT` (a `{GEE_PROJECT}` template) via
  `_asset_root()`, and uses `tp.config.event_id` for the event id.
- Proof: `$UV run python -m pytest gee/tests pipeline/tests -q` → 199 passed;
  `$UV run ruff check .` → clean; both `--dry-run` plans print.

## Flood-source contract addition

- `gee/impact.py` emits flat `flood_source`: classifies the resolved flood
  asset id as `nisar` (NISAR), `opera` (OPERA/DSWx), or `observed` (other);
  emits null + reason when no flood mask is used. Existing flood asset gate
  is unchanged; this does not add an OPERA retrieval pipeline for RS.
- String results survive `run()` materialization without `.getInfo()`.
- `data/SCHEMA.md` documents the flat key and mock sources (WA=NISAR,
  RS=OPERA), distinct from actual runtime availability.
- Parameterized fake-mask tests assert the emitted/merged source for all
  three sources and null; a seeded `cases.rs_2024.flood_source='opera'`
  remains intact when the next impact run emits null.
- Commands: `$UV run python -m pytest gee/tests pipeline/tests -q` →
  **204 passed**; `$UV run ruff check .` → **clean**.
