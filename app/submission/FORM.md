# Space Apps 2026 project form: paste-ready

> **DRAFT — pending approval.** One block per field of the official 2026 Project Submission Guide ("Project Page Form Field Instructions", https://www.spaceappschallenge.org/resources/project-submission-guide/). Every field is required, and all text must be in English.
>
> **Deadline:** click **Submit for Judging** before **Sun Nov 15, 23:59, local time of our Local Event**. You can edit and resubmit until then; judges see only the last save.
>
> Before you submit, tick **Terms and Conditions** and **Confirm All Work is Original**.

Before pasting, take out every `[…]` placeholder and every "awaiting Lucas" line. The ML sentence stays without numbers until lead-ml publishes v2 (L33).

---

## 1. Project Name

Tipping Point

## 2. High-Level Project Summary

Paste from `project-page.md` → "High-Level Project Summary". That is the NISAR-led version with the NISAR response line as the headline (decided by conductor (delegated), L38). (`summary.md` is retired; do not paste it.)

## 3. Link to Project Demo

`[VIDEO LINK — YouTube Unlisted/Public or Google Drive "anyone with the link"; ≤30 s; test it in a private window]`

The video is built from `storyboard.md` + `captions.srt` (L38 response-first VO, EN; PT-BR track `captions.pt-BR.srt`). The backup is the 7-slide PDF (`slides.pdf`), linked from GitHub; use it only if the video is missing. A video longer than 30 s, or a deck longer than 7 slides, is not eligible for Global Judging.

## 4. Link to Final Project

https://tippingpoint.l17s.dev

It is public, needs no login, and returns 200. The code links are in the description below:
- https://github.com/r2l2pj/tipping-point (pipeline at the root, web app in `app/`)

Both repos are public.

## 5. Detailed Project Description

Paste from `project-page.md` → "Detailed Project Description". Its sections are:
- what it does
- how it works
- what NISAR shows and why
- a tested hypothesis (double bounce in flooded towns and forests; supported)
- the future of NISAR for this problem
- what's new vs LHASA / OPERA / GFMS
- a validation table with n and caveats
- limitations
- try-it links
- **Use of AI** (judge-visible, as the guide requires; the SI field below is not shown to judges)
- tools and team

## 6. Use of Super Intelligence (SI)

This field is for data collection only and is not shown to judges. Source: `ai-declaration.md`. One open item: the voiceover line.

> Yes. The team used AI assistants under human direction throughout the hackathon. Tools: Anthropic Claude (Claude Code, Claude Opus models) to plan, write and review code, documentation and submission text and to script the demo recording; Cognition Devin to implement scoped coding tasks; jcode with OpenAI GPT models for research (literature, datasets, official sources) and independent code and science reviews. What the humans did: the team wrote the idea and the method — the Tipping Point Index, the choice of NASA datasets, the replay and radar flood-candidate design — in our own project document (docs/PLAN.md) before any AI-assisted build; a team member directed every task, made every product and wording decision, and reviewed every data source, threshold and reported number (several numbers were withdrawn or corrected after those reviews). Acknowledgements: descriptive text in the repository README ("Use of AI") and commit metadata (Co-Authored-By trailers on AI-assisted commits). Images and video: the app screens and screenshots show real data; the demo video is a screen recording of the real app with text overlays and captions; two explanatory diagrams (what-nisar-sees, future-of-nisar) were drawn as SVG code by an AI assistant from our text and carry a visible "AI-assisted diagram" label; no AI-generated photographs or footage are used. Voiceover: none; the video is silent, with burned-in English captions.

## 7. NASA Data

Source: `nasa-data.md` (awaiting Lucas):

> - **Index:** GPM IMERG V07 (Final; Early in the live view), SMAP L4 (SPL4SMGP v008, root-zone wetness), NASADEM.
> - **Radar response (flood candidates):** NISAR L2 GCOV, HH (HV published for comparison), track 172D frame 064, BETA; post-event optical: HLS.
> - **Live flood and scar candidates:** OPERA DSWx-S1 and OPERA DIST-ALERT-HLS V1 (vegetation-loss alerts used to flag landslide scar candidates).
> - **Land cover:** MODIS MCD12Q1.
> - **Impact:** VIIRS Black Marble VNP46A2; GPW v4.11 population count (2020, SEDAC).
> - **Validation:** OPERA DSWx, SWOT (Hydrocron river levels).
> - **Landslide labels:** NASA Global Landslide Catalog / COOLR (ML training labels: 2,348+ dated landslide points worldwide; NASA event inventories for the Petrópolis, Angra dos Reis and São Sebastião TEST events).
>
> Accessed through NASA Earthdata (earthaccess), ASF (asf_search) and Google Earth Engine's NASA collections.

## 8. Space Agency Partner Data and Other References

> **Non-NASA data**
> - NOAA GFS 0.25° forecast rain. This is the only non-NASA index input, declared on every forecast frame.
> - JRC Global Surface Water v1.4: seasonal-water context.
> - MERIT Hydro: validation of the HAND proxy only.
> - IBGE country and municipality outlines.
> - UFRGS/Cemaden landslide inventory (RS 2024): validation.
> - Copernicus Emergency Management Service, EMSR720 (© European Union): RS 2024 flood maps, validation only.
> - Washington Geological Survey landslide inventory (licence to confirm): Washington validation.
> - Basemap: CARTO Dark Matter (no labels) — © CARTO, © OpenStreetMap contributors.
>
> **Event facts (RS 2024):** 478 municipalities affected, 185 deaths and, at the peak (May 19, 2024), 581,638 people displaced (Government of Rio Grande do Sul, "SOS Rio Grande do Sul — Situação nos municípios", last updated Aug 19, 2025, https://sosenchentes.rs.gov.br/situacao-nos-municipios). No other event figures are used.
>
> **Related NASA work (cited, not reused):** LHASA v2 — Stanley, T. A., et al. (2021), *Frontiers in Earth Science*, doi:10.3389/feart.2021.640043; LHASA-F — Khan, S., et al. (2022), *Frontiers in Earth Science*, doi:10.3389/feart.2022.878996.
>
> **Uruguay River flood, Oct 2026:** ANA (Agência Nacional de Águas e Saneamento Básico) river-gauge telemetry, Uruguaiana station 77150000 (flood stage 8.50 m; above it Oct 5–21, crest 10.02 m on Oct 16). Official red alerts for Itaqui and Uruguaiana: Government of Rio Grande do Sul / Defesa Civil RS, Jul 26–29, 2026.
> **RS 2024 first deaths (onset, Apr 29, 2024):** MetSul technical report (laudo), https://metsul.com/wp-content/uploads/2024/06/metsul-laudo_V4_assinado.pdf.
>
> **Code and tools (open source):** Next.js, React, MapLibre GL, Recharts, Tailwind CSS, geotiff/georaster, the Google Earth Engine API, earthaccess, asf_search, xarray, rioxarray, rasterio, h5py, NumPy, SciPy, pandas, geopandas, scikit-image, scikit-learn, XGBoost, SHAP, cfgrib/ecCodes, Playwright (demo recording) and ffmpeg.
>
> **Services:** our own VPS (hosting), GitHub Actions.
>
> **Space Agency Partners:** ESA Sentinel-1 radar, through NASA OPERA DSWx-S1 (live flood candidates and the Washington validation reference). The Brazilian Space Agency is also a 2026 partner; Brazil-specific data (Cemaden, IBGE) is listed above.
>
> We used no copyrighted images; the demo shows only our own app screens.
