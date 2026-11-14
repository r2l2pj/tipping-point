"""Synthetic-input coverage for predict / lead_time / labels / gee helpers."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ml import config as cfg


def test_calm_days_skip_heavy_rain_windows():
    from ml.gee_features import calm_days

    days = calm_days(50)
    assert len(days) == 50
    assert days.is_monotonic_increasing
    for lo, hi in cfg.EXCLUDED_RAIN_WINDOWS:
        assert not ((days >= lo) & (days <= hi)).any()


def test_labels_dedupe_footprint_and_peak24h():
    import geopandas as gpd
    import xarray as xr
    from shapely.geometry import Point

    from ml.labels import (
        assign_peak24h_times,
        dedupe_to_grid,
        mapped_footprint,
        require_files,
    )

    # duplicate coordinates are always the same 90 m cell → exactly 3 cells
    pts = gpd.GeoDataFrame(
        {"source": ["t"] * 5},
        geometry=[Point(-51.9, -29.9), Point(-51.9, -29.9),
                  Point(-51.5, -29.5), Point(-51.5, -29.5),
                  Point(-51.0, -30.0)],
        crs="EPSG:4326",
    )
    deduped = dedupe_to_grid(pts, cap=1000)
    assert len(deduped) == 3

    # jittered points: output must still hold at most one point per cell
    rng = np.random.default_rng(0)
    jit = gpd.GeoDataFrame(
        {"source": ["t"] * 40},
        geometry=[Point(-52.0 + rng.uniform(0, 0.01), -30.0 + rng.uniform(0, 0.01))
                  for _ in range(40)],
        crs="EPSG:4326",
    )
    dj = dedupe_to_grid(jit, cap=1000).to_crs(jit.estimate_utm_crs())
    cells = {(x, y) for x, y in zip(np.floor(dj.geometry.x / 90), np.floor(dj.geometry.y / 90))}
    assert len(cells) == len(dj)
    hull = mapped_footprint(deduped)
    assert hull.geom_type in ("Polygon", "LineString")
    assert hull.contains(deduped.geometry.union_all().centroid)

    # synthetic 2-day IMERG cube, 30-min steps; rain peaks in the second 24 h
    times = pd.date_range("2024-04-27", periods=96, freq="30min", tz="UTC")
    lats = np.linspace(-30.3, -29.7, 4)
    lons = np.linspace(-52.3, -51.7, 4)
    rate = np.zeros((len(times), len(lats), len(lons)))
    rate[48:, 1, 1] = 5.0  # all rain in second half, one pixel
    rain = xr.DataArray(rate, dims=("time", "lat", "lon"),
                        coords={"time": times, "lat": lats, "lon": lons})
    t = assign_peak24h_times(
        gpd.GeoDataFrame(geometry=[Point(lons[1], lats[1])], crs="EPSG:4326"), rain
    )
    assert t.iloc[0] == times[-1]

    with pytest.raises(FileNotFoundError, match="Zenodo"):
        require_files(["/nonexistent/ufrgs.shp"])


class _RainModel:
    """Stub predictor: hazard = clip(rain_24h / 200)."""

    def predict_proba(self, X):
        p = np.clip(X["rain_24h"].to_numpy(dtype=float) / 200.0, 0, 1)
        return np.stack([1 - p, p], axis=1)


def _write_raster(path, bands: dict, transform, shape, crs="EPSG:4326"):
    import rasterio

    h, w = shape
    with rasterio.open(
        path, "w", driver="GTiff", height=h, width=w, count=len(bands),
        dtype="float32", crs=crs, transform=transform,
    ) as dst:
        for i, (name, arr) in enumerate(bands.items(), start=1):
            dst.write(arr.astype(np.float32), i)
            dst.set_band_description(i, name)


def test_predict_series_and_lead_time(tmp_path):
    import rasterio
    from rasterio.transform import from_origin

    from ml.lead_time import (
        cluster_lead_times,
        first_crossing_frame,
        read_hazard_series,
        summarize,
    )
    from ml.predict import predict_series

    shape = (8, 8)
    transform = from_origin(-52.4, -29.6, 0.01, 0.01)
    slope = np.full(shape, 20.0, dtype=np.float32)
    slope[0] = 2.0  # flat row → masked
    lc = np.full(shape, 10.0, dtype=np.float32)
    lc[1] = 0.0  # water row → masked
    static = tmp_path / "static.tif"
    _write_raster(static, {"slope_mean": slope, "lc": lc}, transform, shape)

    dyn_t = from_origin(-52.4, -29.6, 0.01, 0.01)
    rains = [np.full(shape, r, dtype=np.float32) for r in (50.0, 220.0, 120.0)]
    dyns = []
    for i, r in enumerate(rains):
        p = tmp_path / f"dyn_{i}.tif"
        _write_raster(p, {"rain_24h": r}, dyn_t, shape)
        dyns.append(p)

    out = tmp_path / "cog" / "hazard_slide_series.tif"
    series_path, classes_path = predict_series(
        _RainModel(), static, dyns, out,
        feature_names=["slope_mean", "rain_24h"],
    )
    assert classes_path.exists()
    with rasterio.open(series_path) as src:
        arr = src.read()
        assert arr.shape == (3, *shape)
        assert arr.dtype == np.uint8
        assert (arr[:, 0] == cfg.RASTER_NODATA).all()  # flat row masked
        assert (arr[:, 1] == cfg.RASTER_NODATA).all()  # water row masked
        assert arr[0, 2, 0] == 25 and arr[1, 2, 0] == 100  # 50/200, clip(220/200)

    prob = read_hazard_series(series_path)
    first = first_crossing_frame(prob, threshold=0.5)
    assert first[2, 0] == 1  # crosses only in frame 1 (rain 220 → prob 1.0)
    times = pd.DatetimeIndex(pd.to_datetime(cfg.FRAME_TIMES_UTC[:3]))
    clusters = cluster_lead_times(prob, times, 0.5, pd.Timestamp("2024-05-01", tz="UTC"),
                                min_cluster_px=1)
    assert len(clusters) == 1
    assert clusters["lead_hours"].iloc[0] > 0
    summary = summarize(clusters)
    assert summary["warned_before_pct"] == pytest.approx(100.0)
