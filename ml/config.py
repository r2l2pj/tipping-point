"""Configuration for the RS-2024 landslide trigger model.

Single source of truth for AOI, event window, feature names, GEE
collection/asset IDs, negative-sampling plan and thresholds.
Spec: .crew/reports/ml-predict.md §5.1-5.4.
"""
from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

from tp.config import case as _tp_case
from tp.config import event_id as _tp_event_id
from tp.config import seed as _tp_seed
from tp.config import threshold as _tp_threshold

REPO_ROOT = Path(__file__).resolve().parents[1]
ML_DIR = Path(__file__).resolve().parent
MODEL_DIR = ML_DIR / "models"
DATA_DIR = REPO_ROOT / "data"
ML_CASE = "rs_2024"
EVENT_ID = _tp_event_id(ML_CASE)
SEED = _tp_seed()  # config.yaml seed: 42

# --- AOI / timing (config.yaml cases.rs_2024; events/rs-2024-05.json mirrors) --
_ML = _tp_case(ML_CASE)
AOI_BBOX = tuple(_ML["aoi"])  # west, south, east, north
# Window in which each positive cell gets t = end of its max-24h IMERG window.
EVENT_WINDOW = tuple(d.isoformat() for d in _ML["label_window"])
# Replay frames: one daily 00Z frame per label_window day (spec §3 "10 daily frames").
FRAME_TIMES_UTC = [
    f"{(_ML['label_window'][0] + timedelta(days=i)).isoformat()}T00:00:00Z"
    for i in range((_ML["label_window"][1] - _ML["label_window"][0]).days + 1)
]

BLOCK_SIZE_DEG = 0.25  # ~25 km, larger than IMERG 0.1° and SMAP 9 km pixels
CELL_SIZE_M = 90.0     # static-grid resolution; positives deduped to one per cell
N_FOLDS = 5

# --- GEE collections / assets ----------------------------------------------
GEE_PROJECT = os.environ.get("GEE_PROJECT", "")
ASSET_ROOT = f"projects/{GEE_PROJECT}/assets/tipping-point" if GEE_PROJECT else None
ASSET_SLIDES = "rs2024_slides"        # deduped positive cells with assigned t
ASSET_FOOTPRINT = "rs2024_footprint"  # mapped footprint (convex hull)
ASSET_IMERG_P99 = "imerg_daily_p99"   # one-time P99(daily rain 2001-2023) asset

COL_IMERG = "NASA/GPM_L3/IMERG_V07"
BAND_IMERG = "precipitation"  # mm/h, 30-min steps → sums multiply by 0.5
COL_SMAP = "NASA/SMAP/SPL4SMGP/008"   # /007 is deprecated
BAND_SMAP = "sm_profile_wetness"
IMG_NASADEM = "NASA/NASADEM_HGT/001"
COL_MODIS_LC = "MODIS/061/MCD12Q1"
BAND_LC = "LC_Type1"
LC_YEAR = 2023
SMAP_LAG_DAYS = 3  # latency-honest: SMAP L4 has >2 d latency (LHASA Fig. 2)

# --- Inventory sources (labels only — NASA-only-inputs rule for features) ---
ZENODO_UFRGS = "10.5281/zenodo.15618462"   # ~16.9k initiation points, CC-BY-4.0
ZENODO_CEMADEN = "10.5281/zenodo.13225473" # 15,057 rupture points, CC-BY-4.0

# --- Negative sampling (spec §2.3) ------------------------------------------
POS_CAP = 10_000
N1_COUNT = 20_000          # event-window spatial negatives ("where")
N1_MIN_DIST_M = 250.0      # from any inventory polygon
N1_STEEP_FRAC = 0.5        # half of N1 drawn from slope > 10°
N1_STEEP_SLOPE_DEG = 10.0
N2_CALM_DAYS = 200         # temporal negatives ("when")
N2_RANGE = ("2015-04-01", "2024-03-31")
# Known heavy-rain windows in RS — excluded from N2 (unlabeled slides = noise).
EXCLUDED_RAIN_WINDOWS = [
    ("2023-06-01", "2023-06-30"),
    ("2023-09-01", "2023-09-30"),
    ("2023-11-01", "2023-11-30"),
]

# --- Feature table (spec §5.1) ----------------------------------------------
ID_COLS = ["cell_id", "lon", "lat", "t_utc", "label", "set", "block_id"]
STATIC_FEATURES = [
    "slope_mean", "slope_max", "relief_500m", "curv",
    "aspect_sin", "aspect_cos", "elev", "hand_proxy", "lc",
]
DYNAMIC_FEATURES = ["rain_24h", "rain_ante_2d", "rain_7d", "rain_rel", "sm_profile_wetness"]
FEATURES = STATIC_FEATURES + DYNAMIC_FEATURES
CATEGORICAL_FEATURES = ["lc"]

# XGBoost spec §5.2 — LHASA Table 4 values, depth 3 (interaction constraints dropped).
XGB_PARAMS = {
    "objective": "binary:logistic",
    "n_estimators": 600,
    "learning_rate": 0.05,
    "max_depth": 3,
    "subsample": 0.5,
    "colsample_bytree": 0.8,
    "min_child_weight": 5,
    "max_delta_step": 1,
    "tree_method": "hist",
    "enable_categorical": True,
    "eval_metric": "aucpr",
    "early_stopping_rounds": 50,
}
# "More rain or wetter soil never lowers the hazard."
MONOTONE_CONSTRAINTS = {
    "rain_24h": 1, "rain_ante_2d": 1, "rain_7d": 1, "rain_rel": 1,
    "sm_profile_wetness": 1, "slope_mean": 1, "slope_max": 1, "relief_500m": 1,
}

# --- Thresholds / output classes --------------------------------------------
FPR_TARGET = 0.05          # thresholds are set at fixed FPR on validation folds
HAZARD_THRESHOLD = 0.12    # LHASA-style fallback until refit on fold scores
CLASS_BREAKS = tuple(_tp_threshold("tpi_classes", (0.2, 0.4, 0.6)))  # SCHEMA `tpi` legend: 0-3
# Geomean exponents [R,S,T] for the manual-TPI baseline (evaluate.py);
# uniform thirds = plain (R·S·T)^(1/3). From config.yaml thresholds.
TPI_WEIGHTS = tuple(_tp_threshold("tpi_weights", (1 / 3, 1 / 3, 1 / 3)))
MIN_SLOPE_DEG = 5.0        # mask gentler slopes (LHASA-style)
LC_WATER = 0               # MODIS LC_Type1 water class
RASTER_NODATA = 255        # uint8 nodata for hazard rasters
HAZARD_SCALE = 100         # probabilities written as 0-100 uint8

# --- Lead time ----------------------------------------------------------------
# Anchor for "warned X h before" must be an independently sourced, dated first
# landslide/fatality report (Defesa Civil RS / press). Measuring against the
# assigned label time would be circular (spec §2.2). Empty until sourced.
ANCHOR_TIMES_UTC: dict[str, str] = {}


def asset_path(name: str) -> str:
    """Full GEE asset path; requires GEE_PROJECT."""
    if not ASSET_ROOT:
        raise RuntimeError("GEE_PROJECT is not set; cannot build asset path")
    return f"{ASSET_ROOT}/{name}"
