"""Settings for the local (no-GEE, L25) ML pipeline — PLAN.md Nível 3.

3a  susceptibility T_ML (static) — RF(200, probability) + XGBoost + slope rule.
3b  trigger (dynamic, LHASA-v2 style) — daily P(slide) from rain + soil +
    terrain; horizons 24 h..14 d come from feeding GFS forecast rain.

Every path and constant the stages share lives here. Large downloads and
intermediate cubes go to ML_DATA_DIR (default ~/.cache/tipping-point-ml),
never into the repo. Smoke runs override REGION/dates via the CLI.
"""
from __future__ import annotations

import datetime as dt
import os
from pathlib import Path

from tp.config import now_bounds

REPO_ROOT = Path(__file__).resolve().parents[2]
ML_DIR = REPO_ROOT / "ml"
MODEL_DIR = ML_DIR / "models"

DATA_DIR = Path(os.environ.get("ML_DATA_DIR", Path.home() / ".cache" / "tipping-point-ml"))
# Training/feature store (stdlib sqlite3): landslides, samples (split column), features.
SQLITE_PATH = Path(os.environ.get("ML_SQLITE", REPO_ROOT / "data" / "ml" / "tipping_point_ml.sqlite"))

# --- Regions (west, south, east, north) --------------------------------------
# Training: South + Southeast Brazil (PR, SC, RS, SP, RJ, MG, ES), PLAN Nível 3.
REGION_TRAIN: tuple[float, float, float, float] = (-58.0, -34.0, -39.0, -14.0)
# Smoke runs: ~1°×0.7° of the RS-2024 hills (real data, tiny).
SMOKE_BBOX: tuple[float, float, float, float] = (-51.5, -29.6, -50.5, -28.9)
# 3b smoke: how many GLC points (worldwide) a smoke run samples.
SMOKE_GLC_N = int(os.environ.get("ML_SMOKE_GLC_N", "16"))
# Serving / export: the Now domain (config.yaml domains.now.bbox).
REGION_SERVE: tuple[float, float, float, float] = tuple(now_bounds())

# --- Grids --------------------------------------------------------------------
RES_STATIC_DEG = 1.0 / 1200.0  # 3 arc-sec ≈ 90 m (PLAN: "todas na escala de 90 m")
RES_EXPORT_1KM_DEG = 0.01      # Now-job grid (live/ TPI COGs)
IMERG_RES_DEG = 0.1

# --- Time ---------------------------------------------------------------------
IMERG_START = dt.date(2001, 1, 1)
CLIM_YEARS = (2001, 2023)          # rain climatology (doc: 2001-2023)
SMAP_START = dt.date(2015, 3, 31)  # SPL4SMGP 008 first day; 3b uses dates >= this
SMAP_HOUR_UTC = 10                 # one SPL4SMGP 3-h granule per day (T103000)
SOIL_MEAN_YEARS = (2016, 2023)     # 3a soil_mean climatology
SOIL_MEAN_EVERY_DAYS = 8           # sample one SMAP day every N days for soil_mean
NDVI_YEAR = 2023                   # 3a/3b static NDVI (annual mean of monthly)
LANDCOVER_YEAR = 2023              # MCD12Q1 LC_Type1

# 3b trigger trains like LHASA v2 on a WIDE area (conductor 2026-11-15, rev. 2): S/SE
# Brazil alone has ~23 dated GLC points since 2015 and South America only 92 / 68 events,
# too thin for 5 horizons + a VAL split, so 3b trains on dated GLC/COOLR points WORLDWIDE
# (location accuracy <= 5 km, since SMAP start; point + date extraction only — IMERG
# and SMAP are global). RS 2024 + WA 2025 are held out as unseen TEST events. None = global.
REGION_TRIGGER: tuple[float, float, float, float] | None = None
TRIGGER_TEST_EVENTS = ("rs_2024", "wa_2025")
# Per-region metric breakdown (name -> bbox); first match wins, else "other".
REPORT_REGIONS: dict[str, tuple[float, float, float, float]] = {
    "south_southeast_brazil": REGION_TRAIN,
    "south_america": (-82.0, -56.0, -34.0, 13.0),
    "north_america": (-170.0, 7.0, -50.0, 72.0),
    "asia": (60.0, -11.0, 150.0, 55.0),
    "europe_africa": (-20.0, -35.0, 60.0, 72.0),
}

# --- Labels -------------------------------------------------------------------
GLC_MAX_ACC_KM = 5.0       # keep GLC points with location accuracy <= 5 km
GLC_ACCURACY_KM = {        # GLC location_accuracy -> km (unknown dropped)
    "exact": 0.0, "1km": 1.0, "5km": 5.0, "10km": 10.0, "25km": 25.0,
    "50km": 50.0, "100km": 100.0, "250km": 250.0,
}
GLC_TRIGGERS = None        # None = all triggers; 3b keeps rain-type triggers only
GLC_RAIN_TRIGGERS = ("rain", "downpour", "continuous_rain", "monsoon",
                     "tropical_cyclone", "flooding")
