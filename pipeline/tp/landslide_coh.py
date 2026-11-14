"""Landslide-scar candidates from NISAR GUNW 20 m coherence change.

Candidate: coherence drop > 0.3 between a no-event pair and an event-crossing
pair (wrappedInterferogram/HH/coherenceMagnitude, 20 m), on slopes > 15deg,
where reference coherence > 0.4 (dense vegetation and water are already
incoherent — discard). Confounders are masked out: flood pixels, snow cover,
and ground above an elevation cap (December storms decorrelate via snow/soil
moisture at altitude). Patches < 10 px (0.4 ha at 20 m) are removed.

If GCOV HVHV pre/post rasters are given, pixels also losing > 2 dB of HV
backscatter are promoted to a high-confidence class (a scar strips
vegetation, so HV drops).

Output classes: 0 none, 1 candidate, 2 high-confidence (HV vote).
These are CANDIDATES, not field confirmation (L-band decorrelates for other
reasons too: vegetation, rain, snow).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import rioxarray as rxr
from scipy.ndimage import label

from tp.config import threshold

COH_DROP = float(threshold("coherence_drop", 0.3))
MIN_SLOPE_DEG = float(threshold("slope_range_deg", [15, 30])[0])
MIN_REF_COH = float(threshold("coherence_ref_min", 0.4))
MIN_PATCH_PX = int(threshold("min_blob_pixels", 10))
MAX_ELEV_M = 600.0      # crude snow-line cap; tune visually per event
HV_DROP_DB = 2.0        # high-confidence vote on HV backscatter loss


def _to_db(x: np.ndarray) -> np.ndarray:
    return 10 * np.log10(np.clip(x, 1e-6, None))


def _open_match(path: Path | None, ref) -> np.ndarray | None:
    if path is None:
        return None
    return rxr.open_rasterio(path).squeeze().rio.reproject_match(ref).values


def compute_candidates(
    coh_ref: np.ndarray,
    coh_evt: np.ndarray,
    slope: np.ndarray,
    flood: np.ndarray | None = None,
    snow: np.ndarray | None = None,
    elev: np.ndarray | None = None,
    hv_drop_db: np.ndarray | None = None,
    min_drop: float = COH_DROP,
    min_slope_deg: float = MIN_SLOPE_DEG,
    min_ref_coh: float = MIN_REF_COH,
    max_elev_m: float | None = MAX_ELEV_M,
) -> np.ndarray:
    """Class raster: 0 none, 1 candidate, 2 high-confidence (HV drop > 2 dB)."""
    drop = coh_ref - coh_evt
    cand = (
        np.isfinite(drop)
        & np.isfinite(slope)
        & (drop > min_drop)
        & (slope > min_slope_deg)
        & (coh_ref > min_ref_coh)
    )
    if flood is not None:
        cand &= ~flood.astype(bool)
    if snow is not None:
        cand &= ~snow.astype(bool)
    if elev is not None and max_elev_m is not None:
        cand &= ~np.isfinite(elev) | (elev < max_elev_m)
    out = cand.astype("uint8")
    if hv_drop_db is not None:
        out[cand & np.isfinite(hv_drop_db) & (hv_drop_db > HV_DROP_DB)] = 2
    return out


def _drop_small(classes: np.ndarray, min_px: int) -> np.ndarray:
    """Remove connected components < min_px from a class raster."""
    if min_px <= 1 or not classes.any():
        return classes
    labels, _ = label(classes > 0)
    sizes = np.bincount(labels.ravel())
    drop_ids = np.where((sizes < min_px) & (sizes >= 0))[0]
    out = classes.copy()
    out[np.isin(labels, drop_ids) & (labels > 0)] = 0
    return out


def landslide_candidates(
    coh_ref_path: Path,
    coh_evt_path: Path,
    slope_path: Path,
    out_path: Path,
    flood_path: Path | None = None,
    snow_path: Path | None = None,
    dem_path: Path | None = None,
    hv_pre_path: Path | None = None,
    hv_post_path: Path | None = None,
    min_patch_px: int = MIN_PATCH_PX,
    max_elev_m: float | None = MAX_ELEV_M,
    min_drop: float = COH_DROP,
    min_slope_deg: float = MIN_SLOPE_DEG,
    min_ref_coh: float = MIN_REF_COH,
) -> dict:
    """Write the 3-class candidate COG; return patch counts."""
    coh_ref = rxr.open_rasterio(coh_ref_path).squeeze()
    coh_evt = rxr.open_rasterio(coh_evt_path).squeeze()
    if coh_ref.shape != coh_evt.shape:
        coh_ref = coh_ref.rio.reproject_match(coh_evt)
    slope = rxr.open_rasterio(slope_path).squeeze().rio.reproject_match(coh_evt)

    flood = _open_match(flood_path, coh_evt)
    snow = _open_match(snow_path, coh_evt)
    elev = _open_match(dem_path, coh_evt)
    hv_drop = None
    if hv_pre_path is not None and hv_post_path is not None:
        hv_pre = _open_match(hv_pre_path, coh_evt)
        hv_post = _open_match(hv_post_path, coh_evt)
        hv_drop = _to_db(hv_pre) - _to_db(hv_post)

    classes = _drop_small(
        compute_candidates(
            coh_ref.values, coh_evt.values, slope.values,
            flood=flood, snow=snow, elev=elev, hv_drop_db=hv_drop,
            min_drop=min_drop, min_slope_deg=min_slope_deg,
            min_ref_coh=min_ref_coh, max_elev_m=max_elev_m,
        ),
        min_patch_px,
    )

    _, n = label(classes > 0)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    import rasterio

    with rasterio.open(
        out_path, "w", driver="COG",
        height=classes.shape[0], width=classes.shape[1], count=1,
        dtype="uint8", crs=coh_evt.rio.crs,
        transform=coh_evt.rio.transform(),
    ) as dst:
        dst.write(classes, 1)
    return {
        "candidate_patches": int(n),
        "candidate_px": int((classes == 1).sum()),
        "high_confidence_px": int((classes == 2).sum()),
    }
