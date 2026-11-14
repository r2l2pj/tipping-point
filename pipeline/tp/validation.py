"""Validation against OPERA DSWx (30 m water product, JPL).

WTR layer values: 0 no water, 1 open water, 2 partial water, 253 cloud/shadow,
255 no data. Metrics vs the NISAR water mask: IoU, F1, plus the cloud-cover
percentage (the "why radar" argument). DSWx-HLS is optical; DSWx-S1 (used for
wa_2025, a winter atmospheric river) is Sentinel-1 based and sees through
cloud.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import rioxarray as rxr

from tp.metrics import merge_metrics

WATER_VALS = (1, 2)
CLASS_VALS = (0, 1, 2)
INVALID_VALS = (253, 255)
CLOUD_VAL = 253
NODATA_VAL = 255

# OPERA collection per config.yaml case; default DSWx-HLS, DSWx-S1 for wa
# (optical is mostly cloud during the December atmospheric rivers).
DEFAULT_PRODUCT = "OPERA_L3_DSWX-HLS_V1"
CASE_PRODUCT = {"wa_2025": "OPERA_L3_DSWX-S1_V1"}

CLOUDED_REASON = "optical fully clouded in the event window — cloud_pct is the 'why radar' number"


def case_key(event_id: str) -> str:
    """'wa-2025-12' -> 'wa_2025': the config.yaml case key for an event id."""
    return "_".join(event_id.split("-")[:2])


def opera_product(case_id: str) -> str:
    """OPERA DSWx collection short name for a config.yaml case."""
    return CASE_PRODUCT.get(case_id, DEFAULT_PRODUCT)


def search_download_opera(
    bbox: tuple,
    temporal: tuple,
    dest: Path,
    short_name: str = DEFAULT_PRODUCT,
) -> tuple[list, list]:
    """earthaccess search+download for an OPERA DSWx collection.

    Returns (downloaded paths, granule native-ids) so callers can record the
    granules used under metrics.json "collections".
    """
    import earthaccess

    earthaccess.login()
    granules = earthaccess.search_data(
        short_name=short_name, bounding_box=bbox, temporal=temporal
    )
    paths = earthaccess.download(granules, str(dest))
    ids = [g["meta"]["native-id"] for g in granules]
    return list(paths or []), ids


def mosaic_tiles(paths: list, ref) -> np.ndarray:
    """Merge WTR tiles onto ref's grid (nearest resample).

    Per pixel, in path order: the first observed class (0, 1, 2) wins — over
    later tiles and over cloud; a 253 (cloud/shadow) fills a pixel no tile
    observed; anything never covered stays 255. cloud_pct on the mosaic
    therefore counts the pixels optical never saw.
    """
    out = np.full(ref.shape, NODATA_VAL, dtype=np.float32)
    for path in paths:
        wtr = (
            rxr.open_rasterio(path)
            .squeeze()
            .rio.reproject_match(ref, resampling=0)
            .values.astype(np.float32)
        )
        observed = np.isin(wtr, CLASS_VALS)
        fill = observed & ~np.isin(out, CLASS_VALS)
        out[fill] = wtr[fill]
        out[(wtr == CLOUD_VAL) & (out == NODATA_VAL)] = CLOUD_VAL
    return out


def cloud_pct(wtr) -> float:
    """% of px == 253 (cloud/shadow) over px != 255/nan (nodata)."""
    wtr = np.asarray(wtr, dtype=np.float32)
    nodata = np.isnan(wtr) | (wtr == NODATA_VAL)
    return float(100 * (wtr == CLOUD_VAL).sum() / max((~nodata).sum(), 1))


def compare_wtr(water_nisar, wtr: np.ndarray) -> dict:
    """iou/f1/cloud_pct of a WTR array already on the NISAR grid.

    water_nisar is the pipeline flood raster (truthy = water); reference
    water is WTR 1|2; WTR 253/255/nan pixels are excluded.
    """
    nisar = np.nan_to_num(
        np.asarray(getattr(water_nisar, "values", water_nisar), dtype=np.float32),
        nan=0.0,
    )
    wtr = np.asarray(wtr, dtype=np.float32)
    valid = ~np.isnan(wtr) & ~np.isin(wtr, INVALID_VALS)
    ref = np.isin(wtr, WATER_VALS) & valid
    pred = nisar.astype(bool) & valid

    tp = int((ref & pred).sum())
    fp = int((~ref & pred).sum())
    fn = int((ref & ~pred).sum())
    return {
        "iou": tp / (tp + fp + fn) if (tp + fp + fn) else 0.0,
        "f1": 2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) else 0.0,
        "cloud_pct": cloud_pct(wtr),
    }


def validate_flood(water_nisar_path: Path, opera_wtr) -> dict:
    """IoU/F1/cloud_pct vs the NISAR water mask.

    ``opera_wtr``: a WTR raster path, a list of paths (mosaicked onto the
    NISAR grid), or an array already on that grid.
    """
    nisar = rxr.open_rasterio(water_nisar_path).squeeze()
    if isinstance(opera_wtr, np.ndarray):
        wtr = opera_wtr
    else:
        paths = [opera_wtr] if isinstance(opera_wtr, (str, Path)) else list(opera_wtr)
        wtr = mosaic_tiles(paths, nisar)
    return compare_wtr(nisar, wtr)


def hls_fmask_cloud_pct(fmask_path: Path) -> float:
    """Cloud fraction (%) of an HLS Fmask raster.

    Assumes the HLS Fmask bit layout: bit 1 (value 2) is the cloud bit —
    cirrus (bit 0) and cloud shadow are not counted. Fraction is over px
    != 255 (nodata), matching the WTR cloud_pct definition.
    """
    f = np.asarray(
        rxr.open_rasterio(fmask_path).squeeze().values, dtype=np.float64
    )
    valid = ~np.isnan(f) & (f != NODATA_VAL)
    bits = np.nan_to_num(f, nan=0.0).astype(np.int64)
    cloud = ((bits & 2) != 0) & valid
    return float(100 * cloud.sum() / max(valid.sum(), 1))


def cloud_only_merge(event_id: str, pct: float, source: str, path=None) -> dict:
    """Merge cloud_pct + iou/f1 = null with reasons (the RS case: optical
    fully clouded — the "why radar" number is cloud_pct itself)."""
    return merge_metrics(
        event_id,
        flat={"cloud_pct": pct, "iou": None, "f1": None},
        reasons={"iou": CLOUDED_REASON, "f1": CLOUDED_REASON},
        collections={"cloud_pct_source": source},
        path=path,
    )


def write_metrics(event_id: str, flat: dict, **kwargs) -> dict:
    """Merge validation metrics into data/<event>/metrics.json.

    Routes through tp.metrics.merge_metrics — existing keys survive a
    validation merge (fixes the old file-overwrite bug).
    """
    return merge_metrics(event_id, flat=flat, **kwargs)
