# POLYGONIZE implementation

Branch: unit/polygonize; base 09646bd. No helpers, no model switches, no push.

## Changes

Extended the existing pipeline/tp/polygonize.py and pipeline/scripts/06_polygonize.py instead of introducing duplicate modules/scripts. Scope is these two files, pipeline/tests/test_polygonize.py, and this requested report.

- Writes plain EPSG:4326 GeoJSON FeatureCollections, including empty collections.
- Polygon features carry integer class (and legacy value), kind, area_km2, source raster filename, and date (inferred from YYYYMMDD filename suffix or supplied with --date; null for undated generic inputs).
- Repairs invalid geometries with Shapely make_valid, retaining only nonempty polygon parts. Uses estimated UTM for area and topology-preserving simplification in metres.
- CLI accepts --case/--event, optional --raster, --product, --date, --dry-run, --config, --min-area-km2 and --simplify-m. Without --raster it discovers case COGs and case-scoped raw/nisar inputs. Outputs retain dated source stems under data/<case>/geojson/.
- Config YAML may contain polygonize: {min_area_km2: 0.001, simplify_m: 1.0}; CLI overrides take precedence. No shared config changes.
- Landslide discovery includes values 1 and 2 by default; other products include 1. Missing inputs produce explicit errors except dry-run, which describes missing inputs/planned outputs without writing.
- Synthetic tests cover hand-computed 0.0012 km2 rectangles with 5 ring vertices, EPSG:4326, class/provenance/date fields, area filtering, nodata/empty collections, bowtie repair into two valid triangles, missing input, config behavior, dry-run no writes, and discovery of all three products with both landslide classes.

The ingest library at this base still uses startTableIngestion; output is standard GeoJSON FeatureCollection, also suitable for the separately planned ee.FeatureCollection + Export.table.toAsset path. No ingest or auth code changed.

## Commands and results

All uv invocations used $HOME/.local/share/mise/installs/uv/0.13.8/uv-x86_64-unknown-linux-gnu/uv.

- uv run pytest pipeline/tests/test_polygonize.py -q: initial 10 passed in 2.61s; final expanded suite 11 passed in 0.86s.
- uv run pytest: 525 passed, 1 failed, 31 warnings in 39.03s (526 collected before final discovery test was added). Failure: pipeline/tests/test_validation.py::test_edl_token_env_wires_bearer_header, assertion actual auth.token access_token 'fake' versus expected 'fake-token-123'. This is outside polygonization scope; reported to t6f immediately, not modified.
- uv run pytest pipeline/tests/test_validation.py::test_edl_token_env_wires_bearer_header -q: 1 passed in 2.81s in isolation, indicating full-suite test state pollution.
- uv run ruff check pipeline/tp/polygonize.py pipeline/scripts/06_polygonize.py pipeline/tests/test_polygonize.py: All checks passed!
- uv run ruff check .: All checks passed! (also repeated after final test).
- git diff --check: exit 0.

Full-suite green acceptance is blocked by the above unrelated test interaction. Polygonization-specific verification and whole-repository Ruff are green. Lead should handle suite pollution outside this unit's write scope.
