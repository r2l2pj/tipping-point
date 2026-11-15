# 01 — Product, scope and architecture

Section 1 of the science review. Audience: the team's geoscientist. It describes what Tipping Point
does as of **2026-11-15 ~12:30 UTC (09:30 BRT)**, what it does not do, and where each claim lives in
the code.

**Snapshot used.** Both local checkouts are behind their remotes (data `main` is 136 commits behind,
app `main` is 63 behind). All citations therefore point at the remote heads:

| Repo | Ref cited | Commit |
|---|---|---|
| `tipping-point-data` | `origin/main` | `90b86c1` (2026-11-15 10:30 BRT) |
| `tipping-point-data` | `origin/fallback` (published data) | `f5fbab1` (2026-11-15 10:30 BRT) |
| `tipping-point-app` | `origin/main` | `0a71cc6` (merge of PR #2) |
| conductor files | `.crew/STATE.md`, `.crew/plans/decisions.md` | working tree |

Decision IDs (L25, A2, …) refer to `.crew/plans/decisions.md`. Line numbers are given where they help.
The project's original design document (Portuguese, v4) is `tipping-point-data/docs/PLAN.md`. Its
first table ("Decisões adotadas que substituem o texto abaixo", lines 5–45) overrides the original
text wherever the two disagree. A geoscientist reading PLAN.md should read that table first.

---

## 1. What the product is for

### 1.1 Stated purpose (original design)

PLAN.md lines 47–64 frame the tool for **municipal civil defense** (Defesa Civil municipal). It answers
two questions: *has the rain pushed the ground past its tipping point?* and *where has the ground
already failed or flooded?* It covers two hazards (landslide and flood) and three NASA missions:

| Layer | Mission | Question (PLAN.md lines 151–159) |
|---|---|---|
| Sky | GPM (IMERG) | How much rain fell, compared with this place's climatology? |
| Soil | SMAP | Was the soil already saturated? |
| Surface | NISAR | Where did the ground move or flood? |

The submission's theme sentence, kept verbatim under L18:

> "Tipping Point turns NASA rainfall and soil moisture data into a near real-time landslide and flood
> warning, then uses NISAR radar to confirm where the ground actually moved or flooded, so civil
> defense teams know where to act first."

### 1.2 Current priority: response first, prediction second (L27, L38)

On 2026-11-15 the priority was reversed from the original "predict, then confirm".

- **L27** (decisions.md:174–177). The main product is **real-time situational awareness for civil
  defense once a disaster is under way**. It shows where it is flooded or sliding now (NISAR, OPERA,
  latest IMERG), who is affected (GPW population, priority zones, night lights) and what changed since
  the last pass. It refreshes every 3 h and shows Brasília time. **Prediction comes second**: the TPI
  headline index plus an ML P(trigger) at 24 h, 48 h, 72 h, 7 d and 14 d, driven by NOAA GFS forecast
  rain. The 7 d and 14 d horizons carry a "low forecast skill" label.
- **L38** (decisions.md:202–203). The pitch's spine is response with NASA radar: NISAR flood candidates
  seen through cloud, who is affected, and the live 3-hourly cycle. Prediction is a supporting line
  only. **No prediction number goes in the headline or the voice-over**, because the number is
  fragile: it falls to 0 % when the damage anchor moves by one day. Prediction numbers appear only in
  the validation table, the README and a slide footnote.
- **Why the change.** Once the replay was re-anchored to documented onset dates (L37), the prediction
  skill was modest:
  - **RS 2024:** 36 % of the 16,027 mapped landslides were High or Critical in the GFS run issued 27 h
    before the first deaths (2024-04-29). Only 2 % were Critical, and the 48 h and 72 h runs scored 0 %.
  - **WA 2025:** the flooded places scored 0 % at 24 h and 48 h.
  - **Official warnings came first** in both cases: by 31.2 h in RS and 71.8 h in WA
    (STATE.md:25, 55; decisions.md:202).

### 1.3 Who it is for, and where it applies

- **Users.** Civil defense in **Southern Brazil (RS, SC, PR)** (L9, L9b). Washington State (WA 2025) is
  kept only as the radar demonstration (L3, L9b).
- **No on-demand analysis (L29, decisions.md:185–186).** A user cannot pick an arbitrary place. Regions
  are configured by hand in `config.yaml`, and the app lists only the regions in the manifest. Going
  global is described as a config edit at release time (L9). That is an engineering claim: the
  climatology and static layers would still have to be rebuilt for each new domain.

---

## 2. The three app modes

The app is a single "Command Center" screen (`tipping-point-app/src/app/page.tsx`, README "Seams"
table). Its right-hand panel is ordered **response first** ("Now on the ground") and **prediction
second** ("Prediction").

| Mode | URL | Region | Data basis | Refresh |
|---|---|---|---|---|
| **Now** (live, "Modo Agora") | `/` (default) | `south_brazil` bbox W −58, S −34, E −48, N −22.5 (`config.yaml:17–20`) | observed IMERG Early + SMAP L4 + latest NOAA GFS run; response layers from OPERA and NISAR | every 3 h (§4) |
| **Replay** | `/?case=rs_2024&day=<offset>`, `/?case=wa_2025…` | RS AOI −52.5/−30.5/−50.5/−29.0. WA AOI −122.6/47.0/−121.5/48.5 (`config.yaml:4–15`) | hindcast from archived inputs, plus issued-GFS forecast frames per day | static (rebuilt by hand) |
| **Public Alerts** | `/alerts` | same as Now | Now alert rule (§2.3) | follows Now |

### 2.1 Now: Southern Brazil every 3 h

Each run publishes the following to `data/now/` (file list from `origin/fallback`):

- **TPI frames.** `tpi_{slide,flood}_{today,d1}.tif` COGs plus PNGs d0–d3. The grid is 0.01° (~1 km)
  (`live/config.py:28`).
- **Daily IMERG Early COGs.** 8 days of 1-day and 3-day sums (`IMERG_LOOKBACK_DAYS = 8`,
  `live/config.py:29`).
- **Response products** (L27; `now.yml:149–196`; data README lines 186–212):
  - **R1:** OPERA DSWx-S1 flood candidates. A pixel is a candidate when its latest observation is
    water and its 60-day baseline water frequency is below 20 %. Exclusions use MCD12Q1, JRC GSW and a
    rain-support test.
  - **R2:** NISAR GCOV change-detection flood candidates. The newest pass is paired with the previous
    pass on the same track and frame, at most 36 d apart.
  - **R3:** a summary and municipal priority zones (union of R1, R2 and Critical TPI; people from GPW
    2020).
  - **R4:** OPERA DIST-ALERT-HLS landslide-scar candidates over the last 30 days.
- **P(trigger) frames** (24 h, 48 h, 72 h, 7 d, 14 d). These are enabled by the repo variable
  `NOW_P_TRIGGER` (`now.yml:198–216`) and were live since about 08:09 UTC (STATE.md:49). The model
  output is labelled *"Relative likelihood … (when × where), 0–1"* and *"a ranking index, not an
  absolute frequency"*. It is the product `P_trigger × T_ML` and covers **Brazilian land only**
  (`data/now/p_trigger/layers.json`).
- **`alerts.json`** and `summary.json` (§2.3, §7).

Example: the run of 2026-11-15 11:39Z (`data/now/summary.json` on fallback).

| Input or output | Value |
|---|---|
| IMERG through | 2026-11-15 00Z (`imerg_source: gesdisc-early`) |
| SMAP L4 granule | `SMAP_L4_SM_gph_20261110T223000_Vv8013_001.h5`, **5 days old** |
| GFS run | 2026-11-15 00Z |
| Climatology | `imerg-final-0p1`, doys 320–323 |
| NISAR pair | 2026-10-25 → 2026-11-06 |
| Latest DSWx-S1 | 2026-11-14 09:06Z |
| Flood TPI by class (today) | Moderado 279 km², Alto 103 km², Crítico 122.5 km² |
| Landslide TPI by class (today) | Moderado 155 km², Alto 26 km², Crítico 0 km² |
| R1 + R2 flooded (standard confidence) | 129.8 km², 536 people (GPW) |

Caveats a scientist should see:

- **The `critical_km2` key in `summary.json` is defined as "Alto or Crítico (TPI ≥ 0.4)".** It is
  not TPI > 0.6. Ruling A8 says "critical" means TPI > 0.6 only, and that High + Critical must be
  labelled "High or above". The JSON key name does not follow that ruling. Check how the UI labels it.
- **The TPI domain is larger than RS + SC + PR.** The class areas sum to about 1.05 M km², while
  RS + SC + PR is about 0.58 M km². So TPI is computed over all land in the bbox, including
  neighbouring states (MS, SP) and countries (UY, AR, PY). This is inferred from the areas. R1 and
  P(trigger) are explicitly clipped to the IBGE Brazil outline. `nodata_km2` is about 202,000 km²,
  probably ocean and SMAP gaps.
- **SMAP latency is days, not hours.** On the example run S was 5 days old. "Today" therefore mixes
  rain observed up to D−1 or D, a GFS forecast for the remainder, and S from about D−5 (§7).
- **R1 and R2 are candidates**, with documented false-positive modes: smooth or bare surfaces read as
  water, rice paddies, and no flow routing in the rain-support test (data README lines 257–258).

### 2.2 Replay: RS 2024 and WA 2025

| | **rs_2024** (home story, L3) | **wa_2025** (radar proof) |
|---|---|---|
| Event date (config) | 2024-05-02, window −10/+5 d, ML label window 2024-04-27..05-06 | 2025-12-10, window −10/+10 d |
| TPI resolution | 90 m (STATE.md:24) | case COGs |
| Radar | **none**. The event predates NISAR (C3). OPERA DSWx-S1 has no 2024 granules over RS. RTC-S1 (2024-05-08/11) waits on an ASF EULA (README:249–252) | NISAR GCOV BETA, track 172D / frame 064: ref 2025-11-10, pre 12-04, post 12-16 (README:217–222) |
| Flood map | null, with a reason | 27.99 km² flooded inside the swath. The swath covers 74.1 % of the AOI, so this is a lower bound (README:268–270) |
| Flood validation | — | vs OPERA DSWx-S1 like-for-like: IoU 0.0816, F1 0.151 (commit `3f7f969`) |
| Landslide reference | UFRGS/Cemaden inventory, **n = 16,027 points** (E8) | WGS inventory, **n = 15 undated points** (A1; README:265–267) |
| Landslide candidates | OPERA DIST-ALERT scars | HLS optical and DIST-ALERT. 176 candidates covering 1.25 km², **none within 500 m of the 15 WGS points** (README:261–264) |
| SWOT | reach series | per-reach WSE chart |
| Forecast frames | issued NOAA GFS per issue day (L22, L28b) | same |

Labels that travel with replay data:

- **`data_basis`.** Every replay case node in the manifest says: *"hindcast — IMERG_V07 + SMAP
  SPL4SMGP as archived in GEE (no real-time latency modelled); not as-available"* (verified in
  `origin/fallback:data/manifest.json`). A replay frame dated D uses SMAP valid on D−1, which in
  reality only became available 2–3 days later (README:164–169).
