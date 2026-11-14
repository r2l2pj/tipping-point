"""Hand-calculated 3-hour replay metrics, including late and never warnings."""
import json

import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.transform import from_origin
from tp.metrics import merge_metrics

from gee.lead_time import ASSUMPTIONS
from ml.lead_time import damage_time, main, replay_metrics


@pytest.fixture
def sample():
    times = pd.date_range("2024-05-01", periods=3, freq="3h", tz="UTC")
    cube = np.array([[[0.7, 0.6, 0.4, 0.2, np.nan, 0.7]],
                     [[0.7, 0.7, 0.5, 0.2, np.nan, 0.7]],
                     [[0.7, 0.7, 0.7, 0.2, np.nan, 0.7]]])
    points = pd.DataFrame({"lon": [0.5, 1.5, 2.5, 3.5, 4.5, 9.5], "lat": [0.5] * 6,
                           "t_utc": ["2024-05-02T00:00Z", "2024-05-01T09:00Z", "2024-05-01T03:00Z",
                                     "2024-05-01T09:00Z", "2024-05-01T09:00Z", "2024-05-01T09:00Z"]})
    event = {"damage_anchor": {"at": "2024-05-01T12:00Z", "source": "independent report"},
             "official_warning": {"at": "2024-05-01T03:00Z", "source": "agency", "url": "https://example.org"}}
    return cube, times, points, from_origin(0, 1, 1, 1), "EPSG:4326", event


def test_every_metric_by_hand(sample):
    result, reasons = replay_metrics(*sample, local_timezone="UTC", pixel_area_km2=2)
    assert result == {
        "lead_time_median_h": 6.0, "lead_time_iqr_h": [1.5, 15.0], "lead_time_mean_h": 9.0,
        "warned_before_pct": 50.0, "warned_24h_pct": 25.0, "never_critical_pct": 25.0,
        "n_points": 4, "n_points_excluded": 2, "anchor_precision": "hour",
        "event_lead_h": 12.0, "official_warning_delta_h": 3.0,
        "lead_time_assumptions": ASSUMPTIONS,
        "lead_time_hist": {"bin_h": 6, "from_h": -6.0, "counts": [1, 0, 1, 0, 0, 1]},
        "critical_area_km2": 8.0, "critical_area_with_damage_pct": 75.0,
        "damage_anchor": sample[-1]["damage_anchor"],
        "alto_lead_time_median_h": 9.0, "alto_lead_time_iqr_h": [4.5, 16.5], "alto_n_points": 3,
    }
    assert reasons == {}


def test_date_local_noon_mixed_and_duplicate_area(sample):
    cube, times, points, transform, crs, event = sample
    points = points.iloc[:2].copy()
    points.loc[0, "t_utc"] = "2024-05-01"
    points = pd.concat([points, points.iloc[[0]]], ignore_index=True)
    result, _ = replay_metrics(cube, times, points, transform, crs, event,
                               local_timezone="America/Sao_Paulo", pixel_area_km2=2)
    assert result["anchor_precision"] == "mixed"
    assert result["lead_time_median_h"] == 15
    assert result["lead_time_iqr_h"] == [10.5, 15]
    assert result["lead_time_mean_h"] == 12
    assert result["critical_area_with_damage_pct"] == 50
    assert damage_time("2024-05-01", "America/Sao_Paulo")[0] == pd.Timestamp("2024-05-01T15:00Z")
    with pytest.raises(ValueError, match="timezone"):
        damage_time("2024-05-01T12:00", "UTC")


