# U25 — publish_fallback merges into fallback branch (S1)

## Changes

- `live/publish_fallback.py` — `sync_data` now MERGES into the fallback
  worktree instead of mirroring: removed rsync `--delete`/`--delete-excluded`
  and the entire unlink/rmdir pass over the target. Oversized-file skip,
  orphan-branch/amend semantics, manifest-last contract and the
  "no changes" early return are unchanged.
- `.github/workflows/nisar.yml`, `.github/workflows/validation.yml` — added a
  `Detect data/ artifacts` step (`id: data_changed`, runs
  `git status --porcelain -- data/`) and gated the publish step with
  `if: steps.data_changed.outputs.changed == 'true'`. Both jobs write under
  `data/` only when they do real work (assets.json, staging geojson,
  metrics.json); validation exits early on "no active case" and now skips
  publish. Kept the steps rather than removing because the jobs do produce
  data/ artifacts conditionally.
- `live/tests/test_publish_fallback.py` —
  - `test_oversized_and_stale_files` updated for merge semantics (stale
    target file and old oversized target copy survive).
  - New regression test `test_merge_preserves_files_absent_from_source`:
    seeds a target-only `now/summary.png`, runs `sync_data`, asserts it
    survives.
  - `test_rsync_size_exclusions` now asserts `--delete`/`--delete-excluded`
    are NOT passed.
- `live/tests/test_workflows.py` — publish step asserted unconditional in
  now.yml, and asserted gated (`if: steps.data_changed.outputs.changed ==
  'true'`, with a `data_changed` detect step) in nisar.yml/validation.yml;
  still absent from reference.yml.

## Commands & results

- `$UV run pytest live/tests -q` → 105 passed, 23 warnings (19s)
- `$UV run ruff check live/` → All checks passed
- `yaml.safe_load` on all four workflow files → all parse ok

## Notes / caveats

- `git status --porcelain -- data/` detects tracked modifications and
  untracked files; `data/raw/` is gitignored but the jobs don't publish raw.
- now.yml publish stays unconditional (it always produces data/now output).
