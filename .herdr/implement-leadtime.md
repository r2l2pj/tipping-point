# LEADTIME implementation

Branch: `unit/leadtime`; base: `ee4d61371f9eafc4629e0ba38c7859080fcaa764`. Verification ran against the implementation working tree on this base; implementation commit is the commit containing this report. No push.

## Changes

- Added `gee/lead_time.py`: `python -m gee.lead_time --case rs_2024 --step 3h --dry-run` prints the entire replay/export plan offline. Execution requires `--imerg-early-asset`; it never silently uses the public Late/Final replacement collection. Accepted ingestion convention: ImageCollection with `precipitationCal` (mm/h), child tags `YYYYMMDDTHHmm`, parsed into observation start timestamps.
- TPI_slide and TPI_flood use the unchanged configured TPI formula and historical climatology at every three-hour step, including 21:00 on the last case day. Rain3 remains the product's trailing 72-hour accumulation, ending four hours before the replay timestamp (144 half-hour samples, each multiplied by 0.5); incomplete rainfall pixels are masked rather than summed as if complete. SMAP is the latest eligible three-hour observation, held until its successor becomes eligible. Its start must be <= t-75h, ensuring interval end <= t-72h. No GEOS-FP layer is used. Both assumptions are recorded explicitly, not claimed to be measured release timestamps.
- Exports separate three-hourly slide/flood COGs with `YYYYMMDDTHHmm` bands on an EPSG:6933 equal-area grid at 1 km; this lets the metrics CLI derive pixel area rather than treating degrees as square kilometres.
- Extended `ml/lead_time.py` with `--replay COG --labels CSV --event CASE`, preserving the legacy hazard entry point. Inputs are independent damage points (`lon,lat,t_utc` or `date`/`t_date`); `t_precision` overrides inferred precision. Date-only values use local noon in America/Sao_Paulo / America/Los_Angeles. Do not use model-assigned peak-rainfall label timestamps as independent anchors.
- New replay path uses strict TPI > 0.6 Critical and > 0.4 Alto from config, validates contiguous three-hour frames, and emits all agreed flat keys with `tp.metrics.merge_metrics`. E7 `at/source/url?` anchors and older `time_utc/date/source/url` anchors are supported. Official warnings require a sourced hour-precision timestamp; URL is optional. Positive official-warning delta means replay crossed first.
- Median, linear-interpolated IQR, mean and six-hour histogram include negative and zero finite leads. Percentage denominator / `n_points` is evaluable points, including never-Critical points. Nodata, partial temporal coverage, absent anchor times and outside-raster points are excluded with `n_points_excluded`; they are not called never Critical. Alto also includes median/IQR/n, rather than a bare lead number.
- Critical area is the union of observed ever-Critical pixels. Damage coverage uses unique damage-containing pixels (no invented buffers or duplicate-point inflation). Scalar or per-pixel area is supported in the function; CLI auto-derives projected metre pixel area or accepts `--pixel-area-km2`; unavailable area is null with a reason. This is inventory point/pixel coverage, not independently mapped damage-polygon area.
- Unknown metrics are null with `_null_reasons`; merge writer's existing non-null values are not clobbered. No synthetic case metrics were written into root `data/metrics.json`.

## Hand calculation and tests

`ml/tests/test_lead_time_replay.py`, `ml/tests/test_lead_time_precision.py`, `gee/tests/test_lead_time_replay.py` provide 10 tests, including a real temporary three-band float GeoTIFF -> CSV damage points -> CLI -> merged metrics.json flow.

