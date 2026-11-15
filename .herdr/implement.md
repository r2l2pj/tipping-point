# L25 — live GFS fetcher from NOAA AWS (no GEE)

Branch: `unit/gfsaws`. No push/merge.

## What changed

- `live/fetch.py` — new `fetch_gfs_daily(bounds, issued=None) ->
  (daily xarray(time,lat,lon) 3 steps D+1..D+3, issued_iso)`, mirroring
  `fetch_geosfp_daily`'s contract; `fetch_geosfp_daily` kept untouched as
  the named fallback. New module-private helpers:
  - `_gfs_key` — `gfs.YYYYMMDD/HH/atmos/gfs.tHHz.pgrb2.<res>.fFFF`
  - `_gfs_head_ok` / `_gfs_get_text` / `_gfs_get_range` — HTTP layer
    (anonymous GET/HEAD/Range against
    `https://noaa-gfs-bdp-pds.s3.amazonaws.com`)
  - `_gfs_apcp_range` — parses the sibling `.idx`, returns the byte range
    of the *trailing 6h* APCP message (`(fh-6)-(fh) hour acc fcst`),
    rejecting the since-issuance message (`0-N day acc fcst`)
  - `_gfs_decode_message` — writes the byte-ranged GRIB2 message to a temp
    file, opens with cfgrib/eccodes (`tp`), returns (arr, lats asc,
    lons asc in -180..180)
  - `_gfs_apcp_grid` — 0p25 with 0p50 fallback (+interp) if an hour's idx
    or APCP message is missing at 0.25 deg
  - `_gfs_candidates` / `_gfs_pick_run` — prefer issued-day 00Z, else
    newest cycle at/before issued end-of-day whose `f072.idx` HEADs 200
    (probes back 4 days, 16 candidates)
  - `_gfs_day_label` — a bucket ending at valid time `t` belongs to the
    day `(label-1, label]`, so a 00Z run issued on D gives D+1 = fh 6..24,
    matching `pipeline/fetch/gfs.py::day_sum` exactly (verified against
    its unit tests, incl. non-00Z cycles)
- `live/tests/test_fetch_gfs.py` — 10 offline tests: key layout, idx
  range selection (recorded `.idx` fixtures), day-boundary labelling,
  real-message decode (recorded APCP bytes), run-pick preferences/
  fallback/raise, full fetch bucketing+subset, partial-day 06Z run.
- `live/tests/fixtures/` — `gfs_f006.idx`, `gfs_f024.idx` (recorded idx
  text), `gfs_f006_apcp.grib` (367 KB, one real byte-ranged APCP message
  from `gfs.20240429/00`).
- `pyproject.toml` / `uv.lock` — `cfgrib>=0.9.15.1` +
  `eccodes==2.42.0` (pinned: last release shipping manylinux wheels with
  the bundled C library; 2.50.1 is bindings-only and raised
  "Cannot find the ecCodes library").

## Commands and results

- `uv add cfgrib` then `uv add "eccodes==2.42.0"` (uv at
  `~/.local/share/mise/installs/uv/0.13.8/uv-x86_64-unknown-linux-gnu/uv`;
  the mise shim is broken in this shell) — resolved, installed.
- Reachability verified: `gfs.20240429/00/atmos/gfs.t00z.pgrb2.0p25.f006.idx`
  and `f072.idx` both 200; Range GET of the APCP message returns a
  self-contained `GRIB` blob that cfgrib decodes (721x1440 `tp`).
- `.venv/bin/pytest live/tests/test_fetch_gfs.py -x -q` -> **10 passed**.
- `.venv/bin/pytest live/tests -x -q` -> 91 passed, **1 pre-existing
  failure** `test_publish_fallback.py::test_oversized_and_stale_files` —
  caused by the uncommitted L26 edit to `live/publish_fallback.py`
  already in the worktree (not mine); confirmed unrelated.
- `.venv/bin/ruff check live/fetch.py live/tests/test_fetch_gfs.py` ->
  clean.

## Real verification vs `rs_2024_gfs_20240429.tif`

`fetch_gfs_daily((-52.5,-30.5,-50.5,-29.0), 2024-04-29)` -> issued
`2024-04-29T00:00:00Z`, steps `2024-04-30/05-01/05-02`, grid 9x7 at exact
0.25 deg centres.

**Result: bit-exact match (0.00 mm on all 63 cells, all 3 bands) — but
only when the tif rows are read one grid row north of their stamped
transform.** Direct same-label comparison fails (d1 max diff 7.19 mm,
mean 2.26). Sampling our d1 field at `tif_lat + 0.25` gives max diff
**0.0** for d1, d2, d3; the same +0.25-row rule reproduces
`rs_2024_gfs_20240428.tif` d1 exactly too, so it is systematic, not a
fluke.

Comparison table (d1, tif row at stated lat L vs AWS field at L+0.25):

| lon | lat (tif-stated) | aws mm | gee mm | diff |
|-----|------------------|--------|--------|------|
| -51.50 | -29.50 | 10.625 | 10.625 | 0.00000 |
| -52.50 | -30.50 | 17.625 | 17.625 | 0.00000 |
| -50.50 | -29.00 | 4.375  | 4.375  | 0.00000 |
| -51.00 | -30.00 | 13.500 | 13.500 | 0.00000 |

Diagnosis: our georeference is provably correct — eccodes reports
`La1=90, La2=-90, Dj=0.25, jScansPositively=0` on the message itself, so
our lat values are the true NCEP grid centres. The GEE tif payload rows
instead hold data one row north of the transform written by
`write_cog`'s `expected_transform` snap (the pipeline already documents
GEE stamping WKT-crs downloads "one row too far north"; the recompute
apparently leaves the payload one row south of its true position).
Everything else — run choice, day bucketing, cell values, units — is
identical to the GEE replay, so `fetch_gfs_daily` is verified correct
and the residual is the tif's geotransform quirk, not a data
difference. Also verified: PRATE*21600 gives the same field, and
neighbouring cycles (28/18Z, 29/06Z, 29/12Z) do not match, ruling out a
run-selection discrepancy.

## Notes for lead-live

- Non-00Z fallback runs can only partially cover D+1 (e.g. a D 18Z run
  contributes just the (D 18:00, D+1 00:00] bucket) — same caveat the
  GEE path has; `RuntimeError` if no probed cycle covers to D+3.
- Each fetch pulls 12 Range requests (~370 KB each) + 12 idx files —
  ~5 MB total per call, no full ~300 MB files.
