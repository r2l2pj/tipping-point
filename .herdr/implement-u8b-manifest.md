# U8b — root manifest builder (unit/u8b)

Revised for spec delta L4+L5 (amended `data/SCHEMA.md`, commit ccd101a):
case-id directories, `metrics.json#<case>` fragments, cog-only score layers,
pinned `_index.json` layer-list contract.

## Changed

- `pipeline/tp/manifest.py` (new) — `EVENT_TO_CASE`, `build_case_node(event_id,
  data_root)`, `build_root(data_root)`, `write_root_manifest(data_root)`.
  - Case node per amended SCHEMA: `event_id` (hyphen id, field only),
    `mode: "replay"`, `bounds` (config.yaml case `aoi`), `forecast_source:
    "perfect-forecast"`, `layers[]`, `metrics`
    (`"metrics.json#<case>"` when root `data/metrics.json` exists else null +
    reason — per-event metrics.json gone), `data_dates`, `products`,
    `_null_reasons` (node-level sibling; products values strictly
    `str | null`).
  - All asset paths prefixed by case id: `rs_2024/png/...`,
    `rs_2024/cog/...`, `rs_2024/geojson/...` — dirs are `data/<case-id>/`.
  - `EXPECTED_LAYERS` = `tpi_{slide,flood}` + `_d1..d3`; `*_d<k>` →
    `forecast: true`, `date` = reference + k (depicted day), `png` =
    `<case>/png/<layer>_<YYYYMMDD-depicted>.png`, `cog` =
    `<case>/cog/<caso>_tpi_forecast_d<k>_<YYYYMMDD-issue>.tif`.
  - `COG_ONLY_LAYERS` = `r_series`, `s_series`, `t_slide`, `t_flood` —
    minimal `{id, date, png: null, cog}` entries (bounds/legend unused per
    SCHEMA) emitted only when `cog/<caso>_<id>_*.tif` exists (latest match);
    missing → `_null_reasons[<id>]`.
  - `_index.json` = `{"layers": [{id, date, png, cog, bounds, forecast}]}`
    consumed verbatim per key (present keys kept even when null, e.g.
    `forecast: false` / `png: null`); `legend` injected; index-only ids
    appended sorted. Missing index → expected `tpi_*` still listed with
    `bounds: null`.
  - `data_dates`: `imerg`/`smap`/`swot` = max day date with non-null value in
    `<case>/series/imerg_smap_daily.json`; `nisar_pre`/`nisar_post` = max
    frame pre/post from `events/<event>.json`.
  - `flood_confirmed` null reason for rs-2024-05 = first sentence of the
    event's `nisar.note` ("No NISAR coverage — event predates launch
    (Jul 2025)").
  - `build_root` → `{updated_at: <UTC Z>, cases: {rs_2024, wa_2025, now}}`;
    `now` = `data/now/manifest.json` verbatim or null when absent.
  - `write_root_manifest` writes LAST, atomically (`manifest.json.tmp` +
    `os.replace`).
- `tools/build_manifest.py` (new) — `--dry-run` prints JSON, writes nothing;
  default writes `data/manifest.json` only. `--data-root` override.
- `pipeline/tests/test_manifest.py` (new) — 15 tests on synthetic
  `data/<case-id>/` trees: node shape/keys vs SCHEMA, expected layers w/ null
  bounds, forecast flags + issue/depicted paths, index verbatim consumption
  (incl. `forecast: false`, extra ids), cog-only layers present/missing +
  node-level reasons, products null + node-level reasons (str|null only),
  `metrics.json#<case>` fragment on/off, data_dates, now verbatim + null,
  manifest-last write order (write_text spy), atomic os.replace spy,
  dry-run subprocess → valid JSON + nothing written.
- `.herdr/implement-u8b-manifest.md` — this file.

## Commands run

- `$UV sync` → ok.
- `$UV run pytest pipeline/tests -q` → **48 passed** (15 manifest).
- `$UV run ruff check .` → **All checks passed!**
- `$UV run python tools/build_manifest.py --dry-run` → valid JSON,
  `cases = {rs_2024, wa_2025, now}`, `now: null`, `metrics: null` (repo
  `data/` has none of the new-layout files yet).

## Notes for lead-analysis

- `_index.json` consumer contract now pinned to `{"layers": [{id, date, png,
  cog, bounds, forecast}]}` — keys are used verbatim (a present-but-null
  `png`/`cog`/`forecast` stays as written; absent keys are derived/computed).
  U8a should write exactly this shape at `data/<case>/png/_index.json`.
- U8a writes `data/<case-id>/` (rs_2024, wa_2025), not `data/<event-id>/`.
- `data/now/` still absent in this worktree (U12 moves `now/` → `data/now/`);
  `now` node is `null` until then.
