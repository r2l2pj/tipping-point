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

Credential coverage is fake-only and uses fake@example.invalid / fake-key; no live Earth Engine authentication or export was attempted. Original isolated stamp test remains as well as the new export-call-site test. No pre-change failing regression run was recorded for the initial implementation.

## Follow-up: duplicate module fixture isolation

Verified diff based on `c5c27ed24eb3888335ad4fce3be3f5932cea978a`; this follow-up commit contains its report.

- Updated `gee/tests/conftest.py::fake_ee` to obtain canonical modules through importlib (sys.modules), patch their ee attributes, and also patch differing gee package common/tpi attributes. All patches use monkeypatch for teardown.
- Added a parameterized regression for both common and tpi: remove the canonical sys.modules entry, re-import under a stub ee, restore sys.modules while leaving the duplicate package attribute, then dynamically request fake_ee. Assert both modules and the test-held common use the fake; call common.aoi_geom and assert the recording geometry call. Preload tpi before duplication so the regression does not introduce a lingering tpi.common reference to its temporary common module.
- Inspected other gee test fixtures: no other fixture patches ee on common/tpi. The HLS export test patches its locally held hls_scar module directly, not a module resolved via a potentially stale package attribute.

Commands (same UV executable as above):

1. Before changes: `uv run pytest pipeline/tests/test_ingest.py gee/tests/test_common.py -q`: **40 passed in 0.46s**. This branch's test_ingest.py does not contain the integration branch's common re-import, so that exact existing-order reproduction did not fail here.
2. Added regression, before fixture fix: `uv run pytest gee/tests/test_common.py -k duplicate_modules -q`: **2 failed, 24 deselected in 0.27s**. Both fail at `assert original.ee is fake`; original.ee is the real ee module, proving the fixture leak without invoking real EE.
3. After fix, final ordered check: `uv run pytest pipeline/tests/test_ingest.py gee/tests/test_common.py -q`: **42 passed in 0.20s**.
4. Final `uv run pytest gee/tests pipeline/tests -q`: **1 failed, 346 passed, 9 warnings in 7.88s**. Only the previously reported unrelated test_fetch_nisar.py::test_execute_requires_creds fails with missing tp.nisar._edl_credentials. No GEE failures.
5. Also ran reversed full ordering `uv run pytest pipeline/tests gee/tests -q`: **1 failed, 346 passed, 9 warnings in 7.15s**, same sole unrelated NISAR failure (before the final regression-only tpi preload refinement).
6. `uv run ruff check gee/tests/conftest.py gee/tests/test_common.py`: **All checks passed!**
7. `git diff --check`: exit 0. Reviewed git diff/status/log. Only scoped tests and this report changed; no production modules changed in this follow-up.

No helpers, no model switch, no push.

## Review follow-up: fall back after configured file credentials fail

Verified working diff based on `577730c` on unit/geefix.

- ee_init now tries GOOGLE_APPLICATION_CREDENTIALS first, then GCP_SA_KEY on any file, JSON/key validation, credential construction, or initialization error. It returns after the first successful initialization. Only after all configured sources fail does it raise, naming every attempted source and GEE_PROJECT; permission-stage errors mention Earth Engine roles.
- Added fake regressions for missing/stale file, invalid JSON file, credential-construction error, and EE initialization error, each followed by a successful JSON-env initialization; also checks both source names when all fail. Existing missing-file-only test now explicitly clears GCP_SA_KEY so host env cannot provide an unintended fallback.
- Key fields are validated for both sources. No credentials or raw environment values are included in the aggregated error message.

Commands use the exact UV executable recorded above:

1. Before fix: `uv run pytest gee/tests/test_common.py -k 'falls_back_to_json or reports_all_failed_sources' -q`: **5 failed, 26 deselected in 0.20s**. Four cases stopped at the failed file source; the fifth error omitted GCP_SA_KEY.
2. Final `uv run pytest pipeline/tests/test_ingest.py gee/tests -q`: **85 passed in 0.47s**.
3. Final `uv run pytest gee/tests pipeline/tests -q --tb=short`: **1 failed, 351 passed, 9 warnings in 12.99s**. Same pre-existing test_fetch_nisar.py::test_execute_requires_creds / missing tp.nisar._edl_credentials; no GEE or OPERA failures. An earlier full run also had 351 passed and the same sole failure.
4. `uv run ruff check gee/common.py gee/tests/test_common.py`: final **All checks passed!** Initial Ruff runs flagged BLE001 on the deferred broad catch; resolved by raising the combined error inside the final-source exception handler.
5. `git diff --check`: exit 0. Reviewed git status/log; only common.py, test_common.py, and this report changed.

No push, helper, model switch, or out-of-scope fixes.
