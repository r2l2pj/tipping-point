# unit/cfg — S5 + S6 + A6 + N2 (+A10 data side)

Branch: `unit/cfg` in worktree `tipping-point-data-wt/cfg`. Not pushed.

## What changed

### S5 — hardcoded values moved into config.yaml / tp.config

- `config.yaml`
  - `cases.rs_2024.label_window: [2024-04-27, 2024-05-06]` — ml positive-cell
    assignment window (was `ml/config.py` EVENT_WINDOW literal).
  - `thresholds`: added `coh_max_elev_m: 600`, `hv_drop_db: 2`,
    `bright_rise_db: 3`, `mmu_ha: 3`, `slope_flood_max_deg: 5`.
  - `climatology`: added `years: [2001, 2023]`, `window_days: 3`,
    `doy_offset_days: 30`, `doy_offset_step: 3`.
- `pipeline/tp/config.py`: new accessors `climatology()` and `seed()`;
  `clim_scale()` now reads via `climatology()`.
- `Makefile`: `EVENT`/`YMD`/`REFDATE` are now `?=` defaults derived from
  config.yaml (REFDATE = case `event_date` via sed; EVENT via
  `events/<case-stem>-*.json`; YMD = REFDATE minus dashes). Still overridable
  on the command line; `env -i make` needs no Python for these.
- `ml/config.py`: `AOI_BBOX`, `EVENT_ID`, `EVENT_WINDOW`, `FRAME_TIMES_UTC`,
  `CLASS_BREAKS`, `TPI_WEIGHTS` all read config.yaml via `tp.config`; direct
  `yaml.safe_load` copy removed; `SEED` added (`tp.config.seed()`).
- `gee/tpi.py`: `SLOPE_FLOOD_MAX_DEG` and `RAIN_PERCENTILES` from
  `threshold()`; climatology window/±30 d/step from `climatology()`;
  percentile band names built from `RAIN_PERCENTILES`. The existing
  `climatology()` module function kept its name — the tp.config accessor is
  imported as `_clim_cfg`.
- `gee/common.py`: `CLIM_YEARS` from `climatology().years` (module constant
  name kept — pinned public API).
- `pipeline/tp/flood_mask.py`: `BRIGHT_RISE_DB`, `MMU_HA` from `threshold()`.
- `pipeline/tp/landslide_coh.py`: `MAX_ELEV_M`, `HV_DROP_DB` from `threshold()`.
- `live/alerts.py` thresholds untouched (lead-live owns).

### S6 — seed 42 in ml/

- `ml/train.py`: `fit_fold`, `cross_validate`, `train_final` and `--seed`
  argparse default all `cfg.SEED` (= config.yaml `seed: 42`).
- `ml/dataset.py`: `inner_block_split` default `cfg.SEED`.
- `ml/gee_features.py`: `randomPoints(..., seed=cfg.SEED)` (was 1).

### A6 — JS mirror headers

- All 9 `gee/*.js` got a 2-line header: "Reference mirror only — the canonical
  implementation is the Python gee/*.py modules, which read config.yaml. This
  script is not used by any pipeline." No other JS change.

### N2 — `make test` includes ml/tests

- `Makefile` test target: `pytest pipeline/tests gee/tests live/tests ml/tests`.

### Guard coverage

- `pipeline/tests/test_config.py`: `test_seed_accessor`,
  `test_climatology_accessor`, `test_migrated_threshold_keys`.
- `ml/tests/test_pipeline.py`: `test_config_reads_config_yaml` (AOI/window/
  frames/EVENT_ID/SEED/CLASS_BREAKS derive from config.yaml),
  `test_train_defaults_to_config_seed` (signature defaults == cfg.SEED).

## Commands run

- `make -n nisar-fetch`, `make -n lead-time CASE=wa_2025`,
  `make -n lead-time REFDATE=2030-01-01` — vars resolve correctly and stay
  overridable.
- `env -i make -n nisar-fetch` — works (no Python needed at parse time).
- `env -i make test` — 575 passed / 1 failed on first run
  (`climatology` name clash in gee/tpi.py) + ruff I001/F811; both fixed.
- `.venv/bin/python -m pytest pipeline/tests gee/tests live/tests ml/tests -q`
  → **576 passed**, ruff `All checks passed!`

## Notes

- `live/config.py:46` SLOPE_FLOOD_MAX_DEG copy is lead-live's (S5 row lists it
  but my scope excluded live/alerts.py-style thresholds; flag if it should
  also migrate).
- App-side S5/A10 items (src/app/page.tsx, lib/tpi.ts, AlertTab.tsx) are in
  the other repo — not touched.
