"""Polygonise a mask raster to GeoJSON (EPSG:4326) with area_km2 attributes."""
from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.features import shapes
from shapely.geometry import shape


def mask_to_geojson(
    raster_path: Path,
    out_path: Path,
    values: tuple[int, ...] = (1,),
    min_area_km2: float = 0.0,
) -> dict:
    """Extract `values` class pixels as polygons.

    Each feature gets {"value": <class>, "area_km2": ...}; `area_km2` is
    computed in the raster's estimated UTM CRS. Output is EPSG:4326.
    """
    with rasterio.open(raster_path) as src:
        band = src.read(1)
        crs = src.crs
        transform = src.transform
        nodata = src.nodata

    mask = np.isin(band, list(values))
    if nodata is not None:
        mask &= band != nodata
    geoms = (
        {"geometry": shape(geom), "properties": {"value": int(v)}}
        for geom, v in shapes(band, mask=mask, transform=transform)
    )
    gdf = gpd.GeoDataFrame.from_features(list(geoms), crs=crs)
    if not gdf.empty:
        utm = gdf.estimate_utm_crs()
        gdf["area_km2"] = gdf.to_crs(utm).area / 1e6
        gdf = gdf[gdf["area_km2"] >= min_area_km2].to_crs("EPSG:4326")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(out_path, driver="GeoJSON")
    return {"features": len(gdf), "path": str(out_path)}
