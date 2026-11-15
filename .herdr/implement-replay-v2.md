# REPLAY v2 (L44) — implement report

Branch: `replay-v2` worktree. All v1 files/keys/manifest/app PNGs untouched
(verified below). New `*_v2` COGs side by side; new metrics key
`cases.<c>.replay_v2` only.

## What I changed

**New files**
- `tools/replay_v2.py` — v2 COG builder. R computed on the 90 m TPI grid:
  P3 = 3-day IMERG sum over [D−3d, D) from `imerg_daily.npz` (0.1°);
  p90/p95/p99 per 0.1° cell (2001–2023, 3-day sums ending D±30d step 3,
  ≥300 complete windows); all bilinear to the 90 m grid, then
  `live.tpi.rain_score`. S = case `s_series` bilinear (v1 recipe); T =
  existing `t_slide`/`t_flood`. TPI = (R·S·T)^(1/3) = NaN on permanent
  water = JRC GSW occurrence ≥50 % (30 m tiles, max-resampled) OR MCD12Q1
  IGBP 17 (GEE `MODIS/061/MCD12Q1/<year>_01_01`, ~500 m, max-resampled).
  GFS `d<k>` frames of issue I depict E=I+k: obs IMERG days < I + GFS cube
  days ≥ I, percentiles at E, S held at band I−1.
- `tools/replay_v2_score.py` — orchestrator: re-runs all six scoring tools
  with `cog_suffix="_v2"` against an in-memory metrics copy, adds
  `_critical_stats` on the v2 bands (native 30″ GPW crop for RS; case pop
  COG for WA — native bounds check failed there, recorded), and writes
  ONLY `cases.<c>.replay_v2 = {method, diagnosis, r_check, before_after,
  detail}`.
- `tools/replay_v2_png.py` — Porto Alegre/Guaíba EPSG:3857 class PNGs for
  v1 and v2 (band 2024-05-02, TPI_flood).
- `pipeline/tests/test_replay_v2.py` — 7 hermetic tests: observed-P3
  window convention, per-cell climatology vs `np.percentile` reference,
  missing-day NaN propagation, L28b frame-day sources, geomean+water
  combine, bilinear cube→grid.
- `docs/replay-v2.md` + `docs/img/replay_v2_rs_2024_flood_20240502_{v1,v2}.png`.

**Modified tools** (all accept `--cog-suffix`, default `""` = v1;
`--write` + suffix is refused so v1 keys can't be overwritten; `*_gfs_*`
globs validated to 8-digit issue dates so v1 runs never pick up v2 files):
- `tools/warned_before.py`: `evaluate/anchored/onset_lead_time/run/apply`
  carry `cog_suffix`; `merge` split into `apply` (in-memory) + file IO.
- `tools/smap_latency_sensitivity.py`: `lagged_frames/run` + CLI.
- `tools/prediction_lift.py`: `run_frames/run` + CLI (`cases=` override so
  it can read v2 nested blobs).
- `tools/rs_ems_validation.py`: `gfs_frames_until/score/apply` + CLI.
- `tools/rs_ems_landslide.py`: `gfs_slide_frames/score_tpi/score/apply` +
  CLI (`cases=` for inventory_comparison).
- `tools/alarm_precision.py`: `iter_issue_files/run_case` + CLI.

**Data**: `data/metrics.json` — JSON load/modify/dump; diff-verified that
ONLY `cases.rs_2024.replay_v2` and `cases.wa_2025.replay_v2` were added.

## Commands run + results

All under `systemd-run --user --scope -p MemoryMax=2G`, python
`~/code/r2l2pj/tipping-point-data/.venv/bin/python`, `PYTHONPATH=.:pipeline`,
temp in `~/.cache/lead-analysis/replay-v2/`.

