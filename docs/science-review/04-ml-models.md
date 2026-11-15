# 04 — Machine-learning models: 3a susceptibility (T_ML) and 3b trigger, as built and evaluated (v2)

Audience: a geoscientist who knows LHASA. This section describes what the code does
and what the evaluation measured. It does not describe what the pitch says. Every
number has an n and a base rate. Every design choice cites the line that implements
it.

**Citation convention.** `ml/local/<file>:<line>` refers to
`tipping-point-data-wt/ml/ml/local/` (worktree of `tipping-point-data`, main
`90b86c1`, the commit that produced the v2 results). Result files:

- `OUT/` = `~/.cache/lead-ml/out/` (`report.md`, `generalise_gfs.md`,
  `generalise_perfect.md`, `by_lead_onset.md`, `combine.md`).
- `MM` = `.crew/research/model-metrics.md`.
- `EDA` = `.crew/research/ml-eda.md`.
- `metrics_ml.json` = `tipping-point-data-wt/ml/ml/models/metrics_ml.json`.
- Decision IDs (L23 … L38) are in `.crew/plans/decisions.md`.

**Status at the time of writing (2026-11-15 ~11:00 BRT).** The v2 headline wording
is on hold until an independent overfit audit reports (`data/ml/ml_card_v2.json`:
`headline_status = "held: independent overfit audit pending"`). That audit's output,
`.crew/research/overfit-audit.md`, **did not exist when this section was finished**,
so its verdict is not included (see §11).

Three findings came out of writing this section. The team's own reports do not
contain them. Each is verified below.

1. **The live map is not serving v2.** The published P_trigger frames come from the
   **v1** trigger model and the **v1** T_ML. That T_ML was trained with RS 2024
   points, the L32 leak (§8.3).
2. **Petrópolis was never scored on a forecast.** Its GFS TEST rows were scored
   with **100 % NaN forecast rain**. The cause is a cache-lookup bug: Petrópolis's
   points fall inside the Angra dos Reis GFS box. So the published "Petrópolis
   forecast miss" is a missing-data artefact, not a measured miss (§9.4).
3. **Most of the VAL-vs-TEST gap is a base-rate effect.** About half of the "~5×"
   PR-AUC gap comes from base rates set by the label windows (24 h VAL base rate
   0.039 vs TEST 0.135). A real ranking gap remains: ROC 0.755 on VAL vs 0.941 on
   TEST, both scored with observed rain (§10).

---

## 1. Two models, and how they relate to LHASA

| | 3a — susceptibility `T_ML` ("where") | 3b — trigger `P_trigger` ("when") |
|---|---|---|
| LHASA analogue | LHASA v1 susceptibility map | LHASA v2 XGBoost nowcast + LHASA-F forecast extension |
| Target | static P(landslide-prone cell) | P(rain-triggered landslide at this point within H), H ∈ {24 h, 48 h, 72 h, 7 d} |
| Training area | S + SE Brazil bbox (−58, −34, −39, −14), `settings.py:29` | **worldwide** dated GLC/COOLR points, `settings.py:52-57` |
| Unit of split | ~50 km blocks (`BLOCK_DEG = 0.5`, `settings.py:153`) | event (GLC points linked within 3 d and 100 km, `settings.py:141-142`, `labels.py:251-288`) |
| Served | `t_ml` band of `ml_static_now.tif` (0.01° Now grid, computed at 90 m and block-averaged 12×12, `export.py:3-6`) | `p_trigger_xgb.json`, daily model × per-horizon calibrators (`trainer.py:280-293`, `serve.py`) |

The live product multiplies the two:

```
P_live_H = P_trigger_H × T_ML    (live/p_trigger.py:186-192)
```

It is masked to Brazilian land (IBGE outline minus MCD12Q1 water,
`live/p_trigger.py:194-201`). It is labelled a "relative likelihood … 0–1 ranking
index, not an absolute frequency".

What is borrowed from NASA, and therefore not new (`MM` lines 94-136):

- XGBoost on IMERG + SMAP L4 + static predictors is LHASA v2 (Stanley et al. 2021).
- Forecast-driven hazard is LHASA-F (Khan et al. 2022, GEOS-FP).

What differs:

- The forecast is NOAA GFS 0.25°, replayed from the archive with no look-ahead (L28b, L30).
- There are explicit horizon labels.
- Disaster events are held out per event, with a leak filter.

LHASA v2 and LHASA-F were **not** scored on our TEST rows, so we make no comparative
claim.

---

## 2. Labels (positives)

### 2.1 NASA GLC / COOLR reports — the only TRAIN/VAL positives for 3b

Source: the `COOLR_Reports_Points` FeatureServer (`labels.py:36-43`), pulled
globally (`settings.py:57`; `table.py:51`).

Filters for 3b (`table.py:204-209`, `labels.py:116-125`):

- location accuracy ≤ 5 km (`settings.py:69-73`; classes "exact", "1km", "5km");
- trigger in {rain, downpour, continuous_rain, monsoon, tropical_cyclone, flooding}
  (`settings.py:75-76`);
- date ≥ 2015-03-31, the first SMAP L4 day (`settings.py:45`).

What survives (`EDA` §1):

- 2,348 points in 1,524 events (median event size 1 point, max 37).
- **95 % of points are from 2015–2018.** GLC stopped systematic ingestion around
  2019. By year: 2017 = 855, 2019 = 41, 2022 = 9, 2025 = 2.
- By region: North America 1,055 · Asia 871 · Europe/Africa 241 · South America 72
  · S/SE Brazil 20.

The date is the reported event date. It is converted from epoch milliseconds to a
UTC day (`labels.py:87-89`). It is never derived from rain. Points are deduplicated
to one per 90 m cell per day (`labels.py:241-248`).

**3a** uses GLC ≤ 5 km inside the S+SE Brazil bbox, any date (`table.py:103-105`).
That gives 169 points (22 dated since 2015).

### 2.2 RS 2024 inventory (UFRGS/Cemaden, CC-BY-4.0)

16,027 points come from the committed `landslide_catalog.geojson`. It is one
MultiPoint with no attributes, exploded in `labels.py:129-148`. That leaves 12,277
points after the 90 m dedupe (`EDA` §1). 3b TEST uses a random 3,000
(`RS2024_MAX_POINTS`, `settings.py:86`; `table.py:219`).

**Dating rule.** The inventory has no per-point dates, so every point gets the fixed
day **2024-05-01** (`settings.py:84`, `table.py:62`). Horizon labels use the
**trigger window Apr 29 – May 2, 2024** (`settings.py:85`, `LABEL_WINDOWS`
`settings.py:97`). An issue day D is positive for horizon H when
[D, D+H−1] overlaps [Apr 29, May 2] (`table.py:349-387`). The inventory window
(Apr 27 – May 13, `settings.py:79`) is excluded from negative dates.

**Peak-rain dating is diagnostic only.** `assign_peak_day` (`labels.py:197-215`)
dates each point by its own peak-24 h IMERG day. An early draft used it for labels.
It was dropped before training because it takes the label from a model input, which
is circular (`EDA` §8.5; `settings.py:80-83`).

**Onset (L37).** The first deaths were Apr 29 (flood, Paverama). The first landslide
deaths were Apr 30 (Itaara, Silveira Martins). The Serra slides probably peaked May
1–2 but are undated (`.crew/research/event-onsets.md`). So May 1 is likely after the
onset. Lead-time tables are re-anchored on Apr 29 (§7.4).

### 2.3 WA December 2025 atmospheric-river reports (WGS)

Washington Geological Survey reports. Only `event_date` and the geometry are
requested, because the layer also stores reporter names (`labels.py:174-194`).
Points are kept only if dated in Dec 1–31 2025 (`settings.py:93`,
`labels.py:151-171`). Of 185 reports, **8** survive the dedupe (`EDA` §1). WA is a
TEST event and is reported but too small to quote alone.

### 2.4 Additional held-out TEST events (v2)

Registry: `ml/local/test_events.yaml` and `test_events.d/*.yaml`, schema in
`events.py:1-24`.

