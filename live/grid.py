"""Common ~1 km lat/lon grid for Modo Agora and regridding helpers."""
from __future__ import annotations

import numpy as np

from .config import NOW_BOUNDS, NOW_RES_DEG


def dst_lats_lons(bounds: tuple[float, float, float, float] = NOW_BOUNDS,
                  res_deg: float = NOW_RES_DEG) -> tuple[np.ndarray, np.ndarray]:
    """Cell-center lat/lon vectors. Lats descend (north first, image order)."""
    w, s, e, n = bounds
    lons = np.arange(w + res_deg / 2, e, res_deg)
    lats = np.arange(n - res_deg / 2, s, -res_deg)
    return lats, lons


def dst_transform(bounds: tuple[float, float, float, float] = NOW_BOUNDS,
                  res_deg: float = NOW_RES_DEG):
    from rasterio.transform import from_origin

    w, _s, _e, n = bounds
    return from_origin(w, n, res_deg, res_deg)


def regrid_points(src_lats: np.ndarray, src_lons: np.ndarray, values: np.ndarray,
                  dst_lats: np.ndarray, dst_lons: np.ndarray,
                  method: str = "linear") -> np.ndarray:
    """Interpolate scattered/native-grid data (e.g. SMAP EASE grid) to dst grid."""
    from scipy.interpolate import griddata

    pts = np.column_stack([np.ravel(src_lats), np.ravel(src_lons)])
    vals = np.ravel(values)
    ok = np.isfinite(vals)
    glat, glon = np.meshgrid(dst_lats, dst_lons, indexing="ij")
    out = griddata(pts[ok], vals[ok], (glat, glon), method=method)
    if method != "nearest":
        near = griddata(pts[ok], vals[ok], (glat, glon), method="nearest")
        out = np.where(np.isfinite(out), out, near)
    return out


def regrid_da(da, dst_lats: np.ndarray, dst_lons: np.ndarray,
              method: str = "linear") -> np.ndarray:
    """Interpolate a lat/lon xarray.DataArray to the dst grid."""
    lat_name = next(d for d in da.dims if "lat" in d.lower() or d.lower() in ("y", "latitude"))
    lon_name = next(d for d in da.dims if "lon" in d.lower() or d.lower() in ("x", "longitude"))
    out = da.interp({lat_name: dst_lats, lon_name: dst_lons}, method=method)
    return np.asarray(out.values, dtype=np.float64)


def pixel_area_km2(lats: np.ndarray, res_deg: float = NOW_RES_DEG) -> np.ndarray:
    """Per-row pixel area (km²); broadcastable to (len(lats), n_cols)."""
    km = 111.195 * res_deg
    return (km * km * np.cos(np.deg2rad(lats)))[:, None]
