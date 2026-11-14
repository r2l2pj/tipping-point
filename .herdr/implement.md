# implement: unit/l19-alerts — L19a official-warning anchors

Base SHA: 42f5eb6feb2e384233ec09089282d44a051f40cc. Only two event JSON files and this report changed. No code/dependency additions, no push/merge.

## Verdict and changes

- WA: retain `2025-12-06T00:10:00Z` and existing `{at,source,url}` shape. Tighten source with actual header **WGUS66 KSEW 060010**, not the WGUS46 in the briefing. FFASEW explicitly covers King, Skagit, Snohomish, Whatcom and ten neighboring counties, and warns of landslides/debris flows from saturated soils. Issued 16:10 PST Dec 5; VTEC effective start Dec 8 12Z is NOT issuance.
- IEM metadata query confirms no earlier December FFASEW, FLWSEW, FFWSEW or SPSSEW product. This verifies the earliest rainfall/river-flood/landslide warning, not every possible coastal warning: a Dec 3 21:29Z CFWSEW Coastal Flood Watch/Advisory covers Grays Harbor coast high tides Dec 4–5, a separate tidal event, excluded from this atmospheric-river anchor.
- RS: NEW earlier official municipal Eclima/Prefeitura de Canoas forecast notice published **2024-04-25T20:40:20Z** (17:40:20 BRT), replacing Apr 26 16:50Z. It explicitly warns of exceptional rain and possible flooding at Praia do Paquetá / Rio dos Sinos basin. Canonical public page date is Apr 25; page JSON-LD `datePublished` and official WordPress REST `date_gmt` independently agree. This is publication time, NOT a demonstrated operational alert-dispatch time. Both qualifications are kept in event note.
- RS caveats: current page was modified Nov 11 2024; body weekday/day pairings are inconsistent (Friday 25, Saturday 26). Timestamp comes from publication metadata, not those body labels. This is earlier evidence, not a proof that no still-earlier official warning exists. Previously found Apr 20 INMET forecast remains date-only. State/federal-specific earlier timestamp was not established.

## Live source queries and results (2026-11-14)

Used webfetch on documented legacy IEM JSON endpoints after `/api/1/nwstext/` and `/api/1/docs/nwstext.md` returned 404. Documentation: https://mesonet.agron.iastate.edu/json/nwstext_search.py?help and https://mesonet.agron.iastate.edu/json/vtec_events_bywfo.py?help .

- https://mesonet.agron.iastate.edu/json/nwstext_search.py?awipsid=FFASEW&sts=2025-12-01T00:00Z&ets=2026-01-01T00:00Z : first result `ttaaii=WGUS66`, `utcvalid=2025-12-06T00:10Z`, `/O.NEW.KSEW.FA.A.0012.251208T1200Z-251213T0000Z/`; full text includes the four counties and landslide risk.
- https://mesonet.agron.iastate.edu/json/nwstext_search.py?awipsid=FLWSEW&sts=2025-12-01T00:00Z&ets=2025-12-09T01:00Z : first result Dec 8 20:29Z, Skokomish at Potlatch (Mason County), then Dec 8 23:56Z Lewis County. Incidental finding: existing damage_anchor alternate claiming Dec 9 00:04Z is first Seattle river warning is not globally first; left untouched (out of official_warning scope).
- https://mesonet.agron.iastate.edu/json/nwstext_search.py?awipsid=FFWSEW&sts=2025-12-01T00:00Z&ets=2025-12-06T00:11Z : `results=[]`.
- https://mesonet.agron.iastate.edu/json/nwstext_search.py?awipsid=SPSSEW&sts=2025-12-01T00:00Z&ets=2025-12-06T00:11Z : `results=[]` (no earlier standalone landslide statement).
- https://mesonet.agron.iastate.edu/json/vtec_events_bywfo.py?wfo=SEW&year=2025&start=2025-12-01T00:00Z&end=2025-12-06T00:11Z : marine/wind/coastal events; cross-check against broader VTEC archive, with coastal exception described above.
- https://mesonet.agron.iastate.edu/json/nwstext_search.py?awipsid=CFWSEW&sts=2025-12-03T21:28Z&ets=2025-12-03T21:30Z : WHUS46 KSEW 032129, Grays Harbor County Coast, flooding around high tide 11:30 AM Thursday / noon Friday.
- https://www.canoas.rs.gov.br/noticias/aviso-canoas-tera-incidencia-de-chuva-forte-nos-proximos-dias/ : dated Apr 25, official Eclima notice; HTML JSON-LD `datePublished=2024-04-25T20:40:20+00:00`.
- https://canoas.rs.gov.br/wp-json/wp/v2/noticias/99605 : `date=2024-04-25T17:40:20`, `date_gmt=2024-04-25T20:40:20`, `modified_gmt=2024-11-11T22:33:22`, same title/content.
- INMET direct article discovered by web search: https://portal.inmet.gov.br/noticias/tempestades-atingem-parte-da-regi%C3%A3o-sul-nos-pr%C3%B3ximos-dias ; publication Apr 26 13h50 BRT corroborates old anchor. Earlier news reports cite Apr 25/26 INMET alerts but are not primary-source exact issuance evidence.

## Commands and verification

- `pwd && git status --short && git branch --show-current && todo log && ls -a`: correct dedicated worktree, clean initial state, branch unit/l19-alerts, logging on.
- `todo add "L19a: verify official-warning anchors for WA 2025 and RS 2024; report and commit scoped changes"`: item 91.
- `curl -fsSL "https://www.canoas.rs.gov.br/noticias/aviso-canoas-tera-incidencia-de-chuva-forte-nos-proximos-dias/" | python -c 'import sys,re; s=sys.stdin.read(); print("\n".join(re.findall(r".{0,100}(?:datePublished|dateModified|published_time|modified_time|wp-json|date_gmt).{0,180}",s)))'`: exit 0, exact datePublished and official REST link above.
- `curl -fsSL "https://mesonet.agron.iastate.edu/json/nwstext_search.py?awipsid=FFASEW&sts=2025-12-01T00:00Z&ets=2025-12-06T00:11Z"`: exit 0, exactly one result, WGUS66 Dec 6 00:10Z, full text checked.
- `git diff --check && python -m json.tool events/wa-2025-12.json > /dev/null && python -m json.tool events/rs-2024-05.json > /dev/null && git diff --stat && git log -4 --oneline && git rev-parse HEAD`: exit 0 on base SHA plus working changes, both JSON valid; event diff 5 insertions/5 deletions. Manual diff review: only official_warning values changed.
- No fetcher was added; these were one-off HTTP research commands, so no live-network test or dependency was introduced. No unrelated tests claimed as evidence of historical truth.