Rules:

- `date` must lie inside `trigger_window` (`events.py:99-101`).
- `inventory_window` defaults to the trigger window ±7 d (`events.py:34,84-86`).
- Every event uses `dating: fixed`: all points get D, and horizon labels use the
  trigger window (`settings.py:112-116`).
- Only geometry is read. Polygons are reduced to a representative point
  (`labels.py:404-420`).
- Each event is capped at 3,000 points (`labels.py:521-523`).

| event | D | trigger window | points used (raw) | source | GFS archive? |
|---|---|---|---|---|---|
| rs_2024 | 2024-05-01 | Apr 29 – May 2 | 3,000 (16,027) | UFRGS/Cemaden | yes |
| wa_2025 | per point | Dec 2025 | 8 (185) | WGS reports | yes |
| helene_2024 | 2024-09-27 | Sep 25–27 | 2,144 (2,217) | USGS LASER, doi:10.5066/P14CHGKS (`test_events.yaml:7-18`) | yes |
| maria_2017 | 2017-09-20 | Sep 20–21 | 3,000 (71,431) | USGS, doi:10.5066/P9BVMD74 (`test_events.yaml:20-31`) | **no** (pre-2021-03-23) |
| kerala_2018 | 2018-08-16 | Aug 14–19 | 3,000 (4,728) | Hao et al. 2020 / DANS (`kerala_2018.yaml`) | **no** |
| emilia_2023 | 2023-05-16 | May 15–17 (inventory window Apr 24 – May 24) | 3,000 (80,997 polygons) | RER2023, Zenodo 13742643 (`emilia_2023.yaml`) | yes |
| saosebastiao_2023 | 2023-02-19 | Feb 18–19 | 310 | NASA COOLR *event* inventory (`coolr_events.yaml:6-17`) | yes |
| petropolis_2022 | 2022-02-15 | Feb 15 | 62 | COOLR event inventory (`coolr_events.yaml:18-29`) | yes |
| angra_2022 | 2022-04-02 | Apr 1–2 | 77 | COOLR event inventory (`coolr_events.yaml:30-41`) | yes |
| himachal_2023 | 2023-07-09 | Jul 8–10 | 156 | COOLR event inventory (`coolr_events.yaml:54-65`) | yes |
| rwanda_2023 | 2023-05-02 | May 2–3 | 137 | COOLR event inventory (`coolr_events.yaml:66-77`) | yes |

Recife 2022 (23 points) and Fiona 2022 (19 points) are registered but disabled
(`coolr_events.yaml:42-53,78-90`).

The COOLR **event** inventories come from a different FeatureServer layer than the
**reports** layer used for training (`coolr_events.yaml:1-4`). Points per event come
from `split_3b_events.csv` (forced rows).

Label-quality notes a geologist should weigh:

- **Emilia** has two rain episodes (May 1–3 and 15–17). Its slides cannot be
  separated by episode, so first-episode slides are labelled May 16
  (`emilia_2023.yaml:20`).
- **Kerala** also had two episodes (Aug 8–9 and Aug 14–17). The label window covers
  only the second (`event-onsets.md`).
- **Maria**: some failures may pre- or post-date the hurricane
  (`test_events.yaml:30`).
- **None** of the imagery-mapped inventories has per-landslide timestamps.

---

## 3. Negatives (case-control design)

### 3.1 Trigger (3b), daily rows

Each positive gets two kinds of negative (`table.py:234-269`):

- **(a) Same point, other dates.** The same point on `NEG_DATES_3B = 5` dates
  (`settings.py:148`). Dates are drawn from one fixed pool of 600 dates between
  2015-03-31 and the last day with real POWER rain (`settings.py:149`;
  `table.py:194-199`). Each date is ≥ 30 days from any day of that event and its
  inventory window (`settings.py:150`; `table.py:241-248`). All points of an event
  share its 5 dates.
- **(b) Elsewhere, same day.** One random point 5–50 km away on the positive's day
  (`table.py:38,257-268`). It must be > 5 km from **any** known landslide
  (`settings.py:147`). It is dropped if it has no DEM, i.e. falls in water
  (`table.py:505`).

Design ratio: about 5 + 1 negatives per positive.

| split | rows | positives | negatives | daily base rate |
|---|---|---|---|---|
| TEST | 88,964 | 14,894 | 74,070 | 0.167 |
| VAL | 4,362 | 642 | 3,720 | 0.147 |
| TRAIN | 8,381 | 1,209 | 7,172 | 0.144 |
| buffer (unused) | 2,935 | 465 | 2,470 | — |

Source: `ml/models/split_summary.json`.

**What each negative type teaches** (`EDA` §8.4). Same-point negatives share the
positive's terrain, so they teach only "when". Elsewhere negatives share the day's
rain at 0.1°, so they teach only "where". Splitting the daily TEST by negative type
(`trainer.py:268-277`; `OUT/report.md` lines 13-28, xgb_all):

| TEST positives vs … | n | base rate | PR-AUC | ROC |
|---|---|---|---|---|
| same_point_other_date ("when") | 83,364 | 0.179 | 0.963 | **0.994** |
| elsewhere_same_day ("where") | 20,494 | 0.727 | 0.805 | **0.676** |

**The trigger has almost no "where" skill.** v1 reported ROC 0.465 against the
elsewhere negatives (`MM` line 165). This is why it is multiplied by T_ML (§6).

### 3.2 Trigger (3b), horizon (issue-day) rows

These rows exist for VAL and TEST, and for TRAIN in the direct models
(`table.py:349-387`).

- For each positive with window [L0, L1], issue days are D = L1 − j for
  j = 0 … 20 (`LOOKBACK_DAYS = 20`, `table.py:346`).
- `label_H = 1` iff [D, D+H−1] overlaps [L0, L1].
- So pre-event issue days at the **same point** are negatives for short horizons.
  These are the hard negatives: wet antecedent days just before the failure.
- Same-point random-date negatives enter as issue day D = N, with every label 0.
- Elsewhere negatives are **not** in the horizon rows (`table.py:376`). They enter
  only the by-lead and when×where analyses (`combo.elsewhere_issue_rows`).

Consequence: **the horizon base rate is set by the width of the label window, not by
nature.**

- A GLC point (1-day window) contributes 1 positive in 26 rows at 24 h.
- RS (4-day window) contributes 4 in 26.
- Helene (3-day window) contributes 3 in 26.

This matters for comparing VAL with TEST (§10).

### 3.3 Susceptibility (3a)

Negatives are random land points inside the region, > 5 km from any known landslide,
at 3:1 (`settings.py:146-147`; `table.py:127-143`). TEST negatives are drawn the same
way, 3 × the TEST positives (`table.py:144-146`). This gives a 3a TEST base rate of
exactly 0.250 (§7.6).

Random land points in S+SE Brazil are mostly lowland. So 3a is close to
separable on slope or relief alone: univariate ROC 0.98 in v1 (`EDA` §3–7).

---

## 4. Leak controls

| Control | Rule | Code | Effect (v2) |
|---|---|---|---|
| Event split | GLC points within 3 d and 100 km are one event; an event never straddles splits | `labels.py:251-288`, `split.py:155-165` | 3b events: TRAIN 1,105, VAL 415, TEST 11 (`split_3b_events.csv`) |
| Spatial buffer | a row within 10 km of a more held-out split (train < val < test) becomes `buffer` | `split.py:168-187`, `settings.py:154` | 3b: 2,935 rows (465 positives) demoted |
| TEST-event leak filter | a GLC point dated within ±3 d of a TEST event's inventory window **and** within 100 km of any of its points is removed, along with its whole event | `labels.py:529-557`, `table.py:77-85, 288-343`, `settings.py:124-125` | 32 GLC points in 4 events, all in VAL: Kerala 29, Maria 2, Helene 1 (`test_event_leaks_3b.csv`); 3a: 0 |
| L32 — T_ML without RS | 3a positives are GLC only; RS stays a 3b TEST event | `settings.py:128-131`, `table.py:107-112` | see §8.1 |
| 3a TEST-site exclusion | a GLC 3a positive within 10 km of any TEST-event point is dropped ("T_ML must not learn the TEST sites") | `table.py:121-125` | 169 GLC → 101 in TRAIN/VAL/buffer (68 removed, inferred from `split_summary.json`) |
| GFS rows only with a run (L33) | GFS metrics only on issue days ≥ 2021-03-23 | `scoring.py:75-81`, `settings.py:127` | 166,967 of 381,244 TEST issue rows dropped from GFS scoring (`metrics_ml.json: 3b.horizon_rows`) |
| POWER lag | drop daily/horizon rows after the last real IMERG-Final day in NASA POWER (v1 had NaN rain on recent negatives) | `table.py:174-199, 506-517` | — |
| Missingness by class | checked on TRAIN: rain 0 % pos / 0.7 % neg, SMAP 0.1 % / 1.8 % | `EDA` §3–7 | judged not class-dependent |

