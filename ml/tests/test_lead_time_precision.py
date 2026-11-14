"""Boundary checks for independent damage-anchor precision."""
import numpy as np
import pandas as pd
from rasterio.transform import from_origin

from ml.lead_time import replay_metrics


def test_authoritative_precision_and_date_alias():
    cube = np.array([[[0.7, 0.7]], [[0.7, 0.7]]])
    times = pd.date_range("2024-05-01", periods=2, freq="3h", tz="UTC")
    points = pd.DataFrame({"lon": [0.5, 1.5], "lat": [0.5, 0.5],
                           "t_utc": ["2024-05-01T15:00Z", None],
                           "t_date": [None, "2024-05-01"], "t_precision": ["date", "date"]})
    out, _ = replay_metrics(cube, times, points, from_origin(0, 1, 1, 1), "EPSG:4326", {},
                            local_timezone="America/Sao_Paulo", pixel_area_km2=np.array([[1, 3]]))
    assert out["anchor_precision"] == "date"
    assert out["lead_time_median_h"] == out["lead_time_mean_h"] == 15
    assert out["lead_time_iqr_h"] == [15, 15]
    assert out["critical_area_km2"] == 4
    assert out["critical_area_with_damage_pct"] == 100


def test_zero_lead_not_warned_before_and_empty_points():
    cube = np.array([[[0.6]], [[0.7]]])
    times = pd.date_range("2024-05-01", periods=2, freq="3h", tz="UTC")
    points = pd.DataFrame({"lon": [0.5], "lat": [0.5], "t_utc": ["2024-05-01T03:00Z"]})
    out, _ = replay_metrics(cube, times, points, from_origin(0, 1, 1, 1), "EPSG:4326", {}, local_timezone="UTC")
    assert out["lead_time_median_h"] == out["lead_time_mean_h"] == 0
    assert out["lead_time_iqr_h"] == [0, 0]
    assert out["warned_before_pct"] == out["warned_24h_pct"] == out["never_critical_pct"] == 0
    assert out["lead_time_hist"] == {"bin_h": 6, "from_h": 0, "counts": [1]}
    out, reasons = replay_metrics(cube, times, points.iloc[:0], from_origin(0, 1, 1, 1), "EPSG:4326", {},
                                  local_timezone="UTC", pixel_area_km2=2)
    assert out["n_points"] == 0
    assert out["lead_time_median_h"] is None
    assert reasons["lead_time_median_h"]
    assert out["critical_area_km2"] == 2
    assert out["critical_area_with_damage_pct"] == 0
