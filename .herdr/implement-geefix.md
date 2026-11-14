# GEEFIX implementation

Branch: `unit/geefix`. Verified working diff based on `2ae0dcf`; the implementation commit includes this report.

## Scope and changes

- Added `gee.common.ee_init()` without changing the existing `init()` API or its legacy credential behavior. Reads `GEE_PROJECT` at call time. Prefers `GOOGLE_APPLICATION_CREDENTIALS` (JSON key file, reads client_email, passes key_file) over `GCP_SA_KEY` (whole JSON env string, checks client_email/private_key, passes key_data). Initializes EE with explicit credentials and project. Missing/invalid credential and permission errors name the relevant environment variables.
- Removed the obsolete missing-common fallback from `gee.hls_scar`; every toAsset export now unconditionally stamps its image.
- Added fake-EE tests for file credentials, JSON credentials, both set/file precedence, neither set, missing project, malformed/incomplete JSON, missing file, and role failure. Added an immutable fake-image export test proving the stamped image, not the input image, reaches toAsset with data_date and updated_at.
- Task 3 was explicitly dropped by lead-analysis: impact.py belongs to unit/u5 and is absent here. No flood_source changes and no SCHEMA.md diff. Did not touch gee/now_tpi.py. No helpers spawned, no model switches, no push.

## Commands and results

UV executable used throughout: `$HOME/.local/share/mise/installs/uv/0.13.8/uv-x86_64-unknown-linux-gnu/uv`.

1. `uv run pytest gee/tests pipeline/tests -q`
   - Exit 1: **1 failed, 344 passed, 9 warnings in 12.87s**.
   - Expected unrelated failure: `pipeline/tests/test_fetch_nisar.py::test_execute_requires_creds`.
   - `pipeline/fetch/nisar.py:365` calls `tp_nisar._edl_credentials()`; `AttributeError: module 'tp.nisar' has no attribute '_edl_credentials'`.
   - No test_fetch_opera failures. Left unrelated fetch code untouched as instructed.
2. `uv run pytest gee/tests -q`
   - Exit 0: **62 passed in 0.73s**.
3. `uv run ruff check gee/common.py gee/hls_scar.py gee/tests/test_common.py`
   - Exit 0: **All checks passed!**
4. `env -u GEE_PROJECT -u GOOGLE_APPLICATION_CREDENTIALS -u GCP_SA_KEY -u GEE_SERVICE_ACCOUNT_KEY <uv> run python -m gee.hls_scar --case wa_2025 --dry-run`
   - Exit 0, no credentials. Printed case/event, HLS collections, pre/post windows ending 2025-12-20, candidate gates, export plan, and metrics keys. Existing CLI requires --case, so supplied wa_2025.
5. `git diff --check`
   - Exit 0, no whitespace errors.
6. Inspected `git status --short`, `git diff`, `git diff --stat`, `git log -4 --oneline`; only common.py, hls_scar.py, test_common.py changed before adding this requested report.

Additional task coordination commands: `todo log`, `todo add`, `todo`, `todo edit 62` after the scope reduction, and `hc send lead-analysis` to report the absent impact.py before task 3 was dropped.

Credential coverage is fake-only and uses fake@example.invalid / fake-key; no live Earth Engine authentication or export was attempted. Original isolated stamp test remains as well as the new export-call-site test. No pre-change failing regression run was recorded.
