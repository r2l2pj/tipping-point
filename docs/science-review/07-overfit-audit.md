# Overfit audit — 3b landslide-trigger model v2 (TEST "too good to be true"?)

Auditor: independent adversarial pass, 2026-11-15. Read-only on the repos. Every number
below was recomputed from the frozen `bundle_3b.joblib` + `horizon_3b_features.csv`
(scripts and per-row scores: `~/.cache/overfit-audit/`). Ranking metrics are on **raw**
scores (the report's `pr_auc_raw`): TEST GFS 24 h xgb_rain_soil raw PR-AUC 0.668 /
ROC 0.906 matches `metrics_ml.json` exactly (the calibrated headline is 0.622 / 0.903).

## Verdict

- **Leakage or overfitting: unlikely to matter (P ≈ 0.10; at most −0.02 PR-AUC).** The
  label-permutation sanity check returns the base rate. No TEST event has TRAIN/VAL
  landslides within 100 km and ±30 days (2 TRAIN points near Himachal, 1 near Kerala).
  Calibration and thresholds come from VAL only. One real timing leak exists: soil
  moisture is read from SMAP at 10:30Z on the issue day. It is worth 0.02 PR-AUC.
- **As a claim about operational skill, the headline is heavily inflated (P ≈ 0.95).**
  The cause is the evaluation design and the choice of TEST events, not the model:
  - TEST is case-control: about 13.5 % of rows are positive, against about 0.15 % of
    days in reality.
  - The positives are hand-picked extreme storms with thousands of points each.
  - Measured on every day at landslide locations, PR-AUC falls from 0.62 to between
    0.007 (typical GLC sites) and 0.07–0.28 (the disaster sites themselves).
  - Alarm-day precision falls from 0.64 to at most 0.02–0.10 (a lower bound, because
    landslide catalogues are incomplete).
- **The ML is a rain-exceedance detector.** One untrained feature matches or beats it on
  TEST and on VAL at 24, 48 and 72 h: forecast max 72-h rain ÷ local 72-h P99.
- **The calendar artefact the conductor found is real, but it only affects the
  climatology baseline.** The climatology model uses day-of-year; the rain models have no
  date input. Against season-matched negatives, the rain models do not drop at all
  (pooled PR-AUC 0.800 → 0.810 at the same base rate). The climatology model's RS score
  goes from 1.00 to 0.55. Do not cite the climatology baseline unless it is scored
  against season-matched negatives.

## Why VAL PR-AUC is ~5× lower than TEST (conductor's question)

| H | VAL base | VAL PR / ROC | TEST GFS base | TEST GFS PR (cal / raw) / ROC |
|---|---|---|---|---|
| 24h | 0.039 | 0.129 / 0.755 | 0.135 | 0.622 / 0.668 / 0.903 |
| 48h | 0.077 | 0.225 / 0.755 | 0.177 | 0.645 / 0.671 / 0.889 |
| 72h | 0.116 | 0.291 / 0.746 | 0.218 | 0.656 / 0.672 / 0.861 |
| 7d | 0.270 | 0.474 / 0.726 | 0.384 | 0.801 / 0.811 / 0.876 |

1. **Base rate explains about half the gap.** PR-AUC scales with prevalence. Each TEST
   event is dated by a 2–6-day window across up to 3,000 points, so many more rows are
   positive (13.5 % against 3.9 % at 24 h). Lift over the base rate is 3.3× on VAL and
   4.6–4.9× on TEST.
2. **ROC also differs (0.75 against 0.90), so the positives themselves differ.** Rain
   on the landslide day, as a multiple of the local daily P99:
   - VAL positives: median 0.27 × P99; 14 % are above P99; 25 % had less than 0.04 × P99,
     which is effectively a dry day (GLC dating noise, or the slide was not triggered by
     that day's rain).
   - TEST positives: median 1.10 × P99; 57 % are above P99.
   - Negatives are the same in both sets: 2.9 % (VAL) and 3.0 % (TEST) are above P99.
   - TEST is curated record storms; VAL is ordinary catalogued landslides.
3. **The window dating is not the cause.** If TEST keeps only the event day as positive
   (the same dating as VAL; base rate 0.046), GFS 24 h still scores PR 0.71 and
   ROC 0.987.
4. **Bigger VAL events look more like TEST.** VAL events with at least 10 points (4
   events) score PR 0.227 / ROC 0.869; with at least 3 points (43 events), 0.210 / 0.838.
5. **Pooled TEST is effectively three storms.** RS, Emilia and Helene are 36 %, 31 % and
   24 % of the GFS rows. RS's 3,000 points fall in only 116 distinct 0.1° rain cells;
   Petrópolis's 62 points fall in 2. An event-bootstrap 90 % CI for the pooled 24 h
   PR-AUC is **0.40–0.79**.

**Which number is the honest operational estimate? Neither PR-AUC.**
- VAL (0.13 at base 0.04; ROC 0.75) is the honest number for "typical catalogued rain
  landslides worldwide".
- TEST (0.62; per-event ROC median 0.93) is the honest number for "finding the timing of
  a documented extreme-rain disaster at a site where one happened".
- For operations, publish alarms per location-year and the share of alarm days that
  match a catalogued slide (check 5 below).

## Evidence table (24 h unless stated; GFS = TEST rows with a GFS run)

| # | Check | Result | Effect on headline |
|---|---|---|---|
| 2 | Labels permuted within event, ×100 (TEST GFS) | PR 0.136 ± 0.001; base rate 0.135 | pipeline sound |
| 3a | TRAIN/VAL positives within 100 km and ±30 d of a TEST event | 0 for 9/11 events; Himachal 2 TRAIN (11 km); Kerala 1 TRAIN (51 km); leak filter already removed 32 | none |
| 3b | Soil = SMAP 10:30Z of issue day D, after the 00Z issue; live SMAP is 2–3 d old | soil from D−3: GFS PR 0.679 → 0.659, ROC 0.896 → 0.891; D−1: −0.002 | **−0.02** |
| 3c | Antecedent rain = POWER IMERG **Final**; live uses IMERG **Early** | not quantified | optimistic, unknown size |
| 3d | Anything chosen on TEST | thresholds and calibrators fit on VAL only; `SERVE_TRIGGER=xgb_rain_soil` set at 05:02 (4ebdc7d) for a non-TEST reason (no terrain inputs); VAL gap between rain models ≤ 0.01. Several TEST dates are "peak-rain day" from reports (Kerala, Maria, Helene) | small coupling |
| 1a | Shipped negatives | 85 % are pre-event days at the same point (157,756 of 185,259 GFS), 15 % random dates | — |
| 1b | Positives vs random-date negatives only / pre-event only (GFS) | 0.935 / 0.682 | random dates are easy, but only 15 % |
| 1c | **Season-matched** negatives (same point, event day ±15 d, other years 2001–2025; same count; perfect forecast) | direct_rain 0.800 → **0.810**; xgb_rain_only 0.776 → 0.790; ROC 0.934 → 0.946 | **none for rain models** |
| 1c′ | Same test for **climatology** (no rain; uses day-of-year) | shipped 0.260 → 0.199 (base 0.115); RS 1.000 → 0.512; Helene 0.823 → 0.536; against pre-event days only, RS and Helene = 1.000 | climatology baseline is an artefact |
| 1d | **Elsewhere-same-day** negatives (5–50 km away, same days; perfect) | direct_rain ROC 0.538, PR 0.762 at base 0.732 | no "where" skill (as designed; T_ML does that) |
| 4 | Untrained 1-feature rule: max 72-h rain ÷ local P99 | TEST GFS **0.698 / 0.905** vs ML 0.668 / 0.906; VAL 0.139 vs 0.131; 48 h GFS 0.694 vs 0.671; 72 h VAL 0.308 vs 0.296 | ML adds nothing over rain exceedance |
| 5 | Every day 2016–2024 at landslide points; direct_rain, VAL F1 threshold, perfect forecast | see table below | precision ≈ 0.02–0.10 |
| 6 | Per event / weighting | per-event ROC median 0.93 (0.60–1.00); PR 0.04–1.00; Petrópolis ≈ base, Rwanda 0.23 | pooled number = 3 storms |

**Why the climatology baseline beat the served model on RS (0.968 against 0.768).**
Each positive point contributes 21 issue days. The positive ones are always the *last*
2–6 days, and the earlier 15–19 days are the negatives. Any input that rises steadily
with the date over three weeks therefore separates them perfectly: day-of-year does
(RS and Helene pre-event-only ROC = 1.000).

The rain models get no date input, and against season-matched negatives they keep their
scores (RS direct_rain 0.996 → 0.970), so the artefact does not touch them. The
climatology's TEST scores are a coin flip per event: VAL 0.043 at base 0.039, and its
apparent skill appears only on RS, Helene and Rwanda. With 3,000 points sharing one
day-of-year curve, each event counts as n ≈ 1.

### Check 5 — scoring every day like operations (direct_rain 24 h, perfect forecast)

"Catalogued day" = within ±1 day of any inventory slide ≤ 5 km from the point.

| 300 points each | alarms / point-yr | share of alarm days on a catalogued day (lower bound) | catalogued days caught | all-days base | all-days PR-AUC / ROC |
|---|---|---|---|---|---|
| TRAIN+VAL GLC sites | 4.4 | 1.6 % | 26 % | 0.13 % | **0.007** / 0.77 |
| RS 2024 sites | 5.4 | 8.2 % | 98 % | 0.19 % | 0.28 / 0.94 |
| Helene sites | 4.5 | 8.2 % | 85 % | 0.22 % | 0.18 / 0.89 |
| Emilia sites | 1.8 | 10.4 % | 88 % | 0.15 % | 0.075 / 0.94 |

xgb_rain_only gives 4.3–7.7 alarms per point-year with 1.3–6.3 % alarm-day precision, and
all-days PR-AUC 0.006–0.11. With GFS instead of observed rain these numbers would be worse.

## Per-event (24 h)

- **GFS / shipped:** raw xgb_rain_soil PR and ROC, GFS rows, shipped negatives.
- **Perfect / season:** direct_rain PR against shipped negatives → against season-matched
  negatives, perfect forecast.
- **Clim.:** climatology raw PR on GFS rows → against season-matched negatives (perfect
  rows; climatology uses no rain).

| event | pos | base | xgb_rain_soil PR/ROC (GFS) | direct_rain PR shipped → season (perfect) | clim. PR shipped → season | elsewhere ROC |
|---|---|---|---|---|---|---|
| rs_2024 | 12000 | 0.167 | 0.791 / 0.943 | 0.996 → 0.970 | 1.000 → 0.512 | 0.56 |
| emilia_2023 | 9000 | 0.143 | 0.628 / 0.844 | 0.654 → 0.785 | 0.146 → 0.127 | 0.52 |
| helene_2024 | 6432 | 0.130 | 0.849 / 0.964 | 0.910 → 0.888 | 0.819 → 0.536 | 0.50 |
| saosebastiao_2023 | 620 | 0.083 | 0.657 / 0.906 | 0.630 → 0.323 | 0.120 → 0.087 | 0.41 |
| himachal_2023 | 468 | 0.120 | 0.896 / 0.981 | 0.963 → 0.943 | 0.129 → 0.115 | 0.52 |
| rwanda_2023 | 274 | 0.087 | 0.231 / 0.781 | 1.000 → 0.925 | 0.499 → 0.428 | 0.64 |
| angra_2022 | 154 | 0.083 | 0.997 / 1.000 | 0.862 → 0.908 | 0.071 → 0.056 | 0.50 |
| petropolis_2022 | 62 | 0.043 | 0.077 / 0.595 | 0.043 → 0.062 | 0.088 → 0.054 | 0.76 |
| wa_2025 | 8 | 0.040 | 0.663 / 0.933 | 0.488 → 0.909 | 0.053 → 0.040 | 0.36 |
| kerala_2018 (perfect only) | 18000 | 0.240 | — | 0.688 → 0.785 | 0.426 → 0.288 | 0.53 |
| maria_2017 (perfect only) | 6000 | 0.080 | — | 1.000 → 0.906 | 0.069 → 0.068 | 0.49 |

## Honest numbers to publish

1. **Timing of documented disasters**, 9 held-out events from 2022–2025, GFS 00Z, 24 h:
   - Per-event ROC-AUC median **0.93** (range 0.60–1.00).
   - Pooled PR-AUC **0.62** at a case-control base rate of 0.135; event-bootstrap 90 % CI
     0.40–0.79.
   - Always state "case-control; precision is not a real-world rate".
2. **Typical catalogued landslides** (VAL; 642 points, 399 events, worldwide): PR-AUC
   **0.13** at base 0.04, ROC **0.75**.
3. **Operational:** about **4–5 alarm-days per location per year** at the VAL threshold.
   At most **2–10 %** of alarm days fall on a catalogued slide (lower bound). It catches
   85–98 % of the documented disaster windows, and 26 % of GLC-dated slide days.
4. **Baseline row to add:** "forecast 72-h rain ÷ local P99" scores PR-AUC 0.70; the ML
   is equivalent to it.
5. **Retract or stop citing:**
   - precision, F1 and accuracy as real-world rates;
   - the climatology baseline in its shipped form (calendar artefact);
   - any "ML beats rain" claim;
   - xgb_rain_soil's soil gain on TEST (0.62 against rain_only 0.55 calibrated): about
     0.02 of it is the SMAP timing leak.

Suggested fixes (not done here):
- read soil from D−3 in the horizon evaluation;
- add season-matched negatives and the 1-feature rule to `report.md`;
- report per-event ROC with the event bootstrap next to the pooled PR-AUC.

## Reproduce

Run from `tipping-point-data-wt/ml` with
`PYTHONPATH=pipeline:. uv run --no-sync python -W ignore ~/.cache/overfit-audit/<script>`
inside `systemd-run --user --scope -p MemoryMax=4G`.

| script | what it does | output |
|---|---|---|
| `a_score.py 24h,48h,72h` | per-row scores | `scores_*.pkl` |
| `b_season.py 24h` | season-matched negatives | `b_season_24h.csv` |
| `c_leak.py` | proximity leak check | `c_leak.out` |
| `d_analyse.py` | baselines, permutation, VAL-vs-TEST, soil lag, bootstrap | `d_analyse.md` |
| `e_ops.py` / `e_ops2.py` | operational all-days check | `e_ops*.csv` |
| `f_calendar.py` | climatology, season and elsewhere per event | `f_calendar_24h.csv` |

GFS 72 h and 7 d per-row scoring was stopped to save time. The 72 h and 7 d rows above
come from `metrics_ml.json`, plus VAL/perfect 72 h from `scores_*_72h.pkl`.
