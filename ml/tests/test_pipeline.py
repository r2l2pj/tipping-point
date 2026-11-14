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


def test_lead_time_tpi_series_label_points(tmp_path):
    """Float TPI series + labeled points → per-point anticipation vs anchor."""
    import json

    import rasterio
    from rasterio.transform import from_origin

    from ml.lead_time import (
        frame_times_from_bands,
        is_float_series,
        main,
        point_lead_times,
        read_series,
        summarize_points,
    )

    shape = (4, 4)
    transform = from_origin(-52.0, -29.5, 0.01, 0.01)
    days = pd.date_range("2024-04-29", periods=4, freq="D", tz="UTC")
    tpi = np.zeros((4, *shape), dtype=np.float32)
    tpi[2:, 0, 0] = 0.7   # crosses ≥0.6 at frame 2 (May 1) → 48 h before anchor
    tpi[1:, 1, 1] = 0.9   # crosses at frame 1 (Apr 30) → 72 h
    tpi[3, 0, 1] = 0.8    # crosses only at frame 3 (May 2) → 24 h
    tpi[:, 2, 2] = 0.4    # never crosses → neg
    tpi[:, 3, 3] = np.nan  # nodata pixel → null

    path = tmp_path / "tpi_series.tif"
    with rasterio.open(
        path, "w", driver="GTiff", height=shape[0], width=shape[1],
        count=4, dtype="float32", crs="EPSG:4326", transform=transform,
        nodata=np.nan,
    ) as dst:
        dst.write(tpi)
        for i, d in enumerate(days, start=1):
            dst.set_band_description(i, d.strftime("%Y%m%d"))

    # dtype auto-detect: float → values are already 0-1 (no /100 scaling)
    assert is_float_series(path)
    prob = read_series(path)
    assert prob[1, 1, 1] == pytest.approx(0.9)
    assert np.isnan(prob[:, 3, 3]).all()
    assert list(frame_times_from_bands(path)) == list(days)

    def center(r, c):
        return (-52.0 + (c + 0.5) * 0.01, -29.5 - (r + 0.5) * 0.01)

    points = pd.DataFrame(
        [center(0, 0), center(1, 1), center(0, 1), center(2, 2), center(3, 3)],
        columns=["lon", "lat"],
    )
    anchor = pd.Timestamp("2024-05-03", tz="UTC")
    table = point_lead_times(prob, pd.DatetimeIndex(days), points,
                             transform, "EPSG:4326", 0.6, anchor)
    assert table["klass"].tolist() == ["pos", "pos", "pos", "neg", "null"]
    assert table["anticipation_h"].tolist()[:3] == [48.0, 72.0, 24.0]
    assert np.isnan(table["anticipation_h"].iloc[3:]).all()

    summary = summarize_points(table)
    assert summary["lead_time_mean_h"] == pytest.approx(48.0)
    assert summary["lead_time_median_h"] == pytest.approx(48.0)
    assert summary["warned_before_pct"] == pytest.approx(75.0)  # 3 pos / 4 evaluable
    assert summary["n_null"] == 1

    # end to end: labels CSV → metrics.json via merge (flat keys + detail)
    labels_csv = tmp_path / "labels.csv"
    points.assign(t_utc=pd.NaT, source="t").to_csv(labels_csv, index=False)
    metrics_path = tmp_path / "rs-2024-05" / "metrics.json"
    assert main(["--hazard", str(path), "--labels", str(labels_csv),
                 "--anchor", "2024-05-03T00:00:00Z",
                 "--event", "rs-2024-05",
                 "--metrics-out", str(metrics_path)]) == 0
    blob = json.loads(metrics_path.read_text())["cases"]["rs_2024"]
    assert blob["lead_time_mean_h"] == pytest.approx(48.0)
    assert blob["warned_before_pct"] == pytest.approx(75.0)
    assert blob["landslide_ml"]["lead_hours"]["mode"] == "label_points"