- **Forecast frames.** Labelled `forecast · NOAA GFS (issued <date>)`. Perfect-forecast frames
  (observed rain shown as D+k) survive **only as a labelled upper bound** (L22, A4).
- **GFS under-forecast the RS peak by about half**: 141 mm forecast against 290 mm observed, as the
  AOI-mean 3-day sum ending 2024-05-02 (README:243–246).

### 2.3 Public Alerts

`/alerts` is the same screen with a public alert panel. It uses the same message format as the
Telegram bot (`src/app/alerts`; app README).

The alert rule is in `data/now/alerts.json` and `live/alerts.py`:

- An alert fires on an **upgrade to Alto or Crítico, today or D+1**, compared with the previous run's
  classes. Those classes are carried between runs in an Actions cache (`now.yml:110–145`).
- **Quiet hours are 23:00–05:59 Brasília time unless Crítico** (`live/config.py:53`; the time-zone
  conversion is in `live/alerts.py:96`).
- Each alert carries a `basis` (e.g. "observed+forecast"), `obs_through`, `gfs_issued`, a
  `basis_label`, a `certainty` (e.g. "medium" when forecast-based) and a `basis_note`, e.g. "based on
  forecast rain (NOAA GFS, issued …)".
- Example from the 11:39Z run: Florianópolis (SC), flood, Crítico, 232 km², certainty medium,
  observed through Nov 14 plus GFS for Nov 15.

