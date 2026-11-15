# G1+G2 — Telegram subscriber alerts for Modo Agora (worker live-g1)

## What changed

- **`live/notify.py` (new)** — `python -m live.notify [--out-dir data/now]`,
  always exits 0 (`main` catches everything; store/network/raster errors are
  one log line each).
  - Gates on `data/now/manifest.json`: `alerts_paused` or `verified != true`
    → sends nothing and changes nothing in the store.
  - For each consenting row from `alerts.subscribers()`, samples the four
    published COGs (`cog/tpi_{slide,flood}_{today,d1}.tif`, EPSG:4326) at
    (lon, lat). Classes via `live.tpi.classify` / `config.TPI_CLASSES`
    (0.2/0.4/0.6 — no new thresholds). `evaluate()` picks the max over
    {today,d1}×{landslide,flood}; ties prefer today, landslide only on exact
    ties. NaN/nodata/out-of-extent = class 0.
  - Municipality via `now_geojson.municipality_lookup(out_dir/"geojson")`
    → `{"municipality","uf"}` or None → "sua região" fallback.
  - Send rules: alert only on upgrade to Alto/Crítico (`c >= 2 and
    c > last_class`); ≤1 message per 6 h per chat (`last_alert_ts`, ISO or
    epoch parsed); never 23h–6h America/Sao_Paulo unless Crítico; drops send
    "risco reduzido" (not in quiet hours). Held-back or failed sends keep
    `last_class` unchanged so the next run retries. `current_label`
    (e.g. "Alto — deslizamento, amanhã") and `data_date` update every run.
  - Sends only when `TELEGRAM_TOKEN` is set (`requests.post` to
    `api.telegram.org/bot<token>/sendMessage`, timeout 30, `ok` check like
    `alerts.update_subscribers_from_regions`); without it → labels only, one
    log line.
  - Text (G2): signed "Tipping Point (não oficial)", never attributes to
    Defesa Civil, always ends with the exact line
    "Siga as orientações da Defesa Civil. SMS 40199." Region precision is
    "sua região", never "sua casa". `build_alert_text` / `build_reduced_text`
    build the two shapes; basis comes from the winning layer's
    `basis`/`gfs_issued`/`obs_through` fields (falling back to
    `data_dates`), dates in dd/mm. SMAP date prefers `data_dates.smap_label`,
    else parses ISO or YYYYMMDD out of the granule id.
- **`live/alerts.py`** — `subscribers()` SELECT now also returns
  `last_alert_ts` (needed for the 6 h rate limit; both backends).
  `update_subscribers_from_regions` sends the same signature/footer via
  `live.notify` (lazy import — "— Defesa Civil • Tipping Point" removed).
- **`.github/workflows/now.yml`** — new step "Notify Telegram subscribers"
  after "Publish data/ to fallback branch" (before Neon storage publish):
  `continue-on-error: true`, env `DATABASE_URL`/`TELEGRAM_TOKEN` (secrets)
  and `APP_URL=https://tipping-point-app.vercel.app`,
  `run: uv run python -m live.notify`.
- **`live/tests/test_notify.py` (new, 13 tests)** — sqlite tmp store,
  synthetic 10×10 COGs + manifest + boundaries, monkeypatched
  `requests.post`, injectable `now`: Crítico upgrade sends once with the G2
  text; 6 h rate limit holds then releases; Alto deferred in quiet hours
  (`last_class` kept) while Crítico still sends; drop → "caiu para";
  paused/unverified → nothing; no token → labels only; unreachable store and
  missing manifest → exit 0; out-of-extent → Baixo; `smap_label` preferred;
  APP_URL line omitted when unset; quiet-held reductions retry daytime.
- **`live/tests/test_alerts.py`** — pinned footer assertion updated to the
  new signature ("Tipping Point (não oficial)" + SMS 40199 footer,
  "Defesa Civil •" forbidden).
- **`live/tests/test_workflows.py`** — `test_now_notify_step_after_fallback_publish`
  pins the new step's name/command/env/placement.

## Commands run

- `ruff check live/notify.py live/tests/test_notify.py live/alerts.py
  live/tests/test_workflows.py` → All checks passed (test_alerts.py has two
  pre-existing findings untouched: RUF015 line 69, I001 line 104).
- `pytest -q live/tests/test_notify.py live/tests/test_alerts.py
  live/tests/test_workflows.py` → 67 passed.
- `PYTHONPATH=pipeline:. uv run --no-sync pytest -q pipeline/tests
  live/tests ml/tests` → **763 passed** (32 s).
- `python -m live.notify` on the seed manifest → "[notify] manifest not
  verified — nothing sent", exit 0.
- `git checkout -- data` after the suite (data/ untouched — conftest guard).

## Notes

- `smap_label` isn't in `run_now.py`'s `data_dates` on this branch yet
  (G4 merged elsewhere); `_smap_day` accepts both shapes already.
- `last_class` semantics: it is the class last *communicated* — advances
  only on a delivered send, so sub-threshold oscillation never spams
  "risco reduzido".
