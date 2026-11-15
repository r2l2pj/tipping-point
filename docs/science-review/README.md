# Tipping Point — technical review for the science/geology lead

Written 2026-11-15 (NASA Space Apps 2026, challenge "Dancing with the SARs") so a geoscientist can review exactly what the system does today, every decision behind it, and refine the science. Each section cites code (`path:line`), published data keys (`data/metrics.json`) and decision IDs (L1–L39 in the project decision log).

| Section | Contents | Status |
|---|---|---|
| [01 — Product & architecture](01-product-architecture.md) | purpose (response first, prediction second), modes, data flow, datasets and roles, regions, honesty rules, decision history | done |
| 02 — TPI method | formula, R/S/T factors for landslide and flood, climatology, HAND proxy, class bands, forecast alignment, GFS handling, bugs fixed, limitations | in progress |
| [03 — Observations & response](03-observations-response.md) | NISAR flood candidates, OPERA DSWx-S1, DIST-ALERT scars, validation vs OPERA, impact (GPW, night lights, priority zones), alerts, SWOT, rain gauges | done |
| 04 — Machine learning | labels, negatives, splits, models, served model, v2 results, early-warning evaluation, overfitting audit | in progress |
| [05 — Validation & public claims](05-validation-claims.md) | replay cases, event onsets, "warned before", every public claim mapped to data, withdrawn claims | done |
| 06 — Open questions for the reviewer | consolidated list of science decisions that need expert review | to come |

Numbers move while the team iterates; each section states the commit/run it was checked against.
