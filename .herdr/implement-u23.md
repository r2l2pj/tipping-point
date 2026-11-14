# U23 — subscriber store (L12c: sqlite + Neon dual backend)

Supabase spec superseded mid-task -> Neon HTTP SQL; then L12c added a sqlite
backend behind the same interface.

## Changed

- `live/alerts.py` — subscriber-store section:
  - `_backend()` chooses store from `DATABASE_URL`: `postgres(s)://` -> "neon";
    unset or `sqlite:<path>` -> "sqlite" (default `.data/tipping-point.sqlite`);
    anything else -> skip.
  - `_sqlite_conn()` opens/creates the db and applies `db/schema.sql` when the
    `subscribers` table is missing.
  - `_neon_endpoint()`/`_neon_query()` POST `https://<host>/sql` with the
    `Neon-Connection-String` header and `{query, params}` body; `{fields, rows}`
    response mapped to `list[dict]`; RuntimeError on non-2xx.
  - `subscribers()` — consent-only `SELECT chat_id, lat, lon, last_class` on
    either backend (`WHERE consent = true` / `WHERE consent`).
  - `update_subscriber(chat_id, fields)` — parameterized UPDATE on either
    backend (`$n` for Neon, `?` for sqlite).
  - `update_subscribers_from_regions(regions, data_date)` — shapely
    point-in-polygon, highest class wins, writes only on class change
    (0->0 skipped); unrecognized scheme or unreachable/erroring store prints
    a clear skip line and returns 0; per-row update failures are logged and
    skipped — store errors never fail the job.
- `db/schema.sql` (new) — portable CREATE TABLE IF NOT EXISTS subscribers
  (TEXT/NUMERIC/BOOLEAN/INTEGER/TEXT; `created_at TEXT DEFAULT
  CURRENT_TIMESTAMP`; no timestamptz).
- `.gitignore` — added `.data/`.
- `live/jobs.py` — `DATABASE_URL` in `SUBPROCESS_ENV_KEYS`; `_refresh_alerts`
  calls the pass before writing alerts.json inside try/except (noqa BLE001,
  same convention as live/fetch.py).
- `.github/workflows/now.yml` — `DATABASE_URL: ${{ secrets.DATABASE_URL }}` on
  the "Run now job" step; cron `0 */3` -> `0 */6` per L13.
- `live/tests/test_alerts.py` — Neon tests vs a REAL HTTPS fake server
  (stdlib http.server + self-signed cert via `cryptography`, trusted through
  `REQUESTS_CA_BUNDLE`; no mocked requests): POST `/sql` + connection-string
  header + `{query, params}` body, fields/rows->dict, UPDATE only for changed
  rows, non-200 -> RuntimeError. Sqlite tests on a real tmp db: update flow
  (inside 0->3, outside 1->0, same unchanged), schema auto-create at the
  default path with DATABASE_URL unset.

## Commands run

- `$UV run pytest live/tests/test_alerts.py -q` -> 10 passed
- `$UV run pytest live/tests/ -q` -> 84 passed, 2 failed (see blocker)
- `$UV run ruff check live/` -> all checks passed
- `yaml.safe_load(now.yml)` -> ok
- Commits on `unit/u23` (not pushed): `1e7966b` (L12b Neon store, L13 cron),
  plus L12c dual-backend commit.

## Blocker (out of file scope)

`live/tests/test_workflows.py` hard-codes the old now cron `0 */3 * * *`
(lines 13 and 77), so `test_parses_and_triggers[now.yml]` and
`test_now_cron_and_commit_paths` fail after the mandated `*/6` change. That
file is outside my write scope — needs its SPECS/assertions updated to
`0 */6 * * *` by whoever owns it.
