# Deploy

Operator runbook for deploying `tipping-point-app` to Vercel. The app reads
static data files (L53): the public GitHub data branch first, the pipeline's
SQLite `docs` table (`app/.data/tipping_point.sqlite`) next, the bundled copy
last. Earth Engine is not on any runtime path by
default (L25) — it is opt-in with `GEE_ENABLED`.

**Credential rule:** credentials live in environment variables / platform
secrets only — never in the repository. No key, token, or service-account JSON
is ever committed; `.gitignore` already blocks the usual key file patterns.

## Vercel

1. Import the repo into a Vercel project (Hobby plan is fine for a public repo).
   The build runs `prebuild` → `scripts/sync-data.mjs`, which copies artifacts
   from the sibling `tipping-point-data` checkout into `public/data`. On Vercel
   the sibling checkout is absent, `DATA_REPO` is unset, and the sync step
   warns and exits 0 — the committed `public/data` fixtures become the build
   fallback.
2. Set the environment variables below in the Vercel project settings.

| Variable | Purpose |
| --- | --- |
| `DATA_BASE_URL` | Leave **unset** in production. Optional override: when set it is the ONLY remote source (tests pin it unreachable to force the bundled copy). Unset, reads go GitHub `fallback` branch → SQLite `docs` table → bundled copy. |
| `DATA_SQLITE` | Optional path of the SQLite file (default `app/.data/tipping_point.sqlite`, else the monorepo's `data/tipping_point.sqlite`). `DATA_SQLITE_REFRESH=0` stops the background download (every ≤ 3 h from the fallback branch). |
| `GEE_ENABLED` | Optional, default **off** (L25). `1`/`true` turns on the Earth Engine routes (`/api/tiles`, `/api/layers`, `/api/numbers`, `/api/point`), and the GEE overlay of the case loader. Off: those routes answer 503 `runtime GEE disabled` and `/api/metrics` serves the manifest copy. `/api/health` is a liveness probe: 200 `{"ok":true}` while the server is up. |
| `GEE_PROJECT` | Only with `GEE_ENABLED`: Google Cloud project registered for the Earth Engine API. |
| `GCP_SA_KEY` | Only with `GEE_ENABLED`: service-account JSON key (single line). Server-side only — never `NEXT_PUBLIC_`-prefixed. |
| `APP_URL` | Canonical public URL of the deployment (e.g. `https://<project>.vercel.app`), used to register the webhook. |

## Data availability

GitHub is the data store (L26). Read order, per file:

1. **GitHub `fallback` branch (primary)** — orphan branch of
   `r2l2pj/tipping-point-data`, read at
   `https://raw.githubusercontent.com/r2l2pj/tipping-point-data/fallback/data/`.
   The 3-hourly `now.yml` job publishes it with `GITHUB_TOKEN` as one
   force-pushed commit per run (no history growth), app-facing files only,
   `manifest.json` written last. Files > 50 MB go to a GitHub Release asset.
   raw.githubusercontent caches ~5 min; the app tolerates that.
2. **SQLite `docs` table (L53, replaces Neon)** — lead-ml's
   `tipping_point.sqlite` carries every app-facing JSON/GeoJSON by
   data-relative path (gzip). The server keeps a copy in `app/.data/`,
   downloaded from the fallback branch at most every 3 h while GitHub answers,
   and reads it when GitHub misses (PNG frames then come from the bundled copy).
3. **Bundled `public/data`** — committed copy synced by `bun run sync:data`;
   the last resort when every remote read misses.

Browser assets (PNG frames, COGs, lazy GeoJSON) resolve against the remote
that served the root manifest; when GitHub is down they come from the bundled copy.
The "Showing last saved data" status appears only when the root manifest came
from the bundled copy, or when live data is older than 6 h — never because a
single optional file is missing. `DATA_BASE_URL` stays an explicit override
(sole remote source), read at request time; updating data never needs a
redeploy.

## Neon (retired, L53)

Neon is no longer used (L53): the app stopped reading the bucket and the workflows stopped uploading to it; only the legacy `live/publish_s3.sh` and a `static_fetch` fallback path remain until merge step 7 (after 48 h), when the bucket, the Neon project and the `NEON_S3_*` secrets are deleted. Telegram and its Neon Postgres subscriber table were removed earlier (L55).

## Free-tier limits

All runtime services must stay on free tiers; no paid plan is required.

- GEE: non-commercial access.
- GitHub Actions: public-repo workflows.
- `raw.githubusercontent.com`: fallback small files only; no large COGs.
- Vercel Hobby: set API routes' `maxDuration` within the plan's function
  duration limit.
- Earthdata: free access.

## Scheduled jobs (GitHub Actions)

No GEE at runtime (L25): the app serves the static files above; Earth
Engine is only hosted copies + one-off compute. Workflows on the data repo:

- `now.yml` — every 3 h (cron `0 */3 * * *`) + `workflow_dispatch`:
  IMERG Early + SMAP L4 + NOAA GFS forecast rain fetched directly
  (Earthdata / NOAA open data), TPI computed in the job, P(trigger) frames,
  published to the force-pushed `fallback` branch (`manifest.json` written
  last; L26).
- `nisar.yml` — daily: `asf_search` for new GCOV granules over the case AOIs
  and active alert areas.
- `validation.yml` — daily: OPERA DSWx + SWOT Hydrocron table asset.
- `reference.yml` — weekly + `workflow_dispatch`: landslide catalogs +
  municipal boundaries.

Freshness copy is “updated X h ago” against the three-hour refresh cadence.
The stale-data warning threshold is **6 h** since the data update timestamp.

### Command Center settings

One public setting, read by the Command Center screen. It is optional.

| Variable | Default | Effect |
| --- | --- | --- |
| `NEXT_PUBLIC_REFRESH_HOURS` | `3` | Refresh period of the live run in hours. The screen calls the live data "stale" after two periods (6 h by default). Change it together with the cron of `now.yml`. |

`NEXT_PUBLIC_*` values are fixed at build time: redeploy after a change.

A scripted browser pass over the screen lives in
`scripts/command-center-check.mjs` (not a CI job). Its header lists the
settings.

Secrets (`EARTHDATA_TOKEN` ~60-day renewal, `GCP_SA_KEY`) live in GitHub
secrets; a missing/expired token fails the job loudly. Jobs are idempotent,
log their ingestions, and publish the last-good static copy the app falls
back to.

## Data pipeline secrets (GitHub)

These live as GitHub Actions secrets on the data repo — they are NOT app
runtime vars, so keep them out of the Vercel env table.

- `EARTHDATA_TOKEN` — NASA Earthdata bearer token used by the data pipeline
  (earthaccess env strategy / asf_search `auth_with_token` /
  `GDAL_HTTP_HEADERS` `Authorization: Bearer`). Expires after ~60 days, so
  renewal is a recurring ops task.
- `EDL_USER` / `EDL_PASS` — Earthdata login pair, kept only as a local
  fallback for pipeline runs outside CI.

## Prerequisites owned by humans

These are setup steps, not code: the app runs against local fixtures/mocks
until they exist.

- Vercel project created with the env vars above (`DATA_BASE_URL` unset,
  `GEE_ENABLED` unset).
- Optional: `GEE_PROJECT` registered for the Earth Engine API with a service
  account, only if `GEE_ENABLED` is turned on.
