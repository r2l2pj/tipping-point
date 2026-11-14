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

## Commands run

- `$UV run pytest live/tests/ -q` -> 73 passed (21 warnings, pre-existing
  NotGeoreferenced/PendingDeprecation warnings)
- `$UV run ruff check live/` -> All checks passed!
- `git commit` on `unit/u17`: `8e7ac0c U17: build_static raw-TIFF
  getDownloadURL + ee_init adoption` (2 files, +67/-4)

## Notes

- Did not create `gee/common.py` (owned by lead-analysis).
- Did not push.