---

# implement: unit/wainv — WGS validation reference (A1/B5)

Base SHA: e1d8f18. Seven scoped files including this report; no push/merge.

Changes:
- `pipeline/fetch/featureserver.py`: shared bbox query and paginated GeoJSON FeatureServer HTTP transport, service-error checks.
- `pipeline/fetch/glc.py`: reuse shared transport/bbox builder; existing GLC tests unchanged.
- `pipeline/fetch/wgs.py`: WA-only WGS mapping, attribution/license/source URL, source date retention, date-only local-noon America/Los_Angeles anchors, explicit times, no invented dates; no event-window filtering. Excludes personal/contact source fields.
- `pipeline/fetch/reference.py`: `--kind wgs --case wa_2025` fetches WGS then uses ingest_table; dry-run lists without network/writes; rejects other cases. Existing manifest skip/--force semantics retained. `--kind all` routing unchanged (explicit WGS kind is required).
- `pipeline/tests/test_fetch_wgs.py`: 14 offline cases covering timestamp rules, missing/invalid dates/times, DST, bbox-only pagination, source replacement, schema/attribution, ingest wiring, dry-run/case guard and service errors.
- `data/wa_2025/geojson/landslide_catalog.geojson`: actual public-service bbox fetch, 15 WGS points replacing GLC, all 15 date-less (`t_precision=none`), no GLC merge. This mutable service snapshot is validation-only, not independently certified as a Dec-2025-only inventory.

Commands/results (UV=/home/lucascoelho/.local/share/mise/installs/uv/0.13.8/uv-x86_64-unknown-linux-gnu/uv):
- `curl -fsSL 'https://services.arcgis.com/4x406oNViizbGo13/ArcGIS/rest/services/service_d63bc45f07fc4e859f0bdde6c847ec40/FeatureServer/0?f=json'`: verified Point layer, event_date alias Date of Landslide (esriFieldTypeDate), event_time alias Time of Landslide (string), pagination supported, maxRecordCount=1000; no declared copyright/license. License property truthfully says not specified, not an invented grant.
- `$UV sync`: exit 0, installed 119 packages.
- Initial `$UV run pytest pipeline/tests -q`: 359 passed, 1 failed (unknown time discarded valid date); fixed the parser to preserve the known date.
- Initial scoped Ruff: one redundant-import-alias finding; removed alias and injected the existing GLC HTTP getter into shared transport.
- Final `$UV run pytest pipeline/tests -q`: exit 0, **360 passed, 9 warnings in 8.33s** (Rasterio fixture warnings; Python 3.15 emitted sys.excepthook shutdown noise after success).
- `$UV run ruff check pipeline`: exit 0, All checks passed!
- `PYTHONPATH=pipeline $UV run python -c '...wgs_catalog(load_event("wa-2025-12"))...'`: exit 0, features=15, precision={none:15}, sources={WA DNR WGS Clearinghouse (public):15}.
- `$UV run python -m pipeline.fetch.reference --kind wgs --case wa_2025 --dry-run`: exit 0, wa_2025/landslide_catalog: dry-run -> projects/hermes-fesolucoes/assets/tipping-point/wa_2025/landslide_catalog (would re-fetch wa-2025-12).
- `git diff --check`: exit 0. Reviewed status/diff/log before scoped commit.

No live GEE ingestion was run. The offline ingest_table fixture proves the wiring; replacing an already-manifested remote asset requires the existing explicit `--force` flag.

---

# implement: unit/imerg-range — IMERG Early case-window hindcast
# implement — review follow-up: preserve inventory source dates (t120)

- `pipeline/fetch/inventory.py`: captures `source_dates` immediately after parsing each row, before noon normalization or timezone localization. Both emitted `event_date` and the event-window filter use this separate original-calendar-date series; only the timestamp series becomes `t_utc`.
- `pipeline/tests/test_fetch_inventory.py`: regression with four positive/negative-offset datetimes crossing UTC midnight at both event-window boundaries; asserts included row indices, exact original `event_date`, converted `t_utc`, and hour precision.
- Reproduction result on pre-change `c1a703c`: `$UV run pytest pipeline/tests/test_fetch_inventory.py::test_catalog_filter_preserves_source_date_across_utc_boundaries -q` passed (`1 passed in 0.34s`). The reported shift was not reproduced: the existing implementation retained the parsed offset. This change makes the requested separation explicit rather than claiming a demonstrated failing case.
- Post-change `$UV run pytest pipeline/tests -q`: `301 passed, 9 warnings in 7.39s` (existing Rasterio fixture warnings).
- Post-change `$UV run ruff check .`: `All checks passed!`; `git diff --check`: exit 0.
- Lead requested amendment; this follow-up amends the latest commit (formerly `c1a703c`) without modifying other files. Previous timestamp commit `2a2fc29` remains unchanged. Three files changed in this review follow-up, including this report.

---

# implement — event warning follow-up (unit/lsltime, t120)

Requested after timestamp commit `2a2fc29`; only the two explicitly added event JSON files plus this report changed.

- `events/wa-2025-12.json`: adds the supplied NWS Seattle Flood Watch issuance (`2025-12-06T00:10:00Z`) and VTEC URL; clarifies that the damage anchor's `2025-12-09T00:35:00Z` is the Flood Warning Center opening for Phase 2 operations, not a separate public alert.
- `events/rs-2024-05.json`: adds the supplied INMET warning (`2024-04-26T16:50:00Z`), source URL, and earlier date-only forecast caveat; qualifies the April 27 damage as state-level RS rain/flood reports, not first damage in Canoas. Existing date and alternate sources retained.
- Sources/times were supplied by lead-gathering; this follow-up does not independently verify the cited publications.

Verification on base `2a2fc29` plus the event diff:

- `$UV run python -m json.tool events/wa-2025-12.json > /dev/null`: exit 0.
- `$UV run python -m json.tool events/rs-2024-05.json > /dev/null`: exit 0.
- `$UV run pytest pipeline/tests/test_config.py -q`: `7 passed in 0.03s`.
- `$UV run pytest pipeline/tests -q`: `300 passed, 9 warnings in 7.49s` (same Rasterio fixture warnings).
- `$UV run ruff check .`: `All checks passed!`.
- `git diff --check`: exit 0.

