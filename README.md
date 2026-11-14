# tipping-point-data

Data & analysis pipeline for **Tipping Point** — NASA Space Apps Challenge 2026,
"Dancing with the SARs". **GPM + SMAP predict; NISAR confirms.** IMERG rain and
SMAP soil wetness feed a daily Tipping Point Index (TPI) that warns of
flood/landslide risk; NISAR radar acquisitions after the event confirm what
actually flooded or slid.

Produces the artifacts in `data/` consumed by
[`r2l2pj/tipping-point-app`](https://github.com/r2l2pj/tipping-point-app).
The contract between this repo and the app is
[`data/SCHEMA.md`](data/SCHEMA.md) — read it first; the app never computes and
never globs.

## Quickstart

```bash
uv sync                    # creates .venv (Python 3.12 via the Makefile)
make test                  # pytest pipeline/ gee/ live/ + ruff — no creds needed
make all CASE=rs_2024      # replay chain for one case (rs_2024 | wa_2025)
make now                   # live "modo agora" chain (live/jobs.py now)
```

Every Python step runs through `uv run`. Steps that need credentials print
`SKIP <step>: missing <var>` and the chain still exits 0 — a bare
`env -i make all CASE=rs_2024` exercises the whole wiring with no secrets.

Replay chain order (`make all`): GEE TPI series → HAND → impact → NISAR fetch
→ terrain → flood mask → polygonize → OPERA+HLS validation → perfect-forecast
layers → landslide hit-rate → HLS scar → lead time → case publish → root
manifest (always last — `data/SCHEMA.md` explains why).

## Credentials

| Variable | Needed for |
| --- | --- |
| `EARTHDATA_TOKEN` | NASA Earthdata downloads: NISAR GCOV, OPERA DSWx, HLS, SWOT, IMERG/SMAP/GEOS-FP live fetches |
| `GEE_PROJECT` | Google Earth Engine cloud project (shared, team) — all `gee/` and GEE-backed steps |
| `GOOGLE_APPLICATION_CREDENTIALS` **or** `GCP_SA_KEY` | Earth Engine service-account JSON key (path, or raw JSON for `GCP_SA_KEY`). Keep the key out of the repo |

Local fallback: `EDL_USER`/`EDL_PASS` or a `~/.netrc` entry for
`urs.earthdata.nasa.gov` (see `.env.example`). CI sets `EARTHDATA_TOKEN`,
`GCP_SA_KEY` and `GEE_PROJECT` (`.github/workflows/now.yml`). There is no
Cloud Storage bucket (ruling L10): `tools/export_fallback.py` renders
fallback files locally into `data/`, and the live workflow pushes `data/`
to the orphan branch `fallback`, which the app reads via
`DATA_BASE_URL=https://raw.githubusercontent.com/r2l2pj/tipping-point-data/fallback/data/`.

## Layout

| Dir | Contents |
| --- | --- |
| `pipeline/` | `tp/` package (processing logic), `scripts/` thin CLIs numbered in run order, `fetch/` downloaders, `tests/` |
| `gee/` | Earth Engine scripts — JS (Code Editor) plus Python ports (`python -m gee.tpi --case …`). See `gee/README.md` for the asset namespace |
| `live/` | "Modo agora" live pipeline — `run_now.py`, `jobs.py` (scheduled job orchestrator), fetch/render/publish/alerts |
| `ml/` | XGBoost susceptibility model + `lead_time.py` (warning lead vs an anchor report) |
| `tools/` | `publish_case.py` (per-case app bundle), `build_manifest.py` (root manifest, always last), `ingest_assets.py`, `now_tpi.py`, `build_simular.py` |
| `data/` | Published artifacts (COG/GeoJSON/JSON per case + `now/`). `data/raw/` is gitignored. Contract: `data/SCHEMA.md` |
| `events/` | One JSON per event: AOI, dates, NISAR frames |
| `static/` | Local fallback arrays for `run_now.py --local` |

## Domains and the rain climatology

`config.yaml` carries a `domains` block alongside `cases`: `domains.now`
(name `sul_brasil`, W/S/E/N bbox) is the live coverage area, read via
`tp.config.now_bounds()` (same bbox as `live.config.NOW_BOUNDS`).

The 3-day-rain climatology backing the R-score percentiles lives in GEE as an
ImageCollection per coverage area, **int16 mm×10** storage (~0.9 GB total —
divide by 10 on read):

- `tipping-point/<case>/clim_rain` — per case: ~25 images, the doys of the
  case event window, clipped to that case AOI.
- `tipping-point/static/sul_brasil/clim_rain` — for the live job: a rolling
  ~75-doy window over the `domains.now` bbox.

Interface is unchanged in both: one image per doy with a `doy` property and
bands `p90|p95|p99` (3-day-sum mm percentiles, ~10 km). Readers filter on
`doy` and scale by 0.1.

## Rules

- Inputs are NASA (or challenge-partner) data only. MERIT Hydro is a
  validation reference, never an index input.
- Secrets live in `.env` / `~/.netrc` / CI secrets — never committed.
- NISAR products are HDF5 — subset to the AOI before writing COGs.
- Manifests are written LAST (`data/now/manifest.json` per run,
  `data/manifest.json` for the repo); readers key on a non-null `updated_at`.
