"""Shared helpers: synthetic in-memory rasters (no downloaded fixtures)."""
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin


def write_tif(
    path: Path,
    array: np.ndarray,
    res: float = 10.0,
    crs: str = "EPSG:32610",
    nodata: float | None = np.nan,
) -> Path:
    h, w = array.shape
    transform = from_origin(500000, 5300000, res, res)
    with rasterio.open(
        path, "w", driver="GTiff", height=h, width=w, count=1,
        dtype=array.dtype, crs=crs, transform=transform, nodata=nodata,
    ) as dst:
        dst.write(array, 1)
    return path
