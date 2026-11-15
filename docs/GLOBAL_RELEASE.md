# Modo Agora: changing the domain at release (L9 / L9b)

The Now domain comes from one place: `config.yaml` → `domains.now.bbox` (west, south, east, north), read by `live/config.py` (`NOW_BOUNDS`). No code has a literal bbox.

The bbox edit is one line, but the domain also needs **static inputs that cover it**. Until they exist the job fails safe. `clim_checks` sets `clim_covers_domain=false`, so `checks.passed=false`, the manifest publishes `verified=false` / `alerts_paused=true`, and no alert is issued (test: `live/tests/test_static_fetch.py::test_domain_switch_outside_static_cover_is_unverified`).

## What has to be rebuilt for a new domain

| Input | Today (Southern Brazil) | For a new domain |
| --- | --- | --- |
| Rain climatology `clim_imerg_0p1.tif` (static-v1 Release, sha-pinned in now.yml) | IMERG Final 0.1° p90/p95/p99 of 3-day rain by leap-calendar doy, domain subset | Recompute for the new bbox (same script and calendar), upload to the Release, update the sha256 in now.yml, and add ≥3 reference points to `live/clim_reference.json` from an independent IMERG-Final computation |
| Terrain `slope_deg`, `hand_m`, `hand_90m` (static-v1) | NASADEM-derived, domain subset | Re-export for the bbox; `terrain_checks` (slope p99 in [12, 45], T_slide>0 ≥ 1 %) gates them |
| Response statics (gpw, lc_type1, gsw, steep15, municipalities_now) | domain subsets in static-v1 | Re-export; now.yml cache key `static-v1-response-N` must be bumped |
| Municipalities (`municipalities_now.geojson`, IBGE) | Brazil only | Needs a boundary source for the new country (GADM / national agency). Without it alerts have no municipality and `municipalities_loaded` fails |
| Land mask `data/now/static/brazil_outline.geojson` | IBGE outline | A country outline for the new domain |
| P(trigger) (`live/p_trigger.py`) | Brazilian land only, a product-scope decision | Lead-ml decides whether labels and an audience exist for the new domain. Until then, keep `NOW_P_TRIGGER` off there |
| Telegram text | pt-BR, "SMS 40199" (Brazilian Defesa Civil) | Each country needs its own civil-protection contact line |

## Limits

- Grid: the job runs on a 0.01° grid on a GitHub-hosted runner (7 GB RAM). Southern Brazil is ~1150 × 1000 cells. A continent at 0.01° will not fit, so a global release needs either tiling (one job per tile) or a coarser grid. That is a design change, not a config edit.
- Inputs: IMERG Early, SMAP L4 and NOAA GFS are global, so fetching needs no change beyond the bbox.

## Release checklist

1. Edit `config.yaml domains.now.bbox`.
2. Rebuild and upload the static inputs above, then update the sha256 values in `.github/workflows/now.yml`.
3. Update `live/clim_reference.json` with reference points inside the new domain.
4. Dispatch now.yml. Require `verified=true` and every check true in `data/now/manifest.json` before announcing.