The Telegram bot is coded (`now.yml:250–256`, `src/app/api/telegram`) but needs credentials that only
Lucas can supply: `TELEGRAM_TOKEN` (STATE.md:52). The subscriber store follows LGPD: coordinates are
rounded to 2 decimals, consent is recorded, and `/parar` deletes the row.

---

## 3. End-to-end data flow

```
 NASA DAACs (Earthdata bearer token)          NOAA open data (AWS)
 GES DISC: IMERG Early HH/daily, Final daily  noaa-gfs-bdp-pds: GFS 0.25° APCP (GRIB2, .idx byte ranges)
 NSIDC: SMAP L4 SPL4SMGP v008
 PO.DAAC: OPERA DSWx-S1, SWOT Hydrocron
 LP DAAC: OPERA DIST-ALERT-HLS, NASADEM, MCD12Q1
 ASF: NISAR L2 GCOV (CMR search, HTTP range reads)
 SEDAC: GPW v4.11 ; LAADS: Black Marble
            │
            ▼
 ┌─────────────────────── GitHub Actions (public repo r2l2pj/tipping-point-data) ─────────────┐
 │ now.yml (3-hourly) : fetch → local numpy TPI → R1 DSWx → R2 NISAR → R3 summary → R4 DIST   │
 │                      → P(trigger) → manifest (last) → sqlite mirror → publish              │
 │ static inputs  ◄── GitHub Release "static-v1" (climatology, HAND, GPW, MCD12Q1, JRC GSW,   │
 │                    IBGE municipalities, steep-slope mask, ML model + static features)      │
 └────────────────────────────────────────────────────────────────────────────────────────────┘
            │ one force-pushed orphan commit per run (app-facing files only, manifest last)
            ▼
 branch `fallback` of r2l2pj/tipping-point-data  ── PRIMARY data host (L26)
   https://raw.githubusercontent.com/r2l2pj/tipping-point-data/fallback/data/
            │                         └── secondary copy: Neon Object Storage bucket (best-effort)
            ▼
 Next.js 16 app (VPS, L39) — reads GitHub raw → Neon bucket → copy bundled at build time
   no computation in the app; never globs; reads only paths listed in the manifest
            │
            └── Neon Postgres: ONLY the Telegram subscriber table (L12b)

 Replay cases: computed once, interactively (L15), partly in Google Earth Engine (GEE) before L25,
 partly locally; published into the same `fallback` tree under data/rs_2024/, data/wa_2025/.
```

