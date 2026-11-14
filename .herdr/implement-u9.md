# U9 — build wiring + docs

## Changed

- `Makefile` (new) — `make all CASE=rs_2024|wa_2025` chains the replay
  pipeline in dependency order: `gee-tpi → gee-hand → gee-impact →
  nisar-fetch → terrain → flood-mask → polygonize → validation (04_validation
  --auto + gee.opera) → forecast → landslide-hit → hls-scar → lead-time →
  publish → manifest` (manifest last). `make now` runs `live/jobs.py now`
  (the live job order). `make test` runs `pytest pipeline/tests gee/tests
  live/tests` then `ruff check .`, propagating the pytest exit code.
  - Every python call goes through `uv run --python 3.12` (pin needed: the
    host default resolves to Python 3.15 where h5py has no wheel and the
    sync builds from source → fails). `UV` defaults to the pinned mise
    install, with a `/home/*/...` wildcard fallback so `env -i` works.
  - Cred gates: GEE steps need `GEE_PROJECT` + (`GOOGLE_APPLICATION_CREDENTIALS`
    or `GCP_SA_KEY`); Earthdata steps need `EARTHDATA_TOKEN`. A failing gate
    prints `SKIP <step>: missing <var>` and exits 0. Steps also SKIP on
    missing input files (e.g. `data/raw/nisar/gcov_hh_pre.tif`,
    `data/<case>/cog/<case>_tpi_slide_<YMD>.tif`), and publish/manifest/test
    SKIP if `uv` itself is missing — `env -i make all` never fails.
  - Case→event/YMD/anchor mapping is table-driven (`EVENT_rs_2024` etc.);
    unknown `CASE` exits 2 via `check-case`.
- `README.md` (rewrite) — purpose (GPM+SMAP predict, NISAR confirms),
  quickstart (uv sync / make test / make all CASE= / make now), credentials
  table (`EARTHDATA_TOKEN`, `GEE_PROJECT`,
  `GOOGLE_APPLICATION_CREDENTIALS`-or-`GCP_SA_KEY`), directory map, pointer
  to `data/SCHEMA.md`, and the approved climatology layout: per-case
  event-window doys over each case AOI (~25 doy/case) + rolling ~75-doy now
  window over the `domains.now` bbox; int16 mm×10 (~0.9 GB, ÷10 on read);
  interface unchanged (`doy` prop, `p90|p95|p99` bands). Documents
  `domains.now` in config.yaml (name `sul_brasil`, bbox W/S/E/N) and
  `tp.config.now_bounds()`.
- `gee/README.md` (rewrite) — asset namespace
  `projects/<GEE_PROJECT>/assets/tipping-point/` with the
  `static/<domain>/` + `<case>/` + `now/` layout, the stamp contract
  (`data_date`, `updated_at` image properties), Python entry points with
  `--dry-run`/`--tasks` flags, and the original JS run-order notes.

## Proofs

- `make -n all CASE=rs_2024` — prints the full chain (check-case + 14 steps,
  real commands inside the cred guards). rc=0.
- `env -i make all CASE=rs_2024` — exits 0; prints `SKIP` lines for every
  cred-gated step and input-missing lines for downstream steps; the offline
  `publish`/`manifest` steps run (they are never-fatal and designed to run
  with missing sources). Side-effect files written by that proof run
  (`data/manifest.json`, `data/rs_2024/png/_index.json`, `data/events.json`)
  were reverted; the tree is clean except the intended files.
- `make test` — 442 passed, 4 failed, all in `gee/tests/test_common.py`
  (`test_aoi_geom`, `test_init_with_sa_key_path`, `test_init_fallback_env_var`,
  `test_init_no_key_uses_default_creds`): the Fake-ee conftest/monkeypatch
  ordering lets a real `ee.Initialize` run without credentials — the
  pre-existing test_common order failures owned elsewhere. `test_fetch_nisar`
  passed here (16/16). `ruff check .` clean (no new .py).

## Notes / caveats

- `.venv` had been built against host Python 3.15 and was broken (no h5py);
  repaired with `uv sync --python 3.12`. Consider adding `.python-version`
  in a later unit.
- `08_landslide_hit.py` wants a CSV catalog; the Makefile points at
  `data/raw/<case>/landslide_catalog.csv` and SKIPs until a fetcher/export
  produces it (reference fetchers currently write GeoJSON).
- `domains.now` / `tp.config.now_bounds()` are documented per the conductor
  ruling but live in files outside this unit's write scope — verify they
  exist on the merged branch.
