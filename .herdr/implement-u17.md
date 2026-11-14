# U17 — build_static raw-TIFF getDownloadURL + ee_init adoption

## Changed (only files in scope)

- `live/build_static.py`
  - `_download_band`: after `requests.get(...).content`, checks magic bytes.
    `b'PK\x03\x04'` -> existing zip extract-and-open path; otherwise
    `rasterio.open(io.BytesIO(data))` directly (raw GeoTIFF, which is what
    getDownloadURL actually returns today). Same URL params, same
    `src.read(1).astype(np.float64)` return.
  - `_ee_init`: now tries `from gee.common import ee_init; return ee_init()`
    first, falling back on ImportError to the unchanged
    GCP_SA_KEY -> temp file -> ServiceAccountCredentials path.
    Note: current `gee/common.py` on this branch exposes `init()`, not
    `ee_init` — the ImportError fallback covers that; the try-import lands
    as soon as lead-analysis adds the alias.

- `live/tests/test_build_static.py`
  - Added `_DlImg` fake (select().getDownloadURL() -> fake URL),
    `_tif_bytes` (writes a real 3x4 float64 GeoTIFF via rasterio MemoryFile),
    `_run_download` (monkeypatch requests.get -> SimpleNamespace(content=...)).
  - `test_download_band_accepts_zip_payload`: zipped .tif returns the array.
  - `test_download_band_accepts_raw_tiff_payload`: raw TIFF bytes return the
    array. Both assert float64 dtype and exact array equality.
  - `test_build_assets_clim_windows_are_per_doy`: spies on
    `ee.Date.fromYMD`, asserts every exported doy's (month, day) is
    sampled across every year in YEARS (superseded by subset — see below).

## Scope refinement (t6g, overrides float32 choice): per-case collections

Final layout (conductor-approved):
- `static/<case_id>/clim_rain` per events/*.json case, region = aoi.bbox,
  doys = doy(window.start)..doy(window.end) inclusive, top-level `window`
  field used as-is, wrap-aware across the year boundary
  (`_doys_between` — wa-2025-12 Nov1..Jan31 wraps 306..366 + 1..31).
- `static/<STATIC_DOMAIN>/clim_rain` over NOW_BOUNDS, rolling now window
  = dt.date.today() +-35d (`_doys_around`, also wrap-aware).
- Bands int16 = millimetres*10: `img.multiply(10).round().toInt16()`
  with image property scale=10 (divide-by-10 documented in docstring;
  now_tpi reads config.climatology.scale). FLAGGED per conductor: int16
  was the approved override, not float32.
- Descriptions prefixed by domain: `<case_id>_clim_rain_doy_NNN` /
  `south_brazil_clim_rain_doy_NNN`.
- Removed _clim_doys/EVENT_PAD_DAYS; added _clim_cases, _doys_between.
  ~205 clim images total today (42 rs + 92 wa + 71 now) + slope + hand.

Tests:
- `test_clim_cases_reads_window_doys` — tmp events dir, contiguous +
  year-wrap + single-day windows.
- `test_build_assets_exports_clim_collections_and_terrain` — collection
  creation, assetIds/regions/descriptions per case, doy+scale=10 props,
  `.toInt16()` source check, terrain unchanged.
- `test_build_assets_clim_windows_are_per_doy` — fromYMD spy over the
  exported doy set (n_clim * 23 * 21 calls, every doy's month/day).
- `test_build_assets_dry_run_starts_nothing` — n_clim+2 tasks, nothing
  started/created.

## Scope addition (t6g): per-doy clim_rain windows

Read `gee/01_tpi_index.js` lines 31-39: for a reference (month, day) the
climatology samples `ee.Date.fromYMD(y, month, day).advance(o, 'day')`,
y in 2001-2023, o in -30..30 step 3, each a 3-day IMERG sum ending there
— same month/day-of-month per year.

`live/build_static.py` changes:
- Split `_climatology_for_month` into shared `_climatology(ee, month, day)`;
  `_climatology_for_month` now calls it with day=15 (npz path UNCHANGED,
  same output as before — mid-month windows).
- New `_climatology_for_doy(ee, doy)`: maps doy -> (month, day) of the
  leap-year 2000 calendar, then `_climatology`.
- `build_assets`: dropped the `month_imgs` reuse; each of the 366 doy
  exports now computes its own +-30d-window percentile image. Still 366
  images, `doy` property, bands p90/p95/p99, same scale/assetIds.

## Commands run

- `$UV run pytest live/tests/ -q` -> 75 passed (21 warnings, pre-existing
  NotGeoreferenced/PendingDeprecation warnings)
- `$UV run ruff check live/` -> All checks passed!
- Commits on `unit/u17`:
  - `8e7ac0c` U17: build_static raw-TIFF getDownloadURL + ee_init adoption
  - `cdf7190` U17: unit report
  - `2339d60` U17: per-doy climatology windows in build_assets
  - `7191e0b` U17: per-case clim_rain collections, int16 mm*10

## Notes

- Did not create `gee/common.py` (owned by lead-analysis).
- Did not push.
