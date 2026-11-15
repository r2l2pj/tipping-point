# Task: Now-job climatology at IMERG native 0.1° (lead-live unit L30-clim)

Worktree: ~/code/r2l2pj/tipping-point-data-wt/live-clim (branch unit/live-clim, = origin/main f91dcb5). Work ONLY here; never touch the shared checkout ~/code/r2l2pj/tipping-point-data. Report to lead-live: `hc send t17q "<two lines>"` when done or stuck. You may start one helper (depth 3) only for a diff review.

## Why
The Now job's climatology lives as 58 per-doy COGs (doy_284..341) on the 0.01° NOW grid (1150x1000, bilinear-upsampled from a 10 km GEE export, 9.3 MB each). IMERG is 0.1°, so that is 100x redundant, and doys outside 284-341 don't exist (nearest-doy substitution kicks in after ~7 Dec). Goal: the WHOLE year (366 doys) at native 0.1° in one small file (~20-25 MB), computed from NASA IMERG directly (L25: no GEE), cached in CI with actions/cache.

## Definition (must match live/build_static.py `_climatology` exactly in semantics)
For doy N on the leap-year calendar (date(2000,1,1)+N-1 -> month, day): for each year y in 2001..2023 and offset o in range(-30, 31, 3): end = date(y, month, day) + o days (Feb 29 in a non-leap year -> Mar 1, as ee.Date.fromYMD rolls over); sample = sum of daily rain over [end-3d, end) (the 3 days BEFORE end, end exclusive). 23*21 = 483 samples per pixel; p90/p95/p99 per pixel (numpy percentile, linear). Units mm.

## Source
IMERG Final daily V07 `GPM_3IMERGDF.07` via GES DISC OPeNDAP with bearer `EARTHDATA_TOKEN` (in `.env` of the shared checkout — `set -a; source ~/code/r2l2pj/tipping-point-data/.env; set +a`; never print it). Verified working:
  https://gpm1.gesdisc.eosdis.nasa.gov/opendap/GPM_L3/GPM_3IMERGDF.07/2001/01/3B-DAY.MS.MRG.3IMERG.20010101-S000000-E235959.V07B.nc4.dods?precipitation[0:0][1219:1320][559:675]
  (curl -L -H "Authorization: Bearer $EARTHDATA_TOKEN"; dims are [time][lon][lat]; lon centers -179.95+i*0.1, lat centers -89.95+j*0.1 ascending; that slice = 102 lon x 117 lat centers covering NOW_BOUNDS [-58,-34,-48,-22.5] padded by one cell; precipitation mm/day.) Check the file-name version suffix per year (V07B; confirm via the directory listing if a year 404s).
Each request takes ~7-8 s server-side, so: dates 2000-11-28 .. 2024-02-02 (~8470 days), threaded (start ~16 workers, back off on 429/5xx, retries), cache each day under data/raw/imerg_final_daily/ (gitignored, resumable; npy/npz per year is fine). Start the download FIRST and in the background, then write the rest while it runs. If you find a much faster subset route (e.g. Harmony, .dap.nc4) feel free, but don't burn >15 min exploring.

## Build
- New script `live/build_clim.py`: download/cache -> compute 366 x {p90,p95,p99} -> write `data/static/south_brazil/clim/clim_imerg_0p1.tif`: EPSG:4326, the padded 0.1° grid (north-up, rows descending), 1098 bands, band index (doy-1)*3 + {0:p90,1:p95,2:p99}, band descriptions like `doy001_p90`, float32 rounded to 0.1 mm, DEFLATE + PREDICTOR=3, INTERLEAVE=BAND, tiled. Tags: source/collection/years/window/method. Target <= 25 MB; if larger, store uint16 x10 with scale_factor 0.1 metadata and say so.
- gitignore that file (data/static is partly tracked in git; this file must never be committed).

## Runtime (live/static_fetch.py, live/run_now.py)
- static_fetch: climatology source order = local `clim_imerg_0p1.tif` -> download it whole from the static-v1 Release URL (STATIC_RELEASE_BASE) into that local path -> legacy per-doy COGs (current code path, keep it). Read only the 3 bands for each needed doy.
- Interpolate the 0.1° p90/p95/p99 to the NOW dst grid the SAME way IMERG obs are regridded in run_now (live/grid.regrid_da, xarray linear interp on cell centers) so R compares like with like; the 1-cell pad avoids NaN edges. Keep `clim_doy_used` recording; with 366 doys substitution should never fire.
- `.github/workflows/now.yml`: an actions/cache@v4 step (path = that tif, key e.g. `clim-imerg-0p1-v1`) before the run step, so CI downloads it at most once.
- Make sure publish_fallback.py does not ship this tif to the fallback branch (check its filters; exclude data/static/ clim cache if it would).
- Tests (hermetic, synthetic tiny rasters): percentile window/date logic incl. Feb 29 and year-boundary windows; band indexing; static_fetch reads the right 3 bands and interpolates. Full suite: `$HOME/.local/share/mise/installs/uv/0.13.8/uv-x86_64-unknown-linux-gnu/uv run pytest pipeline/tests live/tests ml/tests` must be green (in a worktree you may need PYTHONPATH=pipeline + `uv run --no-sync`).

## Verify (report these numbers)
1. For doys 284-341: new 0.1° (interpolated to the 0.01° grid) vs existing doy_NNN.tif (local in ~/code/r2l2pj/tipping-point-data/data/static/south_brazil/clim/ or the Release): per band median |diff| mm, p95 |diff|, Pearson r. Expect small differences (GEE percentile reducer is approximate; 10 km export + bilinear).
2. Today's TPI (doy 319, 2026-11-15): run the Now TPI with identical fetched inputs once with the legacy doy_319 COG and once with the new file; report % pixels with identical class, and Alto/Crítico km² for slide+flood both ways. Easiest: run live/run_now.py --local twice into two scratch out dirs (one forcing the legacy path, e.g. via an env/flag you add), or reuse its functions in a small script. Do not write into the shared checkout's data/.
3. Upload the file to the Release: `gh release upload static-v1 data/static/south_brazil/clim/clim_imerg_0p1.tif -R r2l2pj/tipping-point-data` (do NOT delete or clobber existing doy_* assets), then confirm the runtime fetch works with the local copy removed.
4. Commit on unit/live-clim with explicit `git add <paths>` (message ends with "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"). Do NOT push to main — lead-live merges after review. Write your report to .herdr/implement-clim01.md in the worktree.

Ask lead-live before adding dependencies. No credentials in files or logs.
