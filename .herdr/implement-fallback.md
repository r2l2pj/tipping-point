# FALLBACK — copy-only static exporter

Implemented on unit/fallback, base SHA 3ae2211d28b59e5864cbf8853b5708c1f06662b3.

## Changes

- tools/export_fallback.py: mutually exclusive --now / --case rs_2024|wa_2025, --root data, --prefix fallback/, --bucket gs://NAME, --dry-run. Dry run prints only ordered object keys without credentials, storage import, or network.
- pipeline/tp/fallback.py: validates required root/live manifests, live summary/alerts/critical areas, case metrics, and non-null PNG/COG/product manifest references; collects all existing files recursively from the selected producer tree; uploads bytes without generating or modifying artifacts. Paths remain relative to data/ below the prefix. Deterministic order: sorted ordinary files, group manifests, root manifest last overall. No deletions or remote cleanup.
- Storage uses the existing e2109d5 fetch.ingest helper's credential guard, bucket resolution/validation, and lazy storage SDK loader. Its stage_to_gcs function cannot be used directly because it hardcodes staging/<case>/<name><suffix>; fallback needs arbitrary tree keys. No dependency added. Fake clients can be injected for offline tests; normal runs require GOOGLE_APPLICATION_CREDENTIALS and fail nonzero when absent. CLI emits sanitized SDK failure messages rather than credential-bearing exception details.
- pipeline/tests/test_fallback.py: 17 offline checks, including byte preservation, repeated identical uploads, exact now/case key sets, manifest-last order, missing-input failures, dry-run SDK isolation, missing credentials, ingest-loader reuse, failure abort before manifests and redaction, and subprocess dry-run exit 0.

## Commands and results

Read data/SCHEMA.md and live/run_now.py first; inspected git show e2109d5, pipeline/fetch/ingest.py, existing CLI/test conventions, live/jobs.py.

Verification ran on the implementation atop base SHA 3ae2211 (before the implementation commit):

```
$HOME/.local/share/mise/installs/uv/0.13.8/uv-x86_64-unknown-linux-gnu/uv run pytest pipeline/tests/test_fallback.py pipeline/tests/test_ingest.py live/tests/test_jobs.py -q
.............................................                            [100%]
45 passed in 0.14s

$HOME/.local/share/mise/installs/uv/0.13.8/uv-x86_64-unknown-linux-gnu/uv run ruff check tools/export_fallback.py pipeline/tp/fallback.py pipeline/tests/test_fallback.py
All checks passed!

git diff --check
(exit 0; no output)
```

Initial focused run: 15 passed; expanded with helper/failure tests to 17. Initial ruff findings (explicit subprocess check, nested conditional, blind exception chaining) were corrected; final ruff is clean. uv created the ignored .venv and installed the existing locked environment (no dependency-file modifications).

No live upload attempted: no network/credential proof is claimed. Checked-in data/ is only scaffolding, so a real export requires complete producer outputs first; missing files intentionally fail.

## Integration follow-up (outside assigned file scope)

Reported to lead-analysis t6f via hc send: current live/jobs.py calls export_fallback BEFORE alerts refresh and build_manifest. For a copy-only exporter it must run after producers finish summary/alerts/critical areas and both manifests. This unit deliberately does not regenerate them or change live/jobs.py. Root manifest is uploaded last, but existing scheduling must be fixed by its owner to avoid publishing stale alerts/manifests. Storage SDK installation remains the existing ingest environment's responsibility; missing SDK fails clearly, no new dependency was added.
