"""Terrain slope (degrees) from a DEM via Horn's method (3x3, GDAL gdaldem).

Geodesic-aware: on a geographic CRS (degrees, e.g. EPSG:4326) the pixel size
is per-row metres from WGS84 degree lengths at each row's latitude — treating
degrees as metres would mis-scale dx by ~1/cos(lat) x 1e5; on a projected CRS
the transform's units/pixel are used directly. Nodata: the output is NaN
where the centre cell is nodata; a missing neighbour falls back to the
centre value (the gdaldem rule), so a nodata hole produces only a NaN hole.
"""
from __future__ import annotations

import numpy as np


def _deg_len_m(lat_deg: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """WGS84 metres per degree of (latitude, longitude) at `lat_deg`."""
    p = np.deg2rad(lat_deg)
    lat_m = (111132.92 - 559.82 * np.cos(2 * p) + 1.175 * np.cos(4 * p)
             - 0.0023 * np.cos(6 * p))
    lon_m = (111412.84 * np.cos(p) - 93.5 * np.cos(3 * p)
             + 0.118 * np.cos(5 * p))
    return lat_m, lon_m


def _pixel_size_m(transform, crs, nrows: int):
    """(dx, dy) in metres — per-row (nrows,) arrays for geographic CRS, else scalars."""
    if crs is not None and crs.is_geographic:
        lat = transform.f + (np.arange(nrows) + 0.5) * transform.e
        lat_m, lon_m = _deg_len_m(lat)
        return abs(transform.a) * lon_m, abs(transform.e) * lat_m
    return abs(transform.a), abs(transform.e)


def _horn(dem: np.ndarray, dx, dy) -> np.ndarray:
    """Horn 3x3 slope in degrees (float32). NaN centre -> NaN; NaN neighbour -> centre."""
    h, w = dem.shape
    c = dem.astype(np.float64)
    pad = np.pad(c, 1, constant_values=np.nan)

    def nb(r0: int, c0: int) -> np.ndarray:
        v = pad[r0 : r0 + h, c0 : c0 + w]
        return np.where(np.isfinite(v), v, c)

    z1, z2, z3 = nb(0, 0), nb(0, 1), nb(0, 2)
    z4, z6 = nb(1, 0), nb(1, 2)
    z7, z8, z9 = nb(2, 0), nb(2, 1), nb(2, 2)

    dx = np.asarray(dx, dtype=np.float64)
    dy = np.asarray(dy, dtype=np.float64)
    if dx.ndim:  # per-row sizes broadcast down the columns
        dx = dx[:, None]
        dy = dy[:, None]
    dzdx = ((z3 + 2 * z6 + z9) - (z1 + 2 * z4 + z7)) / (8 * dx)
    dzdy = ((z7 + 2 * z8 + z9) - (z1 + 2 * z2 + z3)) / (8 * dy)
    slope = np.degrees(np.arctan(np.hypot(dzdx, dzdy)))
    return np.where(np.isfinite(c), slope, np.nan).astype(np.float32)


def slope_deg(src, cellsize: float | tuple[float, float] | None = None):
    """Slope in degrees from a DEM.

    `src` is an open rasterio dataset, a path/URL, or a 2-D ndarray.
    `cellsize` is (dx, dy) units per pixel for ndarray input (default 1 m).

    Returns (slope, profile): a float32 degrees array, plus a rasterio
    profile for writing it (None for ndarray input).
    """
    if isinstance(src, np.ndarray):
        dem = np.asarray(src, dtype=np.float64)
        if dem.ndim != 2:
            raise ValueError("DEM array must be 2-D")
        if cellsize is None:
            dx = dy = 1.0
        elif np.isscalar(cellsize):
            dx = dy = float(cellsize)
        else:
            dx, dy = cellsize
        return _horn(dem, dx, dy), None

    import rasterio

    own = not hasattr(src, "read")
    ds = rasterio.open(src) if own else src
    try:
        dem = ds.read(1).astype(np.float64)
        dem[~np.isfinite(dem)] = np.nan
        nodata = ds.nodata
        if nodata is not None and not np.isnan(nodata):
            dem[dem == nodata] = np.nan
        dx, dy = _pixel_size_m(ds.transform, ds.crs, dem.shape[0])
        profile = dict(ds.profile)
        profile.update(dtype="float32", nodata=np.nan, count=1)
    finally:
        if own:
            ds.close()
    return _horn(dem, dx, dy), profile
