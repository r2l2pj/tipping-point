"""wa_2025 landslide scar CANDIDATES from post-event HLS optical dNBR.

A landslide strips vegetation, so the Normalized Burn Ratio drops:
dNBR = NBR_pre - NBR_post > dnbr_min marks a candidate pixel. Extra gates:
the post scene's Fmask must be clear (bit 1 cloud + bit 2 cloud shadow are
masked out), terrain slope must exceed the config minimum (scars need
steep ground), and blobs smaller than min_blob_pixels are dropped.

Input layout per GeoTIFF (documented — no band-name metadata):
  1 band   -> precomputed NBR
  2+ bands -> [NIR, SWIR2]; NBR = (NIR - SWIR2) / (NIR + SWIR2)
  post only, 3+ bands -> band 3 is Fmask. When no Fmask band is present no
  cloud mask is applied and the run reports fmask="absent".

LIMITATION: the NISAR GUNW coherence pair spanning the 2025-12 event is
missing (the GCOV track 172D frame 064 stack covers 2025-11-10 /
2025-12-04 / 2025-12-16 but no event-crossing GUNW pair exists), so the
coherence-change path in tp/landslide_coh.py does not apply. Every output
here is a CANDIDATE — there is no confirmation step.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import rioxarray as rxr
from scipy.ndimage import label

from tp.config import threshold

MIN_SLOPE_DEG = float(threshold("slope_range_deg", [15, 30])[0])
MIN_BLOB_PX = int(threshold("min_blob_pixels", 10))
DNBR_MIN = 0.1                 # USGS burn-severity convention: 0.1 = low severity
FMASK_CLOUD_SHADOW = 0b110     # Fmask bit1 cloud + bit2 cloud shadow


def _as_nbr(da) -> np.ndarray:
    """NBR array: 1 band = precomputed NBR; else band1=NIR, band2=SWIR2."""
    arr = np.asarray(da.values, dtype=np.float64)
    if arr.shape[0] < 2:
        return arr[0]
    nir, swir2 = arr[0], arr[1]
    denom = nir + swir2
    return np.where(np.abs(denom) > 1e-9, (nir - swir2) / denom, np.nan)


def _fmask(da) -> np.ndarray | None:
    """Post band 3 as Fmask, or None when the raster has < 3 bands."""
    arr = np.asarray(da.values)
    return arr[2].astype(np.float64) if arr.shape[0] >= 3 else None


def _drop_small(mask: np.ndarray, min_px: int) -> np.ndarray:
    """Class raster {0,1} with connected components < min_px removed."""
    out = np.zeros(mask.shape, dtype="uint8")
    if min_px <= 1 or not mask.any():
        out[mask] = 1
        return out
    labels, _ = label(mask)
    sizes = np.bincount(labels.ravel())
    keep = np.isin(labels, np.where(sizes >= min_px)[0])
    out[keep & (labels > 0)] = 1
    return out


def detect(
    pre_tif: Path,
    post_tif: Path,
    out_tif: Path,
    slope_tif: Path | None = None,
    min_slope_deg: float = MIN_SLOPE_DEG,
    min_px: int = MIN_BLOB_PX,
    dnbr_min: float = DNBR_MIN,
) -> dict:
    """Write a uint8 class COG (0 none, 1 candidate) on the post grid."""
    post = rxr.open_rasterio(post_tif)
    pre = rxr.open_rasterio(pre_tif)
    if pre.shape != post.shape or pre.rio.crs != post.rio.crs:
        pre = pre.rio.reproject_match(post)

    dnbr = _as_nbr(pre) - _as_nbr(post)
    cand = np.isfinite(dnbr) & (dnbr > dnbr_min)
    stats = {"fmask": "absent", "slope_gate": slope_tif is not None,
             "dnbr_min": dnbr_min}

    fmask = _fmask(post)
    if fmask is not None:
        fm_int = np.where(np.isfinite(fmask), fmask, 0).astype(np.int64)
        clear = np.isfinite(fmask) & ((fm_int & FMASK_CLOUD_SHADOW) == 0)
        stats["fmask"] = "applied"
        stats["fmask_masked_px"] = int((cand & ~clear).sum())
        cand &= clear

    if slope_tif is not None:
        slope = rxr.open_rasterio(slope_tif).squeeze().rio.reproject_match(post)
        cand &= np.isfinite(slope.values) & (slope.values > min_slope_deg)

    classes = _drop_small(cand, min_px)
    _, n = label(classes > 0)

    out_tif = Path(out_tif)
    out_tif.parent.mkdir(parents=True, exist_ok=True)
    import rasterio

    with rasterio.open(
        out_tif, "w", driver="COG",
        height=classes.shape[0], width=classes.shape[1], count=1,
        dtype="uint8", crs=post.rio.crs,
        transform=post.rio.transform(),
    ) as dst:
        dst.write(classes, 1)

    stats.update(
        candidate_patches=int(n),
        candidate_px=int(classes.sum()),
    )
    return stats