The leak filter has a residual gap. It removes GLC reports that may **be** a TEST
event. It does not remove GLC reports from the same region in other years: for
example, earlier Serra do Mar slides are still in TRAIN for the Petrópolis, Angra
and São Sebastião tests. For a trigger model with almost no terrain signal, that
is mild.

---

## 5. Features

Every 3b feature is extracted at a point, by date. 3a uses the same static stack on
a regional 90 m grid.

| Group | Features | Source | Code |
|---|---|---|---|
| Rain (3b daily) | `rain_24h`, `rain_72h`, `rain_7d`; `rain_24h_rel` = 24 h / local p99 of daily rain; `rain_72h_rel` = 72 h / local p99 of 3-day sums | **Training:** NASA POWER `IMERG_PRECTOT` per-point daily series (IMERG Final, UTC days). **Serving:** IMERG **Early** daily from GES DISC | `settings.py:169`, `sources_dynamic.py:1-28, 1102-1190`, `scoring.py:102-112` |
| Rain (direct models) | `ante_24h`, `ante_72h`, `ante_7d`, `ante_72h_rel` (observed, D−7 … D−1); `fc_sum`, `fc_max24`, `fc_max72`, `fc_max24_rel`, `fc_max72_rel` over the window D … D+H−1 | observed IMERG for TRAIN/VAL ("perfect forecast"); GFS at TEST/serving | `settings.py:206-208`, `scoring.py:115-128` |
| Forecast rain | GFS 0.25° 00Z run of D, APCP 6 h buckets summed into UTC days (L28b); nearest 0.25° cell, **no rescaling to IMERG** | NOAA `noaa-gfs-bdp-pds` on AWS (archive from 2021-03-23) | `sources_dynamic.py:1409-1469`, `scoring.py:84-99` |
| Soil (dynamic) | `sm_surface`, `sm_rootzone`, `sm_profile_wetness` | SMAP L4 `SPL4SMGP.008`, one T10:30Z granule per day, 9 km EASE-2. Daily rows: target day. Horizon rows: **held at issue day D** | `settings.py:45-46,170`, `sources_dynamic.py` `SmapPoints` |
| Soil texture | `clay`, `sand`, `porosity` | SMAP `SPL4SMLM.008` land-model constants (9 km) | `settings.py:167`, `sources_dynamic.py:914` |
| Terrain | `elevation`, `slope`, `aspect_sin/cos`, `curvature`, `relief` (5×5 max−min) | NASADEM 1″ → 3″ (~90 m) | `settings.py:165`, `sources_static.py:1-12` |
| Land cover | `landcover` (IGBP LC_Type1, categorical), `ndvi` (annual mean 2023) | MCD12Q1.061 (500 m); VIIRS VNP13C2 0.05° CMG | `settings.py:166`, `sources_static.py:12-20` |
| Climate (3a only) | `rain_mean` (IMERG monthly 2001–2023), `soil_mean` (SMAP 2016–2023, every 8th day) | GES DISC / NSIDC | `settings.py:44,47-48,168` |
| Season (climatology baseline only) | `doy_sin`, `doy_cos` | — | `trainer.py:179`, `table.py:464-467` |

**Monotone constraints.** XGBoost is constrained to be non-decreasing in every rain
and SMAP feature: "more rain / wetter soil never lowers the hazard"
(`settings.py:197-201,208`; `models.py:42`). Slope is **not** constrained in v2, even
though the old model card says so (`ml/models/model_card.md` is the pre-v1 RS-only
card and is stale).

**Train/serve differences** a reviewer should know about:

1. **Rain product.** Training uses IMERG Final (via POWER). Live serving uses IMERG
   Early. Early is known to run lower and differ in the tails, which would shift the
   `_rel` features.
2. **Forecast scale.** The model only ever learned observed IMERG. At TEST and in
   serving it receives raw GFS precipitation, not quantile-mapped. LHASA-F rescales
   GEOS-FP for exactly this reason.
3. **Rain climatology.** The `_rel` p99 denominators are all-year p99 from the POWER
   series at each point (`sources_dynamic.py:1176-1190`), not seasonal.
4. **Same-day SMAP in daily training.** In the daily training rows, SMAP is the
   10:30Z granule of the landslide day itself. It can already reflect that day's
   rain (and SMAP L4 is precipitation-forced). Horizon scoring and serving hold SMAP
   at the issue day, so there is no look-ahead at TEST. But the daily model may
   have learned a slightly optimistic soil signal. The soil ablation is ~0 anyway
   (§7.7).
5. **Resolution.** Training terrain is 90 m point terrain. The Now grid is 0.01°,
   with terrain block-averaged (median slope 2.6°, p99 18°; `MM` lines 199-204). This
   is why the served trigger is the terrain-free `xgb_rain_soil` (`settings.py:218-220`).

---

## 6. Models, horizons, and what each split is for

### 6.1 Split roles (`split.py:1-20`, `models.py:1-6`, `trainer.py:1-10`)

| Split | What it is (3b) | Used for |
|---|---|---|
| TRAIN | 1,105 GLC events → 8,381 daily rows (1,209 positives); 95 % from 2015–2018 | fitting trees |
| VAL | 415 GLC events → 4,362 daily rows (642 positives); horizon rows 16,667 | XGB early stopping (50 rounds on `aucpr`, `settings.py:212-217`); calibration (isotonic if ≥ 30 VAL positives, else Platt; `models.py:24,98-108`); threshold = max-F1 on calibrated VAL (`models.py:111-119`); per-horizon calibrators and thresholds; 3a model choice (`trainer.py:107-119`); blend weights (`combine.py`) |
| TEST | 11 held-out events → 88,964 daily rows (14,894 positives); 381,244 horizon rows (214,277 with a GFS run) | scored once, by `evaluate_*` (`trainer.py:163-175, 296-352`) |

Caveats on these roles:

- **Horizon calibration uses observed IMERG as a stand-in for the forecast.** The
  GFS archive starts 2021-03-23 and almost all VAL is 2015–2018 (`EDA` §8.2). So the
  calibrators and thresholds never see GFS error.
- **VAL is reused.** The same VAL events serve for early stopping, calibration and
  threshold. VAL metrics are therefore slightly optimistic. TEST is not affected.

3a uses the same roles, with GLC blocks split 75/25 TRAIN/VAL (`settings.py:137-139`).
3a TEST is the held-out Brazilian event inventories (§7.6).

### 6.2 3b models (`trainer.py:193-238`)