Key rules:

- **The data contract is `tipping-point-data/data/SCHEMA.md`** (E3). The app never computes and
  never globs (data README:11–13).
- **Write order puts the manifest last.** `data/now/manifest.json` is written per run and
  `data/manifest.json` at root; readers key on a non-null `updated_at` (README:305–306). A crashed run
  therefore leaves the previous snapshot live.
- **Projections:** PNG overlays are EPSG:3857; COG and GeoJSON are EPSG:4326 (AGENTS.md; PLAN.md
  decisions table).
- **The `fallback` branch is a single force-pushed orphan commit per run (L26).** There is no history,
  so past Now runs are **not archived** on the branch. A scientist wanting a time series of past live
  runs cannot recover it from GitHub. Only the sqlite mirror (`tools/build_sqlite.py`) and the Neon
  copy of the latest run exist.
- **Large files go to a release.** Any file over 50 MB becomes a GitHub Release asset. The static
  inputs live in Release **`static-v1`** (created 2026-11-15 03:45Z). It holds 58 per-doy climatology
  files (`doy_284…`, about 9.3 MB each), plus:

| Asset | Size |
|---|---|
| `clim_imerg_0p1.tif` | 23.6 MB |
| `hand_90m.tif` | 390 MB |
| `hand_m.tif` | 3.1 MB |
| `slope_deg.tif` | 5.7 MB |
| `steep15_1as.tif` | 47 MB |
| `gpw_popcount_2020_30sec.tif` | 2.7 MB |
| `lc_type1_2025.tif` | 1.1 MB |
| `gsw_occurrence_2021.tif`, `gsw_seasonality_2021.tif` | 1.8 MB, 0.8 MB |
| `municipalities_now.geojson` | 0.8 MB |
| `ml_static_now{,_v2}.tif` | ~48 MB each |
| `p_trigger_xgb{,_v2}.json` | 0.06 MB, 0.26 MB |

  CI downloads them sha256-checked and caches them (`now.yml:61–97, 120–128`).

---

## 4. What runs where, and when

### 4.1 Scheduled and runtime components

| Component | Where | When | Notes |
|---|---|---|---|
| `now.yml` "modo-agora" | GitHub Actions (data repo) | cron `17 */3 * * *` (00:17, 03:17 … UTC) + manual dispatch (`now.yml:12`) | Moved off :00 because GitHub dropped top-of-hour schedules. 45-min timeout. Response steps use `continue-on-error`, so a failure never blocks the TPI publish |
| `now-watchdog.yml` | GitHub Actions | crons `41 1-22/3` and `53 2-23/3` (`now-watchdog.yml:14–15`) | Dispatches `now.yml` if the last successful run is older than 3.5 h. **Fails, which emails the owner, if the published Now manifest is older than 6 h** (L13b stale alarm, lines 48, 76) |
| Local systemd timer `tp-now-watchdog.timer` | Lucas's workstation | `00/3:23 UTC` | Runs `gh workflow run now-watchdog.yml`. A backup for dropped GitHub schedules (`~/.config/systemd/user/tp-now-watchdog.{timer,service}`) |
| `nisar.yml`, `validation.yml`, `reference.yml` | GitHub Actions | daily 06:30, daily 07:00, weekly Mon 08:00 UTC | GEE-asset ingestion jobs from the L8a era. **`gh run list` shows no runs for any of them**, and they still call GEE. They are not on the live path; R2 NISAR runs inside `now.yml` |
| App | **Own VPS (L39)**: Docker, Node 24 `next start`, `/api/health` healthcheck (`docs/DEPLOY-VPS.md`) | always on | Moved from Vercel because of the Hobby deploy cap. Data updates never need a redeploy |
| Replay compute | developer machines (+ GEE one-off before L25) | by hand | not scheduled |

**Observed reliability.** The six most recent `now.yml` runs (06:51Z–11:35Z, 2026-11-15) all
succeeded, and **all were `workflow_dispatch`** (watchdog or manual), none scheduled. GitHub's cron
alone has not been reliable. STATE.md:46 records one scheduled run in 6 h. The watchdog and local
timer are what keep the 3-hour cadence.

### 4.2 No Google Earth Engine at runtime (L25, decisions.md:162–167)

GEE is now used only for one-off computation and for hosted copies of the static layers. Nothing on
the live path touches it: not the app, not the Now job, not the model. The reason is the
non-commercial quota in restricted mode (150 EECU-h per month), which was a risk during judging.

