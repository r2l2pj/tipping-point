# Tipping Point — technical review for the science/geology lead

Written 2026-11-15 (NASA Space Apps 2026, challenge "Dancing with the SARs") so a geoscientist can review exactly what the system does today, every decision behind it, and refine the science. Each section cites code (`path:line`), published data keys (`data/metrics.json`) and decision IDs (L1–L39 in the project decision log).

| Section | Contents | Status |
|---|---|---|
| [01 — Product & architecture](01-product-architecture.md) | purpose (response first, prediction second), modes, data flow, datasets and roles, regions, honesty rules, decision history | done |
| [02 — TPI method](02-tpi-method.md) | formula, R/S/T factors for landslide and flood, climatology, HAND proxy, class bands, forecast alignment, GFS handling, bugs fixed, limitations | done |
| [03 — Observations & response](03-observations-response.md) | NISAR flood candidates, OPERA DSWx-S1, DIST-ALERT scars, validation vs OPERA, impact (GPW, night lights, priority zones), alerts, SWOT, rain gauges | done |
| [04 — Machine learning](04-ml-models.md) | labels, negatives, splits, models, served model, v2 results, early-warning evaluation, overfitting audit | done (audit verdict pending) |
| [05 — Validation & public claims](05-validation-claims.md) | replay cases, event onsets, "warned before", every public claim mapped to data, withdrawn claims | done |
| [06 — Open questions for the reviewer](06-open-questions.md) | consolidated, prioritised list of open science questions (top 10 first), grouped A–F: landslide triggers, flood physics, data & latency, validation design, ML, product & claims; each with current state, suggested experiment and effort | done |

Numbers move while the team iterates; each section states the commit/run it was checked against.
| [07 — Independent overfitting audit](07-overfit-audit.md) | verdict: no leakage/overfitting, but the TEST set (curated mega-storms) overstates operational skill; ML ≈ a simple forecast-rain/local-P99 rule; honest numbers + alarm-days per site-year | done |

**Updates after writing (11:00 BRT):** the audit (07) is in; the Petrópolis NaN-forecast bug (06 E6) is fixed and being re-scored; the live P(trigger) map is switching to v2 (06 E5); ML v3 with a hand-curated event-level split is being prepared (decision L41).
