# U3 — metrics + config plumbing (lead-analysis)

## Public API (pinned)

- `tp.config`: `REPO_ROOT`, `CONFIG_PATH`, `load_config() -> dict` (cached
  `yaml.safe_load` of repo-root config.yaml), `thresholds() -> dict`,
  `case(case_id) -> dict` (KeyError "unknown case ..." listing valid ids).
  Plus `threshold(name, default)` convenience getter used by the modules.
- `tp.metrics`: `merge_metrics(event_id, flat=None, reasons=None,
  collections=None, path=None) -> dict` — returns the merged dict; default
  path `data/<event_id>/metrics.json`. Merge-only (never drops existing
  keys); `None` -> null + reason under `_null_reasons` (generic fallback
  "not yet computed"; a later real value clears the stale reason);
  `collections` merges under `collections`; always sets `event`. Atomic:
  `<path>.tmp` + `os.replace`; `json.dumps(indent=2)` + trailing newline.

## What changed

- `pipeline/tp/metrics.py` (new) — as above; mirrors
  `ml/evaluate.py:update_metrics_json` semantics.
- `pipeline/tp/config.py` (new) — cached config.yaml loader + accessors.
- `pipeline/tp/flood_mask.py` — `FLOOD_DROP_DB`/`WATER_DB` now from
  `flood_db_drop`/`water_db_max`; `HAND_FLOOD_M`/`HAND_BRIGHT_M` from
  `hand_range_m[1]`/`[0]`. New pass-through params: `compute_flood(drop_db,
  hand_flood_m)`, `compute_bright(rise_db, hand_bright_m)`,
  `flood_mask(drop_db=)`. `BRIGHT_RISE_DB`, `MMU_HA`, `TILE_PX`, `_FILL_DB`
  unchanged (no config keys).
- `pipeline/tp/landslide_coh.py` — `COH_DROP`->`coherence_drop`,
  `MIN_REF_COH`->`coherence_ref_min`, `MIN_PATCH_PX`->`min_blob_pixels`,
  `MIN_SLOPE_DEG`->`slope_range_deg[0]`; `landslide_candidates` gained
  `min_drop`/`min_slope_deg`/`min_ref_coh` pass-throughs. `MAX_ELEV_M`,
  `HV_DROP_DB` unchanged.
- `pipeline/scripts/02_flood_mask.py` — merges `flooded_km2`, `water_km2`,
  `threshold_db`, and `flood_bright_km2` when bright ran (still prints stats).
- `pipeline/scripts/03_landslide_coh.py` — merges `landslide_candidates`
  (= `candidate_patches`) and `landslide_high_conf_px` (candidates only).
- `gee/03_rf_susceptibility.js` — `projects/GEE_PROJECT/` ->
  `projects/<GEE_PROJECT>/` (matches the placeholder in the header comment).
- `pipeline/tests/test_metrics.py` (new, 8 tests) — create+event, preserve
  keys, null+reason (custom + generic), reason cleared on real value,
  collections merge, tmp+os.replace spy, no partial file on failed write,
  repeated merges accumulate.
- `pipeline/tests/test_config.py` (new, 5 tests) — load/caching, thresholds
  accessors + defaults, `case()` incl. KeyError message, custom-path load.
- `config.yaml` not touched — every needed key already existed.
- `pipeline/tests/conftest.py` not touched — tmp_path sufficed.

## Commands + results

- `uv run pytest pipeline/tests live/tests ml/tests -q` → **59 passed**
  (baseline 46 + 13 new).
- `uv run ruff check .` → All checks passed.
- `node --check gee/03_rf_susceptibility.js` → OK.
- `grep -n 'write_text\|os.replace\|\.tmp' pipeline/tp/metrics.py` →
  tmp write at line 51, `os.replace` at line 52 (atomic, no partial writes).

## Notes

- `data/SCHEMA.md` modification + `.herdr/implement-u2-schema.md` in the
  worktree belong to U2 — left unstaged/untouched.
- `merge_metrics` returns the merged **dict** per the pinned API; the two
  scripts ignore the return value.
