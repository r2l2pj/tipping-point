"""Shared constants for Modo Agora — single source of truth is config.yaml.

The TPI math in live/tpi.py mirrors gee/01_tpi_index.js; the thresholds below
come from the same config.yaml the GEE scripts paste by hand.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
_pipeline = str(REPO_ROOT / "pipeline")
if _pipeline not in sys.path:
    sys.path.insert(0, _pipeline)
from tp.config import now_bounds as _now_bounds
from tp.config import now_name as _now_name

CONFIG_PATH = REPO_ROOT / "config.yaml"
STATIC_DIR = REPO_ROOT / "static"
NOW_DIR = REPO_ROOT / "data" / "now"
STATIC_NPZ = "sul_static.npz"

# Sul do Brasil inteiro (RS, SC, PR) — doc v4 manifest "now".bounds
NOW_BOUNDS = tuple(_now_bounds())          # west, south, east, north — config.yaml domains.now
NOW_RES_DEG = 0.01                          # ~1 km
IMERG_LOOKBACK_DAYS = 8   # >= 8: R1's rain-context rule reads 8 daily COGs
NISAR_LOOKBACK_DAYS = 14
FORECAST_DAYS = 3

LABELS = ["Baixo", "Moderado", "Alto", "Crítico"]
PALETTE_HEX = ["ffffb2", "fecc5c", "fd8d3c", "bd0026"]

_cfg = yaml.safe_load(CONFIG_PATH.read_text())["thresholds"]

RAIN_PERCENTILES = tuple(_cfg["rain_percentiles"])          # (90, 95, 99)
SOIL_WETNESS_RANGE = tuple(_cfg["soil_wetness_range"])      # (0.5, 0.9)
SLOPE_RANGE_DEG = tuple(_cfg["slope_range_deg"])            # (15, 30)
HAND_RANGE_M = tuple(_cfg["hand_range_m"])                  # (5, 15)
HAND_PROXY_RADIUS_M = _cfg["hand_proxy_radius_m"]           # 1500
TPI_CLASSES = tuple(_cfg["tpi_classes"])                    # (0.2, 0.4, 0.6)
TPI_WEIGHTS = tuple(_cfg.get("tpi_weights", (1 / 3, 1 / 3, 1 / 3)))  # geomean [R,S,T]
MIN_BLOB_PIXELS = _cfg["min_blob_pixels"]                   # 10
SLOPE_FLOOD_MAX_DEG = 5.0                                   # gee/01: slope.gt(5) -> T_flood=0

QUIET_HOURS = frozenset({23, 0, 1, 2, 3, 4, 5})             # alert rules, doc "Regras de envio"
ALERT_MIN_CLASS = 2                                         # send only on upgrade to Alto/Crítico

# L8 — GEE assets are the single source of truth; the local numpy path is
# only a fallback (run_now --local). These constants name the locked
# interface shared with lead-gathering (pipeline/fetch/*) and lead-analysis
# (tools/*, derived now/* assets).
GEE_PROJECT = os.environ.get("GEE_PROJECT", "jovial-idiom-514715-j8")
ASSET_ROOT = f"projects/{GEE_PROJECT}/assets/tipping-point"
NOW_COLLECTIONS = {"imerg_early", "smap_l4", "geosfp"}
STATIC_DOMAIN = _now_name()                 # config.yaml domains.now.name
