# U21 — L10 fallback branch publisher

## Implementation

Code commit: `84e404614643d957bdc5c20f91cf70383628b46c` on `unit/u21`.
Commit subject: `U21: orphan fallback branch publish + remove bucket machinery (L10)`.

- Added standalone `live/publish_fallback.py`: repo-root data snapshot, 10 MiB default per-file cap, counted oversized-file warnings, rsync with deletion/exclusions when installed, shutil stale-file cleanup otherwise, no-op staged-diff detection, UTC snapshot message, forced fallback push, and finally-block worktree removal. Dry-run performs no Git calls. Missing data and invalid size limits fail before publishing. Git errors do not echo captured stderr or environment contents.
- Manifest-last remains the producers' responsibility, documented in the module docstring.
- Removed NOW_GCS_BUCKET only from the scoped jobs environment tuple and four workflow environments.
- now/nisar/validation publish to fallback after their job; reference neither publishes nor commits data to main. Existing permissions, schedules, gates, and concurrency remain unchanged.
- Added seven offline publisher tests: existing branch with amend, missing branch/orphan, unchanged, oversized/stale files, cleanup on failed commit, rsync exclusions, and dry-run. Updated workflow shape coverage.

## Confirmed plan clarifications

Asked t6g about two contradictory recipe details before implementing them. t6g explicitly confirmed:
1. Existing fallback snapshot uses `commit --amend --reset-author`, rather than growing its history with ordinary commits. First publication uses the orphan branch path.
2. Remove reference.yml's existing main-data commit step entirely; do not add a publisher there.

Git commit identity is passed with per-command `git -c` options; no git config is modified. No helpers spawned. No actual publisher push, branch rewrite, or network publication was executed during verification.

## Commands and results

UV binary throughout:
`$HOME/.local/share/mise/installs/uv/0.13.8/uv-x86_64-unknown-linux-gnu/uv`

- `todo log`: request logging on; added U21 task.
- `git status --short`: initially clean.
- `git log -3 --oneline`: base was `09646bd` (U20).
- Initial `$UV run pytest live/tests/test_publish_fallback.py -q` before creating implementation: expected collection error, cannot import publish_fallback.
- `$UV run pytest live/tests/test_publish_fallback.py live/tests/test_workflows.py -q`: 27 passed before adding the seventh publisher test.
- Initial full suite: 92 passed, 21 warnings; Ruff identified import ordering, explicit subprocess check flags, and unused unpacked variables in new files. Corrected all findings.
- Final command, rerun at code SHA `84e404614643d957bdc5c20f91cf70383628b46c`:
  `$UV run pytest live/tests/ -q && $UV run ruff check live/`
  Result: `93 passed, 21 warnings in 10.58s`, then `All checks passed!`.
  Warnings concern existing rasterio georeferencing and affine multiplication paths.
- `$UV run python live/publish_fallback.py --dry-run`: exit 0; prints the repo data path, origin fallback, and 10 MB cap. Offline test independently asserts zero subprocess calls for this mode.
- Requested system `python3 -c "import yaml,glob; [yaml.safe_load(open(f)) for f in glob.glob('.github/workflows/*.yml')]; print('yaml ok')"`: failed because system Python lacks PyYAML. Same command under `$UV run python -c ...`: exit 0, `yaml ok`.
- `git diff --check`: exit 0.
- Reviewed scoped workflow/jobs/test diff; staged only eight authorized code/test files.
- `git commit -m ...`: exit 0; created code commit above, with required subject and Devin attribution.

## Limits

Publisher Git behavior is exercised with offline subprocess mocks, as requested; no live GitHub/GEE operation was performed. Report is unit-specific per `/tmp/u21-task.md`, avoiding overwriting the pre-existing `.herdr/implement.md` report.