RS2024_CATALOG = REPO_ROOT / "data" / "rs_2024" / "geojson" / "landslide_catalog.geojson"
RS2024_EVENT_ID = "rs_2024"
RS2024_WINDOW = (dt.date(2024, 4, 27), dt.date(2024, 5, 13))  # inventory window
# RS points are undated. Dating each one by its own peak-rain day would take the label
# from a model input (circular — conductor 2026-11-15). Instead: one fixed date for every
# RS point, independent of rain, and horizon labels score "landslide within the main
# triggering window" (Apr 29 - May 2, 2024).
RS2024_DATE = dt.date(2024, 5, 1)
RS2024_TRIGGER_WINDOW = (dt.date(2024, 4, 29), dt.date(2024, 5, 2))
RS2024_MAX_POINTS = 3000   # cap so one storm does not drown every other event
# WA 2025: Washington Geological Survey "December 2025 Atmospheric River Landslides"
# reports (FeatureServer layer 0). Only event_date + geometry are read — the layer also
# holds reporter names/emails, which are never requested. Validation-only TEST event.
WA2025_URL = ("https://services.arcgis.com/4x406oNViizbGo13/ArcGIS/rest/services/"
              "service_d63bc45f07fc4e859f0bdde6c847ec40/FeatureServer/0/query")
WA2025_EVENT_ID = "wa_2025"
WA2025_WINDOW = (dt.date(2025, 12, 1), dt.date(2025, 12, 31))
# Event-day windows excluded from same-point negative dates, per held-out event.
EVENT_WINDOWS = {RS2024_EVENT_ID: RS2024_WINDOW, WA2025_EVENT_ID: WA2025_WINDOW}
# Horizon-label windows for events dated by window, not per point (else [date, date]).
LABEL_WINDOWS = {RS2024_EVENT_ID: RS2024_TRIGGER_WINDOW}
# Fixed event day D per window-dated event (by-lead scoring); else each point's date.
EVENT_DAYS = {RS2024_EVENT_ID: RS2024_DATE}

# More held-out TEST events (ml/local/test_events.yaml, conductor 2026-11-15): enabled
# ones join TRIGGER_TEST_EVENTS / EVENT_WINDOWS / LABEL_WINDOWS / EVENT_DAYS here, so
# every stage sees them. ML_TEST_EVENTS=<id,id|none> overrides `enabled:` in the file.
# Enabling an event changes the 3b split -> re-run sample .. evaluate (and retrain).
from .events import load_registry as _load_registry, select as _select  # noqa: E402

TEST_EVENTS_FILE = Path(os.environ.get("ML_TEST_EVENTS_FILE",
                                       Path(__file__).with_name("test_events.yaml")))
TEST_EVENT_REGISTRY = _load_registry(TEST_EVENTS_FILE)
EXTRA_TEST_EVENTS = _select(TEST_EVENT_REGISTRY, os.environ.get("ML_TEST_EVENTS"))
TRIGGER_TEST_EVENTS = TRIGGER_TEST_EVENTS + tuple(e.event_id for e in EXTRA_TEST_EVENTS)
for _e in EXTRA_TEST_EVENTS:
    EVENT_WINDOWS[_e.event_id] = _e.inventory_window
    if _e.dating == "fixed":
        LABEL_WINDOWS[_e.event_id] = _e.trigger_window
        EVENT_DAYS[_e.event_id] = _e.date
# Leakage guard: GLC/COOLR points within LEAK_DAYS of a TEST event's inventory window
# and LEAK_KM of any of its points are removed from 3b TRAIN/VAL and 3a positives.
LEAK_DAYS = 3
LEAK_KM = 100.0
# NOAA GFS archive on AWS starts here; earlier TEST events get perfect-forecast only.
GFS_ARCHIVE_START = dt.date(2021, 3, 23)
# 3a T_ML positives (L32, conductor 2026-11-15): GLC only. RS 2024 points were 3a
# positives split by 50 km blocks, so 59 % of RS 3b TEST positives were T_ML TRAIN
# points (P_trigger x T_ML on RS leaked). RS stays a 3b TEST event; T_ML never sees it.
T_ML_POSITIVE_SOURCES = ("glc",)

EVENT_LINK_DAYS = 3        # GLC points within 3 days ...
EVENT_LINK_KM = 100.0      # ... and 100 km are one event

