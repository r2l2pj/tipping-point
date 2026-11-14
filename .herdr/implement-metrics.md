# Metrics audit implementation

Branch/worktree: `unit/metrics`, `/home/lucascoelho/code/r2l2pj/tipping-point-data-wt/metrics`.
Base SHA: `ae77b47`. Tested the working-tree changes committed with this report.

## Changes

- S7/A8 (`pipeline/tp/fallback.py`): critical summary and vector masks use strictly TPI > config's Critical cut (0.6), not High+Critical. GPWv411 latest population-count epoch is reduced over the Critical union and flood zone for people_critical/people_exposed/people_flooded; absent coverage retains null plus reason. No High+Critical aggregate is emitted.
- S11 (`gee/hand.py`, `data/SCHEMA.md`): hand_validation is the selected radius's IoU * 100 scalar; per-radius r/IoU remain under hand_validation_radii. Best radius is chosen by r+IoU, ties preferring the smallest radius. Normal merged runs write that radius back to thresholds.hand_proxy_radius_m while preserving YAML formatting/comments and clearing the config cache; --tasks does not write it. No valid samples produces null plus reasons and leaves config unchanged. Tests use isolated config files, so no fake radius is committed. Lead-analysis confirmed coordination of this shape with lead-integration t6b.
- A7 (`gee/tpi.py`): replace the toDrive call with size-limited getDownloadURL writes to each planned data/<case>/cog path using the existing raw/zip GeoTIFF helper. The legacy export-plan discriminator drive_cog is retained for compatibility; execution no longer uses Drive.
- S12/A5 (`gee/lead_time.py`, `Makefile`): default replay needs no IMERG GEE asset. Fetch GES DISC GPM_3IMERGHHE V07 archive through existing fetch helpers, subset each granule before concatenation, and calculate both hazard cubes locally with numpy every 3 hours. Require all 144 half-hour intervals ending by t-4h; missing intervals/data remain NaN. Download static climatology/terrain and conservatively eligible SMAP (observation start <= t-75h) via getDownloadURL on the native IMERG grid. Record latency assumptions and dated bands in EPSG:4326 rasters. Existing optional --imerg-early-asset execution remains available. Makefile invokes this local path followed by ml.lead_time --replay with inventory labels and event JSON (damage_anchor); it no longer supplies event_date midnight as an anchor.
- Added/updated offline tests for strict mask calls, non-null GPW-derived population, numeric HAND shape/config persistence/null reasons, local latency/missing-data behavior, default CLI routing, and end-to-end local replay GeoTIFF creation with fake archive/GEE downloads.

## Commands and results

Working directory was the metrics worktree. UV below is `/home/lucascoelho/.local/share/mise/installs/uv/0.13.8/uv-x86_64-unknown-linux-gnu/uv`.

- `git worktree add /home/lucascoelho/code/r2l2pj/tipping-point-data-wt/metrics -b unit/metrics`: created isolated branch at ae77b47; original worktree's untracked data untouched.
- `UV run --python 3.12 pytest pipeline/tests/test_fallback.py gee/tests/test_impact_hand.py gee/tests/test_lead_time_replay.py ml/tests/test_lead_time_replay.py`: initially 54 passed.
- `UV run --python 3.12 pytest pipeline/tests gee/tests live/tests ml/tests`: 572 passed, then 574 passed after adding HAND-null and raster-output tests (269 dependency/fixture warnings).
- `UV run --python 3.12 pytest gee/tests/test_lead_time_replay.py gee/tests/test_impact_hand.py pipeline/tests/test_fallback.py`: 50 passed after final local per-granule subsetting change.
- `UV run --python 3.12 ruff check .`: initially caught one C408 in a new test; changed dict() to a literal; subsequent run: All checks passed.
- `git diff --check`: passed.
- `env -u EARTHDATA_TOKEN -u GEE_PROJECT -u GOOGLE_APPLICATION_CREDENTIALS -u GCP_SA_KEY make lead-time`: exit 0, `SKIP lead-time: missing EARTHDATA_TOKEN`.
- Reviewed `git diff`, `git status --short`, and recent commit style before staging only this unit's paths.

## Limits / real-data proof still required

No credentials were read or used, no network-backed replay was run, and no real impact numbers are claimed. The local replay uses the native IMERG lat/lon grid, not a new 1-km terrain-preserving resampling pipeline. Existing ml.lead_time null+reason behavior applies to geographic pixel-area metrics unless areas are provided; the default Makefile evaluates the slide cube while both slide/flood cubes are written. Production GES DISC/GEE downloads, quota/size behavior, independent damage anchors/inventories, and real metrics require an authorized run. No dependencies, app files, or credential files were changed. No push.
