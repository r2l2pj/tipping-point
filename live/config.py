"""Shared constants for Modo Agora — single source of truth is config.yaml.

The TPI math in live/tpi.py mirrors gee/01_tpi_index.js; the thresholds below
come from the same config.yaml the GEE scripts paste by hand.
"""
from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "config.yaml"
STATIC_DIR = REPO_ROOT / "static"
NOW_DIR = REPO_ROOT / "data" / "now"
STATIC_NPZ = "sul_static.npz"

# Sul do Brasil inteiro (RS, SC, PR) — doc v4 manifest "now".bounds
NOW_BOUNDS = (-58.0, -34.0, -48.0, -22.5)  # west, south, east, north
NOW_RES_DEG = 0.01                          # ~1 km
IMERG_LOOKBACK_DAYS = 7
NISAR_LOOKBACK_DAYS = 14
FORECAST_DAYS = 3
GCS_BUCKET = "tipping-point-data"

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