# --- Sampling -----------------------------------------------------------------
SEED = 42
NEG_RATIO_3A = 3           # 3 negatives per positive (PLAN 3a step 2)
NEG_MIN_DIST_KM = 5.0      # negatives > 5 km from any known landslide
NEG_DATES_3B = 5           # same point on 5 random no-event dates (PLAN 3b)
NEG_DATE_POOL = 600        # 3b negative dates drawn from a fixed pool (bounds SMAP fetch)
NEG_DATE_GAP_DAYS = 30     # negative dates >= 30 d from any event at that point

# --- Split (decided before training; file written by `split` stage) -----------
BLOCK_DEG = 0.5            # ~50 km spatial blocks (PLAN 3a step 4)
BUFFER_KM = 10.0           # TRAIN/VAL rows this close to another split's block are dropped
SPLIT_FRACS = {"train": 0.6, "val": 0.2, "test": 0.2}  # by positive count

# --- Horizons (3b) --------------------------------------------------------------
# Issue time = 00 UTC of day D. Horizon window = UTC days D .. D+H-1.
# Observed rain = IMERG days <= D-1; forecast rain = GFS 00Z run of D.
HORIZONS: dict[str, int] = {"24h": 1, "48h": 2, "72h": 3, "7d": 7, "14d": 14}
GFS_MAX_LEAD_DAYS = 16     # GFS runs to 384 h

# --- Features -------------------------------------------------------------------
TERRAIN = ["elevation", "slope", "aspect_sin", "aspect_cos", "curvature", "relief"]
LANDCOVER = ["landcover", "ndvi"]
SOIL_TEXTURE = ["clay", "sand", "porosity"]
CLIMATE = ["rain_mean", "soil_mean"]
RAIN = ["rain_24h", "rain_72h", "rain_7d", "rain_24h_rel", "rain_72h_rel"]
SOIL_DYN = ["sm_surface", "sm_rootzone", "sm_profile_wetness"]
CATEGORICAL = ["landcover"]

FEATURE_GROUPS_3A: dict[str, list[str]] = {
    "terrain": TERRAIN,
    "landcover": LANDCOVER,
    "climate": CLIMATE,
    "soil_texture": SOIL_TEXTURE,
}
FEATURE_GROUPS_3B: dict[str, list[str]] = {
    "rain": RAIN,
    "soil": SOIL_DYN + SOIL_TEXTURE,
    "terrain": TERRAIN,
    "landcover": LANDCOVER,
}
# Ablations (conductor 2026-11-15): what soil and terrain add over rain.
ABLATIONS_3B: dict[str, list[str]] = {
    "rain_only": ["rain"],
    "rain_soil": ["rain", "soil"],
    "all": ["rain", "soil", "terrain", "landcover"],
}
ABLATIONS_3A: dict[str, list[str]] = {
    "terrain_only": ["terrain"],
    "terrain_landcover": ["terrain", "landcover"],
    "all": ["terrain", "landcover", "climate", "soil_texture"],
}

# "More rain / wetter soil / steeper slope never lowers the hazard."
MONOTONE = {
    "rain_24h": 1, "rain_72h": 1, "rain_7d": 1, "rain_24h_rel": 1, "rain_72h_rel": 1,
    "sm_surface": 1, "sm_rootzone": 1, "sm_profile_wetness": 1,
}

# --- Models -------------------------------------------------------------------
RF_PARAMS = {"n_estimators": 200, "min_samples_leaf": 5, "n_jobs": -1}  # PLAN: smileRandomForest(200)
XGB_PARAMS = {
    "objective": "binary:logistic", "n_estimators": 600, "learning_rate": 0.05,
    "max_depth": 3, "subsample": 0.7, "colsample_bytree": 0.8,
    "min_child_weight": 5, "tree_method": "hist", "eval_metric": "aucpr",
    "early_stopping_rounds": 50, "enable_categorical": True,
}
# Served trigger (lead-live Now job). xgb_rain_soil has no terrain inputs, so the
# 0.01° serving grid (block-averaged terrain) cannot shift it; WHERE comes from T_ML.
SERVE_TRIGGER = "xgb_rain_soil"
TPI_RULE_THRESHOLD = 0.6   # TPI > 0.6 = Crítico (rule baseline)


def path(*parts: str) -> Path:
    """Path under DATA_DIR, parents created."""
    p = DATA_DIR.joinpath(*parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def features_3a(groups: list[str] | None = None) -> list[str]:
    groups = groups or list(FEATURE_GROUPS_3A)
    return [f for g in groups for f in FEATURE_GROUPS_3A[g]]


def features_3b(groups: list[str] | None = None) -> list[str]:
    groups = groups or list(FEATURE_GROUPS_3B)
    return [f for g in groups for f in FEATURE_GROUPS_3B[g]]