| name | what | features |
|---|---|---|
| `xgb_rain_only` | XGBoost daily | rain (5) |
| **`xgb_rain_soil`** (served) | XGBoost daily | rain (5) + SMAP (3) + texture (3) |
| `xgb_all` | XGBoost daily | + terrain (6) + land cover (2) |
| `rf_all` | RandomForest(200, min_samples_leaf 5), daily | as `xgb_all` (`settings.py:211`) |
| `climatology` | XGBoost, **no weather**: terrain + land cover + texture + day of year | baseline (`trainer.py:179, 205-206`) |
| `nowcast_xgb_all` | `xgb_all` scored at target D−1 with observed rain only, **no forecast** | persistence baseline (`scoring.py:161-166`) |
| `rule_tpi` | TPI = (R·S·T)^(1/3), uniform weights, scored as max_k TPI(D+k) over the window; alarm if > 0.6 | R from 72 h rain vs seasonal p90/p95/p99; S from SMAP rootzone wetness; T from slope 15–30° (`scoring.py:64-71,171-177`; `settings.py:221`) |
| **`direct_rain`** (v2) | one XGBoost **per horizon**, trained on TRAIN issue-day rows built exactly like VAL/TEST, with observed rain in the forecast window | `DIRECT_RAIN` (9) + texture (`trainer.py:241-265`) |

XGBoost hyper-parameters (`settings.py:212-217`): depth 3, learning rate 0.05, up to
600 trees, subsample 0.7, colsample 0.8, `min_child_weight` 5, early stopping 50.
Class weighting is off by default, because it would distort calibrated P
(`EDA` §8.7).

**From a daily model to horizons** (`scoring.py:1-11, 137-184`; served identically in
`serve.py:47-83`):

```
p_k = model(features at target day D+k), k = 0 … H−1
      (observed rain for days < D, forecast rain for days ≥ D, SMAP held at D)
P_H = 1 − Π_k (1 − p_k)        independence across days, declared
then a per-horizon isotonic calibrator (fit on VAL issue rows)
```

The per-horizon calibrators can cross by about 1e-3. Serving therefore applies a
running max over increasing H: a longer window can never be less likely
(`serve.py:84-91`).

**Horizons and issue time** (`settings.py:157-161`):

- Issue time is 00 UTC on day D.
- Observed rain covers IMERG days ≤ D−1.
- The forecast is the GFS 00Z run of D.
- The window is UTC days D … D+H−1.
- Horizons are 24 h / 48 h / 72 h / 7 d. 14 d was dropped in v2. 7 d is labelled
  "low forecast skill" (L27).

### 6.3 3a models (`trainer.py:83-104`)

- XGBoost ablations: `terrain_only`, `terrain_landcover`, `all` (terrain + land cover
  + climate + texture) (`settings.py:191-195`).
- `rf_all`: RandomForest(200).
- `rule_slope`: the TPI terrain score, 0 at 15° rising to 1 at 30°
  (`scoring.py:53-61`).

The served T_ML is the XGB ablation with the best **VAL** PR-AUC
(`trainer.py:107-119`; `export.py:46-51`).

### 6.4 Which trigger is served, and why

`SERVE_TRIGGER = "xgb_rain_soil"` is fixed in `settings.py:220`. It is not chosen by
VAL. The reasons:

- It has no terrain input, so 0.01° block-averaged terrain cannot distort it.
- "Where" comes from T_ML.
- One daily model gives every horizon (`settings.py:218-219`; `ml_card_v2.json:served`).

VAL prefers `direct_rain` at all four horizons (horizon VAL PR-AUC from
`metrics_ml.json: val_3b.horizon_val`):

| H | VAL base rate | xgb_rain_soil | direct_rain | margin |
|---|---|---|---|---|
| 24 h | 0.039 | 0.129 | 0.138 | 0.009 |
| 48 h | 0.077 | 0.225 | 0.234 | 0.009 |
| 72 h | 0.116 | 0.291 | 0.302 | 0.011 |
| 7 d | 0.270 | 0.474 | 0.498 | 0.024 |

These margins are small next to VAL size (642 positives, all GLC). The served choice
is defensible but was not made by VAL.

---

## 7. Results (v2, TEST)

All metrics come from `scoring.py:26-49`:

- PR-AUC is average precision.
- Precision, recall, F1 and accuracy are taken at the VAL max-F1 threshold, never
  re-tuned on TEST.
- `pr_auc` and `roc_auc` are on **calibrated** scores. Isotonic ties cost
  0.00–0.05 against raw scores (`trainer.py:59-72`).

**Precision and accuracy depend on the case-control ratio. They are not
operational rates.** Recall and ROC are ratio-free.

### 7.1 Pooled TEST, operational (NOAA GFS 00Z)

n = 214,277 issue-day rows. These are rows with an archived run; Maria and Kerala
contribute 12,000 negative-only rows (§9.3). Source: `OUT/report.md`.

**24 h** (29,018 positives; base rate 0.135)

| model | P | R | F1 | acc | PR-AUC | ROC |
|---|---|---|---|---|---|---|
| xgb_rain_soil (served) | 0.639 | 0.592 | 0.615 | 0.899 | **0.622** | **0.903** |
| direct_rain | 0.722 | 0.586 | 0.647 | 0.913 | 0.644 | 0.895 |
| xgb_rain_only | 0.593 | 0.597 | 0.595 | 0.890 | 0.553 | 0.888 |
| xgb_all | 0.597 | 0.586 | 0.592 | 0.890 | 0.539 | 0.887 |
| rf_all | 0.668 | 0.583 | 0.623 | 0.904 | 0.540 | 0.874 |
| nowcast_xgb_all (no forecast) | 0.869 | 0.353 | 0.502 | 0.905 | 0.618 | 0.882 |
| rule_tpi (TPI > 0.6) | 0.697 | 0.290 | 0.409 | 0.887 | 0.455 | 0.729 |
| climatology | 0.185 | 0.971 | 0.311 | 0.418 | 0.315 | 0.813 |

**48 h** (37,912 positives; base rate 0.177)

| model | P | R | F1 | acc | PR-AUC | ROC |
|---|---|---|---|---|---|---|
| xgb_rain_soil | 0.546 | 0.624 | 0.582 | 0.842 | **0.645** | 0.889 |
| direct_rain | 0.512 | 0.613 | 0.558 | 0.828 | 0.662 | 0.867 |
| xgb_all | 0.567 | 0.572 | 0.570 | 0.847 | 0.555 | 0.870 |
| rf_all | 0.605 | 0.607 | 0.606 | 0.860 | 0.604 | 0.869 |
| nowcast_xgb_all | 0.564 | 0.462 | 0.508 | 0.842 | 0.580 | 0.843 |
| rule_tpi | 0.617 | 0.254 | 0.360 | 0.840 | 0.420 | 0.699 |
| climatology | 0.233 | 0.968 | 0.376 | 0.432 | 0.345 | 0.779 |

**72 h** (46,806 positives; base rate 0.218)

| model | P | R | F1 | acc | PR-AUC | ROC |
|---|---|---|---|---|---|---|
| xgb_rain_soil | 0.550 | 0.640 | 0.591 | 0.807 | **0.656** | 0.861 |
| direct_rain | 0.468 | 0.664 | 0.549 | 0.762 | 0.658 | 0.821 |
| xgb_all | 0.584 | 0.607 | 0.595 | 0.820 | 0.570 | 0.842 |
| rf_all | 0.573 | 0.623 | 0.597 | 0.816 | 0.625 | 0.847 |
| nowcast_xgb_all | 0.588 | 0.443 | 0.505 | 0.810 | 0.596 | 0.817 |
| rule_tpi | 0.584 | 0.227 | 0.327 | 0.796 | 0.404 | 0.650 |
| climatology | 0.227 | 0.989 | 0.369 | 0.261 | 0.369 | 0.753 |

**7 d, low forecast skill** (82,382 positives; base rate 0.384, so read ROC more than PR-AUC)

| model | P | R | F1 | acc | PR-AUC | ROC |
|---|---|---|---|---|---|---|
| xgb_rain_soil | 0.565 | 0.899 | 0.694 | 0.695 | **0.801** | **0.876** |
| direct_rain | 0.521 | 0.918 | 0.664 | 0.644 | 0.764 | 0.844 |
| xgb_all | 0.575 | 0.808 | 0.672 | 0.697 | 0.742 | 0.832 |
| rf_all | 0.588 | 0.851 | 0.695 | 0.713 | 0.789 | 0.855 |
| nowcast_xgb_all | 0.507 | 0.704 | 0.589 | 0.623 | 0.628 | 0.706 |
| rule_tpi | 0.742 | 0.272 | 0.398 | 0.684 | 0.581 | 0.660 |
| climatology | 0.385 | 1.000 | 0.556 | 0.385 | 0.484 | 0.671 |

