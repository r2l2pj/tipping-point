"""Render Modo Agora rasters to app-ready artifacts.

Each class layer becomes a pre-colored RGBA PNG on an EPSG:3857 pixel grid
(the app shows it as a MapLibre image source; Web-Mercator PNGs are required
because an EPSG:4326 PNG stretched over lon/lat corners misaligns at these
latitudes — doc v4 "Regras de visualização"). The manifest layer `bounds`
carries the 3857 image extent in lon/lat. GeoJSON stays EPSG:4326.
"""
from __future__ import annotations

import io
import json

import numpy as np

from .config import LABELS, MIN_BLOB_PIXELS, PALETTE_HEX


def _rgba(class_arr: np.ndarray, valid: np.ndarray) -> np.ndarray:
    rgba = np.zeros((*class_arr.shape, 4), dtype=np.uint8)
    for cls, hexcol in enumerate(PALETTE_HEX):
        rgb = tuple(int(hexcol[i:i + 2], 16) for i in (0, 2, 4))
        m = (class_arr == cls) & valid
        rgba[m] = (*rgb, 255)
    return np.moveaxis(rgba, -1, 0)  # bands first for rasterio


def classes_png_bytes(class_arr: np.ndarray, valid: np.ndarray | None = None) -> bytes:
    """Encode a 0..3 class array as a 4-band RGBA PNG (nodata -> transparent)."""
    import rasterio

    class_arr = np.asarray(class_arr, dtype=np.uint8)
    if valid is None:
        valid = np.isfinite(class_arr)
    bands = _rgba(class_arr, valid)
    with rasterio.MemoryFile() as mf:
        with mf.open(driver="PNG", height=class_arr.shape[0],
                     width=class_arr.shape[1], count=4, dtype="uint8") as dst:
            dst.write(bands)
        return mf.read()


def warp_to_3857(arr: np.ndarray, src_transform, src_crs: str = "EPSG:4326"):
    """Nearest-neighbor warp of a (H,W) array onto an EPSG:3857 grid.

    Returns (dst_arr, dst_transform, bounds_lonlat) where bounds_lonlat is
    [w, s, e, n] — the 3857 image extent back in lon/lat, i.e. the `bounds`
    the manifest layer carries for the MapLibre image source.
    """
    from rasterio.crs import CRS
    from rasterio.transform import array_bounds
    from rasterio.warp import (
        Resampling,
        calculate_default_transform,
        reproject,
        transform_bounds,
    )

    src_crs = CRS.from_user_input(src_crs)
    dst_crs = CRS.from_epsg(3857)
    h, w = arr.shape
    left, bottom, right, top = array_bounds(h, w, src_transform)
    dst_tr, dst_w, dst_h = calculate_default_transform(
        src_crs, dst_crs, w, h, left=left, bottom=bottom, right=right, top=top)
    dst = np.empty((dst_h, dst_w), dtype=arr.dtype)
    reproject(arr, dst, src_transform=src_transform, src_crs=src_crs,
              dst_transform=dst_tr, dst_crs=dst_crs,
              resampling=Resampling.nearest, src_nodata=0, dst_nodata=0)
    bounds = transform_bounds(dst_crs, CRS.from_epsg(4326),
                              *array_bounds(dst_h, dst_w, dst_tr))
    return dst, dst_tr, [round(float(b), 6) for b in bounds]


def layer_png_3857(class_arr: np.ndarray, transform, valid: np.ndarray | None = None):
    """Encode a 4326 class grid as an EPSG:3857 RGBA PNG for MapLibre.

    Returns (png_bytes, bounds_lonlat). `transform` is the affine of the
    source EPSG:4326 grid (live.grid.dst_transform).
    """
    class_arr = np.asarray(class_arr, dtype=np.uint8)
    if valid is None:
        valid = np.isfinite(class_arr)
    cls3857, _, bounds = warp_to_3857(class_arr, transform)
    val3857, _, _ = warp_to_3857(np.asarray(valid, dtype=np.uint8), transform)
    return classes_png_bytes(cls3857, val3857.astype(bool)), bounds


def critical_features(class_arr: np.ndarray, transform, kind: str, day: str,
                      min_pixels: int = MIN_BLOB_PIXELS) -> list[dict]:
    """Polygonize connected areas with class >= 2 (Alto/Crítico) to GeoJSON."""
    import rasterio.features
    from scipy.ndimage import label

    cls = np.asarray(class_arr, dtype=np.uint8)
    feats = []
    for c in (2, 3):
        mask = cls == c
        if not mask.any():
            continue
        lab, n = label(mask)
        sizes = np.bincount(lab.ravel())
        for i in range(1, n + 1):
            if sizes[i] < min_pixels:
                continue
            for geom, _ in rasterio.features.shapes(
                    np.uint8(lab == i), mask=(lab == i), transform=transform):
                feats.append({
                    "type": "Feature",
                    "geometry": geom,
                    "properties": {
                        "kind": kind, "day": day,
                        "class": int(c), "label": LABELS[c],
                        "pixels": int(sizes[i]),
                    },
                })
    return feats


def feature_collection(features: list[dict]) -> dict:
    return {"type": "FeatureCollection", "features": features}


def json_bytes(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")


def npz_bytes(**arrays) -> bytes:
    buf = io.BytesIO()
    np.savez(buf, **arrays)
    return buf.getvalue()
