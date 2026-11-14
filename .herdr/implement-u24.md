# U24 — GEE IMERG_V07 stand-in for IMERG Early

Branch `unit/u24`, commit `b64723e` (not pushed). Scope: `live/fetch.py`,
`live/run_now.py`, `live/tests/test_fetch.py`. `test_run_now.py` untouched —
existing tests still pass unchanged (default `--imer-source gesdisc`).

## What changed

- `live/fetch.py`
  - `fetch_imerg_gee_standin(bounds, days=7)`: lazy `import ee`, init via
    `from gee.common import ee_init; ee_init()` (return ignored). Collection
    `NASA/GPM_L3/IMERG_V07`, band `precipitation` (mm/h per half-hour step).
    Latest image from `aggregate_max("system:time_start")`; iterates the last
    `days` UTC days ending at that day, per-day
    `filterDate(day, day+1).sum().multiply(0.5)` — same 0.5 factor as
    `build_static._climatology.accum`. Each day downloaded as a single-region
    GeoTIFF via `getDownloadURL` at `scale=10_000` (~0.1 deg, well under the
    EE memory limit). Returns `(xarray(time,lat,lon) mm/day, product_id)` with
    `product_id = "gee:NASA/GPM_L3/IMERG_V07 latest=<iso>"`.
  - `_download_grid_geotiff(url)`: requests.get → unzip if needed → rasterio
    masked read → NaN; lat/lon cell centres from the GeoTIFF transform.
  - `fetch_imerg_daily` (a.k.a. the IMERG Early path) unchanged.
- `live/run_now.py`
  - New `--imer-source gesdisc|gee` flag (default `gesdisc`; daily workflow
    unaffected). `gee` calls the stand-in and records
    `imerg_source="gee-imer-v07-standin"` in summary.json (`"gesdisc-early"`
    otherwise); product_id still flows into `data_dates.imerg`.
  - On a GES DISC failure containing "403"/"eula", prints a stderr hint about
    `--imer-source gee` and re-raises (no silent fallback).
  - Module docstring documents the stand-in is temporary until EULA accepted.
- `live/tests/test_fetch.py` (appended, offline)
  - `test_fetch_imerg_gee_standin`: stub `ee` module + `gee.common` in
    sys.modules, canned per-day GeoTIFFs via stubbed `requests.get`
    (rasterio.MemoryFile). Asserts shape (3,4,5), day ordering, the 0.5
    multiply factor recorded per day, masked→NaN, scale/crs params, and the
    `gee:` product_id with `latest=2026-11-13T05:30:00Z`.
  - `test_run_now_imer_source_flag`: parse_args accepts `--imer-source gee`,
    defaults to `gesdisc`.

## Commands run

- `$UV run pytest live/tests/ -q` → **87 passed** (12.9s)
- `$UV run ruff check live/` → **All checks passed!**
- `$UV run python live/run_now.py --help` → shows `--imer-source {gesdisc,gee}`

No real EE/GES DISC calls were made (no credentials in env); the stand-in is
verified end-to-end only against stubs. Real `--local --imer-source gee` proof
still requires `GCP_SA_KEY`/`GEE_PROJECT` at run time.

## Add-on (commit 11a0fe5): _case_id normalization

NOTE: `_case_id` did not exist in this worktree (nor on any local branch) —
`_clim_cases` passed raw event ids through. Implemented it per the lead's
description: `live/build_static.py::_case_id(raw_id)` resolves via
`tp.config.case_id` (`rs-2024-05` → `rs_2024`, verified in the uv env);
catches only `(ImportError, KeyError)` (no BLE001) and falls back to
`"_".join(raw.split("-")[:-1])` (`x-case` → `x`). `_clim_cases` now emits
`"id": _case_id(case["id"])`, so asset paths become `static/rs_2024/clim_rain`.
Test `test_clim_cases_reads_window_doys` updated to expect normalized ids
`["x", "y", "z"]`. Verify: 87 passed, ruff clean.
