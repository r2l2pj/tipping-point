# FALLBACK — static copies plus missing live GEE renders

Branch unit/fallback. Original copy-only implementation: 2f65e8442d971b6ba1a08a6c9f4ad3891e8a0ff3, base 3ae2211d28b59e5864cbf8853b5708c1f06662b3. This report supersedes the original copy-only design after lead-analysis explicitly requested the live-rendering rework.

## Scope and behavior

Changed only tools/export_fallback.py, pipeline/tp/fallback.py, pipeline/tests/test_fallback.py and this requested report. No new dependencies, helpers, push, or changes to producer ownership.

- CLI: --now | --case rs_2024|wa_2025, --root data, --prefix fallback/, --bucket gs://NAME, --dry-run.
- --case retains deterministic copy-only publication of all files recursively under the selected case plus metrics.json and root manifest.json.
- --now copies existing local files and renders missing PNG/COG outputs listed by producer data/now/manifest.json, plus missing summary.json and geojson/critical_areas.geojson. Existing files are preserved byte-for-byte, including local producer summary/critical areas. A complete local bundle needs no GEE import or calls.
- PNG: single-band float TPI assets classified at config.yaml tpi_classes [0.2,0.4,0.6]; getThumbURL uses EPSG:3857, region from producer manifest bounds, 2048 maximum dimension and four-class palette. HTTP fetched with timeout.
- COG: Export.image.toCloudStorage with EPSG:4326, 1000 m scale, GeoTIFF/cloudOptimized. Polls only the COG tasks this exporter starts; FAILED/CANCELLED/timeouts are errors. Downloads completed COGs through the existing storage client into temporary files then moves into place. Does not poll or wait on upstream toAsset tasks.
- Summary: pixelArea/1e6 + mask reduceRegion(sum). Emits today's slide/flood/union Alto+ areas, worst-hazard class areas, forecast_source, data_dates, actual source asset timestamps/dates. Population is honestly null with explicit reasons: the canonical TPI assets contain no population counts. Existing producer summary is never regenerated.
- Critical areas: no alert-polygon asset exists (confirmed by lead-analysis). Vectorizes classes >=2 (raw TPI >=0.4, Alto+) for slide/flood d0 and d1 via reduceToVectors, EPSG:4326 output, records numeric class, Portuguese label, hazard and actual asset data_date. Existing producer GeoJSON is never regenerated.
- alerts.json and both manifests remain strictly producer-owned: exporter never generates or rewrites them. They must exist before --now runs. Producer live manifest must list derived TPI layers with id, asset, png/cog paths and bounds; d0/d1 slide/flood are required for reductions/vectorization. Missing GEE assets, asset timestamps and required local files cause errors, never silent skips.
- Publication is sorted non-manifest files, then now/manifest.json last in live group, root manifest.json last overall. No deletion or remote cleanup. Same inputs/complete local bundle produce the same object keys and byte copies on rerun.
- Existing e2109d5 fetch.ingest storage helpers provide lazy SDK import, credential guard, bucket resolution and validation. stage_to_gcs itself hardcodes staging/<case>/<name><suffix>, so cannot directly express fallback tree keys. No dependency added. Normal storage runs require GOOGLE_APPLICATION_CREDENTIALS; real GEE init uses existing gee.common.init (GCP_SA_KEY/GEE_PROJECT convention). CLI sanitizes SDK/HTTP rendering failures rather than echoing URLs or credential-bearing error details.
- --dry-run prints only ordered keys and does not render, import GEE/storage clients, use credentials, create files or access the network. Missing producer manifests/alerts still fail; missing renderable outputs are included in the offline plan.

## Investigation and resolved sequencing question

Read data/SCHEMA.md and live/run_now.py first, then git show e2109d5, pipeline/fetch/ingest.py, CLI/test conventions and live/jobs.py. During rework, read sibling ../nowtpi/gee/now_tpi.py and wrapper read-only: float32 one-band TPI_slide/TPI_flood assets with data_date/updated_at, async toAsset exports.

Initially paused and reported that live/jobs.py called exporter before alerts/root manifest, and upstream exports were asynchronous. Lead-analysis clarified via hc: lead-live owns moving exporter LAST after alerts and build_manifest; do not regenerate alerts/manifests and do not wait for upstream toAsset status. Implementation follows this decision. Reads whichever asset version exists (last-good); a missing asset fails. This worktree intentionally does not modify live/jobs.py. Lead-analysis notified of required producer manifest render-target contract. Producer source dates may be newer than the last-good asset; generated summary/GeoJSON explicitly retain actual asset dates, without changing producer manifests.

## Verification evidence

Rework verified on working tree based on SHA 2f65e8442d971b6ba1a08a6c9f4ad3891e8a0ff3 before the rework commit:

```
$HOME/.local/share/mise/installs/uv/0.13.8/uv-x86_64-unknown-linux-gnu/uv run pytest pipeline/tests/test_fallback.py pipeline/tests/test_ingest.py live/tests/test_jobs.py -q
........................................................                 [100%]
56 passed in 0.24s

$HOME/.local/share/mise/installs/uv/0.13.8/uv-x86_64-unknown-linux-gnu/uv run ruff check tools/export_fallback.py pipeline/tp/fallback.py pipeline/tests/test_fallback.py
All checks passed!

git diff --check
(exit 0; no output)
```

28 fallback tests (original 17 plus 11 rework checks): exact now/case keys, byte-preserving fake-client uploads, deterministic reruns, manifest-last order, missing local files, credential failure, helper reuse, SDK redaction, subprocess dry-run exit 0, missing-live rendering offline plans, 8 thumbs/4 COG exports, COG CRS/cloud optimization, PNG CRS, area reduction results, critical-area class/threshold calls and labels, preservation of producer alerts/manifests/local products, repeat render no-op, missing GEE timestamps, and COG failure/cancellation/timeout aborts before manifest uploads.

Commands also run: scoped uv run ruff check; git diff --check; git status --short; git log -3 --oneline; todo log/add/edit; hc send t6f contract questions, resolved design and implementation status. Initial copy-only tests were 45 passed with scoped ruff clean; rework's first ruff pass found one test-only lambda style issue and diff --check found trailing blank line, both corrected. Final verification recorded in completion message.

No live GEE/GCS run attempted; all rendering and storage evidence uses injected fakes. No claim of real cloud output or real pixel correctness beyond the declared EE operations/parameters. Existing locked uv environment reused; no dependency-file modifications.
