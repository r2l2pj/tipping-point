# Tipping Point — slides outline (7 slides)

> Order: NISAR opens the technical part (slide 3, the river's dance); the old demo slide is folded into slide 7 ("Try it").

> **DRAFT — pending approval.**

## Slide 1 — Title
- **Tipping Point** — *Rain warns. Radar sees through.*
- NASA Space Apps 2026 · Challenge: Dancing with the SARs
- One line: NISAR radar sees new flooding through cloud; we turn each pass into a priority list
- Team `[TEAM NAME]` · members `[names]` · Local Event `Florianópolis (Founder Haus, Jurerê)` (the guide requires the team name, the member names and the challenge on the slides)
- *Visual:* our wordmark + tagline over a dark map (no NASA logos or insignia anywhere in the deck: guide rule); the header's "TIPPING POINT | Rain warns. Radar sees through." wordmark

## Slide 2 — The problem (main case: Uruguay River, Uruguaiana and Itaqui)
- The Uruguay River floods our home state's western border towns again and again: red alerts for Itaqui and Uruguaiana on Jul 26–29, 2026 (Defesa Civil RS) and new floods in October 2026: the Uruguaiana gauge (ANA) stayed above its 8.50 m flood stage Oct 5–21, crest 10.02 m on Oct 16, and again Oct 29–31
- Clouds hide the river from optical satellites during the flood; alerts cover whole municipalities, not which streets are under water
- Context: May 2024, Rio Grande do Sul floods — 478 municipalities affected, 185 deaths, 581,638 people displaced at the peak (Government of Rio Grande do Sul, SOS Rio Grande do Sul, updated Aug 19, 2025)
- *Visual:* the Uruguaiana NISAR before/after swipe (Sep 30 → Oct 12) — `.crew/gallery/v6b/1-uy-nisar.png`

## Slide 3 — NISAR: what it sees through cloud (opens the technical part)
- *Visual:* "The river's dance" NISAR strip, Uruguaiana (lead-ml, red = new water): `.crew/deck/img/dance_slide.png`
- **The river's dance:** Across 9 NISAR passes (Sep 18 → Nov 5, 2026), the area of new water the radar saw (red) tracks the Uruguay River gauge at Uruguaiana: r = 0.92 (fixed −15 dB, two tracks mixed, provisional products, 9 passes)
- Calm floodwater turns dark to radar; radar sees through cloud (C-band, our OPERA reference, does too); NISAR's L-band also reaches water under vegetation and passes every 12 days
- The index (NASA rain, soil, terrain + NOAA GFS) tells where and when to look between passes
- **What we learned:** flooded towns and forests stay bright (double bounce), so a darkening rule misses where people live (Washington: 45% of misses in towns or forest vs 5% of hits); BETA/provisional scenes fell to the fixed −15 dB threshold (every Uruguaiana pair we ran was rejected); 12-day revisit and swath edges limit operations; NISAR soil moisture (SME2) stayed flat while SMAP rose, so it is not used

## Slide 4 — Prevent the disaster, not the flood: when · where · who (Uruguaiana, Oct 2026)
- **When:** our index, fed by NASA satellite rain (GPM IMERG), turned the Uruguaiana region Critical on Sep 30, five days before flood stage (Oct 5; 13,129 km², 6,121 on the low floodplain); NOAA's forecast from Sep 27 already pointed there, 8 days ahead. Near-real-time rain; holds when scaled to the final product (3,541 km² on the floodplain)
- **Honest:** 15 Critical days Sep 15 → Oct 31; 3 small ones (Sep 20–22) had no flood stage within 10 days. The Critical area was only 1.34× concentrated on the floodplain: right days, not yet the right streets
- **Where (radar):** NISAR then saw the water through cloud; 54% of the NISAR flood candidates (a teammate's polygons, 1.8 km²) lie on the floodplain, which is 28% of the land in this case area (1.92×); our stricter automatic check has not reproduced them yet
- **Who:** about 98,000 people live on the low floodplain (GPW v4.11)
- How: the Tipping Point Index of NASA rain, soil (SMAP) and terrain (NASADEM), with NOAA GFS for the next days, tells where and when to look; builds on NASA's LHASA (doi:10.3389/feart.2021.640043; LHASA-F doi:10.3389/feart.2022.878996); an experimental XGBoost model sits beside it

## Slide 5 — Did it work?
- **NISAR, Washington Dec 2025:** "Clouds hid 93% of the area from optical satellites; NISAR radar mapped 28 km² of flood candidates through them" — home to about 950 people. C-band radar (NASA OPERA surface water, our reference) sees open flood through cloud too; NISAR's L-band also reaches water under vegetation
- **Radar physics checked:** candidates stable Nov 10 → Dec 4 (−9.7 dB), then −6.5 dB by Dec 16; double-bounce hypothesis supported — 45% of the missed flood is in towns or forest (vs 5% of what we caught), recall 25% open land vs 3% towns, 1% forest
- **Prediction, tied to NISAR:** the forecast shows where risk is rising; NISAR shows when radar will check it. Washington: the GFS run issued Dec 3, 2025 showed Critical landslide risk for Dec 6. Rio Grande do Sul: scored consistently, 5.8% of the 16,027 landslides were already High or Critical in the forecast issued 27 h before the first deaths, while it flagged only 2.1% of the area (2.7× better than chance, though terrain alone does better: slide 6); runs issued 51 and 75 h before: 0%; Washington's flooded places were not flagged ahead (upstream rain)
- **Official warnings came first** in both replays; the index adds where and who. RS flood index vs independent EU emergency maps during the flood: F1 0.33 (Taquari valley)
- *Visual:* NISAR before/after swipe still + a 4-row metrics table

## Slide 6 — Impact + honest limitations
- Impact: WA about 950 people inside the NISAR flood candidates; 225,583 in index-critical areas and night-light drop 44% (Dec 12, 2025); RS 1,627,862 in index-critical areas (9,175 km² Critical) + 33% (peak day May 3, 2024) — night lights indicative only (cloud/snow)
- Limitations: Thresholds are not yet calibrated for Brazil; validated on 2 replay events + 11 disaster events for the ML; IMERG/SMAP inputs ~10 km; NISAR revisits every 12 days; at the peak, rain saturates and the landslide index reads like a slope map (rain drives when, terrain drives where); no rain/snow split yet (part of Washington's Critical slope area is in the Cascades, about 40% above 1,000 m); rain tells when, terrain tells where: terrain alone locates RS slides better than the forecast flag (18.7× vs 2.71×); rain and soil add to where only at Uruguaiana (ratio 2.7, 18 cells); official warnings still came before the index
- The video demo is a replay; the live view covers Southern Brazil every 3 h. The replay forecast is NOAA GFS as issued at the time (our only non-NASA input) — perfect-forecast frames appear only as a labelled upper bound
- Landslides are **candidates only**, never confirmed — no GUNW pair spans the WA event
- *Visual:* **Impact** block (under **Show details: validation and impact**) + a short "What we don't claim" callout box

## Slide 7 — Roadmap + credits
- Roadmap: refresh on each IMERG update (today every 3 h) → NISAR check per pass → threshold calibration on the landslide catalog
- Future of NISAR: InSAR slow slope motion (flags some creeping slopes; fast shallow slides often give no warning) · ~200 m soil moisture · flooding under forest canopy · 12-day radar coverage of Brazil
- Other regions after local threshold calibration
- NASA inputs: GPM IMERG · SMAP L4 · NASADEM · NISAR (L-band radar) · HLS · VIIRS Black Marble · GPW · OPERA DSWx · OPERA DIST-ALERT · SWOT
- Forecast rain = NOAA GFS 0.25°, our only non-NASA input; every observation is NASA
- MERIT Hydro used as a validation reference only, not a NASA input
- Try it: live view `/` (Now, every 3 h; NISAR radar card, Who is affected, Priority areas), Uruguaiana replay (▶ Play from Sep 28; NISAR before/after Sep 30 → Oct 12), `/alerts` (experimental research product, not official warnings)
- Repo + live app links; built with AI assistants under human review
- *Visual:* data-source grid as plain text (no mission or agency logos: the 2026 guide forbids NASA branding in team-made content) + QR code to the app/repo

*Note: every number on these slides comes from the published `data/metrics.json`; the number → key sheet is `NUMBERS.md` (L59 / external review C1). Replay v2 is the served replay; v1 figures (36%, 8.8×) are retired.*
