# L30-clim — Now-job climatology at IMERG native 0.1° (clim01)

Branch `unit/live-clim`, rebased on origin/main 5faf2bf (723 tests pass there). Task: `.herdr/task-clim01.md`.

## Result

- `data/static/south_brazil/clim/clim_imerg_0p1.tif` — **23.6 MB**, float32 (no uint16 needed),
  1098 bands (366 doys × p90/p95/p99), band `(doy-1)*3+{0,1,2}`, descriptions `doy001_p90`…`doy366_p99`,
  EPSG:4326, 102×117 px at 0.1° (NOW_BOUNDS padded one cell: lon −58.1…−47.9, lat −34.1…−22.4), north-up,
  DEFLATE + PREDICTOR=3, INTERLEAVE=BAND, tiled 128. Tags: source/collection/years/window/method/units/grid.
  No NaN. sha256 `ed375cc2be20500c9f9ed2a51d7729a67dd54f2651fe7d1ce2c86018f41e482d`.
- Uploaded to Release `static-v1` as `clim_imerg_0p1.tif` (the 58 `doy_*` assets are untouched; total 59 clim assets).
  The runtime fetch was checked with the local copy deleted: static_fetch downloaded it from the Release (sha identical),
  `clim_source=imerg-final-0p1`, doys 320–323 read.
- Gitignored; publish_fallback already excludes any `static/` dir on main (a test now also covers this file).

## The legacy doy_NNN.tif COGs are spatially scrambled (prod R input)

Each legacy COG (1150 rows) holds **two vertically squashed full-domain copies**:
fine rows 0–654 and 655–1149 each span lat −22.5…−34 (best split by correlation: row 655, r = 0.996).
Columns are fine. This is probably the tile stitching in the tiled GEE download (U22).
The new file's geolocation is confirmed from the OPeNDAP response's own lon/lat maps (lon −58.05…−47.95, lat −34.05…−22.45 ascending).

## Verify 1 — new (interpolated to the 0.01° grid) vs legacy, doys 284–341 (all pixels pooled)

| band | as-is: median abs diff | p95 abs diff | r | legacy rows remapped: median abs diff | p95 abs diff | r | bias new−legacy |
|---|---|---|---|---|---|---|---|
| p90 | 5.55 mm | 23.74 mm | 0.347 | **0.50 mm** | 1.96 mm | **0.995** | −0.11 mm |
| p95 | 7.57 mm | 30.11 mm | 0.362 | **0.70 mm** | 2.91 mm | **0.994** | −0.11 mm |
| p99 | 16.79 mm | 53.08 mm | 0.296 | **3.95 mm** | 14.79 mm | **0.973** | −4.82 mm |

The p99 bias most likely comes from GEE's approximate percentile reducer plus the 10 km export, not from the data.
Adjacent-doy self-consistency: r = 0.976 for new 320 vs 321, 0.983 for legacy.

## Verify 2 — today's Now TPI (2026-11-15), identical fetched inputs, rebased code

`run_now.run` was called three times in one process with the fetches memoized, so IMERG Early, SMAP and GFS are identical across variants; terrain is shared as well.
- A = legacy COG keyed by `tm_yday` (what main read: doy_319 = Nov 14 climatology)
- B = legacy COG at the leap-calendar doy (doy_320)
- C = new file (doy 320)

| variant | slide Alto / Crítico km² | flood Alto / Crítico km² | Alto+Crítico slide / flood | checks.clim_source_ok / passed |
|---|---|---|---|---|
| A legacy, tm_yday | 0 / 0 | 46 / 5 | **0 / 50** | False / False |
| B legacy, leap doy | 8 / 0 | 49 / 7 | 8 / 56 | False / False |
| C new 0.1° | 8 / 0 | 48 / 7 | **8 / 55** | **True / True** |

