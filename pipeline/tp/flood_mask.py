"""Flood mask from NISAR GCOV backscatter (pre/post dB ratio).

Rule: pixel is flooded when (post_db - pre_db) < -3 AND post_db < ~-15 AND it was
not already water before the event. Water is dark in radar.
Caveat to declare: flooded urban areas can BRIGHTEN in L-band (double bounce).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import rioxarray as rxr
from scipy.ndimage import median_filter

FLOOD_DROP_DB = -3.0
WATER_DB = -15.0


def to_db(x: np.ndarray) -> np.ndarray:
    return 10 * np.log10(np.clip(x, 1e-6, None))


def flood_mask(pre_path: Path, post_path: Path, out_dir: Path) -> dict:
    """Write flood_nisar.tif + water_nisar.tif COGs; return stats."""
    pre = rxr.open_rasterio(pre_path).squeeze()
    post = rxr.open_rasterio(post_path).squeeze()

    pre_db = median_filter(to_db(pre.values), size=3)
    post_db = median_filter(to_db(post.values), size=3)

    water_before = pre_db < WATER_DB
    water_now = post_db < WATER_DB
    flood = (post_db - pre_db < FLOOD_DROP_DB) & water_now & ~water_before

    out_dir.mkdir(parents=True, exist_ok=True)
    pre.copy(data=flood.astype("uint8")).rio.to_raster(
        out_dir / "flood_nisar.tif", driver="COG"
    )
    pre.copy(data=water_now.astype("uint8")).rio.to_raster(
        out_dir / "water_nisar.tif", driver="COG"
    )

    res_x, res_y = pre.rio.resolution()
    return {"flood_km2": float(flood.sum() * abs(res_x * res_y) / 1e6)}