- Every live input is fetched directly from its DAAC with `EARTHDATA_TOKEN`. GFS comes from NOAA AWS.
- The GEE-computed statics (climatology doys, slope, HAND) were exported once to files.
- The app's GEE routes (`/api/tiles`, `/api/layers`, `/api/numbers`, `/api/point`) answer 503 unless
  `GEE_ENABLED=1` (app `docs/DEPLOY.md`).

Residue to know about: `now.yml` still writes a `GCP_SA_KEY` credential file and carries comments and
step names that mention GEOS-FP and GEE assets (`now.yml:130–137`). The code path behind them
(`NOW_LOCAL=1`, `live/run_now.py`) uses GFS and local files. `live/config.py:56–62` still names GEE
asset collections. These are labels, not runtime calls. They can confuse a code reader.

### 4.3 Storage and hosting decisions in order

The data store moved several times:

1. GCS bucket (D2).
2. GEE assets as the single source of truth (L8).
3. No bucket: a GitHub `fallback` branch for small files (L10/L21, no GCP billing).
4. Neon Object Storage as primary (L17).
5. **GitHub `fallback` branch as primary, Neon as best-effort secondary (L26).**

Neon Postgres holds only the subscriber table (L12 → L12b → L12c). Everything runs on free tiers
(L11).

---

## 5. Datasets and their roles

Rule of thumb from the README and decisions (D7, L19, L30): **every observation is NASA. The only
non-NASA input to any index or model is NOAA GFS forecast rain.** Other non-NASA data are
context, reference or validation only, never index inputs.

### 5.1 NASA datasets

| Dataset | Product / access (code) | Role | Mode | Resolution / latency notes |
|---|---|---|---|---|
| **IMERG Early** V07 | `GPM_3IMERGHHE` half-hourly and `GPM_3IMERGDE` daily, GES DISC (`pipeline/fetch/imerg.py:60–61`, `live/fetch.py:47`) | **R score** (3-day sum vs climatology). R1 rain-support rule (8-day sum) | Now | 0.1°. ~4 h latency for Early |
| **IMERG Final** V07 | `GPM_3IMERGDF.07` daily via GES DISC OPeNDAP (`live/build_clim.py:53`) | **Rain climatology**: p90/p95/p99 of 3-day sums, 2001–2023, ±30 d around each doy in 3-d steps = 483 samples per doy (`live/build_clim.py:1–20`; `config.yaml:43–48`) | Now (static) | 0.1° native |
| IMERG V07 (GEE `NASA/GPM_L3/IMERG_V07`) | archived | R in replay hindcast. Replay climatology per case (int16 mm×10) | Replay | Late/Final archive, **no latency modelled** |
| **SMAP L4** | `SPL4SMGP` v008, NSIDC (`live/fetch.py:179`, `pipeline/fetch/smap.py:42`); `/007` is deprecated | **S score**: `sm_rootzone_wetness` rescaled 0.5→0.9 (`config.yaml:24`) | Both | ~9 km. Several days old at run time (5 d in the example). L31 fills gaps from the nearest valid cell within 9 km |
| **NASADEM** | GEE `NASA/NASADEM_HGT/001` exported once; static-v1 `slope_deg`, `hand_m`, `hand_90m`, `steep15_1as` | **T score**: slope 15→30° for slide; HAND proxy (z − focalMin 1500 m) 5→15 m for flood, zero above 5° slope (`config.yaml:25–27, 39`). Steep mask (>15°) gates R4 | Both | 1 arc-sec source; 90 m HAND. HAND proxy vs MERIT Hydro: WA IoU 0.26 / r 0.87, RS IoU 0.42 / r 0.90 (commit `b4aacf9`) |
| **GPW v4.11** | 2020 count, 30 arc-sec (`13_response_dswx.py:26–27`) | People exposed / flooded / in Critical areas | Both | Epoch 2020 for both cases (latest available) |
| **VIIRS Black Marble** | `VNP46A2` `DNB_BRDF_Corrected_NTL`, QF==0 (`gee/impact.py:51`) | Pre/post night-lights change (outage proxy) | Replay | 500 m. **Not in the Now job** despite L27 listing night lights |
| **NISAR L2 GCOV** | Replay: `NISAR_L2_GCOV_BETA_V1`. Now: `NISAR_L2_GCOV_V1` (`pipeline/fetch/nisar.py:46, 454`); CMR search anonymous, bytes via bearer token (C2) | **Flood candidates** by backscatter change: per-scene Otsu, −15 dB clamp, −3 dB drop, HAND < 15 m, bright-flood +3 dB, 3 ha MMU (A2; `config.yaml:28–38`) | Both (R2 in Now) | ~12-day revisit. Replay BETA frames are pre-calibration, so only relative change is used |
| NISAR GUNW | coherence | Landslide candidates by coherence drop (0.3, ref ≥ 0.4) | — | **Not used for either case**: no GUNW pair crosses WA 2025 (README:98–101) |
| **OPERA DSWx-S1** | PO.DAAC `OPERA_L3_DSWX-S1_V1` via CMR (`pipeline/fetch/opera.py:32`); GEE `OPERA/DSWX/L3_V1/S1` for replay | Now: **R1 flood candidates**. Replay: WA flood-map validation | Both | 30 m. No 2024 granules over RS |
| OPERA DSWx-HLS | GEE `OPERA/DSWX/L3_V1/HLS` | RS validation attempt (L10b) | Replay | optical, so cloud-limited |
| **OPERA DIST-ALERT-HLS** | LP DAAC `OPERA_L3_DIST-ALERT-HLS_V1` (`VEG-DIST-STATUS/DATE`) | **R4 landslide-scar candidates**, gated by slope > 15° and MCD12Q1 | Both | 30 m, cloud and snow limited |
| HLS L30/S30 | GEE `NASA/HLS/HLS{L,S}30/v002` | WA scar candidates, cloud fraction | Replay | — |
| **SWOT** | Hydrocron (PO.DAAC), reach ids in `events/*.json` | River WSE series (validation and context) | Replay | WA 46 records, RS 62 records (commit `0e7db33`) |
| **GLC / COOLR** | NASA Global Landslide Catalog via ArcGIS FeatureServer (`pipeline/fetch/glc.py:25–30`) | ML labels (3a susceptibility retrained without RS after L32). WA reference attempt | ML | point catalogue, location precision varies |
| **MODIS MCD12Q1** | Land cover type 1, 2025 tile (`lc_type1_2025.tif`) | ML features; R1 exclusion of permanent wetland and water; lowers confidence for cropland (rice); R4 gating. **Not a TPI input** (A3) | Both | 500 m |