Identical class, over 958,218 valid pixels: C vs A slide 99.99 %, flood 100.00 %; C vs B 100.00 % / 100.00 %; B vs A 99.99 % / 99.99 %.
Today is a dry day, so R is small almost everywhere and the scrambled climatology barely changes the classes.
On a wet day it would move whole latitude bands. In run B, doys 322/323 were substituted by 321 (a transient Release read of the legacy COG); today's doy 320 was read exactly.

## Code changes

- `live/build_clim.py` (new) does three things:
  - Downloads IMERG Final daily V07 (`GPM_3IMERGDF.07`) through GES DISC OPeNDAP with the bearer token: one `.dods` subset per day, parsed from DAP2 XDR, falling back to the month listing when a 404 suggests a different version suffix, with backoff and retries.
  - Caches each day as `data/raw/imerg_final_daily/YYYY/YYYYMMDD.npy`; the cache is resumable and gitignored.
  - Computes p90/p95/p99 with `_climatology` semantics: leap-calendar doy, years 2001–2023, offsets −30..30 step 3, Feb 29 → Mar 1, sum over [end−3d, end), `numpy.nanpercentile` linear.
  It also has a `--reverse` flag to run a second downloader newest-first.
- `live/static_fetch.py` reads the climatology in this order: the local tif, then the whole file downloaded from the Release, then the legacy per-doy COGs.
  - Only the 3 bands of each needed doy are read, and they are interpolated with `grid.regrid_da` (linear), the same way the IMERG observations are regridded.
  - `static["clim_source"]` is `imerg-final-0p1` or `legacy-doy-cogs` and equals main's `run_now.CLIM_SOURCE_OK`. If any doy falls back to the legacy COGs, the whole run is marked legacy.
  - `NOW_CLIM_SOURCE=legacy` forces the legacy path (for verification only).
  - **Leap-calendar doy fix:** `leap_doy()` replaces `tm_yday`. The old lookup was off by one after Feb in non-leap years: 2026-11-15 read doy_319, which is Nov 14.
- `live/run_now.py` passes `--bounds` to load_static and adds `clim_source` to `summary.data_dates`. `checks.clim_source_ok` is main's (87654d5).
- `.github/workflows/now.yml` gets an `actions/cache@v4` step (path is the tif, key `clim-imerg-0p1-v1`), placed after the static response-input caches and before the run step.
- `.gitignore` covers the tif and `*.tmp.tif`.
- Tests:
  - `live/tests/test_build_clim.py` (new) covers the window/date logic, Feb 29, year-boundary windows, needed-day span, band indexing, the native grid matching the verified slice, the dods parser, percentiles against brute force, and the tif layout.
  - `test_static_fetch.py` covers reading the right 3 bands plus interpolation, source order and the legacy env, the Release download and failure cleanup, the leap-doy calendar, and that `CLIM_SOURCE_NEW == run_now.CLIM_SOURCE_OK`.
  - `test_publish_fallback.py` checks that the clim tif is never app-facing.
  - `test_workflows.py` checks that the cache step comes before the run.

## Commands / results

- Download: 8,462 days (2000-11-29..2024-01-29), 16 + 12 workers, ~2.2 days/s combined (~65 min). 0 failures, 0 NaN; 400 MB cache.
- Build: `PYTHONPATH=pipeline:. .venv/bin/python live/build_clim.py` took 1 min 56 s and wrote 23.6 MB.
- `PYTHONPATH=pipeline .venv/bin/python -m pytest -q pipeline/tests live/tests ml/tests` → **711 passed**.
- `ruff check` is clean on new and changed files. The remaining ruff errors in run_now and older tests were already on main.
- Verify scripts are in the session scratchpad: `verify_clim.py`, `verify_clim_remap.py`, `verify_tpi.py`.

## Follow-ups (not done)

- The legacy `doy_*` Release assets are scrambled. Delete or replace them once everything reads the new file; I did not touch them.
- The case clim_rain COGs and any other output of the tiled GEE download (U22) should be checked for the same stacking bug.
