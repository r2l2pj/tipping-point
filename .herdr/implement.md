# L42-now-nisar — live "Now" NISAR multi-date series + HH/HV pair PNGs

## Deliverables (all real data, no mocks)

- `data/now/response/nisar_timeseries.json` — WA-contract series, 4 same-mode
  dates (≥3 required): dates/roles/units/areas (hh_db, hv_db, median/p10/p90,
  n_px_by_date, km2)/frames/reading/reading_line/status/grids/passes/
  rejected_passes/guards/caveats.
- `data/now/response/png/nisar_{hh,hv}_{20261025,20261106}.png` — EPSG:3857
  gray 2–98 % stretch (bundle._gray_png_3857), bounds per frame.
- `data/now/response/nisar_layers.json` — layer entries merged by
  `live/now_manifest.merge_nisar_layers` (file-existence filter on png).
- `data/now/manifest.json` — regenerated via `live.now_manifest.finalize`:
  layers nisar_pre/nisar_post/nisar_hv_pre/nisar_hv_post +
  products.nisar_timeseries = "response/nisar_timeseries.json".
- `tools/now_nisar_timeseries.py` — reproducible producer (anonymous CMR
  search → same-mode selection → chunked block-average fetch → guarded stats
  → PNGs → JSON). `--cache-dir` reuses per-date npy (avoids refetch).

## Discovery (anonymous CMR, collection NISAR_L2_GCOV_V1, 60 d window)

- Track 024 D frame 101: only **2** passes exist — 2026-10-25 and 2026-11-06.
  NISAR systematic GCOV coverage of Southern Brazil only began ~Oct 22 (102
  granules total over the live bbox, Oct 22→Nov 6); no earlier repeat exists.
- To reach ≥3 dates, widened to "passes covering the candidate footprint"
  (same-mode 4005_DHDH_A, looks=100 only, per lead-analysis guard):
  - 2026-10-25 024D_101 P (pair geometry, partial swath, EPSG:32723)
  - 2026-10-29 074A_166 F (ascending context, EPSG:32722)
  - 2026-11-01 125D_100 P (descending context, EPSG:32722)
  - 2026-11-06 024D_101 P (pair geometry, partial swath, EPSG:32723)
- 5 granules rejected with reasons recorded in json (mixed-mode 2005 product
  on Oct 25 — the published pre granule — dupes, no-coverage, no-h5).

## Guard application (9456cc8, imported from tp.response_nisar — not copied)

- `rn.pair_mismatch`: published pair 2005_DHDH_M (looks 25) → 4005_DHDH_A
  (looks 100) is mixed-mode → rejected; series uses same-mode 4005 products.
- `threshold_method == "fallback"` (-15 dB) in nisar_meta.json → the pass is
  not flood evidence (recorded in guards.published_pair_rejected).
- `rn.edge_zone` (2 km): candidate strip (234 px) → 1 px kept Oct 25,
  **0 px** Nov 6. Urban control fully in edge zone on both 024D dates.
- `rn.states_geom`/`rn.in_states` on municipal_boundaries.geojson: all 8
  candidate polygons in **São Miguel Arcanjo, SP** → outside RS/SC/PR,
  dropped (dropped_outside_states = 8). Boundaries file taken from the
  fallback branch copy at ~/.cache (pending lead-gathering's product).

## Numbers (HH linear-power mean dB, edge-guarded)

| area | Oct 25 | Oct 29 | Nov 1 | Nov 6 |
|---|---|---|---|---|
| flood_candidates (2.35 km²) | -14.13 (n=1) | -5.61 (n=235) | -5.15 (n=235) | — (n=0) |
| control_open_field (55.5 km²) | -8.78 | -6.60 | -6.26 | -9.05 |
| control_urban (4.6 km²) | — | -4.88 | +1.01 | — |

Unguarded pair-date strip median: -16.8 (Oct 25) → -17.5 dB (Nov 6) — already
dark pre-event, i.e. edge-zone artifact, NOT new flooding. Context passes
show ordinary land over the same strip; urban double-bounce (+1.0 dB HH)
visible Nov 1.

## Commands run

- `uv sync` (uv binary: ~/.local/share/mise/installs/uv/0.13.8/uv-*; mise
  shim broken), `.env` sourced for EARTHDATA_TOKEN (never printed).
- probes/fetch under `systemd-run --user --scope -p MemoryMax=3G`,
  cache ~/.cache/l42-now-nisar/{fetch_passes.py,probe.py,same-mode_*.npy,
  passes.json,municipal_boundaries.geojson}.
- `git fetch + rebase onto 9456cc8` (clean).
- `python tools/now_nisar_timeseries.py --cache-dir ~/.cache/l42-now-nisar`
  (3 runs: mode_token fix, km2-from-mask fix).
- `python -m live.now_manifest --out-dir data/now`.
- `uv run pytest live/tests pipeline/tests` → **753 passed**.

## Caveats

- Cross-track context dates have different incidence — levels indicative
  only; the pair dates are same-geometry.
- flood_candidates Oct-25 value is n=1 px (edge-clipped) — shown for
  transparency with n_px_by_date.
- The live candidates fail every guard → series reports them honestly as
  rejected/edge-artifact, never as flooding ("candidates, never confirmed").