### 5.2 Non-NASA datasets (declared)

| Dataset | Access | Role | Status |
|---|---|---|---|
| **NOAA GFS 0.25°** | AWS `noaa-gfs-bdp-pds`, APCP 6-h buckets via `.idx` byte ranges, GRIB2 read with GDAL (`live/fetch.py:434`; L25, L28) | **Forecast rain** for Now D+1..D+3 and the ML horizons, and replay forecast frames per issue day | **The only non-NASA index/model input** (L22, L30). Replaced GEOS-FP, whose public archive keeps only ~2 weeks. GEE's `NOAA/GFS0P25` was found to be one 0.25° row off (L28), so replay COGs were rebuilt from raw GRIB (37 issue days × 24 cells, 0 mismatches; README:107–113) |
| **JRC Global Surface Water** v1.4 | `gsw_occurrence_2021`, `gsw_seasonality_2021` | R1: lowers confidence where occurrence ≥ 25 % or seasonality ≥ 3 months | context only |
| **IBGE** | municipalities and state outlines (`pipeline/fetch/boundaries.py:22–24`); static copy because the IBGE API is unreachable from runners (`now.yml:89`) | Brazil clip, municipality names, priority-zone ranking | reference only (D7) |
| UFRGS/Cemaden RS inventory | `data/rs_2024/geojson/landslide_catalog.geojson` | RS validation, n = 16,027 | validation only (E8) |
| WGS inventory (WA) | `pipeline/fetch/wgs.py` | WA validation, n = 15 undated | validation only (A1) |
| INMET gauges | `pipeline/fetch/inmet.py` | IMERG vs gauge check, RS Apr–May 2024 (L19) | validation only |
| NOAA NWS API | — | WA official-warning time (`official_warning_delta_h`) | validation only (L19) |
| MERIT Hydro | — | HAND-proxy check | validation only |
| HydroBASINS lev10 | `tools/build_upstream_basins.py` | L35 upstream-rain experiment | **not served** (STATE.md:55) |
| OSM / CARTO basemap | app | base map and labels | reference only |

---

## 6. Regions configured (L9, L29)

| Key | Name | Bbox (W, S, E, N) | Use |
|---|---|---|---|
| `domains.now` | `south_brazil` | −58.0, −34.0, −48.0, −22.5 | live Now domain. The single loader is `tp.config.now_bounds()`, and tests assert there is no hard-coded bbox elsewhere (L9) |
| `cases.rs_2024` | Rio Grande do Sul, Apr–May 2024 | −52.5, −30.5, −50.5, −29.0 | replay (central/eastern RS: Serra Gaúcha to Porto Alegre) |
| `cases.wa_2025` | Western Washington, Dec 2025 | −122.6, 47.0, −121.5, 48.5 | replay (Snohomish / Skykomish / Skagit) |

Sources: `config.yaml:4–20`. Nothing else is configured. The app lists only what is in the manifest
(L29).

