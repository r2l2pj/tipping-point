"""Polygonise a mask raster to GeoJSON (EPSG:4326) with area_km2 attributes.

CRS conventions (doc v4): GeoJSON vectors and COGs stay EPSG:4326 — only
pre-rendered PNGs for the app's MapLibre image sources are EPSG:3857
(rendered by live/render.py; a 4326 PNG stretched over lon/lat corners
misaligns at these latitudes).
"""
from __future__ import annotations

import json
import math
import re
from datetime import date as iso_date
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.features import shapes
from shapely import make_valid
from shapely.geometry import mapping, shape

PRODUCT_PATTERNS = {
    "flood_confirmed": ("*flood_nisar*.tif", "*flooded_area*.tif"),
    "permanent_water": ("*water_nisar*.tif",),
    "landslide_candidates": ("*landslide_candidates*.tif",),
}


def polygon_parts(geometry):
    geometry = make_valid(geometry)
    if geometry.geom_type == "Polygon":
        if not geometry.is_empty and geometry.area > 0:
            yield geometry
    elif hasattr(geometry, "geoms"):
        for part in geometry.geoms:
            yield from polygon_parts(part)


def product_kind(path: Path) -> str:
    for kind, patterns in PRODUCT_PATTERNS.items():
        if any(path.match(pattern) for pattern in patterns):
            return kind
    return "mask"


def mask_to_geojson(
    raster_path: Path,
    out_path: Path,
    values: tuple[int, ...] = (1,),
    min_area_km2: float = 0.0,
    simplify_m: float = 1.0,
    kind: str | None = None,
    date: str | None = None,
) -> dict:
    """Extract `values` class pixels as polygons.

    Each feature gets {"value": <class>, "area_km2": ...}; `area_km2` is
    computed in the raster's estimated UTM CRS. Output is EPSG:4326.
    """
    raster_path, out_path = Path(raster_path), Path(out_path)
    if not raster_path.is_file():
        raise FileNotFoundError(f"input mask raster not found: {raster_path}")
    if any(not math.isfinite(v) or v < 0 for v in (min_area_km2, simplify_m)):
        raise ValueError("min_area_km2 and simplify_m must be finite and non-negative")
    if date is None:
        match = re.search(r"(\d{8})$", raster_path.stem)
        date = iso_date.fromisoformat(match[1]).isoformat() if match else None
    elif date:
        date = iso_date.fromisoformat(date).isoformat()
    with rasterio.open(raster_path) as src:
        if src.crs is None:
            raise ValueError(f"input mask raster has no CRS: {raster_path}")
        band = src.read(1, masked=True)
        mask = np.isin(band.data, values) & ~np.ma.getmaskarray(band)
        records = [
            (part, int(value))
            for geom, value in shapes(band.data.astype("float32"), mask=mask, transform=src.transform)
            for part in polygon_parts(shape(geom))
        ]
        crs = src.crs
    features = []
    if records:
        series = gpd.GeoSeries([geom for geom, _ in records], crs=crs)
        metric = series.to_crs(series.estimate_utm_crs())
        for geometry, (_, value) in zip(metric, records, strict=True):
            for part in polygon_parts(geometry.simplify(simplify_m, preserve_topology=True)):
                area = part.area / 1e6
                if area < min_area_km2:
                    continue
                geographic = gpd.GeoSeries([part], crs=metric.crs).to_crs("EPSG:4326").iloc[0]
                for polygon in polygon_parts(geographic):
                    features.append({
                        "type": "Feature", "geometry": mapping(polygon),
                        "properties": {
                            "value": value, "class": value, "kind": kind or product_kind(raster_path),
                            "area_km2": area, "source": raster_path.name, "date": date,
                        },
                    })
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"type": "FeatureCollection", "features": features}, allow_nan=False) + "\n")
    return {"features": len(features), "path": str(out_path)}
