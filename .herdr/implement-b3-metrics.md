# implement-b3-metrics — science-review honesty fixes (t195)

Branch `b3-metrics`, worktree `tipping-point-data-wt/b3-metrics`.
All `metrics.json` edits done via `json` load/modify/dump
(`indent=2, ensure_ascii=False`), touching only the keys listed below
(edit script: `~/.cache/lead-analysis/impl-b3-metrics/edit_metrics.py`).

## 1. Withdrawn v1 T_ML / GFS scores — `data/metrics.json` `cases.rs_2024`

New case keys: `withdrawn` `{key: {value, reason}}`, `_sources`
`{key: "file#key.path"}`. Old values moved to `withdrawn`; live keys now
carry the v2 number.

| key | before (v1) | after (v2) | source |
|---|---|---|---|
| `xgb_auc` | 0.97397 | 0.92918 | `ml/models/metrics_ml.json#3a.models.xgb_all.test.roc_auc` (v2 served T_ML = `xgb_all`, `data/ml/t_ml_v2.json#model`) |
| `xgb_all_auc` | 0.87114 | 0.92918 | `#3a.models.xgb_all.test.roc_auc` |
| `xgb_pr_auc` | 0.49025 | 0.74668 | `#3a.models.xgb_all.test.pr_auc` |
| `rf_auc` | 0.92170 | 0.89640 | `#3a.models.rf_all.test.roc_auc` |
| `rf_pr_auc` | 0.15562 | 0.70551 | `#3a.models.rf_all.test.pr_auc` |
| `rule_auc` | 0.85649 | 0.91813 | `#3a.models.rule_slope.test.roc_auc` |
| `t_ml_test_n` / `_n_pos` / `_base_rate` | 2039 / 45 / 0.0221 | 5796 / 1449 / 0.25 | `#3a.models.xgb_all.test.*` |
| `feature_importance` | v1 gain share | v2 `gain_share_feature` | `#3a.importance.gain_share_feature` |
| `feature_importance_model`, `xgb_model` | `xgb_terrain_landcover` | `xgb_all` | `data/ml/t_ml_v2.json#model` |
| `t_ml_note` | v1 text | v2 text (names served model + TEST design) | `data/ml/ml_card_v2.json#t_ml` |
| `landslide_ml.t_ml` | v1 TEST block | v2 block verbatim | `data/ml/ml_card_v2.json#t_ml` |
| `landslide_ml.trigger_gfs_headline` | v1 (24h xgb_all PR-AUC 0.752) | rebuilt `{24h,48h,72h,7d}.{xgb_all,rf_all,rule_tpi}` | `data/ml/ml_card_v2.json#trigger.<h>.gfs.<m>.test` |
| `landslide_ml.split`, `.detail` | v1 text | v2 split text + `ml_card_v2.json` pointer | `data/ml/ml_card_v2.json#split` |

Reasons: `L32: T_ML v1 leaked RS 2024 points into TEST; superseded by ML v2`
for all T_ML keys; `L33: v1 GFS-source ML numbers superseded by ML v2 (GFS
scored only on issue days with an archived run)` for
`landslide_ml.trigger_gfs_headline`. v2 has no 14d horizon, so the rebuilt
headline drops it (the v1 block with 7d/14d stays in `withdrawn`).
`xgb_model_selected_by` unchanged ("best VAL PR-AUC" in v2 as well).
`cases.wa_2025.{rf_auc,rule_auc,feature_importance}` were already null +
`_null_reasons` — untouched. No like-for-like gaps, so no new
`_null_reasons` were needed.

## 2. `README.md` — retired numbers replaced with onset-anchored values

- Forecast lead time: **rs 111 h / wa 144.6 h** → onset-anchored
  (`forecast_lead_time_onset`) **rs median 87 h** (onset 2024-04-29 BRT,
  earliest 2024-04-23/147 h, 2 of 7) and **wa median 80 h** (onset
  2025-12-08 PST, earliest 2025-12-03/128 h, 5 of 8 — all landslide cells),
  with the lenient AOI-anywhere caveat stated.
