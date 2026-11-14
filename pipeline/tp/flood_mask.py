"""Flood mask from NISAR GCOV backscatter (pre/post dB ratio).

A pixel is flooded when (post_db - pre_db) < -3 AND post_db < T AND it was
not already water before the event AND HAND < 15 m AND it is not in the
NISAR_WATERMASK permanent-water layer. T is per-scene adaptive: Otsu over
the post-image dB histogram on bimodal 512 px tiles (DSWx-NI style), with
-15 dB as fallback/clamp for BETA uncalibrated data.

Bright flooding (inundated vegetation / urban double bounce brightens in
L-band) is a separate lower-confidence layer: HH increase > +3 dB, HAND < 5 m.
Minimum mapping unit ~3 ha removes speckle-level blobs.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import rioxarray as rxr
from scipy.ndimage import label, median_filter
from skimage.filters import threshold_otsu

from tp.config import threshold

FLOOD_DROP_DB = float(threshold("flood_db_drop", -3.0))
WATER_DB = float(threshold("water_db_max", -15.0))  # fallback and upper clamp for the adaptive threshold
BRIGHT_RISE_DB = 3.0
_HAND_RANGE_M = threshold("hand_range_m", [5, 15])
HAND_FLOOD_M = float(_HAND_RANGE_M[1])
HAND_BRIGHT_M = float(_HAND_RANGE_M[0])
MMU_HA = 3.0
TILE_PX = 512
_FILL_DB = 10.0           # bright fill so nodata never reads as water


def to_db(x: np.ndarray) -> np.ndarray:
    """Linear gamma0 power -> dB. NaN stays NaN; mask it out downstream."""
    x = np.asarray(x, dtype=np.float64)
    out = np.full_like(x, np.nan)
    np.log10(np.clip(x, 1e-6, None), where=np.isfinite(x), out=out)
    return 10 * out


def _is_bimodal(values: np.ndarray, thr: float, min_frac: float = 0.05) -> bool:
    """Both Otsu classes hold >= min_frac of the tile and a real valley splits them."""
    lo, hi = values[values <= thr], values[values > thr]
    if min(lo.size, hi.size) < min_frac * values.size:
        return False
    hist, _ = np.histogram(values, bins=64)
    i = np.clip(
        np.searchsorted(np.linspace(values.min(), values.max(), 65), thr) - 1,
        0, 63,
    )
    peak_lo, peak_hi = hist[: i + 1].max(), hist[i + 1 :].max()
    if peak_lo == 0 or peak_hi == 0:
        return False
    i_lo = int(np.argmax(hist[: i + 1]))
    i_hi = i + 1 + int(np.argmax(hist[i + 1 :]))
    valley = hist[i_lo : i_hi + 1].min()
    return valley < 0.5 * min(peak_lo, peak_hi)


def adaptive_threshold(
    post_db: np.ndarray, tile_px: int = TILE_PX, fallback: float = WATER_DB
) -> tuple[float, str]:
    """Per-scene water threshold from Otsu on bimodal `tile_px` tiles.

    Returns (threshold_db, "adaptive" | "fallback"). Tiles whose histogram
    is not bimodal (uniform land, all water, all nodata) are skipped; the
    scene threshold is the median of the bimodal-tile Otsu values, clamped
    to be no brighter than `fallback` (BETA data may sit above truth).
    """
    vals = post_db[np.isfinite(post_db)]
    if vals.size == 0:
        return fallback, "fallback"
    thresholds = []
    h, w = post_db.shape
    for y in range(0, h, tile_px):
        for x in range(0, w, tile_px):
            tile = post_db[y : y + tile_px, x : x + tile_px]
            t = tile[np.isfinite(tile)]
            if t.size < 100 or t.min() >= 0 or t.max() <= -35:
                continue
            try:
                thr = float(threshold_otsu(t))
            except ValueError:
                continue
            if _is_bimodal(t, thr):
                thresholds.append(thr)
    if not thresholds:
        return fallback, "fallback"
    return min(float(np.median(thresholds)), fallback), "adaptive"


def min_mapping_unit(mask: np.ndarray, min_px: int) -> np.ndarray:
    """Drop connected components smaller than `min_px` pixels."""
    if min_px <= 1 or not mask.any():
        return mask.astype(bool)
    labels, _ = label(mask)
    sizes = np.bincount(labels.ravel())
    keep_ids = np.where(sizes >= min_px)[0]
    return np.isin(labels, keep_ids) & (labels > 0)


def compute_flood(
    pre_db: np.ndarray,
    post_db: np.ndarray,
    threshold_db: float,
    watermask: np.ndarray | None = None,
    hand: np.ndarray | None = None,
    drop_db: float = FLOOD_DROP_DB,
    hand_flood_m: float = HAND_FLOOD_M,
) -> tuple[np.ndarray, np.ndarray]:
    """Core rule: darkening > 3 dB into water-dark, not prior/permanent water, HAND gate."""
    water_before = pre_db < threshold_db
    water_now = post_db < threshold_db
    flood = (post_db - pre_db < drop_db) & water_now & ~water_before
    if watermask is not None:
        flood &= ~watermask.astype(bool)
        water_now &= ~watermask.astype(bool)
    if hand is not None:
        flood &= np.isfinite(hand) & (hand < hand_flood_m)
    return flood & np.isfinite(post_db) & np.isfinite(pre_db), water_now


def compute_bright(
    pre_db: np.ndarray,
    post_db: np.ndarray,
    threshold_db: float,
    watermask: np.ndarray | None = None,
    hand: np.ndarray | None = None,
    rise_db: float = BRIGHT_RISE_DB,
    hand_bright_m: float = HAND_BRIGHT_M,
) -> np.ndarray:
    """Bright-flood candidates: +3 dB brightening in the near-flat (HAND < 5 m)."""
    water_before = pre_db < threshold_db
    bright = (post_db - pre_db > rise_db) & ~water_before
    if watermask is not None:
        bright &= ~watermask.astype(bool)
    if hand is not None:
        bright &= np.isfinite(hand) & (hand < hand_bright_m)
    return bright & np.isfinite(post_db) & np.isfinite(pre_db)


def _open_match(path: Path | None, ref) -> np.ndarray | None:
    if path is None:
        return None
    da = rxr.open_rasterio(path).squeeze().rio.reproject_match(ref, resampling=0)
    return da.values


def _write_mask(ref, mask: np.ndarray, path: Path) -> None:
    """Write a uint8 mask COG with the reference grid (no nodata tag).

    COGs keep their native/4326 CRS (doc v4); only pre-rendered PNGs for the
    app's MapLibre image sources are reprojected to EPSG:3857 (live/render.py).
    """
    import rasterio

    with rasterio.open(
        path, "w", driver="COG",
        height=mask.shape[0], width=mask.shape[1], count=1, dtype="uint8",
        crs=ref.rio.crs, transform=ref.rio.transform(),
    ) as dst:
        dst.write(mask.astype("uint8"), 1)


def flood_mask(
    pre_path: Path,
    post_path: Path,
    out_dir: Path,
    watermask_path: Path | None = None,
    hand_path: Path | None = None,
    mmu_ha: float = MMU_HA,
    fallback_db: float = WATER_DB,
    drop_db: float = FLOOD_DROP_DB,
    bright: bool = True,
) -> dict:
    """Write flood/water/bright COGs; return stats incl. the threshold used."""
    pre = rxr.open_rasterio(pre_path).squeeze()
    post = rxr.open_rasterio(post_path).squeeze()
    if pre.shape != post.shape:
        post = post.rio.reproject_match(pre)

    valid = np.isfinite(pre.values) & np.isfinite(post.values)
    pre_db = median_filter(np.where(np.isfinite(pre.values), to_db(pre.values), _FILL_DB), 3)
    post_db = median_filter(np.where(np.isfinite(post.values), to_db(post.values), _FILL_DB), 3)
    pre_db = np.where(valid, pre_db, np.nan)
    post_db = np.where(valid, post_db, np.nan)

    thr, method = adaptive_threshold(post_db, fallback=fallback_db)
    watermask = _open_match(watermask_path, pre)
    hand = _open_match(hand_path, pre)

    flood, water_now = compute_flood(
        pre_db, post_db, thr, watermask, hand, drop_db=drop_db
    )

    res_x, res_y = pre.rio.resolution()
    px_ha = abs(res_x * res_y) / 1e4
    flood = min_mapping_unit(flood, int(np.ceil(mmu_ha / px_ha)))

    out_dir.mkdir(parents=True, exist_ok=True)
    _write_mask(pre, flood, out_dir / "flood_nisar.tif")
    _write_mask(pre, water_now, out_dir / "water_nisar.tif")

    stats = {
        "threshold_db": thr,
        "threshold_method": method,
        "flood_km2": float(flood.sum() * abs(res_x * res_y) / 1e6),
        "water_km2": float(water_now.sum() * abs(res_x * res_y) / 1e6),
    }
    if bright:
        bright_mask = min_mapping_unit(
            compute_bright(pre_db, post_db, thr, watermask, hand),
            int(np.ceil(mmu_ha / px_ha)),
        )
        _write_mask(pre, bright_mask, out_dir / "flood_bright_nisar.tif")
        stats["flood_bright_km2"] = float(
            bright_mask.sum() * abs(res_x * res_y) / 1e6
        )
    return stats