How to read these tables:

- **The ML beats the TPI > 0.6 rule everywhere.** The rule is a fixed-threshold
  index. At its 0.6 threshold it has high precision but recall of only 0.23–0.29.
- **At 24 h the forecast adds almost nothing over persistence.**
  `nowcast_xgb_all` uses observed rain to D−1 and no forecast. It scores PR-AUC
  0.618, the same as the served model (0.622), and beats the GFS-driven `xgb_all`
  (0.539). The forecast earns its keep from 48 h on (+0.06 PR-AUC) and especially
  at 7 d (+0.17).
- **Adding terrain and land cover to the trigger hurts on held-out events.**
  `xgb_all` is below `xgb_rain_soil` at every horizon.

### 7.2 Pooled TEST, upper bound (observed IMERG used as the "forecast")

n = 381,244 rows, all 11 events (`OUT/report.md`).

| H | base rate | xgb_rain_soil PR / ROC | direct_rain PR / ROC | rule_tpi PR / ROC | climatology PR / ROC |
|---|---|---|---|---|---|
| 24 h | 0.139 | 0.780 / 0.941 | 0.766 / 0.933 | 0.480 / 0.745 | 0.273 / 0.754 |
| 48 h | 0.178 | 0.784 / 0.940 | 0.825 / 0.939 | 0.476 / 0.729 | 0.306 / 0.731 |
| 72 h | 0.217 | 0.803 / 0.934 | 0.861 / 0.941 | 0.482 / 0.712 | 0.335 / 0.709 |
| 7 d | 0.373 | 0.875 / 0.925 | 0.924 / 0.944 | 0.588 / 0.680 | 0.480 / 0.672 |

The gap between GFS and the upper bound is the cost of forecast error: 24 h PR-AUC
0.622 vs 0.780. The two blocks are not the same rows, because GFS drops the
pre-2021 rows.

### 7.3 Per-event spread and leave-one-event-out (GFS, PR-AUC)

Source: `OUT/generalise_gfs.md`, `generalise.py:25-49`. Per-event values are
summarised only for events with ≥ 20 positive rows (`generalise.py:22`). WA enters
only at 72 h and 7 d.

| H | model | pooled | per-event median [min – max] (n events) | LOEO pooled range |
|---|---|---|---|---|
| 24 h | xgb_rain_soil | 0.622 | 0.677 [0.038 – 0.997] (8) | 0.562 – 0.673 |
| 24 h | direct_rain | 0.644 | 0.676 [0.043 – 0.981] (8) | 0.578 – 0.704 |
| 24 h | rule_tpi | 0.455 | 0.457 [0.043 – 0.699] (8) | 0.323 – 0.519 |
| 48 h | xgb_rain_soil | 0.645 | 0.720 [0.083 – 0.880] (8) | 0.605 – 0.690 |
| 72 h | xgb_rain_soil | 0.656 | 0.694 [0.140 – 0.874] (9) | 0.621 – 0.691 |
| 7 d | xgb_rain_soil | 0.801 | 0.821 [0.545 – 0.956] (9) | 0.753 – 0.833 |

24 h per event (GFS, PR-AUC; base rate in brackets from `OUT/report.md`):

| model | Angra [0.083] | Emilia [0.143] | Helene [0.130] | Himachal [0.120] | Petrópolis [0.043] | RS [0.167] | Rwanda [0.087] | São Sebastião [0.083] |
|---|---|---|---|---|---|---|---|---|
| xgb_rain_soil | 1.00 | 0.51 | 0.83 | 0.86 | 0.04* | 0.77 | 0.23 | 0.59 |
| direct_rain | 0.98 | 0.51 | 0.85 | 0.86 | 0.04* | 0.75 | 0.44 | 0.60 |
| rule_tpi | 0.70 | 0.30 | 0.49 | 0.52 | 0.04* | 0.62 | 0.21 | 0.42 |
| climatology | 0.08 | 0.14 | 0.63 | 0.13 | 0.04* | **0.97** | 0.50 | 0.12 |

\* Petrópolis GFS rows have NaN forecast rain. They are invalid (§9.4).

How to read this:

- **The spread across events is the real uncertainty.** Excluding the invalid
  Petrópolis row, 24 h PR-AUC for the served model runs from 0.23 (Rwanda) to 1.00
  (Angra). The two Serra do Mar events in Brazil are inconsistent: Angra 1.00,
  São Sebastião 0.59.
- **The pooled number is mostly three events.** RS (72,000 rows), Emilia (63,000)
  and Helene (49,312) are 86 % of the 214,277 GFS rows.
- **The climatology baseline scoring 0.97 on RS is a design artefact, not skill.**
  Every RS positive is in late April. The same-point negatives are spread across
  the year, so season alone separates them (`MM` line 57). Rwanda shows the same
  thing at 72 h (0.99).

### 7.4 By lead time, anchored on the documented onset (L37)

`by_lead.py` (`by_lead.py:1-22`) re-scores the frozen models with no retraining.
`OUT/by_lead_onset.md` holds the outputs, with onsets set by `ML_EVENT_ONSETS`
(`settings.py:117-121`). The setup:

- The forecast is issued on onset − k (GFS 00Z of that day). It is scored on the
  shortest horizon whose window still reaches the onset: k0 → 24 h, k1 → 48 h,
  k2 → 72 h, k3–6 → 7 d (`by_lead.py:72-78`).
- Onsets: RS Apr 29, Helene Sep 26, the rest = event day (`MM` line 63).
- Negatives are TEST same-point random-date rows plus elsewhere points (5–50 km)
  issued on the same day. Thresholds are the VAL ones.

**What "before the damage" means.**

- **k = 0 is not "before".** The 00Z run is available at about 03:30 UTC, which is
  after local midnight in Brazil. So a k0 hit is issued on the onset day itself.
- **k ≥ 1 is genuinely "before"** (`MM` lines 64-65).

Pooled, 9 events with GFS positives: 8,894 positives vs 30,796 negatives per k
(base rate 0.224).

| k (d) | H | served xgb_rain_soil recall / PR / ROC | direct_rain recall / PR / ROC | TPI rule recall / PR / ROC | upper bound xgb_all (observed rain) recall / PR / ROC |
|---|---|---|---|---|---|
| 0 | 24 h | 0.65 / 0.70 / 0.90 | 0.64 / 0.67 / 0.89 | 0.12 / 0.38 / 0.67 | 0.70 / 0.75 / 0.96 |
| 1 | 48 h | 0.63 / 0.55 / 0.87 | 0.65 / 0.66 / 0.88 | 0.07 / 0.29 / 0.63 | 0.40 / 0.61 / 0.93 |
| 2 | 72 h | 0.40 / 0.39 / 0.73 | 0.46 / 0.46 / 0.72 | 0.06 / 0.22 / 0.52 | 0.36 / 0.56 / 0.91 |
| 3 | 7 d | 0.82 / 0.48 / 0.82 | 0.86 / 0.61 / 0.87 | 0.09 / 0.27 / 0.61 | 1.00 / 0.74 / 0.96 |
| 4 | 7 d | 0.66 / 0.44 / 0.78 | 0.70 / 0.49 / 0.81 | 0.07 / 0.25 / 0.59 | 1.00 / 0.70 / 0.96 |
| 5 | 7 d | 0.97 / 0.44 / 0.81 | 0.89 / 0.51 / 0.84 | 0.19 / 0.32 / 0.67 | 0.99 / 0.63 / 0.94 |
| 6 | 7 d | 0.83 / 0.45 / 0.81 | 0.82 / 0.52 / 0.84 | 0.21 / 0.32 / 0.67 | 0.57 / 0.50 / 0.86 |

The pooled k ≤ 1 rows include Petrópolis's NaN-forecast rows (62 positives,
recall 1.00 by artefact; §9.4). Removing them would lower the pooled recall slightly.

