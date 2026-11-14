# U10a — event metadata fixes

## What changed

### events/wa-2025-12.json
- `nisar.frames`: removed the GUNW frame object entirely (track/frame,
  reference_pair, event_pair, collections, note all gone).
- GCOV frame (track 172D, frame 064) now carries a `scenes` list of three
  acquisitions with the verified granule ids from the spec:
  - 2025-11-10, role "no-event reference",
    NISAR_L2_PR_GCOV_004_172_D_064_4005_DHDH_A_20251110T031814_20251110T031849_X05009_N_F_J_001
  - 2025-12-04, role "pre",
    NISAR_L2_PR_GCOV_006_172_D_064_4005_DHDH_A_20251204T031815_20251204T031850_X05009_N_F_J_001
  - 2025-12-16, role "post",
    NISAR_L2_PR_GCOV_007_172_D_064_4005_DHDH_A_20251216T031815_20251216T031850_X05009_N_F_J_001
  `pre`/`post`/`collections` keys kept so `tp.nisar.download_plan` still works.
- Added `nisar.gunw_note`: "No GUNW pair crosses the event — declared
  limitation; landslide candidates come from post-event HLS optical, always
  'candidates' never 'confirmed'; radar confirms floods only."
- Added `swot` block with `reach_ids` from a real lookup (see below).

### events/rs-2024-05.json
- Added `swot` block with `reach_ids` for the Jacui main stem + Guaiba head
  reach at Porto Alegre (see below), plus a `note` documenting why FTS name
  search failed and how the ids were found.

### data/events.json
- wa-2025-12 `reference_date` "2025-12-12" -> "2025-12-10". Single-token edit;
  all other bytes identical (verified via `git diff`).

## Follow-up (ruling E7) — `damage_anchor` blocks

Added a top-level `damage_anchor` object to each `events/*.json` (inserted
between `swot` and `hazards`), verbatim from the lead's spec — earliest
independent, dated, citable first-damage report anchoring the public
"warned X hours before" claim (wording pending Lucas).

- **rs-2024-05**: date `2024-04-27`, `time_utc: null` (date-level confidence),
  MetSul report for Canoas city gov; alternates: Defesa Civil RS alert 254
  (2024-04-28, alert not damage) and State Civil Defense bulletin (2024-04-30).
- **wa-2025-12**: date `2025-12-08`, `time_utc: 2025-12-09T00:35:00Z`
  (timestamped), King County Flood Warning Center opening at Snoqualmie
  Phase 2; alternates: NWS FLW for Cowlitz at Packwood (2025-12-08) and
  Snohomish County release (2025-12-09).

Fields: `date`, `time_utc`, `time_note`, `reported`, `source`, `url`,
`confidence`, `alternates[]` (`date`, `kind`, `url`). Committed as a second
commit on `unit/u10a`; both files re-validated with `python -m json.tool`.

## SWOT reach lookup (real, not null)

- **WA** — FTS name lookup worked:
  `GET https://fts.podaac.earthdata.nasa.gov/v1/rivers/Skagit%20River`
  (8 pages, 722 node hits), filtered to nodes inside the event bbox
  [-122.6, 47.0, -121.5, 48.5] -> 6 distinct reaches, downstream->upstream:
  `78310800011, 78310800021, 78310800031, 78310800055, 78310800071, 78310800141`
  (055 = North Fork delta distributary; dist_out gap 68-121 km is upstream of
  the AOI north edge).

- **RS** — FTS name queries for `Rio Jacuí`, `Rio Jacui`, `Guaíba`, `Guaiba`,
  `Jacui`, `Rio Guaiba` all 404 or match only other rivers (SWORD labels the
  Jacui main stem `no_data`/`Taquari River` in this basin). Fell back to the
  documented spatial path:
  `SWOT_L2_HR_RiverSP_reach_vD_Attributes/FeatureServer/1/query` on
  gis.earthdata.nasa.gov with an esriGeometryEnvelope over the AOI
  (14,628 records -> 112 distinct reaches), then identified the main-stem
  chain around Porto Alegre and ordered it by `dist_out` via
  FTS `/v1/rivers/reach/<id>`:
  `64110700015, 64120100045, 64120100031, 64120100021, 64120100011,
   64120300011, 64120300024, 64120300031, 64120300041, 64120300051,
   64120300061, 64120300071, 64120500011, 64120500021, 64120500031`
  (64110700015 = terminal reach at the Jacui delta / Guaiba head; the 641203+
  reaches continue upstream to the AOI west edge).

## Commands run

- `python3 -m json.tool` on all three edited files -> all parse OK.
- `download_plan` smoke test (earthaccess stubbed; uv/earthaccess not
  installed in this env): wa-2025-12 -> 6 GCOV steps (pre/post x
  HHHH/HVHV/mask) + watermask, no GUNW; rs-2024-05 -> empty plan. Unchanged
  consumer behaviour.

## Not done / limitations

- `reach_ids_reason` not needed — both lookups succeeded.
- RS reach_ids cover the Jacui main stem + Guaiba head only; tributaries
  (Cai 641108*, Sinos, Taquari 641202*, Forqueta, Rio Pardo) are in the AOI
  but out of the spec's river scope.
