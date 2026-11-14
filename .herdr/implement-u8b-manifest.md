# U8b — root manifest builder (unit/u8b)

## Changed

- `pipeline/tp/manifest.py` (new) — `EVENT_TO_CASE`, `build_case_node(event_id,
  data_root)`, `build_root(data_root)`, `write_root_manifest(data_root)`.
  - Case node per SCHEMA: `event_id`, `mode: "replay"`, `bounds` (config.yaml
    case `aoi` via `tp.config.case`), `forecast_source: "perfect-forecast"`,
    `layers[]`, `metrics` (`<event>/metrics.json` when the file exists else
    null), `data_dates`, `products`, `_null_reasons` (node-level sibling of
    `products`; products values strictly `str | null`).
  - `EXPECTED_LAYERS` = `tpi_{slide,flood}` + `_d1..d3`; `*_d<k>` layers get
    `forecast: true`, `date` = reference_date + k, `png` =
    `<event>/png/<layer>_<YYYYMMDD-depicted>.png`, `cog` =
    `<event>/cog/<caso>_tpi_forecast_d<k>_<YYYYMMDD-issue>.tif` (SCHEMA
    example naming). Non-forecast cogs = `<caso>_<layer>_<YYYYMMDD>.tif`.
  - Legend pinned `["Baixo","Moderado","Alto","Crítico"]` on every layer.
  - `data_dates` best-effort: `imerg`/`smap`/`swot` = max day date with a
    non-null value in `series/imerg_smap_daily.json`; `nisar_pre`/`nisar_post`
    = max frame pre/post from `events/<id>.json`.
  - `products`: `<event>/geojson/<name>.geojson` when the file exists else
    null + node-level reason (`flood_confirmed` uses the event's `nisar.note`
    first sentence when frames are empty — "No NISAR coverage — event
    predates launch (Jul 2025)" for rs-2024-05).
  - `build_root` → `{updated_at: <UTC Z>, cases: {rs_2024, wa_2025, now}}`;
    `now` = `data/now/manifest.json` verbatim or null when absent.
  - `write_root_manifest` writes LAST, atomically (`manifest.json.tmp` +
    `os.replace`, same pattern as `tp.metrics`).
- `tools/build_manifest.py` (new) — `--dry-run` prints JSON, writes nothing;
  default writes `data/manifest.json` only. `--data-root` override.
- `pipeline/tests/test_manifest.py` (new) — 14 tests: node shape/keys vs
  SCHEMA field lists, expected layers w/ null bounds, forecast flags + issue-
  vs depicted-date paths, `_index.json` real bounds + extra layers, products
  null + node-level reasons (values only str|null), metrics path exists/null,
  data_dates from series + NISAR frames, now verbatim + null, manifest-last
  write order (Path.write_text spy), atomic tmp+os.replace spy, dry-run
  subprocess → valid JSON + nothing written.
- `.herdr/implement-u8b-manifest.md` — this file.

## `_index.json` contract (consumer side, for U8a alignment)

`data/<event>/png/_index.json` = `{ "<layer_id>": {"date": "YYYY-MM-DD"|null,
"bounds": [w,s,e,n]|null, "cog": "<optional data-root-relative override>"} }`.
Missing/absent index → expected `tpi_*` layers still listed with `bounds:
null`. U8a (tp/bundle.py) did not exist in the sibling worktree yet — if it
writes a different shape, this reader must be updated.

## Commands run

- `$UV sync` → ok.
- `$UV run pytest pipeline/tests -q` → **47 passed**.
- `$UV run ruff check .` → **All checks passed!** (fixed UP017, 3× BLE001,
  RUF100).
- `$UV run python tools/build_manifest.py --dry-run` → valid JSON,
  `cases = {rs_2024, wa_2025, now}`, `now: null` (no `data/now/` in this
  worktree yet), no creds.

## Notes

- Both `bounds` sources used per spec: node `bounds` = config.yaml `aoi`;
  layer `bounds` = real PNG extent from `_index.json`.
- `data/now/` does not exist yet (U12 moves `now/` → `data/now/`); `now` node
  is correctly `null` until then.
