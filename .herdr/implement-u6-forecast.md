# U6 — replay forecast + lead time (unit/u6)

## What changed

- `pipeline/tp/forecast.py` (new): `perfect_forecast(series_cog, event_id,
  issue_date, days=(1,2,3), out_dir, metrics_path)`. Reads a float TPI series
  COG (bands named YYYYMMDD); for each k writes
  `data/<case-id>/cog/<caso>_tpi_forecast_d<k>_<AAAAMMDD>.tif` (AAAAMMDD =
  issue date) as a single-band COG whose pixels are the observed band of
  issue+k; the output band keeps the depicted date as its description.
  Missing band → nothing written for that k, reported under `missing`.
  `forecast_source: "perfect-forecast"` is merged via
  `tp.metrics.merge_metrics` on every run. (The plan's sketch named the 2nd
  param `event_date`; implemented as `event_id` — `<caso>` stem and the
  metrics merge both need it.)
- **Spec delta L4** (applied 2026-11-14): outputs route to
  `data/<case-id>/cog/` (`rs_2024`, `wa_2025`), not `data/<event-id>/`.
  `tp.config.case_id()` does not exist in this worktree yet, so forecast.py
  carries `case_id()` (either form → underscore case key) and
  `resolve_event_id()` (case id → event id via `events/*.json`, needed for
  `load_event`/`artifact_stem` and for `merge_metrics`, which keeps its
  signature and takes the event id — the helper routes to root
  `data/metrics.json` keyed by case once its L4 update lands; metrics.py is
  outside my write scope). `perfect_forecast` accepts either id form.
- `pipeline/scripts/07_forecast.py` (new): CLI `--event --series [--issue]
  [--days 1,2,3]`; `--event` accepts event or case id; issue defaults to
  `events/<event>.json` `reference_date`; writes into
  `data/<case-id>/cog/` via `event_data_dir(case_id)`.
- `ml/lead_time.py`: accepts float TPI series — dtype auto-detect
  (`is_float_series`) or `--tpi`; TPI values used as-is (no /100 scaling),
  default threshold 0.6 (Alto cut). New label-point flow (`--labels` CSV,
  the ml/labels.py GEE-table lon/lat format): each labeled point is classed
  `pos` (crossed strictly before anchor, `anticipation_h` = anchor − first
  crossing), `neg` (never crossed or only at/after anchor) or `null`
  (nodata pixel / outside raster). `warned_before_pct` = pos share of
  evaluable (non-null) points; mean/median over pos anticipations. Without
  `--labels` the original cluster flow runs unchanged. Frame times default
  to band names (YYYYMMDD), falling back to `cfg.FRAME_TIMES_UTC`.
  `ANCHOR_TIMES_UTC` untouched (still empty by design). Metrics now go
  through `tp.metrics.merge_metrics` (flat keys); the pre-existing
  `landslide_ml.lead_hours` detail block is kept via a second atomic
  tmp+replace write.
- `pipeline/tests/test_forecast.py` (new): 16-band synthetic float series;
  d1–d3 rasters equal the shifted observed bands; missing date → report +
  no file; nodata preserved; metrics.json gets `perfect-forecast`; L4 case
  routing — default out_dir lands at `data/rs_2024/cog/...` (DATA_DIR
  monkeypatched) and case-id input resolves to the event; CLI `--help`
  resolves.
- `ml/tests/test_pipeline.py`: new `test_lead_time_tpi_series_label_points` —
  4-frame float series, hand-computed leads (48/72/24 h), pos/neg/null
  classes, summaries, and end-to-end `main()` → metrics.json.

## Commands run + results

- `uv sync` — ok.
- `uv run pytest pipeline/tests ml/tests live/tests -q` — **65 passed**
  (baseline was 46).
- `uv run ruff check .` — **All checks passed**.
- `uv run python pipeline/scripts/07_forecast.py --help` — resolves.
- `uv run python -m ml.lead_time --help` — resolves.

## Notes / judgment calls

- `warned_before_pct` denominator = evaluable labeled points (pos + neg);
  nulls are excluded and counted under `n_null` in the detail block.
- Forecast COGs are written with `driver="COG"` (matches
  `tp/flood_mask._write_mask`), dtype/CRS/transform/nodata copied from the
  series.
- Committed on `unit/u6`; not pushed (per unit rules — the owning lead
  pushes after review).