- `tools/replay_v2.py --case rs_2024` → 17 v2 COGs (16-day slide+flood
  series + 15 GFS issues). `tools/replay_v2.py --case wa_2025` → 22 v2
  COGs (21-day series + 20 issues). gfs_skipped = [] both cases.
  - RS: water_cells 318,066 (6.9 % of finite; gsw_ge50 311,658, mcd17
    253,628). WA: 325,170 (12.9 %; gsw 325,435, mcd 0 — grid is east of
    Puget Sound, no IGBP-17 cells at 500 m).
  - **R verification**: domain-mean R v1 → v2 = 0.11961 → 0.12445 (+4.0 %)
    both cases. On high-signal days agreement is tight (2024-04-30
    0.820→0.849 +3.5 %; 05-01..03 1.0→1.0); low-mean days differ
    relatively more (0.071→0.102) — expected from bilinear-vs-nearest
    reweighting of which cells cross p90.
  - Two bugs found + fixed during build: (a) a `{0,1}` uint8 mask with
    `dst_nodata=0` resamples pathologically under GDAL warp (99.96 % of
    cells flagged) — fixed by warping `{0,255}` and thresholding >127;
    (b) GSW tile names are NW-corner: WA needed `130W_50N` not `130W_40N`
    (first run masked 0 WA cells; rebuilt).
- `tools/replay_v2_score.py --build-report build_report.json` → merged
  `replay_v2` for both cases; every headline re-scored (warned_before
  onset by_lead + sensitivity, smap latency, EMS flood + landslide,
  prediction_lift, alarm_precision, critical_km2/people_critical).
- COG integrity: same shape/transform/CRS/band descriptions as v1,
  nodata=NaN; masked-away cells = 286,408 RS / 325,104 WA (matches mask).
- `pytest -q pipeline/tests live/tests gee/tests` → **912 passed**
  (incl. the 7 new hermetic tests).
- metrics.json deep-diff vs HEAD: only `replay_v2` added (no v1 value
  touched); `data/manifest.json` unchanged; no app PNGs rendered.

## Before / after headlines

rs_2024 (v1 → v2):
- slide_warned_pct 36.45 → 5.77; critical 1.87 → 0.18
- slide_warned_observed_pct 0.25 → 0.59
- forecast_lead_time_onset: earliest critical issue 2024-04-23,
  would_have_warned_h 147, median lead 87 h (v1: none)
- flood_ems_iou 0.185 → 0.197; f1 0.312 → 0.329; precision 0.46 → 0.75;
  recall 0.235 → 0.211
- flood_ems_flagged_before_onset_pct 6.56 → 0.0
- landslide_ems_warned_gfs_24h_pct 81.09 → 0.0 (v1's high-R block over the
  Taquari AOIs was an artifact)
- slide_warned_gfs_24h_lift 8.76 → 2.71
- alarm_precision_critical_pct 33.68 → 34.81; alarm_lift 1.35 → 1.40
- critical_km2 10870.6 → 9174.6 (flood 7646.8 → 5955.9; slide 3223.8 →
  3218.7); people_critical 1.49 M → 1.63 M (native 30″ GPW denominator)

wa_2025 (v1 → v2):
- flood_warned_* ~0 both (pixel-local-R explanation holds)
- forecast_lead_time_onset: earliest critical issue 2025-12-03,
  would_have_warned_h 128, median 80 h (v1: none)
- flood_warned_gfs_24h_area_share_pct 0.0 → 0.16; lift None → 0.0
- alarm_precision_critical_pct 19.7 → 20.29; alarm_lift 1.97 → 2.03;
  lead_h_median 56 → 44
- critical_km2 4127.5 → 3428.7 (flood 1464.7 → 738.6; slide 2662.7 →
  2690.0); people_critical 229,782 → 196,670 (case pop COG, as v1)

Full key-by-key table: `cases.<c>.replay_v2.before_after` in
data/metrics.json + docs/replay-v2.md.

## Caveats / not done

- WA people_critical used the case pop COG (native GPW bounds check
  rejected the WA grid) — recorded in `people_critical_source_v2`.
- v1 `forecast_lead_time_onset` keys were never written for these cases —
  v2 reports them fresh (v1 column shows null).
- RS EMS-landslide GFS-24h flagged share dropped to 0 — scientifically
  consistent with removing the 10 km R plateau over the Taquari valley,
  but worth eyeballing against the crops if the lead wants confirmation.