---

# implement — landslide timestamps (unit/lsltime, t120)

## Changed

- `pipeline/fetch/glc.py`: emits `t_utc` and `t_precision` (`hour`/`date`/`none`), preserving existing catalog properties. ArcGIS epoch dates retain day precision; date-only anchors use local noon (America/Los_Angeles for WA; America/Sao_Paulo for RS). Explicit offsets are converted to UTC; naive datetime values use the case zone.
- `pipeline/fetch/inventory.py`: emits the same properties, using local noon America/Sao_Paulo for date-only rows; omits `t_utc` for undated rows. Handles sparse date columns across concatenated inventories and retains all of the final event-window day. Native midnight values without explicit textual time are conservatively date precision.
- `pipeline/fetch/reference.py`: inspected; no code/API change needed. Existing staging preserves the new properties.
- Tests in `test_fetch_glc.py`, `test_fetch_inventory.py`, `test_fetch_reference.py`: 12 added cases covering epoch dates, strings/native dates, offset/naive/UTC datetimes, null/invalid dates, sparse mixed-inventory dates, empty AOI, local-noon DST conversion, and reference passthrough.
- No GeoJSON products regenerated. Code/test diff: 5 files, 141 insertions, 25 deletions.

## Commands and results

Base SHA: `c7e5322`; commands ran on the unit/lsltime working tree containing the changes above. UV below is `$HOME/.local/share/mise/installs/uv/0.13.8/uv-x86_64-unknown-linux-gnu/uv`.

- `$UV sync`: exit 0; Python 3.15.2 environment, 119 packages installed.
- `$UV run pytest pipeline/tests -q`: final result `300 passed, 9 warnings in 9.35s` (warnings are Rasterio NotGeoreferencedWarning from existing bundle fixtures).
- `$UV run pytest pipeline/tests/test_fetch_glc.py pipeline/tests/test_fetch_inventory.py pipeline/tests/test_fetch_reference.py -q`: `38 passed in 0.69s`.
- `$UV run ruff check .`: `All checks passed!`.
- `git diff --check`: exit 0.

Contract update from lead-gathering applied before final verification: property is `t_precision`, and date-only GLC anchors also use local noon. Shared Herdr to-do item 1 was accidentally reworded, then restored verbatim with its done state preserved after lead supplied its original text.

---

# implement — ingest (GEE asset ingest unit, t9r) — round 3 fixes

## Changed files

- `pipeline/fetch/ingest.py` (+26, amended +29): new
  `ensure_collection(asset_parent_id, ee_data=None)` — validates the id,
  `getAsset` → `exists` skip (only a not-found proceeds, auth errors
  propagate), else `createAsset({"type": "IMAGE_COLLECTION"}, path=...)` →
  `created`. Race-safe per review: createAsset wrapped — already-exists
  errors (409 / ALREADY_EXISTS / text) return `exists` via `_is_already_exists`
  (same status-first/auth-veto rule as `_is_not_found`); other errors raise.
  `ee_data` injectable; otherwise creds + `_init_ee()` + lazy `ee` import.
- `pipeline/fetch/imerg.py`: case-hindcast mode.
  - `collection_path(case)` replaces hard-coded `COLLECTION` (now-mode
    default unchanged); `asset_id(project, tag, case="now")`.
  - `--case CASE`, `--start`, `--end` flags. Case mode resolves bounds from
    `events/<case>.json` (`aoi.bbox` → `gee.aoi_bbox`) unless `--bounds`;
    window defaults to events `window`/`gee.window` unless flags given.
    `--start/--end` without `--case` → SystemExit. `--dry-run` prints
    `plan_case` JSON.
  - `search_granules_range` (temporal start..end+1d), `granule_start_tag`
    (IMERG `S`-stamp regex, ISO fallback) → per-granule ids
    `<case>/imerg_early/<yyyymmddThhmm>`; `load_event`, `case_bounds`,
    `case_window`.
  - `run_case`: search → download → `open_halfhourly` per granule →
    `write_cog` → `ensure_collection` once → `stage_and_ingest(case=...)`.
  - `stage_and_ingest` gained `case` kwarg (default `"now"`); staging blob
    name changed `cog/imerg_early/<file>.tif` → `cog-imerg_early-<stem>`
    because real `staging_uri` rejects `/` in name (was unreachable code
    before — only fakes had exercised it).
  - `scan_cogs` product detection: `T`-tags → `halfhourly`.
- `pipeline/tests/test_ingest.py`: fake ee records `createAsset` (feeds the
  asset store); +5 `ensure_collection` tests (create-once, injected
  ee_data, create-race → exists across text/409/grpc-ALREADY_EXISTS,
  non-exists + auth-veto create errors propagate, id validation).
- `pipeline/tests/test_fetch_now.py`: fake ee + fake storage helpers; +7
  tests — granule tag parsing, collection created once + 3 child ids
  `rs-2024-05/imerg_early/<yyyymmddThhmm>`, `--start/--end` temporal window,
  events-json bounds + `--bounds` override, second-run idempotency (1
  createAsset, 3 startIngestion), dry-run plan, events-window default,
  `--start/--end` without `--case` rejected. All offline (faked
  earthaccess/ee/storage/rasterio; `tp.events.DATA_DIR` → tmp_path).

## Commands

- `$UV sync` — ok
- `$UV run pytest pipeline/tests -q` — **301 passed** (+12 new tests)
- `$UV run ruff check .` — All checks passed

## Notes