- "Did the alert come before?": **88.1 %** → **36.45 % Alto+ / 1.87 %
  Crítico** of 16,027 points in the GFS 00Z run issued 2024-04-28 (27 h
  before the 2024-04-29 onset); 0 % at 48/72 h; observed 0.25 %. WA:
  **0 % Alto+ at 24/48 h, 0.14 % at 72 h** of the 27.9 km² NISAR-mapped
  flood candidates, 0 % observed; observed curve Dec-10 41 % → Dec-11
  89 % → Dec-12 99 %.
- ML scope: "no `t_ml.tif` and no T_ML this weekend" → v2 wording (trigger
  `xgb_rain_soil` + T_ML layer `t_ml_v2.json` served `xgb_all`, 3a TEST
  ROC-AUC 0.93; withdrawn/_sources convention named). Tagline "NISAR
  confirms" → "NISAR maps" (candidates rule).
- All quoted numbers verified present in `data/metrics.json` on this branch
  (`slide_warned_*`, `flood_warned_*`, `forecast_lead_time_onset`,
  `by_date_observed`).

## 3. "NISAR-confirmed" → "NISAR-mapped flood candidates"

- `metrics.json` string values: `wa.flood_warned.unit`,
  `wa.flood_warned.definition`, `wa.flood_warned.confirmed_flood_km2_label`,
  `wa.flood_warned_denominator_label`,
  `wa.flood_warned_by_lead_definition`. Added
  `wa.flood_warned.mapped_flood_km2 = 27.921` (alias of
  `confirmed_flood_km2`; key names NOT renamed).
- `tools/warned_before.py`: docstring + `unit` + `definition` + label
  strings; added `mapped_flood_km2` next to `confirmed_flood_km2` so future
  reruns keep the alias.
- `data/SCHEMA.md`: `flood_warned_pct` table row reworded; alias documented
  in the object row.
- `scar_method`'s "candidates, never confirmed" left as is (correct
  negative phrasing). `flood_confirmed` key names untouched.

## 4. HAND served radius — `metrics.json` both cases + `SCHEMA.md`

`config.yaml thresholds.hand_proxy_radius_m` = **1500** (checked). Added per
case: `hand_validation_served_radius_m` = 1500,
`hand_validation_served_iou` (rs 0.34367, wa 0.19144 — from
`hand_validation_by_radius["1500"].iou`), `hand_validation_served_r`
(rs 0.85934, wa 0.85441), and `hand_validation_note` = "served radius
1500 m (TPI uses it); 1000 m validates better — radius choice by validation
is post-submission (changes TPI)". Keys documented in SCHEMA.md's
`hand_validation` bullet.

## 5. WA OPERA comparison areas

Findings: **no km² derived with 900 m²/px existed anywhere.**
`metrics.json wa_2025.confusion*`/`hls_confusion` are pixel counts only;
`data/wa_2025/validation_metrics.json` likewise;
`data/wa_2025/opera_validation.json` legend `km2` is **already geodesic**
(verified: `tools/wa_opera_layer.py:class_km2` uses per-row
cos(lat) cell area; TP 8718 px → 5.207 km² ⇒ ~597 m²/px at ~48°N, not 900).

Action: added geodesic `*_km2` companions so the areas are published
correctly — `confusion_km2`, `confusion_raw_km2`, `hls_confusion_km2`
(px × geodesic pixel area at the **AOI-mean latitude 47.75 °N**, stated in
`validation_area_note`; lattice px ≈ 600.9 m² there, not 900 m²) in both
`metrics.json wa_2025` and `data/wa_2025/validation_metrics.json`, plus
`validation_area_note`. IoU/F1/precision/recall untouched. Example:
like-for-like TP 8777 px → **5.274 km²** (vs 7.90 km² under the wrong
900 m²).

## Commands run

- `systemd-run --user --scope -p MemoryMax=2G .venv/bin/python
  ~/.cache/lead-analysis/impl-b3-metrics/edit_metrics.py` — wrote both JSONs.
- `systemd-run --user --scope -p MemoryMax=2G env PYTHONPATH=.:pipeline
  .venv/bin/python -m pytest -q pipeline/tests live/tests gee/tests` —
  **848 passed**, 397 warnings, 40.5 s.
- JSON diff-audit vs `HEAD`: only the keys listed above changed.

## Files changed

`data/metrics.json`, `data/wa_2025/validation_metrics.json`,
`data/SCHEMA.md`, `README.md`, `tools/warned_before.py`,
`.herdr/implement-b3-metrics.md`.
