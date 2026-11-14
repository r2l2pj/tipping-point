"""Render Modo Agora rasters to app-ready artifacts.

Each class layer becomes a pre-colored RGBA PNG (the app shows it as a
MapLibre image source with corner coords from the manifest) and the
Alto/Crítico areas become a GeoJSON FeatureCollection.
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