Commit: files above on `unit/opera-dswx`, not pushed.
- `pipeline/fetch/reference.py` (new, ~287 lines) — library + CLI per the
  interface update: `python -m pipeline.fetch.reference --kind
  glc|inventory|boundaries|all [--case id (repeatable) | --all] [--refresh]
  [--dry-run] [--force] [--window]`. No tools/ file.
  - Product map: `boundaries` -> `geojson/municipal_boundaries.geojson` ->
    table asset `municipal_boundaries`; `glc`/`inventory` ->
    `geojson/landslide_catalog.geojson` -> table asset `landslide_catalog`;
    under `projects/<GEE_PROJECT>/assets/tipping-point/<case>/`.
  - `--kind all` = boundaries + the case's catalog source (wa-* -> glc,
    else inventory — same split as `pipeline/scripts/12_aux_products.py`).
  - INGEST CONTRACT: `stage_to_gcs` / `ingest_asset` / `append_manifest`
    imported from `fetch.ingest` inside try/except (relative then absolute)
    -> `None` when unit/ingest is unmerged; `--dry-run` still works, real
    runs raise a clean `RuntimeError` naming the module.
  - `t_utc`: added to catalog feature properties only when a date exists
    (`t_utc`, then `event_date|date|datetime|timestamp`; ISO strings +
    epoch s/ms; naive treated as UTC) -> `%Y-%m-%dT%H:%M:%SZ`. Nothing is
    invented — rs inventory points stay date-less. If no feature gains
    `t_utc` (boundaries always; undated catalogs) the SOURCE file is staged
    byte-identical (no staging copy). Attribution props pass through
    untouched.
  - Idempotent via `data/<case>/assets.json` (tolerant reader: list,
    dict-with-`assets`, or name-keyed dict — matches fetch.ingest's
    `tipping-point/gee-assets@1` doc) -> `skipped` unless `--force`.
  - `--refresh` re-runs `fetch.{boundaries,glc,inventory}` for every event
    mapping to the case before ingesting; in `--dry-run` it reports
    `would re-fetch <ids>` without side effects.
  - `--window START,END` (with `--refresh --kind glc`): explicit ISO-date
    filter forwarded to `glc_catalog(window=...)`; without it the fetcher
    is called bare and `event['glc_window']` applies inside glc.py — the
    whole `event['window']` is never used.
  - Env: `GEE_PROJECT` (default `hermes-fesolucoes`), `NOW_GCS_BUCKET`
    (then `GCS_BUCKET` — same order/gate as fetch.ingest and live/).
    Missing bucket on a real run -> `RuntimeError` naming the var.
  - Staging copies, when needed, go to `data/<case>/staging/<file>`.

- `pipeline/fetch/glc.py` (scope addition) — `_query_params` default is now
  bbox-only (`where: "1=1"`, no `event_date` clause): the GLC is a
  reference catalog and community reports lag (259 all-time features in the
  wa bbox vs 0 in Dec-2025). Optional window via `event['glc_window']` or
  `glc_catalog(event, window=...)`. `event_date` epoch-ms -> ISO
  normalization kept.

- `pipeline/tests/test_fetch_reference.py` (new, 15 tests incl. 3-param
  refresh) — a fake `fetch.ingest` module built on fake
  `google.cloud.storage` (`Client.bucket().blob().upload_from_filename`)
  and fake `ee.data` (`getAsset`/`startTableIngestion`) injected via
  `sys.modules["fetch.ingest"]` + `importlib.reload`, proving the real
  `from .ingest import` contract path. Covers: product->asset mapping +
  `gs://` staging URI, `t_utc` present on dated / absent on undated
  features (incl. `event_date: null`), attribution preserved, source staged
  byte-identical when undated, idempotent skip + `--force`, manifest entry
  written, `--refresh` calls the matching fetcher per kind, `--window`
  forwards the event window (glc only), `--kind all --all` mapping,
  wa -> glc pick, dry-run zero side-effects, dry-run works when ingest
  names are `None` (+ RuntimeError on real run), missing product, missing
  bucket env. No network.

- `pipeline/tests/test_fetch_glc.py` — default assert now `where == "1=1"`
  (no DATE clause); new `test_query_params_window_opt_in` covers both
  `event['glc_window']` and the explicit `window=` arg.

## Commands & results

- `$UV sync` — ok (editable install; `pipeline/` on sys.path via .pth, so
  `tp`/`fetch` are top-level and `python -m pipeline.fetch.reference` works
  through the namespace package).
- `$UV run pytest pipeline/tests -q` — **108 passed** (was 92 before this
  unit: +16 new, glc suite updated 4->5).
- `$UV run pytest pipeline/tests/test_fetch_reference.py -q` — 16 passed.
- `$UV run ruff check .` — All checks passed (after fixing FURB162 + 2
  style hits).
- `python -m pipeline.fetch.reference --kind all --all --dry-run` — prints
  all four `case/product -> projects/hermes-fesolucoes/assets/tipping-
  point/<case>/<name>` lines against the real data/.
- `python -m pipeline.fetch.reference --kind boundaries --case rs_2024`
  (no ingest.py, no creds) — clean `RuntimeError: fetch.ingest is not
  importable ... only --dry-run works`.
- `--dry-run --refresh` — `rs_2024/landslide_catalog: dry-run -> ...
  (would re-fetch rs-2024-05)`.

## Notes for the lead

- Real ingestion is blocked on `hermes-fesolucoes` not being registered
  with Earth Engine + no GCS creds in this env — dry-run + fake-SDK tests
  are the proof, same caveat as unit/ingest.
- `fetch.ingest` doesn't exist on this branch yet; contract verified
  against the sibling worktree copy (entry shape `{"schema",
  "assets":[{name,gee_id,gcs_uri,kind,status,ingested_at,operation?}]}`;
  staged URI is `gs://<bucket>/staging/<case>/<name><ext>`).
- No subagents used. Two `todo` items logged (L8a reference unit, glc fix).
## Behaviour

