"""Catalog hit-rate: landslide inventory points vs the event-day TPI classes.

Points from a landslide catalog/inventory CSV (GLC for wa_2025, the
UFRGS/Cemaden inventory for rs_2024 — validation references, never index
inputs) are sampled on the event-day classified TPI COG (nearest pixel;
classes 0..3 = Baixo/Moderado/Alto/Crítico). landslide_hit_pct is
100 * points falling on Alto|Crítico / points on valid pixels; points on
nodata or outside the raster are skipped and their count reported.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from rasterio.warp import transform as warp_transform

LAT_COLS = ("lat", "latitude", "y")
LON_COLS = ("lon", "lng", "long", "longitude", "x")
NODATA_CLASS = 255           # uint8 class grids use 255 (data/SCHEMA.md)


def _col(cols: dict, candidates: tuple[str, ...], kind: str) -> str:
    for name in candidates:
        if name in cols:
            return cols[name]
    raise ValueError(f"no {kind} column; tried {candidates}, got {sorted(cols)}")


def read_points(points_csv: Path) -> pd.DataFrame:
    """Read a catalog CSV; normalize to 'lat'/'lon' (+ optional 'source')."""
    df = pd.read_csv(points_csv)
    cols = {str(c).strip().lower(): c for c in df.columns}
    df = df.rename(columns={
        _col(cols, LAT_COLS, "latitude"): "lat",
        _col(cols, LON_COLS, "longitude"): "lon",
    })
    if "source" in cols:
        df = df.rename(columns={cols["source"]: "source"})
    return df


def landslide_hit(
    points_csv: Path,
    tpi_class_cog: Path,
    valid_classes: tuple[int, ...] = (2, 3),
) -> dict:
    """Sample catalog points on the class COG; return hit stats + collections."""
    df = read_points(points_csv)
    with rasterio.open(tpi_class_cog) as src:
        arr = src.read(1, masked=True)
        xs, ys = warp_transform(
            "EPSG:4326", src.crs, df["lon"].tolist(), df["lat"].tolist())
        nodata = {NODATA_CLASS}
        if src.nodata is not None:
            nodata.add(src.nodata)
        valid = hits = skipped = 0
        for x, y in zip(xs, ys):
            if not (np.isfinite(x) and np.isfinite(y)):
                skipped += 1
                continue
            r, c = src.index(x, y)
            if not (0 <= r < src.height and 0 <= c < src.width):
                skipped += 1
                continue
            v = arr[r, c]
            if np.ma.is_masked(v) or not np.isfinite(v) or int(v) in nodata:
                skipped += 1
                continue
            valid += 1
            hits += int(v) in valid_classes

    if "source" in df.columns:
        sources = sorted({s for s in df["source"].dropna().astype(str).str.strip() if s})
    else:
        sources = [Path(points_csv).stem]
    return {
        "points_total": len(df),
        "points_valid": int(valid),
        "points_hit": int(hits),
        "points_skipped": int(skipped),
        "hit_pct": 100.0 * hits / valid if valid else 0.0,
        "collections": {
            "landslide_catalog": "+".join(sources) or Path(points_csv).stem,
            "tpi": Path(tpi_class_cog).stem,
        },
    }