**RS 2024 alone** (onset Apr 29; 3,000 positives / 9,889 negatives per k):

| k | H | served recall / F1 / PR / ROC | TPI rule recall / PR / ROC | upper bound xgb_all recall / PR / ROC |
|---|---|---|---|---|
| 0 | 24 h | **0.00** / 0.00 / 0.72 / **0.94** | 0.00 / 0.24 / 0.51 | 0.15 / 0.69 / 0.96 |
| 1 | 48 h | **0.00** / 0.00 / 0.76 / **0.95** | 0.00 / 0.29 / 0.58 | 0.01 / 0.62 / 0.95 |
| 2 | 72 h | **0.00** / 0.00 / 0.26 / 0.54 | 0.00 / 0.23 / 0.37 | 0.01 / 0.53 / 0.92 |
| 3 | 7 d | 0.48 / 0.45 / 0.43 / 0.80 | 0.09 / 0.31 / 0.67 | 1.00 / 0.78 / 0.97 |
| 4 | 7 d | 0.07 / 0.09 / 0.37 / 0.72 | 0.02 / 0.24 / 0.58 | 1.00 / 0.71 / 0.97 |
| 5 | 7 d | 0.98 / 0.72 / 0.57 / 0.88 | 0.37 / 0.53 / 0.84 | 1.00 / 0.70 / 0.96 |
| 6 | 7 d | 1.00 / 0.73 / 0.80 / 0.94 | 0.49 / 0.56 / 0.86 | 0.10 / 0.44 / 0.88 |

**What RS shows.** One day before onset, the model *ranks* RS correctly: ROC 0.95,
PR 0.76 at k1. But the calibrated probability never crosses the VAL-fit threshold
for k = 0–2, so an alarm would not have fired. Even the observed-rain upper bound
reaches only 0.15 recall at k0.

The two likely reasons:

1. The VAL thresholds come from small GLC events. They do not transfer to a storm
   whose first day (Apr 29) is only the start of a 4-day accumulation.
2. v1 found that the Apr 28 – May 1 GFS runs put the RS peak about one day late,
   on May 2, and too small: about 60 mm/day forecast vs 138 mm observed on May 1
   (`MM` lines 254-262).

The earlier May-1 anchor (k0 recall 0.98) overstated lead time by about 2 days
(`MM` line 79).

**Per event at k1 (48 h, served):**

| event | recall | PR | ROC |
|---|---|---|---|
| Helene | 0.94 | 0.75 | 0.93 |
| São Sebastião | 0.99 | 0.87 | 0.97 |
| Himachal | 1.00 | 0.61 | 0.93 |
| Angra | 1.00 | 0.68 | 0.94 |
| Rwanda | 0.99 | 0.64 | 0.89 |
| WA (8 positives) | 0.88 | 0.53 | 0.89 |
| Emilia | 0.95 | 0.89 | 0.83 |
| RS | **0.00** | 0.76 | 0.95 |

So the RS failure at k ≤ 2 is specific to RS (onset-anchored), not general.

Pitch numbers (L38) are a separate replay calculation of the TPI index, not this ML.
They are "RS 36 % of 16,027 mapped slides High/Critical in the run 27 h before the
first deaths; Critical 2 %; 48/72 h runs 0 %". L38 also rules that no prediction
number goes in the headline.

### 7.5 Combinations (no retraining; `combine.py`, `OUT/combine.md`)

The tested combinations were AND, OR and majority votes, and VAL-fit weighted blends
of `xgb_all`, `rf_all` and `rule_tpi`. None beats the single `xgb_rain_soil` or
`direct_rain` on pooled GFS PR-AUC. At 24 h the best blend scores 0.590 vs 0.622 /
0.644. VAL F1 of the blends is 0.20–0.36, the same low-VAL regime as §10.

### 7.6 3a T_ML v2 vs the slope rule

TEST is 1,449 positives from the held-out Brazilian inventories inside the training
bbox (RS 1,000 cap, São Sebastião 310, Angra 77, Petrópolis 62) plus 4,347 random
land negatives. n = 5,796, base rate 0.250 (`table.py:115-153`, `OUT/report.md`
lines 1-11).

| model | P | R | F1 | acc | PR-AUC | ROC | VAL PR-AUC (n = 69, 20 positives) |
|---|---|---|---|---|---|---|---|
| xgb_all (**served v2 T_ML**, best VAL) | 0.654 | 0.994 | 0.789 | 0.867 | 0.747 | 0.929 | 0.817 |
| xgb_terrain_only | 0.463 | 0.999 | 0.633 | 0.710 | 0.765 | **0.938** | 0.639 |
| xgb_terrain_landcover | 0.556 | 0.995 | 0.714 | 0.800 | 0.696 | 0.864 | 0.816 |
| rf_all | 0.545 | 0.986 | 0.702 | 0.791 | 0.706 | 0.896 | 0.640 |
| **slope rule 15–30°** | **0.846** | 0.847 | **0.846** | **0.923** | **0.829** | 0.918 | 0.547 |

**The fixed slope rule beats the learned T_ML on PR-AUC: 0.829 vs 0.747.** It also
beats it on F1 and accuracy. T_ML wins only on ROC, by 0.01–0.02.

The model was trained on **174 rows (73 positives)** and validated on **69 rows
(20 positives)** (`t_ml_xgb.meta.json`; `split_summary.json`). The VAL choice
between `xgb_all` (0.817) and `xgb_terrain_landcover` (0.816) is a 0.001 difference
on 20 positives, which is noise. With fewer than 30 VAL positives the calibrator is
Platt (`models.py:24,106-108`). Platt is monotone, so calibrated and raw ranks are
equal.

Group importance (`metrics_ml.json: 3a.importance`):

| group | gain share (TRAIN) | permutation PR-AUC drop (TEST) |
|---|---|---|
| land cover | 0.63 | 0.220 |
| terrain | 0.23 | 0.258 |
| texture | 0.13 | 0.021 |
| climate | 0.01 | 0.000 |

A geomorphologist would expect slope or relief to dominate. With 73 positives the
trees lean on land cover instead. v2 T_ML is published as `t_ml_v2.*`. Whether the
live layer switches is listed as the conductor's decision (`MM` line 92).

### 7.7 Feature importance, 3b (`metrics_ml.json: 3b.importance`; `trainer.py:128-160`)

These are computed on `xgb_all`, daily rows.

| group | gain share (TRAIN) | permutation PR-AUC drop (TEST daily) |
|---|---|---|
| rain | 0.647 | **0.587** |
| terrain | 0.179 | 0.079 |
| land cover | 0.146 | −0.053 |
| soil (SMAP + texture) | 0.027 | **0.002** |

The top single features by gain are `rain_72h_rel` 0.362, `rain_24h_rel` 0.136 and
`rain_7d` 0.121. In other words, relative 3-day rain carries the model, which is
consistent with LHASA's antecedent-rain framing.

EDA on TRAIN (`EDA` §3–7) shows the same thing from the data side:

- The landslide rate across rain_72h/p99 quintiles goes 2 → 5 → 10 → 18 → 38 %.
- Within the top rain quintile, SMAP-wetness quartiles barely move the rate
  (38 / 33 / 35 / 39 %).
- The SMAP signal is collinear with recent rain (ρ high; univariate ROC 0.61–0.66
  vs 0.74–0.77 for rain).

**Caveat on "soil ≈ 0".** In the horizon ablation, `xgb_rain_soil` beats
`xgb_rain_only`: GFS 24 h PR-AUC 0.622 vs 0.553, and observed-rain 0.780 vs 0.736.
The "soil" group there includes static texture, which can act as a regional prior.
So "soil adds ~0" holds for the permutation test on the daily model. It does not
hold for the horizon product.

---

## 8. History: flaws found and fixed

### 8.1 L32 — susceptibility leaked RS into the RS trigger test (v1 → v2)