- Iterates events/*.json with a `nisar` block (`--case` accepts case key
  `wa_2025` or event id `wa-2025-12`). rs_2024 (maturity null, empty frames)
  still searched — spec says "with a nisar block".
- Known scenes = `nisar.frames[*].scenes` granule_ids/dates (dict+list+flat
  shapes tolerated, same as `tp.nisar.download_plan`) + any string matching
  `nisar_gcov_\d{8}` inside data/<case>/assets.json entries (tolerates list,
  {"assets":[...]} and {"<id>":{...}} shapes).
- `since` = --since > max known scene date > min(window.start or
  reference_date, today). REVIEW-FIX: window.end fallback could be
  future-dated and dropped passes between the event and its end; the
  min(..., today) clamp keeps a future window.start from returning empty.
- Search: lazy `import asf_search`, `search(shortName=<collections.BETA or
  NISAR_L2_GCOV_BETA_V1>, intersectsWith=<wkt>, start=<since>)` per AOI.
  VERIFIED LIVE (anonymous): 14 GCOV granules over the wa AOI since
  2025-12-16. NB: adding `dataset=DATASET.NISAR` alongside shortName silently
  defeats the filter (9176 results, all NISAR products) — kept shortName only.
- Dedup: granule_id/date in known -> "known-scene"; asset_id in assets.json
  -> "asset-exists"; second granule on an already-planned date ->
  "duplicate-date". One asset per date (nisar_gcov_<yyyymmdd>); event's
  declared (track,look,frame) wins the same-date pick (stable pre-sort).
- --alerts-geojson: data/now/alerts.json candidates' centroids -> ±0.25 deg
  box AOIs appended to the search (GeoJSON FeatureCollections also accepted).
- --execute: creds EARTHDATA_TOKEN > tp.nisar._edl_credentials
  (EDL_USER/EDL_PASS/netrc); GDAL env = GDAL_HTTP_BEARER (token) or
  tp.nisar._gdal_auth_env. Per scene: `tp.nisar._subset_h5_variable` ->
  data/raw/nisar/<case>_nisar_gcov_<ymd>.tif (gitignored), then
  stage_to_gcs -> ingest_asset(asset_id=projects/<GEE_PROJECT>/assets/
  tipping-point/<case>/nisar/nisar_gcov_<ymd>, kind='image') ->
  append_manifest. Requires NOW_GCS_BUCKET + GEE_PROJECT + fetch.ingest;
  clear RuntimeError naming whichever is missing. Idempotent: assets.json
  re-read per case at execute time; second run over same plan is a no-op.
- fetch.ingest imported lazily inside _load_ingest() (try/except) so
  --dry-run works while ingest.py only exists on unit/ingest. Contract names
  used exactly: stage_to_gcs(local_path, *, bucket, case, name),
  ingest_asset(gcs_uri, *, asset_id, kind='image', force=False),
  append_manifest(case, entry). asset_id is passed as the FULL gee path;
  manifest entry carries both asset_id (short) and gee_asset — reconcile if
  ingest lands expecting the short name.

## Plan JSON (--dry-run stdout)

`{schema: tipping-point/nisar-update@1, generated_at, alert_aois, cases:
{<case>: {event_id, collection, since, bbox, search_aois[], known_dates[],
found, new_scenes[{granule_id,date,track,look,frame,asset_id,gee_asset,
variable,h5_path,h5_url,cog}], skipped[{granule_id,reason}]}}}` — plus
`executed[]` when run with --execute.

## Commands + results

- `$UV sync` — ok (141 resolved, asf_search 14.1.0)
- `$UV run pytest pipeline/tests/test_fetch_nisar.py -q` — 16 passed
- `$UV run pytest pipeline/tests -q` — 107 passed (whole suite)
- `$UV run ruff check .` — All checks passed (auto-fixed import sort)
- LIVE `python -m pipeline.fetch.nisar --case wa_2025 --dry-run` — real asf
  metadata search OK: found 14, new dates 2025-12-23/28, 2026-01-02/04/09
  (declared frame 064 picked over 065 on shared dates), h5_url resolves to
  nisar.asf.earthdatacloud.nasa.gov .h5 links.

## Notes / risks

- --execute untested end-to-end (no creds/GCS/GEE in env; fetch.ingest absent
  on this branch). All ingest calls verified against the fake module.
- GDAL_HTTP_BEARER for EARTHDATA_TOKEN assumed (GDAL >=3.9); user/pass path
  reuses tp.nisar._gdal_auth_env unchanged.

## OPERA full-suite isolation follow-up

Rebased unit/opera-dswx onto main (3ae2211), including the ingest library.
Changed only pipeline/fetch/opera.py, pipeline/tests/test_fetch_opera.py,
and this report.

- _ingest() now uses call-time importlib.import_module with the current
  package name. Unlike `from . import ingest`, this respects sys.modules
  replacement even when the package retains a cached real ingest attribute.
- The download test patches both fetch.ingest and pipeline.fetch.ingest;
  monkeypatch restores those entries. Added an explicit cached-package
  attribute regression test using a disposable sentinel (no real SDK call).
- Autouse fixture clears all eight requested credential/config variables
  before every test and registers restoration even for variables created
  directly by _edl_login (EARTHDATA_USERNAME/PASSWORD).
- NOW_GCS_BUCKET is validated before Earthdata login for non-deduplicated
  assets. Regression test verifies a missing bucket causes zero login calls.

Commands/results:
- git rebase main: successful.
- $UV run pytest pipeline/tests/test_fetch_opera.py -q: 15 passed.
- $UV run pytest pipeline/tests live/tests ml/tests -q: 268 passed,
  27 warnings (raster georeferencing and dependency deprecations).
- $UV run ruff check .: All checks passed.

The original OPERA commit is already on main behind two other-unit commits;
this fix is a separate commit rather than amending unrelated HEAD history.
EARTHDATA_TOKEN-first auth wired everywhere in scope; EDL_USER/EDL_PASS and
~/.netrc fallbacks preserved; clean RuntimeError when nothing set. Committed
on branch `unit/edlauth` as `e83bab6` (amended once for the review
NEEDS-FIX; no push, no merge).

## Review round 2 (NEEDS-FIX — credential-leak sanitization)

- `fetch/edl.py::earthaccess_login` no longer interpolates the raw
  exception (`{e}`) into the raised RuntimeError — an underlying auth
  error can echo credentials. It now raises a fixed
  "Earthdata login failed: check EARTHDATA_TOKEN, EDL_USER/EDL_PASS, or
  ~/.netrc credentials" message, still chained `from e` for tracebacks.
- Committed as `fa749fd` on `unit/edlauth` (small follow-up commit — HEAD
  was the `8e8b6ce` report commit; amending it would have folded a source
  change into a docs commit).
- `ruff check .` clean; `pytest pipeline/tests -q` — **211 passed, 1
  failed**: `test_fetch_opera.py::test_fetch_scenes_download_ingests`,
  which is NOT mine and NOT caused by this change (verified by stashing
  the edit — it still fails). Cause: cross-unit test-isolation bug —
  `test_ingest.py` imports real `fetch.ingest` at collection time, binding
  it as a `fetch` package attribute, which defeats `opera._ingest()`'s
  `from . import ingest` + the test's `sys.modules["fetch.ingest"]` fake.
  Already fixed upstream: `e52403d "Keep OPERA ingest mocks isolated from
  cached package imports"` on main / unit/nisar-update / unit/opera-dswx.
  This worktree predates that merge; not cherry-picked (out of scope).
# implement — Modo Agora inputs (IMERG Early, SMAP L4, GEOS-FP)

Branch `unit/now-inputs`, worktree `now-inputs`. Scope per the interface update:
three `python -m` fetch modules + one offline test file. No tools/ file, no
now_inputs.py, no live/ edits. Not pushed, not merged.

## Files created (4)

| File | Lines | Role |
|---|---|---|
| `pipeline/fetch/imerg.py` | ~440 | GPM_3IMERGHHE v07 -> daily mm/day + rolling 3-day COGs -> `now/imerg_early` |
| `pipeline/fetch/smap.py` | ~330 | SPL4SMGP v008 sm_rootzone_wetness -> regular-grid daily COG -> `now/smap_l4` |
| `pipeline/fetch/geosfp.py` | ~380 | GMAO GDS `.ascii` fcast PRECTOT -> per-lead-day COGs D+1..D+3 -> `now/geosfp` |
| `pipeline/tests/test_fetch_now.py` | ~620 | 28 offline tests (fake earthaccess, rasterio, requests, ingest) |

Not modified: `.gitignore` (see Open items), any existing file.

## Interface, as implemented

- CLI per module: `--date`, `--bounds W S E N` (default `NOW_BOUNDS` = v4 manifest
  bounds -58,-34,-48,-22.5), `--days`, `--out-dir`, `--assets-json`, `--bucket`
  (env `NOW_GCS_BUCKET`, default `tipping-point-data`), `--project` (env
  `GEE_PROJECT`), `--dry-run`, `--no-download`, `--no-ingest`, `--force`.
- Collections: `projects/<GEE_PROJECT>/assets/tipping-point/now/{imerg_early,smap_l4,geosfp}`.
- Asset ids: `.../<collection>/<YYYYMMDD>` (imerg 3-day: `<YYYYMMDD>_3day`;
  geosfp tag = forecast valid date).
- Outputs: `data/now/cog/<leaf>/<leaf>_<tag>.tif`, EPSG:4326 float32, NaN nodata.
- Idempotency: `data/now/assets.json`, read by a schema-tolerant scan of
  `asset_id`/`asset`/`id` keys; matching ids skip unless `--force`.
- Auth split: CMR search is anonymous in all three; `download_granules` is the
  only place that calls `earthaccess.login`. Order: `EARTHDATA_TOKEN` bearer →
  `EDL_USER`+`EDL_PASS` pair (mapped onto `EARTHDATA_USERNAME`/`PASSWORD`,
  partial pairs rejected — same rule as `fetch/opera.py`, review fix) →
  `~/.netrc`. Clean `RuntimeError` otherwise. `GOOGLE_APPLICATION_CREDENTIALS`
  is read by Google's libraries, not by these modules.
- Ingest contract: `stage_to_gcs(path, bucket=, case='now', name=)`,
  `ingest_asset(uri, asset_id=, kind='image', force=)`,
  `append_manifest('now', entry)`. Looked up best-effort by module name at import;
  if absent, status is `ingest_unavailable` and `--dry-run` / `--no-download` /
  `--no-ingest` still work.

## Math (covered by tests)

- IMERG: daily = sum(mm/h half-hourly) * 0.5. 3-day = rolling sum over 3
  calendar days, min_periods=3, so the first two days are NaN. Test: 2 mm/h
  gives 48 mm/day and 144 mm rolling. Partial last day counted (24 steps -> 24 mm).
- SMAP: EASE 2-D cell lat/lon scattered -> regular 0.09 deg grid via linear
  griddata with nearest fill. Coverage is gridded over all source cells, so a
  hole stays NaN. Mirrors the `run_now` rule.
- GEOS-FP: PRECTOT (mm/s) * 3600 summed per UTC day. Newest run whose coverage
  reaches end of D+N (last step >= 23:00). Runs that are too short fall back to
  older runs. Time decode and parsers are the live/fetch.py logic, condensed.

## Commands run

- `$UV sync` — resolved 141 packages, ok.
- `$UV run pytest pipeline/tests/test_fetch_now.py -q` — first run: 5 failed,
  21 passed. Fixed: fake rasterio never created the output file (test fake, now
  touches on close), `_gds_vector` missing trailing newline (test fake), and
  SMAP coverage mask computed over valid points only (real bug, now gridded
  over all source cells). Rerun: **26 passed**; **28** after the review fix
  (+ partial-creds rejection + netrc fallback tests).
- `$UV run ruff check .` — first run: 8 errors (unused noqa x4, unused unpacked
  vars x3, missing `check=` x1). All fixed. Rerun: **All checks passed**.
- `$UV run pytest pipeline/tests -q` — **119 passed** (full suite, no regressions).
- Review NEEDS-FIX applied + amended (`128844d` → `d782095`): `_edl_login` in
  imerg.py/smap.py now requires the full `EDL_USER`+`EDL_PASS` pair before
  mapping (partial creds rejected, `~/.netrc` fallback added), mirroring
  `fetch/opera.py` on unit/opera-dswx.
- `$UV run python -m pipeline.fetch.{imerg,smap,geosfp} --dry-run --date 2026-11-14`
  — each prints valid plan JSON, rc=0.
- `python -m pipeline.fetch.imerg --no-download` with no GEE_PROJECT — clean
  `RuntimeError: set GEE_PROJECT or pass --project`, raised before any network call.

## Not verified (no network, no creds)

- Real CMR short names/versions (GPM_3IMERGHHE/07, SPL4SMGP/008) and the
  SMAP `native-id` date format were not checked online. Test fakes assume them.
- The GEOS-FP `.ascii` path is the live/fetch.py logic, copied; not run against
  the live server.
- rasterio COG driver path is covered only by the GTiff-fallback fake.
- No earthaccess download, no GCS staging, no GEE ingest was exercised. Those
  need real credentials and unit/ingest.

## Open items / deviations

1. **`.gitignore` not changed.** `now.yml` runs `git-auto-commit` with
   `file_pattern: data/now/**`. The default `--out-dir` writes COGs to
   `data/now/cog/<leaf>/`, so a CI run would commit those rasters. Needs one of:
   `data/now/cog/imerg_early/`, `data/now/cog/smap_l4/`, `data/now/cog/geosfp/`
   added to `.gitignore`, or the workflow `file_pattern` narrowed. I did not do
   this because it is outside the file scope. Owner: lead-live or conductor.
2. **Ingest module name is a guess.** `_INGEST_CANDIDATES` tries
   `tp.ingest`, `tools.ingest`, and a few variants. Once unit/ingest lands, replace
   this with the real import path.
3. **Duplicated plumbing.** `write_cog`, `manifest_asset_ids`,
   `stage_and_ingest`, `_load_ingest`, and `_resolve_*` are copied across the
   three modules. Moving them into one shared helper module would be cleaner,
   but that would be a fourth file, which the scope excluded.
4. **Flag coupling.** `imerg.py` duplicates `run_now.py` logic in
   `_subset` and the 0.5 factor. This is intentional so the module has no import
   from `live/`.
pipeline/fetch/ingest.py, pipeline/fetch/opera.py,
pipeline/tests/test_ingest.py, .herdr/implement.md
EE init via shared `gee.common.init` (SA creds from
GCP_SA_KEY/GEE_SERVICE_ACCOUNT_KEY or ADC via
GOOGLE_APPLICATION_CREDENTIALS); `data/<case>/assets.json` upserts keyed
by name; `_is_not_found` = status/code first, text fallback with auth
veto.

## Addendum — ruling L9 (bounds default source)

- `--bounds` default is no longer the constant: each module resolves the AOI
  lazily via `now_bounds()` → `tp.config.now_bounds()` (config.yaml
  `domains.now`), falling back to the bundled `NOW_BOUNDS` while the helper
  doesn't exist here. `parse_args` uses `default=None`; `main`, `run` and
  `plan` all resolve `None` → `now_bounds()`, so programmatic callers get the
  same default. `--bounds` still overrides. New test
  `test_now_bounds_resolution` covers fallback, config-wins and CLI override.
- Branch was already rebased onto current main (my commit is `f52fe7c`,
  followed by report commit `cddea0b`), so the L9 change went in as a NEW
  commit — amending would have folded the shared implement.md report commit
  into mine.
- **Pre-existing failure, not mine:**
  `pipeline/tests/test_fetch_nisar.py::test_execute_requires_creds` —
  `fetch/nisar.py:365` calls `tp_nisar._edl_credentials`, removed by edlauth's
  `d2ccd35` (`fetch/edl.py` now owns EDL auth). unit/nisar-update drift,
  fails without my change. Owner: lead-gathering to route a fix.
- Since the rebase, `fetch/ingest.py` + `fetch/edl.py` exist:
  `_INGEST_CANDIDATES` resolves the real contract (`fetch.ingest` /
  `pipeline.fetch.ingest`) and my `_edl_login` duplicates `edl.earthaccess_login`
  semantics — a future cleanup could swap to `fetch.edl` (left as-is: out of
  the L9 scope).

---

## unit/nowbounds — drop NOW_BOUNDS fallback (2026-11-14, devin)

Conductor ruling: tp.config.now_bounds() exists on main (unit/domains), so
the fetch modules' bundled constant + ImportError fallback are dead weight.

### Changed
- `pipeline/fetch/imerg.py`, `pipeline/fetch/smap.py`,
  `pipeline/fetch/geosfp.py`: removed `NOW_BOUNDS` constant (+ its comment
  block) and the `try/except ImportError` fallback in `now_bounds()` — now
  imports and returns `tp.config.now_bounds()` directly; docstrings updated.
  `--bounds` still overrides (unchanged call sites).
- `pipeline/tests/test_fetch_now.py::test_now_bounds_resolution`: dropped
  the "helper absent -> bundled fallback" leg; keeps the config-wins leg
  (monkeypatched `tp.config.now_bounds`) and the `--bounds` override leg.
- Not touched: `live/config.py` (lead-live's), `test_domains.py`
  KNOWN_MIRRORS (per ruling — tolerated entries stay listed).

Diff: 4 files, +9/-46. Commit `acbf002` on `unit/nowbounds`.

### Commands run
- `$UV sync` — resolved 141 pkgs, installed 119 (uv 0.13.8, mise path).
- `$UV run pytest pipeline/tests -q` — **294 passed**, 9 warnings, 11s.
- `$UV run ruff check .` — **All checks passed!**
- Branch `unit/imerg-range`, worktree `tipping-point-data-wt/imerg-range`.
- Second run of `run_case` skips re-ingest at the GEE layer (`getAsset`
  finds children → status `exists`), not via assets.json — real
  `append_manifest` entries key `gee_id`, which `manifest_asset_ids`
  doesn't scan; harmless since ingest_asset itself is idempotent.
# L10 — no-GCS ingest rework

Ruling L10 applied: vector/table assets ingest bucket-free via a client-side
`ee.FeatureCollection` + one `ee.batch.Export.table.toAsset` task. Research
(r-eeupload) confirmed NO bucket-free raster upload exists — the raster
ingest path was dropped entirely; `--ingest` is warn-only. L10b:
`fetch/opera.py` deleted (OPERA is in the EE catalog).

## Files changed

- `pipeline/fetch/ingest.py` — now table-only. New `ingest_table(geojson_path,
  *, asset_id, force=False, chunk_size=TABLE_CHUNK_FEATURES, description=None)`:
  reads the local GeoJSON FC, wraps features in `ee.Feature` in chunks of
  5000, merges chunks via `ee.FeatureCollection(fcs).flatten()` (single
  chunk used directly; empty FC handled), exports with one
  `Export.table.toAsset` task (`overwrite=force`), `task.start()`, task id
  recorded as `operation`. Same strict getAsset exists-check / not-found
  semantics. Removed `ingest_asset`, `stage_to_gcs`, `staging_uri`,
  `_storage_client`, `_sa_key_path` and the storage/uuid/tempfile imports.
  `staging_bucket()` kept as a LEGACY env helper only — tp.fallback and
  geosfp/imerg/smap still resolve it (out of scope; their units rework).
- `pipeline/fetch/reference.py` — `ingest_product` calls `ingest_table` on
  the (possibly t_utc-rewritten) staged GeoJSON. `bucket` param/env reads/
  required check and `gcs_uri` fields removed.
- `pipeline/fetch/swot.py` — `run()` drops `bucket`/`--bucket`; local
  staging GeoJSON goes to `ingest_table`; entry drops `gcs_uri`.
- `pipeline/fetch/nisar.py` — `execute_plan()` downloads + clips locally,
  manifest entries `status: 'local-only'` with planned `gee_asset`
  (`<GEE_PROJECT>` placeholder tolerated). `_load_ingest` requires only
  `append_manifest`. `--ingest` accepted but only warns on stderr:
  "not supported without a GCS bucket (L10)".
- `pipeline/fetch/opera.py` + `pipeline/tests/test_fetch_opera.py` —
  DELETED (ruling L10b: OPERA is in the EE catalog). live/jobs.py
  untouched (lead-live owns the opera step swap).
- `pipeline/fetch/edl.py`, `smap.py`, `imerg.py` — docstring mentions of
  fetch/opera fixed (edl.py listed opera among its users; smap/imerg
  pointed at opera for the EDL pair rule — now point at edl.py).
- `tools/ingest_assets.py` — minimal rework: table products via
  `ingest_table`; image products recorded with planned `gee_id` +
  `status: 'local-only'`; bucket env and `gs://` staging removed
  (kept working so its tests stay meaningful).
- `glc.py`/`inventory.py` — no ingest call sites existed (they only write
  GeoJSON; reference.py owns the ingest call), nothing to change.

## Tests

- `test_ingest.py`: fake ee (Feature/FeatureCollection/
  batch.Export.table.toAsset/task.start); ingest_table tests cover export
  params, single-chunk (no flatten), >10k features → 3 chunks
  (5000/5000/2345) + 1 merge FC + flatten + exactly one export, exists-skip,
  force-overwrite, getAsset 404/403/NOT_FOUND semantics, creds, EE-init.
  CLI tests: dry-run/manifest-only have no gs:// anywhere; real run exports
  a table; image registry entries get 'local-only'.
- `test_fetch_reference.py`: fake ingest module = `ingest_table` +
  `append_manifest`; `test_no_bucket_env_needed` proves no bucket env.
- `test_fetch_swot.py`: contract fixture patches `ingest_table`.
- `test_fetch_nisar.py`: fake ingest = append_manifest only; execute is
  local-only with/without bucket+project env; `--ingest` warns and still
  records 'local-only'.

## Commands

- `$UV run pytest pipeline/tests -q` → 310 passed
- `$UV run ruff check pipeline/fetch pipeline/tests pipeline/tp tools` → clean

## Chunk approach

`ingest_table` slices the feature list into `TABLE_CHUNK_FEATURES=5000`
slices, builds one `ee.FeatureCollection([ee.Feature(f)...])` per chunk
client-side, combines them via `ee.FeatureCollection(fcs).flatten()`, and
exports once via `ee.batch.Export.table.toAsset(collection, description,
assetId=..., overwrite=...)` — signature confirmed on ee 1.7.52.

## Left for other units

- tp/fallback.py, geosfp.py, imerg.py, smap.py still carry gs:// raster
  code paths — out of scope; `staging_bucket()` was kept so they keep
  importing cleanly (their stage/ingest calls will hit AttributeError on
  their own guarded paths until reworked).
- live/jobs.py still invokes `pipeline.fetch.opera` — lead-live's swap.


## Review-fix pass (NEEDS-FIX on 7786ba5)

1. **opera.py restored as DEPRECATED STUB** — module docstring + `main()`
   printing 'retired (L10b): OPERA DSWx is in the EE catalog
   (OPERA/DSWX/L3_V1/HLS|S1); no fetch needed' and exiting 0, so
   live/jobs.py's `python -m pipeline.fetch.opera` step stays green until
   lead-live drops it. test_fetch_opera.py replaced by a stub test
   asserting the message + exit 0.
2. **geosfp/imerg/smap raster ingest removed** — `stage_to_gcs` /
   `ingest_asset` imports, `--bucket`, `_resolve_bucket`, `DEFAULT_BUCKET`
   and the gs:// staging calls all deleted. `stage_and_ingest` now prints
   'raster ingest is not supported without a GCS bucket (L10)' on stderr
   and records each COG with status `local-only` in assets.json (still
   idempotent via manifest; `--no-ingest` keeps status `planned`).
3. **ingest_table chunking reworked** — no more
   `ee.FeatureCollection(fcs).flatten()` (that still sent every chunk in
   one request). Each ≤5000-feature chunk is now a client-side
   ee.FeatureCollection uploaded by its OWN `Export.table.toAsset` task.
   Single-chunk catalogs land at `asset_id`; larger ones land at
   `<asset_id>_part00`, `_part01`, … with per-part getAsset idempotency
   and a `parts` list in the manifest entry.

Tests: 312 passed (new: 3-part export assetIds/descriptions for a
12,345-feature FC, per-part skip, opera stub). Ruff: clean.

## Review-fix pass 2 (NEEDS-FIX on aa1d165)

**Part assets now live INSIDE a parent** — `<asset_id>_partNN` suffix
assets were wrong; EE table parts must be children of a container.
`ingest_table` multi-chunk path now calls `_ensure_parent(ee, asset_id)`
(getAsset + strict not-found → `ee.data.createAsset({"type": "FOLDER"})`,
same pattern as live/build_static's `_ensure_collection`) then exports
each chunk to `<asset_id>/part00`, `part01`, …; per-part getAsset
idempotency unchanged (parent folder included — existing parent means no
createAsset call). Single-chunk catalogs still land as a plain table
asset at `asset_id`. Tests: fake ee gained `createAsset` recording; the
12,345-feature test asserts the FOLDER creation + `/partNN` ids, and a
new skip test proves an existing parent+part00 skips both.

Tests: 312 passed. Ruff: clean.

## Rebase onto main (d2526b6+/496666f polygonize)

Rebased `unit/l10-ingest` onto current main. Conflicts resolved keeping
L10 semantics while preserving main's new signatures:

- `pipeline/fetch/imerg.py`: main's `case` param kept end-to-end —
  `stage_and_ingest(..., case="now")` -> `collection_path(case)` /
  `append_manifest(case, entry)`; `run_case`/`plan_case`/`--case` CLI kept
  with bucket plumbing removed (`_resolve_bucket`, `bucket` args,
  `args.bucket`, `plan()["bucket"]` all gone — `--bucket` flag was already
  deleted by L10). ensure_collection getattr import kept; children still
  record `local-only` with the L10 warning.
- `pipeline/tests/test_fetch_now.py`: union — main's `--case` hindcast
  tests kept but reworked to L10 (assert `createAsset(IMAGE_COLLECTION)`
  once, `startIngestion == []`, `uploads == []`, statuses `local-only`);
  stage_and_ingest tests keep `local-only` semantics.
- `pipeline/tests/test_ingest.py`: union fake — HEAD's
  `create_asset(value, path=None)` ("create" calls) kept, my
  `("create_asset", ...)` variant dropped and assertions updated;
  Feature/FeatureCollection/Export.table.toAsset attrs unioned.
- `.herdr/implement.md`: union-merged.

Post-rebase: `pytest pipeline/tests -q` 344 passed; `ruff check` clean.
