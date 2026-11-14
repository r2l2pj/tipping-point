# FALLBACK2 — L10 rework: no Cloud Storage, local data/ writes

## Changed

- `pipeline/tp/fallback.py` — removed all GCS: no `google.cloud.storage`
  import, no `staging_bucket()`, no `Export.image.toCloudStorage`, no
  `--prefix`/staging keys, no `upload()`, no `_storage()`. `plan()`/
  `now_plan()` now return data-root-relative POSIX paths (manifest-last
  order preserved). `render_now()` writes everything locally under `data/`:
  PNGs via `getThumbURL` (EPSG:3857, dims 2048), JSON via
  `reduceRegion().getInfo()`, critical_areas.geojson via
  `reduceToVectors().getInfo()` on d0/d1 class grids (class stack starts at
  TPI>=0.4 = `tpi_classes[0]`). COGs use `image.getDownloadURL`
  (EPSG:4326 GeoTIFF) only.
  - 10MB cap method: `_download(url, limit)` streams the response — checks
    `Content-Length` first, and aborts mid-stream if the body still exceeds
    the cap; over-cap → returns None → file skipped with a logged reason on
    stderr (`fallback: skip <name>: COG over 10485760-byte cap`). Zip
    payloads are unzipped to the inner `.tif` before writing.
  - Kept: producer files never regenerated (`path.is_file()` skips),
    last-good semantics, missing-required-input errors, named-band
    selection (TPI_slide/TPI_flood/R/S), required d0/d1 layer check.
- `tools/export_fallback.py` — dropped `--bucket`/`--prefix`/`upload`; `--now`
  renders via `render_now` then prints the data-relative list; `--case`
  verifies and prints. `--dry-run` stays fully offline (no `ee`/`fetch`
  import; missing renders are still listed from `now_plan`).
- `pipeline/tests/test_fallback.py` — rewritten: no storage client anywhere;
  asserts files land under `data/` in manifest-last order; 10MB cap test
  (15MB fake COG → skipped, logged); zip-extraction test; offline dry-run
  import guard extended to `ee`/`gee`/`fetch`; `--bucket` CLI rejection test.
- `data/SCHEMA.md` — new "Fallback branch (no GCS)" section documenting the
  orphan `fallback` branch layout and
  `DATA_BASE_URL=https://raw.githubusercontent.com/r2l2pj/tipping-point-data/fallback/data/`.
- `README.md` — dropped `NOW_GCS_BUCKET`; documents the local-render +
  orphan-branch publish flow.

## Commands run

- `uv run pytest pipeline/tests/test_fallback.py -q` → 29 passed
- `uv run pytest pipeline/tests gee/tests -q` → 426 passed (no other-lead
  failures observed)
- `uv run ruff check pipeline/tp/fallback.py tools/export_fallback.py pipeline/tests/test_fallback.py` → clean
- `env -u GOOGLE_APPLICATION_CREDENTIALS -u GCP_SA_KEY -u GEE_PROJECT uv run
  python tools/export_fallback.py --case rs_2024 --root <tmp> --dry-run` →
  exit 0, printed planned file list
- `grep` for `gs://|google.cloud.storage|toCloudStorage|bucket` in the three
  reworked files → only the intentional test/comment mentions
