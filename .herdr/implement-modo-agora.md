# implement-modo-agora — report

Implemented the "Modo Agora" live pipeline per `.crew/briefs/modo-agora.md` and
`.crew/tp3.txt` ~lines 1094-1330 (doc v3, steps 1-8). Scope respected: only
`live/`, `static/`, `now/`, `.github/`, `tools/` touched. No git commands run.
No credentials used — all auth reads env at runtime.

## Files created

- `live/config.py` — shared constants: NOW bounds (-58,-34,-48,-22.5), ~1 km
  grid (0.01°), labels/palette; all thresholds read from `config.yaml`
  (rain percentiles, soil range, slope range, HAND range, TPI class cuts,
  min_blob_pixels), so TPI math mirrors `gee/01_tpi_index.js`.
- `live/tpi.py` — pure-numpy R/S/TPI: `unit_scale`, `rain_score` (same .where()
  chain), `soil_score`, `terrain_slide`, `terrain_flood` (slope>5°→0), `tpi`,
  `classify` (strict `>` cuts), `accumulate3` (rolling 3-day), `projected_rain3`
  (obs+forecast blend for D+1..D+3).
- `live/grid.py` — destination grid, rasterio transform, `regrid_da` (xarray
  interp) and `regrid_points` (scipy griddata for SMAP EASE grid), pixel areas.
- `live/fetch.py` — `fetch_imerg_daily` (GPM_3IMERGHHE via earthaccess),
  `fetch_smap_l4` (SPL4SMGP/008, Geophysical_Data group),
  `fetch_geosfp_daily` (GMAO OPeNDAP `tavg1_2d_flx_Nx.latest`, PRECTOT→mm/day,
  D+1..D+3), `nisar_recent_passes` (asf_search geo_search, metadata only).
  EDL_USER/EDL_PASS→earthaccess env at call time; lazy imports so `--help`
  and tests work with no creds/network.
- `live/render.py` — class→RGBA PNG bytes (rasterio MemoryFile, palette
  ffffb2→bd0026, nodata transparent), `critical_features` (connected
  class≥2 polygons ≥ min_blob_pixels → GeoJSON), `npz_bytes`.
- `live/publish.py` — `write_outputs` writes files then `manifest.json` LAST
  (returns ordered paths); `upload_gcs` env-gated (NOW_GCS_BUCKET +
  GCP_SA_KEY + gcloud) doing rsync-minus-manifest then manifest copy.
- `live/alerts.py` — stub per brief: emits alerts.json candidates where class
  upgrades to Alto/Crítico on today or D+1 vs `now/prev_classes.npz`; `hold`
  flag during quiet hours 23h-6h unless Crítico. No Telegram sending
  (alerts-spec agent owns that).
- `live/build_static.py` — doc step 1, run-once GEE script: monthly IMERG
  p90/95/99 3-day climatology (2001-2023, ±30d/3d offsets like gee/01),
  T_slide/T_flood/slope/HAND from NASADEM (focalMin 1500 m proxy), GHSL 2020
  pop → `static/sul_static.npz`. Auth via GCP_SA_KEY service account.
- `live/run_now.py` — orchestrator (doc steps 2-6): fetch → regrid → TPI today
  + D+1..D+3 → PNGs (slide+flood × d0..d3) → critical_areas.geojson →
  summary.json (km² critical, people_exposed, data_dates) → alerts.json →
  prev_classes.npz → manifest.json last → optional GCS upload.
- `live/tests/` — 18 tests on synthetic arrays: R/S/T scoring piecewise,
  class cuts, accumulate3/projected_rain3, manifest-last write order (incl.
  second run), alert upgrade/quiet-hours rules, PNG RGBA, GeoJSON polygonize.
- `.github/workflows/now.yml` — `cron: '0 */3 * * *'` + `workflow_dispatch`;
  uv setup, `uv sync --frozen`, fetches `static/sul_static.npz` from GCS when
  absent (GCP_SA_KEY→gcloud), runs `uv run python live/run_now.py`, commits
  `now/` via git-auto-commit-action. Secrets: EDL_USER/EDL_PASS/GCP_SA_KEY
  (+ vars.GEE_PROJECT). Failing runs keep the previous `now/` commit (last good).
- `tools/build_simular.py` — doc ~line 1420 verbatim logic, parameterized:
  GEE simular CSV → simular.json, only class changes, dates from
  config.yaml rs_2024 event_date/window; warns on missing towns (Encantado,
  Muçum, Roca Sales, Lajeado, Canoas, Porto Alegre).
- `static/README.md` — npz contents + regeneration instructions.
- `now/manifest.json` — seed manifest (`updated_at: null`, `layers: []`).

## Commands run (results)

- `uv run python live/run_now.py --help` — resolves, prints usage/options.
  (Local `uv` mise shim is broken on this machine; verified with the real
  binary `~/.local/share/mise/installs/uv/0.13.8/.../uv`.)
- `uv run pytest live/tests/ -q` — **18 passed** (6 benign
  NotGeoreferencedWarning from in-memory PNGs).
- `uv run ruff check live/ tools/` — **all checks passed** (after fixes).
- `yaml.safe_load(.github/workflows/now.yml)` — valid.
- `uv run python tools/build_simular.py --help` — resolves.
- End-to-end smoke: `run()` with mocked fetchers on a 1° grid (synthetic
  static npz) — wrote all 13 files, manifest.json last, data_dates in
  manifest, critical km²/people_exposed computed, alert candidate emitted on
  run 1 and suppressed on run 2 (prev_classes dedupe works).

## Deviations / notes

- Evidence asked for `.herdr/implement.md`; wrote this file as
  `implement-modo-agora.md` per the task instruction.
- `live/fetch.py` remote calls (earthaccess, OPeNDAP `.latest`, asf_search)
  are written but could not be exercised — no credentials/network here.
  SMAP returns 2-D EASE-grid lat/lon regridded via `regrid_points`.
- `people_exposed` uses GHSL pop band from `build_static.py`; `null` in
  summary.json if `pop` absent from the npz.
- `now/` is committed by the workflow (plain-commit publish path); the
  `gs://tipping-point-data/now/` upload only runs when `NOW_GCS_BUCKET` is
  set, manifest copied last there too.
- `static/sul_static.npz` is not committed (large); workflow downloads it
  from `gs://tipping-point-data/static/` via GCP_SA_KEY, or it is rebuilt
  with `live/build_static.py`.
- Doc step 8 ("two consecutive runs clean, app shows right dates") needs real
  credentials — unverifiable in this session.
