# W2b — offline tests for ml/local pipeline modules (lead-ml t175)

Status: DONE — `uv run pytest ml/tests -q` -> **86 passed**, `ruff check
ml/local ml/eda ml/tests` -> All checks passed.

## Files changed

- `ml/tests/test_local_pipeline.py` (new, ~800 lines, 41 tests) — synthetic
  data only, no network; dynamic/static source calls faked via monkeypatch.
- Minimal fixes in lead-ml modules (each listed below with file:line + why).
- `ml/tests/test_local_sources_dynamic.py` — stale test-helper fix (below).

## Coverage of the requested list

- **labels**: `glc_frame` (field parse, ms->day normalize, accuracy map,
  unknown acc -> NaN, geometry fallback to properties, missing coords
  skipped); `filter_glc` (acc<=5, since, rain_only, bbox); `cluster_events`
  (transitive union within 3 d/100 km, far point own event, undated -> own
  `glc_undated_*`, pre-existing event_id kept); `dedupe_grid` (same cell+date
  first wins, different date kept); `min_dist_km` (0 at self, ~111 km/deg,
  empty ref -> inf); `assign_peak_day` (peak wins, ties -> earliest, all-NaN
  -> NaT); `in_bbox`, chord<->km roundtrip, `report_region` (first-match
  ordering), `load_rs2024` on a synthetic MultiPoint catalog.
- **split**: `greedy_assign` (60/20/20 by positive count, zero-pos units drawn,
  forced unit respected AND counted); `split_blocks_3a` (small event spanning
  2 blocks merged -> one unit/split; >merge_max_points event not merged);
  `rows_split_3a` (train row ~1 km from test block -> buffer; deep row stays;
  **test rows never buffered**; near val -> buffer); `buffer_rows` (pointwise
  10 km: train->buffer, test/val never demoted); `split_events_3b` (rs_2024
  forced test, `forced` flag); `summary` counts.
- **models**: `fit_calibrator` — isotonic branch (>=30 pos: monotone output in
  [0,1]), Platt branch (<30 pos: strictly increasing logistic), single class
  -> identity; `to_dict/from_dict` roundtrip; `best_f1_threshold` (perfect
  separation, single class -> 0.5); `prepare_X` (landcover Categorical for
  xgb vs float for rf); `predict` (empty -> len 0); tiny real XGB (8 trees)
  save/load roundtrip via `save_xgb`/`load_xgb`.
- **scoring**: `metrics` exact confusion counts (tn/fp/fn/tp=1,1,1,1),
  NaN-p dropping counted in `n_dropped_nan`, empty -> None fields;
  `slope_rule` 0@15 deg /0.5@22.5/1@30 clip; `rain_series` — shape (n,7+H),
  **column 7 = issue day D**, D-7 at col 0, gfs source replaces cols 7+,
  ValueError on bad source / missing gfs_daily; `rain_feats_at` offsets
  (r24 = col 7+k, r72 = k-2..k, r7 = k-6..k, rel columns);
  `horizon_scores` with `FakeRain` (daily/p99_daily/p99_3day/pctl_3day):
  tiny XGB P grows with H (1-prod), const-model 1-0.9^3 exact,
  kind='tpi' matches live.tpi math exactly (R*S*T geometric mean),
  kind='nowcast' single-day p not compounded, source='gfs' override lowers
  P on zero forecast.
- **serve**: `TriggerModel.predict` on 3x4 grid with tiny saved XGB — keys
  p_trigger_24h..14d, (3,4) float32, all in [0,1], **P_24h <= P_48h <= ...
  <= P_14d monotone** (pre-calibration product identity), wetter forecast ->
  higher P_24h, NaN elevation -> NaN output, short rain_fc -> ValueError.
- **table**: `doy_features`, `_date_pool` (sorted, in-range, unique);
  `horizon_rows` for the **new schema** (one shared issue-row set, pos at
  L-j j=0..lookback, neg at N lead_days=-1, `label_<h>` = 1 iff j<H, no
  horizon/H cols, train rows excluded); `point_needs` (smap sets union,
  gfs dates from test rows only); `load_rows` missing -> FileNotFoundError;
  `sample_3a` with faked `random_land_points` (3:1 negs, block split file
  written); `sample_3b` with small pools (rs_2024 -> test, both neg types,
  same_point negs >= NEG_DATE_GAP_DAYS from event days); `build_table_3a`
  /`build_table_3b` with faked sources (concat, dup-col drop, water-neg
  elevation-NaN rows dropped, horizon features + split_summary written).

## Bugs found and fixed (minimal edits)

1. `ml/local/split.py:50-53` — `greedy_assign` forced units were not counted
   in `got`, contradicting the docstring "applied first (counted)": without
   counting, `split_events_3b` would let ~20% of free positives fall into
   TEST even though rs_2024 alone already overshoots the test share. Now
   `got[sp] += n_pos` for each forced unit (missing uid -> 0).
   `test_greedy_assign_forced_counted` asserts: forced big unit -> test,
   zero free positives to test.
2. `ml/local/table.py:327` — `split_summary.json` write crashed with
   FileNotFoundError whenever `S.MODEL_DIR` does not exist (any smoke/test
   data dir layout): added `mkdir(parents=True)`, same pattern as
   `save_table`/`write_summary`.
3. `ml/tests/test_local_sources_dynamic.py:47-53,273` — stale helper in
   W1's file: `_imerg_nc4` wrote `precipitation` at root while
   `fetch_imerg_monthly` reads `Grid/precipitation` (real monthly HDF5
   layout). Added `monthly=` param; monthly test uses it. (Daily tests keep
   root layout — correct for daily nc4.)
4. Deprecation hygiene in lead-ml modules (all warn-only, now silent):
   - `scoring.py:108,141`, `serve.py:72`, `table.py:271` —
     `dayofyear` -> `day_of_year` (pandas4 rename).
   - `table.py:163-164` — bare `+ 1` on datetime64 -> `np.timedelta64(1,"D")`
     (generic-unit deprecation).
   - `labels.py:165` — `np.datetime64("NaT")` generic unit -> `("NaT","ns")`;
     `DatetimeIndex.values` -> `.to_numpy()` (pandas4).
   - `trainer.py:247` — blind `except Exception` is intentional per its own
     comment ("GFS archive gaps must not kill the run"); marked
     `noqa: BLE001` instead of narrowing.
   - `ruff --fix` auto-applied 14 mechanical fixes across `ml/local`
     + `ml/eda` (RUF046 redundant `int()`, UP037 quoted annotations,
     PIE808 `range(0,18)`).

## Commands + results

    uv run pytest ml/tests/test_local_pipeline.py -q   -> 41 passed
    uv run pytest ml/tests -q                          -> 86 passed, 1 warning
    uv run ruff check ml/local ml/eda ml/tests         -> All checks passed!

Remaining warning: `ml/tests/test_pipeline.py` (pipeline/tp module tests,
outside ml/local scope) uses tz-aware `DatetimeIndex.values` — pandas4
deprecation only, harmless.

## Notes for lead-ml

- `horizon_rows` tested against the new shared-row/`label_<h>` schema you
  announced; `lookback` param injected (5) to keep the test small.
- The `greedy_assign` fix changes 3b event assignment: TEST becomes
  rs_2024-pure (no stray GLC positives). `split_3b_events.csv` should be
  regenerated if one was already written with the old code.
- No commits made; all changes in this worktree only.