The **ML training and TEST** events are a separate scope (L33, STATE.md:52–56). They include Helene,
Maria, Kerala, Emilia, São Sebastião, Petrópolis, Angra, Himachal and Rwanda. They are used for
evaluation only and are not app regions.

---

## 7. Honesty rules: what the product may and may not claim

| Rule | Source | How it is implemented |
|---|---|---|
| **Radar and optical damage maps are "candidates", never "confirmed"** | AGENTS.md hard rule; PLAN.md decisions table; D4 working rules; SCHEMA.md:244 | GeoJSON `status: "candidate"` (`14_response_nisar.py:6`). App copy has no "confirm" wording (app commit `daa2cd8`). **Residue:** the contract still carries a product key named `flood_confirmed` (SCHEMA.md:61, 359) and the original slogan "Radar confirms" (app README:4) |
| **Observed vs forecast labelled** | L22, L27, L30 | Every Now layer and alert carries `basis` (observed / observed+forecast), `obs_through`, `gfs_issued` and a human `basis_label`. Commit `ef5fd33`: "'today' is partly forecast everywhere; alerts say they rest on forecast rain". Long horizons carry `low_skill`. Replay forecast frames are labelled "forecast · NOAA GFS (issued <date>)"; perfect-forecast frames are an "upper bound" |
| **Hindcast labelled** | `data_basis` (commit `56d015e`) | Replay manifest nodes say "hindcast … not as-available" |
| **Brasília time** | L27 | Header "Updated HH:MM BRT (X ago)" (app commit `cc68c96`); quiet hours computed in `America/Sao_Paulo`; stale warning after 6 h (L13b) |
| **Never claim earlier than official warnings** | STATE.md:25; README:126–127 | Official warnings preceded the index in both replays (RS −31.2 h, WA −71.8 h). The forecast lead time is stated next to those numbers, never as "first to warn" |
| **No prediction number in the headline or VO** | L38 | Numbers only in the validation table, README and slide footnote, with n and caveats |
| **Real data only; nulls carry reasons** | L14; README:136–137 | Missing outputs show "pending". Null metrics ship a reason string, never a silent 0 |
| **Radar numbers gated on radar existing** | C3 | RS shows "No radar for this event (before NISAR launch)" |
| **Model output is relative, not a probability** | `p_trigger/layers.json` serve_note | "a ranking index, not an absolute frequency", Brazil land only |
| **Withdrawn and provisional numbers stay withdrawn** | L32, L33, L36, L38 | T_ML v1 (RS leakage), v1 GFS ML metrics (NaN negatives) and "88 % before May 1" are retired. The ML v2 card says "Not a headline yet — independent overfit audit pending" (app commit `4d9b16b`) |

Known tensions for the geoscientist to rule on:

1. **What "critical" means in `summary.json`** (TPI ≥ 0.4) versus A8 (TPI > 0.6).
2. **The TPI is pixel-local.** River floods driven by upstream rain are missed: WA flood warned 0.1 %
   (README:277–287). L35's upstream-rain term was tested and not served.
3. **The replay hindcast ignores SMAP latency.** The live S is days old.
4. **Small denominators.** Forecast lead time rests on 4 (RS) and 8 (WA) scorable issue days. The WA
   landslide hit rate rests on n = 15 points.
5. **Thresholds were set on a single event.** They are the doc v4 values (`config.yaml:22–41`), not
   fitted (app README Limitations).

---

## 8. Chronology of the major decisions

The decisions file holds D1–D10 and E1–E10 (contract and paths; the science-relevant ones are D7, E3,
E6, E7 and E8), A1–A10 (audit rulings: A2 Otsu flood, A3 ML scope, A4 perfect forecast, A5 lead time,
A8 critical = TPI > 0.6) and C1–C3 (English UI, NISAR auth, RS radar gate). The **L-series**, in order,
one line each:

| # | Date (2026-11-) | What changed | Why |
|---|---|---|---|
| L1 | 14 pm | `h5netcdf` dependency approved | read NISAR/SMAP HDF5 |
| L2 | 14 pm | No GEE fallback; replay TPI waits for GEE registration | avoid rebuilding the climatology elsewhere |
| L3 | 14 pm | Demo opens with RS 2024, then WA 2025 | home story first, radar proof second |
| L4 | 14 | Data layout = case-id folders + one root `metrics.json` + manifest last | align with doc bucket layout (supersedes E1) |
| L5 | 14 | `geotiff.js` added; per-case R/S/T/TPI COGs for the click popup | per-pixel R, S, T values for users |
| L6 | 14 | Earthdata via bearer `EARTHDATA_TOKEN` everywhere | one credential, CI-friendly; ~60-day expiry checked |
| L7 | 14 | Non-catalog data clipped and uploaded as GEE assets; analysis in GEE | doc said "sobe como asset" (later undone by L10/L25) |
| L8 / L8a / L8b | 14 | GEE assets = single source of truth; four scheduled jobs (now / nisar / validation / reference); ownership split | GEE-centric architecture (superseded by L25) |
| L9 | 14 | Operational coverage limited to Southern Brazil; single bbox in `config.yaml` | focus; global as a config edit |
| L10 | 14 | No GCS bucket: vectors-only GEE ingest; fallback files on GitHub orphan branch `fallback` | no bucket available |
| L10b | 14 | OPERA DSWx validation read from the GEE catalog; Earthdata OPERA ingest retired | product already in GEE |
| L11 | 14 | Free tiers only at runtime | no budget |
| L12 → L12b → L12c | 14 | Subscriber DB: Supabase → **Neon** Postgres via HTTPS SQL; SQLite locally | only Telegram needs a DB; no new deps |
| L13 | 14 | `now.yml` every 6 h | (superseded by L13b) |
| L13b | 14 | Back to **every 3 h**; stale warning after 6 h | doc cadence |
| L14 | 14 eve | App shows **real data only**; "pending" instead of mocks | honesty |
| L15 | 14 eve | Case products computed interactively, not via the GEE batch queue | speed |
| L16 | 14 night | Monorepo `r2l2pj/tipping-point` at a freeze | single repo (**not done**; two repos remain) |
| L17 | 14 night | Neon Object Storage becomes the published-file store | replace GitHub branch (later reversed by L26) |
| L9b | 14 | Pitch and data start with Southern Brazil | team's home region |
| L18 | 14 | Theme sentence verbatim; lead-time headline with median + IQR + n + "in replay" | doc wording, with uncertainty kept |
| L19 | 14 | Validation-only: NOAA NWS warnings (WA), INMET gauges (RS), Giovanni | independent checks |
| L20 | 14 | Now TPI computed **locally in numpy**; only statics from GEE | no raster ingest without GCS |
| L21 | 14 | No GCP billing (≈R$170 to reopen) | cost; confirms L10/L20 |
| L22 | 14 night | Replay uses a **real issued forecast** (NOAA GFS; no NASA forecast archive); forecast lead time metric; perfect forecast = upper bound | make the replay genuinely predictive |
| L23 | 14 night | XGBoost trigger P(24/48/72 h), spatial-block + temporal holdout, calibrated | doc level 3b; optional |
| L24 / L24a | 14 | Serve the model inside GEE (routes A/B); GTB not exportable, so RF in GEE | live GEE model (dropped by L25) |
| L25 | 14 | **No GEE at runtime**; direct DAAC + NOAA AWS sources; local XGB vs RF vs TPI proof | GEE noncommercial quota (150 EECU-h/month) is a judging risk |
| L26 | 15 | **GitHub `fallback` branch is the primary data host**; one orphan commit per run; >50 MB → Release; Neon secondary | not stated in decisions.md (Lucas ruling); existing raw URL kept "to avoid app churn" |
| L27 | 15 | **Response first, prediction second**; ML gets its own lead; horizons up to 14 d | civil defense needs during the event |
| L28 | 15 | GFS georeference truth = raw NCEP GRIB; GEE GFS one row south; rebuild replay COGs | conductor verified a 0.25° offset |
| L28b | 15 | No-look-ahead alignment: IMERG to D−1 + GFS run D 00Z; frame D+k = 3-day sum ending D+k | old frames double-counted D and dropped D+1 |
| L29 | 15 | **No "pick a place"**; only configured regions | data exists only where prepared |
| L30 | 15 | NOAA GFS for **both** replay and live; GEOS-FP dropped | the replay validates the same forecast the live view uses |
| L31 | 15 | SMAP gap fill from the nearest valid cell ≤ 9 km; report filled/nodata km² | SMAP masks coastal/lagoon cells, the flood hotspots |
| L32 | 15 | T_ML retrained without RS; T_ML-multiplied numbers withdrawn | RS points leaked into RS TEST |
| L33 | 15 | v1 GFS ML numbers superseded; v2 scores only issue days with a GFS run | 40 % of RS negatives had no GFS run (NaN = easy negatives) |
| L34 | 15 | App freeze 13:00 BRT; deadline 23:59 local-event time | submission logistics |
| L35 | 15 | Test an upstream-basin rain term for flood R; serve only if it helps without inflating false Critical | WA river flood missed by pixel-local R; **result: not served** |
| L36 | 15 | Headline RS landslides "88 % in High/Critical 24 h before May 1"; Apache-2.0; one official RS facts source | delegated pitch decisions (headline later retired) |
| L37 | 15 | Measure "before damage" from **event onset** (first documented damage), not the peak | 2024-05-01 was after onset |
| L38 | 15 | After re-anchoring: RS 36 % High/Critical at 27 h before first deaths (Critical 2 %; 48/72 h 0 %); WA 0 %; pitch = response with NISAR; no prediction number in headline/VO | prediction number is fragile to the anchor date |
| L39 | 15 | Production on own **VPS** (Docker); data still from GitHub raw | Vercel Hobby deploy cap |