- **v1.** 3a used RS 2024 points as positives (3,000 by block) alongside 169 GLC
  points. **59 % of RS 3b TEST positives were T_ML TRAIN points** (`decisions.md`
  L32; `MM` lines 117-122; `by_lead.py:46-52`).
- **Effect.** Every P_trigger × T_ML number on RS was inflated. v1 reported
  when×where PR-AUC 0.717 and by-lead "served product" ROC 0.94–0.97. On the 19
  RS points that were T_ML-clean, product ROC was 0.78–0.92 (`MM` lines 117-122).
- **Fix.** T_ML is now GLC-only (`settings.py:128-131`). 3a TEST is built from the
  held-out events, and TRAIN/VAL rows within 10 km of a TEST point are dropped
  (`settings.py:132-139`, `table.py:115-125`).
- **Cost.** TRAIN fell to 73 positives, and the slope rule now wins (§7.6).

### 8.2 L33 — GFS NaN rows made "easy negatives" (v1 → v2)

- **v1.** Same-point negatives are dated 2015–2026, but the GFS archive starts
  2021-03-23. **40 % of RS TEST same-point negatives were scored with NaN forecast
  rain.** XGBoost's default branch turned these into confident "no" scores, which
  inflated every GFS metric. v1's headline was 24 h PR-AUC 0.752 / ROC 0.934; v2 is
  0.622 / 0.903.
- **Fix.** `scoring.source_mask` (`scoring.py:75-81`) keeps only rows whose issue
  day is ≥ 2021-03-23, for evaluate, by-lead and combine. Pre-2021 events (Maria,
  Kerala) get observed-rain scores only.
- **Residual.** A date-based mask does not catch NaN for other reasons. See the
  Petrópolis bug in §9.4.

### 8.3 Live serving was not updated to v2 (found while writing this section)

The Now workflow pins the trigger model and static stack **by sha256 from the
`static-v1` release**:

- `.github/workflows/now.yml:207-215` on `origin/main`:
  `p_trigger_xgb.json 2eba71d6…`, `.meta.json 44df91aa…`,
  `ml_static_now.tif 721b9228…`.
- These are the **v1** artefacts delivered at 05:00 (`MM` lines 209-211).
- The v2 model in the repo has different hashes (`a342d616…`, `e9f63903…`).

The published layer manifest confirms this
(`origin/fallback:data/now/p_trigger/layers.json`, generated 2026-11-15T11:42Z):

- It still lists **five** horizons, including `p_trigger_14d`. v2 dropped 14 d.
- It points `test_metrics` at the v1 `data/ml/ml_card.json`.

**So the live map is the v1 `xgb_rain_soil` × the v1 T_ML.** That T_ML is
`xgb_terrain_landcover`, trained **with** RS 2024 points (`MM` lines 582-598). Its
v1 RS-inclusive numbers do not describe it on the RS domain.

Separately, the v2 model meta has **no `serve_combine` key**. So if v2 were dropped
in as-is, `live/p_trigger.py:188-192` would silently serve the trigger **without**
× T_ML.

Both points need a deliberate decision before anyone says "v2 is live".

### 8.4 RS circular dating (pre-training)

Peak-rain dating of the undated RS points was replaced by a fixed date plus a window
before any model was fit (§2.2; `labels.py:197-215`; `settings.py:80-85`). A residual
issue remains: the window is lenient. Any issue day touching Apr 29 – May 2 counts as
a hit, so RS has 4 positive issue days per point at 24 h.

### 8.5 Calibrator crossing

Each horizon's isotonic calibrator is fit independently, so P_48h < P_24h could
occur by about 1e-3. Serving enforces a running max over H (`serve.py:84-91`).

Evaluation does **not** apply that max. It scores each horizon with its own
calibrator (`trainer.py:329-333`), so evaluated and served values can differ at this
1e-3 level.

### 8.6 Smaller fixes

- **POWER latency.** IMERG Final in POWER lags about 2 months. v1 created recent
  negatives with NaN rain. v2 caps negative dates and drops late rows
  (`table.py:174-199, 506-517`).
- **Raw vs calibrated.** Raw-score PR/ROC were added next to calibrated ones after
  isotonic ties were found to collapse v1 3a rankings under spatial shift
  (`MM` lines 599-631; `trainer.py:59-72`).
- **Lead anchor.** Re-anchoring on onset (L37) replaced the May-1 anchor (§7.4).
- **GFS georeference.** A one-row (0.25°) southward shift in GEE GFS was fixed by
  reading NOAA GRIB with GDAL (L28). The ML GFS path uses the same `live.fetch`
  GRIB helpers (`sources_dynamic.py:13-16`).

---

## 9. Open concerns (ordered by how much they could change a conclusion)

### 9.1 VAL ≪ TEST (the "~5×" gap). See §10.

### 9.2 Effective sample size

- RS's 3,000 points sit in about 113 IMERG 0.1° cells, and fewer GFS 0.25° cells
  (`MM` line 637).
- Every RS positive shares one storm. So 72,000 RS rows carry roughly one event's
  worth of weather information.
- The same applies to Emilia and Helene.
- No confidence intervals were computed. The **per-event spread (§7.3) is the honest
  uncertainty**. A block bootstrap over events would be the right next step.

### 9.3 Pooled GFS metrics include events with no positives

Maria and Kerala have no GFS positives: their event days are before 2021. But their
post-2021 same-point random-date negatives (6,000 rows each) remain in the pooled
GFS TEST (`OUT/report.md`, rows with n_pos = 0). They add easy negatives, mostly dry
random dates, to the pooled metrics. The effect is small (12,000 of 214,277 rows)
but inconsistent with "Maria/Kerala are scored on observed rain only".

### 9.4 Petrópolis GFS rows are NaN, so the "forecast miss" is unmeasured (new, verified)

**Symptom.** At 24 h GFS for Petrópolis:

- `xgb_rain_soil`, `xgb_all` and `direct_rain` flag **every** one of 1,426 rows,
  including random-date negatives: TN = 0, precision 0.043 = base rate
  (`OUT/report.md`, 24 h by-event).
- `direct_rain` scores a ROC of exactly 0.500. That means tied scores.

A real forecast miss would give *low* scores on the positives. It would not give
high scores on dry random dates.

**Verification** (run read-only for this review on the cached features, same code
path as `evaluate_3b`):

| quantity | value |
|---|---|
| Petrópolis GFS rows / positives | 1,426 / 62 |
| fraction of rows with NaN GFS rain, day 0 | **1.000** (every other TEST event: 0.000) |
| `xgb_rain_soil` calibrated score range | 0.258 – 0.316 (VAL threshold 0.123) |
| `direct_rain` calibrated score | constant 0.290 (threshold 0.145) |

**Cause.** `table.gfs_reader` assigns each point to the **first** per-event GFS box
that contains it (`table.py:444-460`). Boxes come in event-id order (`table.py:436-441`):

- Angra's box (−45, −23.75, −42.5, −22.25) contains the Petrópolis points (about
  −43.2, −22.5).
- So Petrópolis is read from Angra's cache. That cache holds only Angra issue dates
  (2022-03-13 … 04-02, plus 3 others).
- `_gfs_day_grids` is cache-only (`sources_dynamic.py:1409-1439`), so every
  Petrópolis issue day returns NaN.

The correct cache does exist: `gfs/-43.75_-23.25_-42.5_-22/20220215`. The 00Z run of
Feb 15 2022 puts about 38 mm in the wettest box cell on day 0. That matches the
team's "GFS daily max ≈ 40 mm".

**So the conclusion "GFS missed Petrópolis" is probably still true. But the metrics
reported for Petrópolis (24 h PR-AUC 0.04; k0–k6 recall 1.00 in by-lead) are an
artefact. They are not a measurement of that miss.**

The same reader feeds `by_lead` and `combine`, so Petrópolis rows are wrong there
too. It is the same class of bug as L33.

**Fix.** Choose the box per event, not per point. Or mask NaN forecast rows the way
`source_mask` masks pre-2021 rows. Then re-score. Expected impact on pooled numbers:
small (1,426 rows). Impact on the per-event narrative: material.

