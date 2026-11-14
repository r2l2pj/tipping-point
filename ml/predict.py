"""Inference: static COG + per-frame dynamic stack → hazard COG series.

Reads the 90 m static stack and one native-resolution dynamic stack per frame
(upsampled nearest to the static grid), runs ``predict_proba`` per frame, masks
slope < 5° and water, and writes (spec §3):

  * ``hazard_slide_series.tif`` — uint8 multiband COG, one band per frame,
    probability ×100, nodata 255
  * ``hazard_classes.tif`` — uint8 classes 0-3 per the SCHEMA `tpi` legend

    uv run python -m ml.predict --static static.tif --dynamic "dyn_*.tif" \
        --out data/rs-2024-05/cog/hazard_slide_series.tif
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from rasterio.warp import Resampling, reproject

from . import config as cfg
from .dataset import prepare_X

log = logging.getLogger(__name__)


def _band_names(src: rasterio.DatasetReader) -> list[str]:
    return [d or f"band_{i+1}" for i, d in enumerate(src.descriptions)]



def _resample_to(src_arr, src_transform, src_crs, dst_shape, dst_transform, dst_crs):
    dst = np.empty(dst_shape, dtype=np.float64)
    reproject(
        source=src_arr.astype(np.float64),
        destination=dst,
        src_transform=src_transform,
        src_crs=src_crs,
        dst_transform=dst_transform,
        dst_crs=dst_crs,
        resampling=Resampling.nearest,
        src_nodata=np.nan,
        dst_nodata=np.nan,
    )
    return dst



def load_feature_bands(
    static_path: str | Path, dynamic_path: str | Path | None
) -> tuple[dict[str, np.ndarray], dict]:
    """Named 2-D feature arrays on the static grid (+ raster profile).

    Dynamic bands are resampled nearest-neighbour from their native 0.1°
    grid to the 90 m static grid.
    """
    with rasterio.open(static_path) as ssrc:
        profile = dict(ssrc.profile)
        shape = (ssrc.height, ssrc.width)
        feats = {
            n: ssrc.read(i + 1).astype(np.float64)
            for i, n in enumerate(_band_names(ssrc))
        }
        if dynamic_path is None:
            return feats, profile
        with rasterio.open(dynamic_path) as dsrc:
            for i, n in enumerate(_band_names(dsrc)):
                feats[n] = _resample_to(
                    dsrc.read(i + 1), dsrc.transform, dsrc.crs,
                    shape, ssrc.transform, ssrc.crs,
                )
    return feats, profile


def predict_frame(model, feats: dict[str, np.ndarray], feature_names: list[str]) -> np.ndarray:
    """Probability raster (float32, NaN where masked/unpredictable)."""
    feature_names = [c for c in feature_names if c in feats]
    shape = next(iter(feats.values())).shape
    X = pd.DataFrame({c: np.asarray(feats[c], dtype=float).ravel() for c in feature_names})
    valid = np.isfinite(X.to_numpy(dtype=float, na_value=np.nan)).all(axis=1)
    prob = np.full(X.shape[0], np.nan)
    if valid.any():
        prob[valid] = model.predict_proba(prepare_X(X.iloc[valid], feature_names))[:, 1]
    return prob.reshape(shape).astype(np.float32)


def apply_masks(prob: np.ndarray, feats: dict[str, np.ndarray]) -> np.ndarray:
    """Mask slope < 5° and open water (MODIS LC_Type1 == 0)."""
    out = prob.copy()
    slope = feats.get("slope_mean", feats.get("slope_max"))
    if slope is not None:
        out[slope < cfg.MIN_SLOPE_DEG] = np.nan
    if "lc" in feats:
        out[feats["lc"] == cfg.LC_WATER] = np.nan
    return out


def to_uint8(prob: np.ndarray) -> np.ndarray:
    out = np.full(prob.shape, cfg.RASTER_NODATA, dtype=np.uint8)
    ok = np.isfinite(prob)
    out[ok] = np.round(np.clip(prob[ok], 0, 1) * cfg.HAZARD_SCALE).astype(np.uint8)
    return out


def classify(prob: np.ndarray, breaks=cfg.CLASS_BREAKS) -> np.ndarray:
    """0 Baixo / 1 Moderado / 2 Alto / 3 Critico; nodata where prob is NaN."""
    cls = np.digitize(np.nan_to_num(prob, nan=-1.0), list(breaks)).astype(np.uint8)
    cls[~np.isfinite(prob)] = cfg.RASTER_NODATA
    return cls


def _write_cog(path: str | Path, arr: np.ndarray, profile: dict) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    prof = dict(profile)
    prof.update(
        driver="GTiff", count=arr.shape[0], dtype="uint8",
        nodata=cfg.RASTER_NODATA, compress="DEFLATE",
        tiled=True, blockxsize=256, blockysize=256, interleave="band",
    )
    prof.pop("photometric", None)
    with rasterio.open(path, "w", **prof) as dst:
        dst.write(arr)
    log.info("wrote %s (%d band(s))", path, arr.shape[0])
    return path


def predict_series(
    model,
    static_path: str | Path,
    dynamic_paths: list[str | Path],
    out_path: str | Path,
    feature_names: list[str] | None = None,
) -> tuple[Path, Path]:
    """All frames → (hazard series COG, per-frame-max class COG)."""
    feature_names = feature_names or cfg.FEATURES
    if not dynamic_paths:
        raise ValueError("predict_series needs at least one dynamic frame")
    frame_probs = []
    for dyn in dynamic_paths:
        feats, profile = load_feature_bands(static_path, dyn)
        frame_probs.append(apply_masks(predict_frame(model, feats, feature_names), feats))
    prob_series = np.stack(frame_probs)                      # (T, H, W)
    hazard = to_uint8(prob_series)
    finite = np.isfinite(prob_series)
    peak = np.where(finite, prob_series, -np.inf).max(axis=0)
    peak[~finite.any(axis=0)] = np.nan
    classes = classify(peak)[None]                           # worst frame per pixel
    return _write_cog(out_path, hazard, profile), _write_cog(
        Path(out_path).with_name("hazard_classes.tif"), classes, profile
    )


def load_model(path: str | Path):
    from xgboost import XGBClassifier

    model = XGBClassifier()
    model.load_model(str(path))
    meta_path = Path(path).with_suffix(".meta.json")
    names = None
    if meta_path.exists():
        import json

        names = json.loads(meta_path.read_text())["feature_names"]
    return model, names


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", default=str(cfg.MODEL_DIR / "slide_xgb.json"))
    ap.add_argument("--static", required=True, help="static-stack COG (90 m)")
    ap.add_argument("--dynamic", nargs="+", required=True,
                    help="one dynamic-stack raster per frame (native 0.1°)")
    ap.add_argument("--out", required=True, help="output hazard series COG")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    model, names = load_model(args.model)
    predict_series(model, args.static, args.dynamic, args.out, feature_names=names)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
