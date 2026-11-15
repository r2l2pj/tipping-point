# C1 audit — data/metrics.json required keys

Sources: doc v3 'Modo 3. Validação' step 7, impact step 3, level 1 step 4, level 2 step 4, level 3 step 8 (tp3.txt l.525–760; tp4 extract) + data/SCHEMA.md 'data/metrics.json'.

## Before (HEAD 8fbac4d)

| key | source | rs_2024 | wa_2025 | now |
|---|---|---|---|---|
| `iou` | Mode 3 | MISSING | 0.0816 | MISSING (no node) |
| `f1` | Mode 3 | MISSING | 0.1509 | MISSING (no node) |
| `cloud_pct` | Mode 3 | 100 | 92.89 | MISSING (no node) |
| `landslide_hit_pct` | Mode 3 (lead-owned) | MISSING | MISSING | MISSING (no node) |
| `hand_validation` | Mode 3 | MISSING | 0.9752 | MISSING (no node) |
| `people_critical` | impact | 1488833 | 229782 | MISSING (no node) |
| `people_flooded` | impact (lead-owned) | null — no NISAR coverage — event predates launch (Jul 2025) | 1151 | MISSING (no node) |
| `flooded_km2` | impact (lead-owned) | null — no NISAR coverage — event predates launch (Jul 2025) | 27.99 | MISSING (no node) |
| `flood_source` | impact/SCHEMA | null — no NISAR coverage — event predates launch (Jul 2025) | "nisar" | MISSING (no node) |
| `night_light_drop_pct` | impact | 33.08 | 43.92 | MISSING (no node) |
| `gpw_year` | impact (README GPW year) | MISSING | MISSING | MISSING (no node) |
| `lead_time_mean_h` | lead time | MISSING | MISSING | MISSING (no node) |
| `lead_time_median_h` | lead time | -57 | null — WGS landslide inventory has 15 points (data/wa_2025/geojson/landslide_catalog.geojson) but they are undated: point-level lead time is undefined | MISSING (no node) |
| `warned_before_pct` | lead time | 0 | null — WGS landslide inventory has 15 points (data/wa_2025/geojson/landslide_catalog.geojson) but they are undated: point-level lead time is undefined | MISSING (no node) |
| `forecast_source` | level 2 flag | MISSING | MISSING | MISSING (no node) |
| `forecast_lead_time` | lead time (lead-owned) | {"forecast_source": "noaa-gfs", "earl… | {"forecast_source": "noaa-gfs", "earl… | MISSING (no node) |
| `rf_auc` | ML | null — No local labeled feature CSV/parquet table or trained ml/models/slide_xgb.json exists. Spatial block cross-validation and matched slope-rule baseline evaluation require labeled feature rows; synthetic test fixtures are not validation data. | null — No local labeled WA feature CSV/parquet table or trained model exists. The ml pipeline is configured for RS-2024; no WA validation labels/features are available to compute case-specific AUC. Synthetic test fixtures are not validation data. | MISSING (no node) |
| `rule_auc` | ML | null — No local labeled feature CSV/parquet table or trained ml/models/slide_xgb.json exists. Spatial block cross-validation and matched slope-rule baseline evaluation require labeled feature rows; synthetic test fixtures are not validation data. | null — No local labeled WA feature CSV/parquet table or trained model exists. The ml pipeline is configured for RS-2024; no WA validation labels/features are available to compute case-specific AUC. Synthetic test fixtures are not validation data. | MISSING (no node) |
| `feature_importance` | ML | MISSING | MISSING | MISSING (no node) |
| `collections` | rule 9 (not fixed) | MISSING | MISSING | MISSING (no node) |

## After (this commit)

| key | source | rs_2024 | wa_2025 | now |
|---|---|---|---|---|
| `iou` | Mode 3 | null — no NISAR water mask: event predates NISAR launch (Jul 2025), so NISAR-vs-OPERA IoU is undefined | 0.0816 | null — live now bundle (live/run_now.py): no event, so there is nothing to validate against; Mode 3 validation is replay-only (no NISAR/OPERA pair) |
| `f1` | Mode 3 | null — no NISAR water mask: event predates NISAR launch (Jul 2025), so NISAR-vs-OPERA F1 is undefined | 0.1509 | null — live now bundle (live/run_now.py): no event, so there is nothing to validate against; Mode 3 validation is replay-only (no NISAR/OPERA pair) |
| `cloud_pct` | Mode 3 | 100 | 92.89 | null — live now bundle (live/run_now.py): no event, so there is nothing to validate against; Mode 3 validation is replay-only (no OPERA DSWx-HLS peak scene) |
| `landslide_hit_pct` | Mode 3 (lead-owned) | MISSING | MISSING | MISSING |
| `hand_validation` | Mode 3 | null — gee/hand.py (HAND proxy vs MERIT hnd) not run/merged for rs_2024; GEE is not used at runtime (L25) | 0.9752 | null — live now bundle (live/run_now.py): HAND-vs-MERIT validation is run per replay case only; not run for the live AOI |
| `people_critical` | impact | 1488833 | 229782 | null — live now bundle (live/run_now.py): computed each 3 h run into data/now/summary.json (people_critical), not merged into metrics.json |
| `people_flooded` | impact (lead-owned) | null — no NISAR coverage — event predates launch (Jul 2025) | 1151 | MISSING |
| `flooded_km2` | impact (lead-owned) | null — no NISAR coverage — event predates launch (Jul 2025) | 27.99 | MISSING |
| `flood_source` | impact/SCHEMA | null — no NISAR coverage — event predates launch (Jul 2025) | "nisar" | null — live now bundle (live/run_now.py): no flood mask or flood validation input in the live bundle |
| `night_light_drop_pct` | impact | 33.08 | 43.92 | null — live now bundle (live/run_now.py): event-based metric (7 nights after vs 30 before); there is no event date |
| `gpw_year` | impact (README GPW year) | null — not merged: the committed people_critical comes from pipeline/tp/bundle.py, which records no GPW epoch; gee/impact.py (writes gpw_year) was not merged for this case | null — not merged: the committed people_critical comes from pipeline/tp/bundle.py, which records no GPW epoch; gee/impact.py (writes gpw_year) was not merged for this case | null — live now bundle (live/run_now.py): population comes from the static bundle; no GPW epoch is recorded in metrics.json |
| `lead_time_mean_h` | lead time | null — not merged: the committed cluster-mode lead-time block carries median/IQR/hist only; mean was not recorded | null — WGS landslide inventory has 15 points (data/wa_2025/geojson/landslide_catalog.geojson) but they are undated: point-level lead time is undefined | null — live now bundle (live/run_now.py): lead time needs dated damage; there is no event or inventory |
| `lead_time_median_h` | lead time | -57 | null — WGS landslide inventory has 15 points (data/wa_2025/geojson/landslide_catalog.geojson) but they are undated: point-level lead time is undefined | null — live now bundle (live/run_now.py): lead time needs dated damage; there is no event or inventory |
| `warned_before_pct` | lead time | 0 | null — WGS landslide inventory has 15 points (data/wa_2025/geojson/landslide_catalog.geojson) but they are undated: point-level lead time is undefined | null — live now bundle (live/run_now.py): lead time needs dated damage; there is no event or inventory |
| `forecast_source` | level 2 flag | null — 07_forecast.py flag not merged as a flat key; the replay forecast source is recorded in forecast_lead_time.forecast_source | null — 07_forecast.py flag not merged as a flat key; the replay forecast source is recorded in forecast_lead_time.forecast_source | null — live now bundle (live/run_now.py): writes forecast_source='noaa-gfs' into data/now/summary.json each run, not into metrics.json |
| `forecast_lead_time` | lead time (lead-owned) | {"forecast_source": "noaa-gfs", "earl… | {"forecast_source": "noaa-gfs", "earl… | MISSING |
| `rf_auc` | ML | null — No local labeled feature CSV/parquet table or trained ml/models/slide_xgb.json exists. Spatial block cross-validation and matched slope-rule baseline evaluation require labeled feature rows; synthetic test fixtures are not validation data. | null — No local labeled WA feature CSV/parquet table or trained model exists. The ml pipeline is configured for RS-2024; no WA validation labels/features are available to compute case-specific AUC. Synthetic test fixtures are not validation data. | null — no trained model: ml/models holds only model_card.md and no labeled feature table exists; the live run applies no learned model |
| `rule_auc` | ML | null — No local labeled feature CSV/parquet table or trained ml/models/slide_xgb.json exists. Spatial block cross-validation and matched slope-rule baseline evaluation require labeled feature rows; synthetic test fixtures are not validation data. | null — No local labeled WA feature CSV/parquet table or trained model exists. The ml pipeline is configured for RS-2024; no WA validation labels/features are available to compute case-specific AUC. Synthetic test fixtures are not validation data. | null — no labeled evaluation set for the live AOI; AUC is computed offline per replay case only |
| `feature_importance` | ML | null — no trained model: ml/models holds only model_card.md (no slide_xgb.json) and no labeled feature table exists, so importance is undefined | null — no trained model: ml/models holds only model_card.md (no slide_xgb.json) and no labeled feature table exists, so importance is undefined | null — no trained model: ml/models holds only model_card.md (no slide_xgb.json) and no labeled feature table exists, so importance is undefined |
| `collections` | rule 9 (not fixed) | MISSING | MISSING | MISSING |

## What changed

- Added as null + reason, only where the key was MISSING (no existing value changed; verified by script that every pre-existing key keeps its value):
  - rs_2024 (7): iou, f1, hand_validation, gpw_year, lead_time_mean_h, feature_importance, forecast_source
  - wa_2025 (4): gpw_year, lead_time_mean_h, feature_importance, forecast_source
  - now (15, node created via `merge_metrics("now")`): iou, f1, cloud_pct, hand_validation, people_critical, night_light_drop_pct, gpw_year, flood_source, lead_time_mean_h, lead_time_median_h, warned_before_pct, rf_auc, rule_auc, feature_importance, forecast_source
- No null-without-reason existed at HEAD.
- File written with `merge_metrics` then re-dumped `indent=2, ensure_ascii=False` (merge_metrics itself escapes non-ASCII — it would have rewritten the em-dashes as \u2014; worth fixing in tp/metrics.py, out of my scope).

## Left open (not in my scope)

- Lead-owned, still MISSING: `landslide_hit_pct` (rs, wa, now — rs has only the `landslide_hit` block), `people_flooded` / `flooded_km2` / `forecast_lead_time` on `now`. Covered by an xfail test in test_metrics_c1.py; drop the xfail once lead-analysis merges.
- `collections` (rule 9) is MISSING in every case. Not nulled: `merge_metrics` does `blob.setdefault("collections", {}).update(...)`, so a null value would crash later writers. Needs real ids recorded (IMERG V07, SPL4SMGP/008, GPWv411 epoch, VNP46A2, OPERA DSWx-S1/HLS, NASADEM, MERIT).
- gpw_year: gee/impact.py:441 hard-codes 2020 (GPWv411 latest epoch); left null per "never invent a number" — lead can merge 2020 from the impact run.
- Flat `forecast_source` on rs/wa is null pointing at `forecast_lead_time.forecast_source` ("noaa-gfs"); SCHEMA still says replay = "perfect-forecast".