### 9.5 Temporal and reporting shift

- TRAIN/VAL is 95 % 2015–2018 GLC news reports: English-language and road/town-biased.
  TEST is 2017–2025 imagery-mapped disaster inventories (`EDA` §8.3–8.4).
- IMERG V07 and SMAP 008 are consistent reprocessings, but no TRAIN-vs-TEST
  feature-drift table was produced, even though `EDA` §8.3 says to "report it".

### 9.6 Case-control probabilities are not frequencies

The daily base rate is about 0.15 by design. Two consequences:

- P_H = 1 − Π(1 − p_k) composes design-rate probabilities. It is then re-mapped by
  VAL isotonic calibrators whose base rate is also a design artefact (0.039 at 24 h).
- The live product multiplies two such scores.

The app correctly labels the output a "relative likelihood … ranking index". **No
number on the map is a probability of failure.**

### 9.7 Thresholds don't transfer

VAL max-F1 thresholds are 0.123 / 0.174 / 0.185 / 0.260 for 24 h / 48 h / 72 h / 7 d
(`p_trigger_xgb.meta.json`). They give pooled TEST recall of about 0.59–0.64 at
≤ 72 h. But they give **zero** recall on RS before onset (§7.4). Any "would have
warned" claim depends on which threshold is used. That is why L38 keeps prediction
numbers out of the headline.

### 9.8 Susceptibility

- The slope rule beats T_ML on PR-AUC (§7.6).
- The live T_ML is the v1, RS-trained one (§8.3).
- 3a negatives are random land, mostly lowland, so ROC ≈ 0.93 is not a hard
  benchmark (`EDA` §3–7).

A more informative 3a test would use **hard negatives**: steep cells inside the same
event footprint that did not fail.

---

## 10. The VAL-vs-TEST gap, decomposed

The team's statement (`MM` lines 40-44; `ml_card_v2.json: val_vs_test_note`):
VAL PR-AUC ~0.13 at 24 h vs TEST ~0.62, "~5×". It suspects evaluation design and
has put the question to an independent audit.

The data in `metrics_ml.json` allow a first decomposition.

**(a) Base rates differ by construction (§3.2).**

| H | VAL base | TEST GFS base | TEST observed-rain base |
|---|---|---|---|
| 24 h | 0.039 | 0.135 | 0.139 |
| 48 h | 0.077 | 0.177 | 0.178 |
| 72 h | 0.116 | 0.218 | 0.217 |
| 7 d | 0.270 | 0.384 | 0.373 |

VAL has GLC events with a 1-day window. TEST has fixed multi-day windows: RS 4 days,
Kerala 6, Helene, Emilia and Himachal 3. PR-AUC scales with base rate. Taking lift
(PR-AUC ÷ base rate) for the served model:

| H | VAL | TEST GFS | TEST observed rain |
|---|---|---|---|
| 24 h | 0.129/0.039 = **3.3×** | 0.622/0.135 = **4.6×** | 0.780/0.139 = 5.6× |
| 48 h | 2.9× | 3.6× | 4.4× |
| 72 h | 2.5× | 3.0× | 3.7× |
| 7 d | 1.8× | 2.1× | 2.3× |

So the "5×" in raw PR-AUC shrinks to about 1.2–1.7× in lift.

**(b) A real ranking gap remains.** ROC does not depend on the base rate.

| H | VAL ROC (observed rain) | TEST ROC, observed rain | TEST ROC, GFS |
|---|---|---|---|
| 24 h | 0.755 | 0.941 | 0.903 |
| 48 h | 0.755 | 0.940 | 0.889 |
| 72 h | 0.746 | 0.934 | 0.861 |
| 7 d | 0.726 | 0.925 | 0.876 |

VAL is scored with observed rain, the *favourable* source, and still ranks much worse
than TEST.

**(c) Plausible reasons TEST is easier** (unverified, for the audit to test):

1. TEST events are extreme, well-defined storms. RS positives have a median 294 mm
   over 72 h against 0.9 mm on negative dates (`EDA` §3–7). GLC VAL events are
   mostly single, loosely dated news reports with ≤ 5 km location error and modest
   rain.
2. The lenient multi-day TEST windows make "hit somewhere in the window" easier
   than hitting a 1-day GLC date that may be off by a day (report date vs failure
   date).
3. Same-point negatives are random dates, so mostly dry days, in both splits. But
   against an extreme storm they are trivially separable. The climatology baseline
   scoring 0.97 on RS shows how separable those negatives are.
4. VAL is GLC from the same 2015–2018 population as TRAIN. If VAL is still hard,
   then the TEST performance reflects disaster-event separability more than skill
   on typical landslide days.

**Bottom line for the geoscientist.** TEST measures "can the model tell a major
rain-triggered landslide disaster from ordinary days at the same place". It does
that well (ROC 0.86–0.90 with GFS). VAL, the closest proxy for routine operational
days, has ROC about 0.75 with perfect rain. Expect operational skill on small,
everyday events nearer VAL than TEST.

---

## 11. Overfit audit

An independent overfit audit was launched. It is meant to write
`.crew/research/overfit-audit.md`. **That file did not exist when this section was
completed** (checked at the end of writing). No verdict is reported here. When it
lands, it should be read against:

- §10 (base-rate and window effects);
- §9.4 (Petrópolis NaN rows);
- §9.3 (negative-only events in the pooled GFS metrics).

---

## 12. Summary table — what can be said, and how

| claim | status | number to use |
|---|---|---|
| The trigger ranks held-out disaster days above ordinary days | supported on TEST | GFS pooled ROC 0.903 (24 h) … 0.876 (7 d); per-event 24 h PR-AUC median 0.68, range 0.23–1.00 (excluding Petrópolis) |
| It beats the TPI > 0.6 rule | supported, every horizon and event | 24 h PR-AUC 0.622 vs 0.455 |
| The forecast adds skill at 24 h | **not supported** | nowcast without forecast 0.618 vs 0.622 |
| It warns before onset at the VAL threshold | **event-dependent; fails on RS** | RS k1 recall 0.00 (ROC 0.95); Helene k1 recall 0.94 |
| Soil moisture matters | weak | permutation drop 0.002; the horizon ablation gain mixes texture in |
| T_ML improves on a slope rule | **no** (v2) | PR-AUC 0.747 vs 0.829 |
| The live map is the v2 model | **no** | static-v1 pins (§8.3) |
| P values are probabilities | **no** | case-control ranking index |

---

**Path:** `~/code/r2l2pj/.crew/science-review/04-ml-models.md`

**Summary**

1. 3b trains LHASA-v2-style XGBoost on about 1.2k worldwide GLC positives (95 % from 2015–2018). It is tested on 11 held-out disaster inventories. On GFS TEST it beats the TPI > 0.6 rule at every horizon: 24 h PR-AUC 0.622 / ROC 0.903, base rate 0.135, n = 214,277.
2. Rain dominates (permutation drop 0.587; SMAP ≈ 0). At 24 h the GFS forecast adds nothing over a no-forecast nowcast (0.618). Skill varies widely by event: per-event 24 h PR-AUC 0.23–1.00. RS ranks well one day before onset (ROC 0.95) but recall is 0 at the VAL threshold for k = 0–2.
3. T_ML v2 (GLC-only after the L32 fix, 73 TRAIN positives) loses to the 15–30° slope rule: PR-AUC 0.747 vs 0.829.
4. New, verified: the live layer is still the v1 model × v1 RS-trained T_ML (static-v1 sha pins); and Petrópolis was scored with 100 % NaN GFS rain, an overlapping-box bug in `gfs_reader`.
5. Of the "~5×" VAL-vs-TEST gap, base rates explain about half (lift 3.3× vs 4.6× at 24 h). A real ROC gap remains (0.755 vs 0.941 with observed rain). The overfit audit had not reported when this was written.

> **Audit verdict (added 11:00 BRT):** see [07-overfit-audit.md](07-overfit-audit.md) — no leakage; TEST overstates operational skill; the model performs about like an untrained "72 h forecast rain / local P99" rule.
