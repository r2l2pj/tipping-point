"""Landslide-scar candidates from NISAR GUNW coherence change.

Candidate: coherence drop > 0.3 between a no-event pair and an event-crossing
pair, on slopes > 15deg, where reference coherence > 0.4 (dense vegetation and
water are already incoherent — discard). Patches < 10 px are removed.
These are CANDIDATES, not field confirmation (L-band decorrelates for other
reasons too: vegetation, rain).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import rioxarray as rxr
from scipy.ndimage import label

COH_DROP = 0.3
MIN_SLOPE_DEG = 15.0
MIN_REF_COH = 0.4
MIN_PATCH_PX = 10


def landslide_candidates(
    coh_ref_path: Path, coh_evt_path: Path, slope_path: Path, out_path: Path
) -> dict:
    coh_ref = rxr.open_rasterio(coh_ref_path).squeeze()
    coh_evt = rxr.open_rasterio(coh_evt_path).squeeze()
    slope = rxr.open_rasterio(slope_path).squeeze().rio.reproject_match(coh_evt)

    drop = coh_ref.values - coh_evt.values
    cand = (drop > COH_DROP) & (slope.values > MIN_SLOPE_DEG) & (coh_ref.values > MIN_REF_COH)

    labels, _ = label(cand)
    sizes = np.bincount(labels.ravel())
    keep = np.isin(labels, np.where(sizes >= MIN_PATCH_PX)[0]) & (labels > 0)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    coh_evt.copy(data=keep.astype("uint8")).rio.to_raster(out_path, driver="COG")
    return {"candidate_patches": int(keep.sum() > 0 and labels[keep].max() or 0)}
