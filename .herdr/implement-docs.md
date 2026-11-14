# unit/docs — S16 + S20 + A3 declarations

Branch `unit/docs`, commit `455464985e0e369ef3cd9aab2a13653164db21e5` (not pushed).

## Static domain name chosen: `south_brazil`

Approved by lead-live (t6g) — matches deployed GEE assets
`projects/<GEE_PROJECT>/assets/tipping-point/static/south_brazil/`.
lead-live's u28 makes `live/config.py` read the name from config, so
`live/config.py` was left untouched (already `STATIC_DOMAIN = "south_brazil"`).

## Changes

- `config.yaml` — `domains.now.name: sul_brasil` → `south_brazil` (S20).
- `pipeline/tp/config.py` — new `now_name()` accessor for
  `domains.now.name` (S20, keeps L9 one-edit true).
- `gee/now_tpi.py` — `static/south_brazil/` literals at
  :87, :171-172, :202, :271 now use `f"static/{now_name()}/…"`;
  docstrings updated (S20).
- `README.md` — fixed `sul_brasil` mentions (:65, :75); added sections
  (S16 + rulings):
  - **Method and thresholds** — R/S/T scores, config threshold keys;
    flood-mask Otsu per A2; perfect-forecast observed D+k incl. S per A4;
    lead-time metric list incl. `n_points`, `n_points_excluded`,
    `alto_n_points`.
  - **Data sources** — GEE ids: `NASA/GPM_L3/IMERG_V07`,
    `NASA/SMAP/SPL4SMGP/008`, `NASA/NASADEM_HGT/001`, `MODIS/061/MCD12Q1`
    (features only, not index input — A3), `CIESIN/GPWv411/GPW_Population_Count`
    (gpw_year = nearest 5-yr epoch, latest 2020), `NASA/VIIRS/002/VNP46A2`,
    OPERA catalog ids per L10b `OPERA/DSWX/L3_V1/HLS` + `/S1`,
    HLS `NASA/HLS/HLSL30/v002` + `HLSS30/v002`; `forecast_source`
    (`perfect-forecast` replay / `geos-fp` live).
  - **NISAR products** — collection `NISAR_L2_GCOV_BETA_V1`, aux
    `NISAR_WATERMASK`/`NISAR_DEM`, wa_2025 ref/pre/post granule ids
    (from `events/wa-2025-12.json`), GCOV/GUNW HDF5 dataset paths
    (from `pipeline/tp/nisar.py:12-18`).
  - **ML scope (A3)** — XGBoost trigger model → `hazard_slide_series`,
    seed 42, T stays slope/HAND, no t_ml.tif/T_ML (roadmap), MODIS LC not
    an index input.
  - **Declared limitations** — Otsu (A2), perfect-forecast S observed
    (A4), coherence gates moot for WA, WA landslide hit may be null,
    BETA pre-calibration frames.

## Commands run

- `.venv/bin/python -m pytest -q` → **571 passed** (34 s).
  (`uv run` broken locally: mise shim has no uv version — used .venv directly.)
- `.venv/bin/ruff check .` → **All checks passed** (the `make test` gate;
  `ruff format --check` reports 129 unformatted files repo-wide, pre-existing,
  not part of the gate).

## Notes

- Untracked `data/wa_2025/cog/` left out of the commit (generated artifacts).
