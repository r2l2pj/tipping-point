"""Validation against OPERA DSWx-HLS (30 m water product, JPL).

WTR layer values: 0 no water, 1 open water, 2 partial water, 253 cloud/shadow,
255 no data. Metrics vs the NISAR water mask: IoU, F1, plus the cloud-cover
percentage (the "why radar" argument).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import rioxarray as rxr

WATER_VALS = (1, 2)
INVALID_VALS = (253, 255)
CLOUD_VAL = 253


def search_download_opera(bbox: tuple, temporal: tuple, dest: Path) -> list:
    """earthaccess search+download for OPERA_L3_DSWX-HLS_V1."""
    import earthaccess

    earthaccess.login()
    granules = earthaccess.search_data(
        short_name="OPERA_L3_DSWX-HLS_V1", bounding_box=bbox, temporal=temporal
    )
    return earthaccess.download(granules, str(dest))


def validate_flood(water_nisar_path: Path, opera_wtr_path: Path) -> dict:
    water_nisar = rxr.open_rasterio(water_nisar_path).squeeze()
    wtr = (
        rxr.open_rasterio(opera_wtr_path)
        .squeeze()
        .rio.reproject_match(water_nisar, resampling=0)
        .values
    )

    valid = ~np.isin(wtr, INVALID_VALS)
    ref = np.isin(wtr, WATER_VALS) & valid
    pred = water_nisar.values.astype(bool) & valid

    tp = int((ref & pred).sum())
    fp = int((~ref & pred).sum())
    fn = int((ref & ~pred).sum())
    return {
        "iou": tp / (tp + fp + fn) if (tp + fp + fn) else 0.0,
        "f1": 2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) else 0.0,
        "cloud_pct": float(100 * (wtr == CLOUD_VAL).sum() / max((wtr != 255).sum(), 1)),
    }


def write_metrics(metrics: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metrics, indent=2))