Main hand-worked fixture: Critical crossing leads are [-3, 6, 24] h; one evaluable point never crosses, one is nodata, one outside. Thus n=4, median=6, mean=9, Q1=1.5, Q3=15, warned-before=2/4=50%, warned>=24h=1/4=25%, never=1/4=25%. Six-hour bins start at -6 and have counts [1,0,1,0,0,1]. Alto leads [0,9,24] give median=9, Q1=4.5, Q3=16.5. Four Critical pixels at 2 km2 each give 8 km2; three contain distinct damage points, giving 75%. Event anchor noon vs first Critical midnight gives 12 h; official warning 03:00 gives +3 h (also tested at -3 h). Every agreed field is checked in a complete expected dictionary. Additional fixtures check exact-threshold exclusions, zero lead, 24 h inclusion, all-date and mixed precision, local noon, duplicate points, variable pixel areas, no points, no crossings, all nodata, null/reason paths and non-null preservation.

Initial targeted tests caught pandas 3's microsecond DatetimeIndex storage (fixed cadence comparison to use Timedelta rather than assume nanoseconds), and corrected the manually written Alto expectation to [0,9,24]. Final targeted tests pass.

## Commands and results

All `uv` invocations below use `$HOME/.local/share/mise/installs/uv/0.13.8/uv-x86_64-unknown-linux-gnu/uv` (abbreviated UV here).

- `UV run pytest ml/tests/test_lead_time_replay.py ml/tests/test_lead_time_precision.py gee/tests/test_lead_time_replay.py -q`: **10 passed in 0.71s** on final implementation.
- `UV run pytest -q`: **485 passed, 1 failed, 27 warnings in 18.08s**. Only failure: `pipeline/tests/test_validation.py::test_edl_token_env_wires_bearer_header`, line 170: expected `fake-token-123`, got `fake`. This unit does not edit Earthdata code or validation tests.
- Baseline-isolation check `UV run pytest -q --ignore=ml/tests/test_lead_time_replay.py --ignore=gee/tests/test_lead_time_replay.py`: **475 passed, same 1 failed, 27 warnings in 14.28s** (run before the two extra precision tests were added). This reproduces the untouched failure without the new tests. Reported to lead-analysis; left out of scope.
- `UV run ruff check .`: **All checks passed!** (also checked changed files explicitly).
- `UV run python -m gee.lead_time --case wa_2025 --step 3h --dry-run`: exit 0; offline plan spans 2025-11-30 00:00 through 2025-12-20 21:00 UTC, 168 frames, Early/SMAP cutoffs and exclusions printed. RS offline test checks 128 frames, 2024-04-22 00:00 through 2024-05-07 21:00. An init spy fails if the dry-run initializes Earth Engine.
- `git diff --check`: clean.
- Inspected `git status`, `git diff`, `git log`, current branch and base SHA before committing. Logged request with project `todo` and sent progress/evidence through `hc send t6f`.

## Review follow-up: explicit SMAP cutoff

On base `6914247`, clarified the recorded SMAP assumption to explicitly state `system:time_start <= t-75h` and its three-hour `interval end <= t-72h` interpretation. The conservative computation is unchanged. Added assertions that the offline plan records both cutoffs; the existing graph test independently checks the actual 75-hour timestamp filter. Verification: `UV run pytest ml/tests/test_lead_time_replay.py ml/tests/test_lead_time_precision.py gee/tests/test_lead_time_replay.py -q` -> **10 passed in 0.98s**; `UV run ruff check .` -> **All checks passed!**; `git diff --check` -> clean. No push.

## Remaining external prerequisites / limits

No authenticated Earth Engine export or real-case measured lead-time number is claimed. Execution needs the ingested archived IMERG Early collection (lead-gathering owns it) and GEE export permissions. Expected asset convention: `projects/hermes-fesolucoes/assets/tipping-point/<case>/imerg_early`. Ingestion must include at least 72h+4h of rainfall before the first replay frame; missing history is deliberately masked. SMAP 72h is a conservative release-latency assumption, not an archived per-granule release audit. Real damage inventories and citable official-warning anchors remain upstream inputs. Public GEE `NASA/GPM_L3/IMERG_V07` is deliberately not treated as historical Early (catalog: https://developers.google.com/earth-engine/datasets/catalog/NASA_GPM_L3_IMERG_V07).
