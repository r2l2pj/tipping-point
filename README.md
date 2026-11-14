# tipping-point-data

Data & analysis pipeline for **Tipping Point** — NASA Space Apps Challenge 2026,
"Dancing with the SARs". Rain warns. Radar confirms.

Produces the artifacts in `data/` consumed by
[`r2l2pj/tipping-point-app`](https://github.com/r2l2pj/tipping-point-app).
The contract between this repo and the app is [`data/SCHEMA.md`](data/SCHEMA.md).

## Layout

| Dir | Contents |
| --- | --- |
| `events/` | One JSON per case: AOI, dates, NISAR frames |
| `gee/` | Earth Engine (JavaScript) scripts — TPI, lead time, RF susceptibility, HAND validation |
| `pipeline/tp/` | Python package: `nisar`, `flood_mask`, `landslide_coh`, `validation`, `swot`, `events` |
| `pipeline/scripts/` | Thin CLIs (`--event <id>`) numbered in run order |
| `pipeline/notebooks/` | Exploration only — never the source of a published artifact |
| `ml/` | Predictive model (susceptibility + trigger probability) |
| `data/` | Published artifacts (COG/GeoJSON/JSON). `data/raw/` is gitignored |

## Setup

```bash
uv sync
cp .env.example .env   # fill EDL_USER/EDL_PASS or use ~/.netrc
```

Accounts needed: NASA Earthdata Login (ASF/PO.DAAC/LANCE), Google Earth Engine
Cloud project (shared, for `gee/` scripts and the Python API).

## Run order (per event)

```bash
uv run python pipeline/scripts/01_get_nisar.py --event wa-2025-12   # coverage check FIRST
uv run python pipeline/scripts/02_flood_mask.py --event ... --pre ... --post ...
uv run python pipeline/scripts/03_landslide_coh.py --event ... --coh-ref ... --coh-evt ... --slope ...
uv run python pipeline/scripts/04_validation.py --event ... --opera-wtr ...
uv run python pipeline/scripts/05_swot.py --event ... --reach-id ...
```

In parallel, run `gee/01_tpi_index.js` → `04_hand_vs_merit.js` in the Code Editor
and drop the exported COGs into `data/<event>/cog/`.

## Rules

- Inputs are NASA (or challenge-partner) data only. MERIT Hydro is a validation
  reference, never an index input.
- Secrets live in `.env` / `~/.netrc` — never committed.
- NISAR products are HDF5 — subset to the AOI before writing COGs.
