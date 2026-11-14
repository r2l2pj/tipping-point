# U3b — root metrics.json keyed by case id (L4 ruling)

Branch `unit/u3-metrics-root` on the shared main (includes U3 + others'
landed units).

## What changed

- `pipeline/tp/config.py` — added `case_id(name)` (canonical `rs_2024` /
  `wa_2025` from case or event id; event ids map via the
  `<ev-prefix>_<YYYY>` convention against `config.yaml` cases; KeyError
  naming the valid ids) and `event_id(name)` (event ids pass through; case
  ids resolve via `events/*.json` glob `rs-2024-*.json`; falls back to the
  input unchanged).
- `pipeline/tp/metrics.py` — `merge_metrics(event_or_case, flat, reasons,
  collections, path) -> dict` now writes ONE root `data/metrics.json` with
  shape `{cases: {<case_id>: {event, ...flat, _null_reasons, collections}}}`.
  Blob `event` = resolved event id. Still: never drops keys, null+reason
  under the case blob's `_null_reasons`, real value clears the reason,
  `collections` merges under the blob, atomic `<path>.tmp` + `os.replace`,
  `indent=2` + trailing newline. New (per committed SCHEMA.md rule): a
  `None` never clobbers an already-written non-null value.
- `ml/evaluate.py` — `update_metrics_json` routes through
  `tp.metrics.merge_metrics`; `landslide_ml` section still shallow-merges
  inside the case blob; `flat` keys land at blob level; signature/Path
  return kept.
- `pipeline/tests/test_metrics.py` — rewritten for the keyed shape
  (10 tests): root `cases` shape, case-id input deriving event via
  events/*.json, cross-case key preservation, null+reason (custom +
  generic), reason cleared, null-never-clobbers, collections merge,
  tmp+`os.replace` spy, no partial file on failed write, repeated merges.
- `pipeline/tests/test_config.py` — added `case_id`/`event_id` resolution
  tests incl. KeyError on unknown.

## Commands + results

- `$UV sync` in worktree → env created, all deps installed.
- `$UV run pytest pipeline/tests ml/tests live/tests -q` → **68 passed**.
- `$UV run ruff check .` → All checks passed (one I001 import-sort in
  ml/evaluate.py fixed with `--fix`).

## Notes

- Existing callers (`pipeline/scripts/02`, `03`) pass event ids — resolve
  correctly into case blobs with no changes needed there.
- `data/SCHEMA.md` still documents per-event metrics.json (pre-L4 text);
  updating it is the contract owner's follow-up, not in this scope.