def test_null_paths_and_nonclobber(sample, tmp_path):
    cube, times, points, transform, crs, _ = sample
    cube = np.full_like(cube, np.nan)
    out, reasons = replay_metrics(cube, times, points, transform, crs, {}, local_timezone="UTC")
    null_keys = {"lead_time_median_h", "lead_time_iqr_h", "lead_time_mean_h", "warned_before_pct",
                 "warned_24h_pct", "never_critical_pct", "anchor_precision", "event_lead_h",
                 "official_warning_delta_h", "lead_time_hist", "critical_area_km2",
                 "critical_area_with_damage_pct", "damage_anchor", "alto_lead_time_median_h", "alto_lead_time_iqr_h"}
    assert {key for key, value in out.items() if value is None} == null_keys
    assert set(reasons) == null_keys
    assert all(reasons.values())
    assert out["n_points"] == 0
    assert out["n_points_excluded"] == 6
    assert out["alto_n_points"] == 0
    path = tmp_path / "metrics.json"
    merge_metrics("rs_2024", flat={"lead_time_median_h": 99}, path=path)
    root = merge_metrics("rs_2024", flat=out, reasons=reasons, path=path)
    assert root["cases"]["rs_2024"]["lead_time_median_h"] == 99
    assert "lead_time_iqr_h" in root["cases"]["rs_2024"]["_null_reasons"]


def test_never_zero_and_negative_warning(sample):
    cube, times, points, transform, crs, event = sample
    event["official_warning"]["at"] = "2024-04-30T21:00Z"
    event["damage_anchor"] = {"date": "2024-05-01", "source": "report", "url": "https://example.org"}
    out, _ = replay_metrics(cube, times, points, transform, crs, event, local_timezone="UTC", pixel_area_km2=2)
    assert out["official_warning_delta_h"] == -3
    event["official_warning"].pop("url")
    out, _ = replay_metrics(cube, times, points, transform, crs, event, local_timezone="UTC")
    assert out["official_warning_delta_h"] == -3
    event["official_warning"]["at"] = "2024-05-01"
    out, reasons = replay_metrics(cube, times, points, transform, crs, event, local_timezone="UTC")
    assert out["official_warning_delta_h"] is None
    assert "hour-precision" in reasons["official_warning_delta_h"]
    assert out["damage_anchor"]["at"] == "2024-05-01"
    assert out["event_lead_h"] == 12
    out, reasons = replay_metrics(np.zeros_like(cube), times, points, transform, crs, event,
                                  local_timezone="UTC", pixel_area_km2=2)
    assert out["never_critical_pct"] == 100
    assert out["warned_before_pct"] == out["warned_24h_pct"] == 0
    assert out["lead_time_median_h"] is None
    assert out["critical_area_km2"] == 0
    assert "No observed Critical area" in reasons["critical_area_with_damage_pct"]


def test_invalid_cadence_and_partial_data(sample):
    cube, times, points, transform, crs, event = sample
    with pytest.raises(ValueError, match="3-hour"):
        replay_metrics(cube, times + pd.to_timedelta([0, 1, 0], unit="h"), points, transform, crs, event,
                       local_timezone="UTC")
    cube[1, 0, 0] = np.nan
    out, _ = replay_metrics(cube, times, points, transform, crs, event, local_timezone="UTC")
    assert out["n_points"] == 3
    assert out["n_points_excluded"] == 3


def test_cli_real_cog_merge(sample, tmp_path, capsys):
    cube, times, points, transform, _, event = sample
    path = tmp_path / "series.tif"
    with rasterio.open(path, "w", driver="GTiff", height=1, width=6, count=3,
                       dtype="float32", crs="EPSG:4326", transform=transform, nodata=np.nan) as dst:
        dst.write(cube.astype("float32"))
        for i, timestamp in enumerate(times, 1):
            dst.set_band_description(i, timestamp.strftime("%Y%m%dT%H%M"))
    points.to_csv(tmp_path / "points.csv", index=False)
    (tmp_path / "event.json").write_text(json.dumps(event))
    assert main(["--replay", str(path), "--labels", str(tmp_path / "points.csv"), "--event", "rs_2024",
                 "--event-json", str(tmp_path / "event.json"), "--pixel-area-km2", "2",
                 "--metrics-out", str(tmp_path / "metrics.json")]) == 0
    root = json.loads((tmp_path / "metrics.json").read_text())
    assert root["cases"]["rs_2024"]["lead_time_iqr_h"] == [1.5, 15]
    assert root["cases"]["rs_2024"]["n_points"] == 4
    assert json.loads(capsys.readouterr().out)["lead_time_median_h"] == 6
