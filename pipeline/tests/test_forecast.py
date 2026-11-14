"""Perfect-forecast: replay d+k layers equal the shifted observed TPI bands."""
import json
from datetime import date, timedelta

import numpy as np
import rasterio
from rasterio.transform import from_origin
from tp.forecast import FORECAST_SOURCE, perfect_forecast

START = date(2024, 4, 24)          # series covers Apr 24 – May 9 (16 bands)
ISSUE = date(2024, 5, 2)           # rs_2024 reference_date


def _write_series(path, dates, shape=(8, 8)):
    """Float TPI series COG: band for date d is filled with its ordinal index."""
    h, w = shape
    transform = from_origin(-52.5, -29.0, 0.01, 0.01)
    with rasterio.open(
        path, "w", driver="GTiff", height=h, width=w, count=len(dates),
        dtype="float32", crs="EPSG:4326", transform=transform, nodata=np.nan,
    ) as dst:
        for i, d in enumerate(dates, start=1):
            dst.write(np.full(shape, float(i), dtype=np.float32), i)
            dst.set_band_description(i, d.strftime("%Y%m%d"))
    return path


def test_forecast_bands_equal_shifted_observed(tmp_path):
    dates = [START + timedelta(days=i) for i in range(16)]
    series = _write_series(tmp_path / "series.tif", dates)
    metrics_path = tmp_path / "rs-2024-05" / "metrics.json"

    report = perfect_forecast(series, "rs-2024-05", ISSUE, days=(1, 2, 3),
                              out_dir=tmp_path / "cog",
                              metrics_path=metrics_path)

    assert report["missing"] == []
    assert len(report["written"]) == 3
    for k in (1, 2, 3):
        out = tmp_path / "cog" / f"rs_2024_tpi_forecast_d{k}_20240502.tif"
        assert out.exists()
        with rasterio.open(out) as src:
            assert src.count == 1
            # d<k> = observed band of issue+k, named by its depicted date
            target = (ISSUE + timedelta(days=k)).strftime("%Y%m%d")
            assert src.descriptions[0] == target
            with rasterio.open(series) as ref:
                idx = list(ref.descriptions).index(target) + 1
                np.testing.assert_array_equal(src.read(1), ref.read(idx))

    metrics = json.loads(metrics_path.read_text())
    assert metrics["forecast_source"] == FORECAST_SOURCE == "perfect-forecast"
    assert metrics["event"] == "rs-2024-05"


def test_forecast_missing_band_writes_nothing_and_reports(tmp_path):
    dates = [d for d in (START + timedelta(days=i) for i in range(16))
             if d != ISSUE + timedelta(days=2)]  # the d2 date is absent
    series = _write_series(tmp_path / "series.tif", dates)

    report = perfect_forecast(series, "rs-2024-05", ISSUE, days=(1, 2, 3),
                              out_dir=tmp_path / "cog",
                              metrics_path=tmp_path / "m" / "metrics.json")

    assert report["missing"] == [{"day": 2, "date": "20240504"}]
    assert not (tmp_path / "cog" / "rs_2024_tpi_forecast_d2_20240502.tif").exists()
    for k in (1, 3):
        assert (tmp_path / "cog" / f"rs_2024_tpi_forecast_d{k}_20240502.tif").exists()


def test_default_out_dir_routes_to_case_dir(tmp_path, monkeypatch):
    """L4: no out_dir → data/<case-id>/cog/; either id form resolves."""
    import tp.events

    dates = [START + timedelta(days=i) for i in range(16)]
    series = _write_series(tmp_path / "series.tif", dates)
    monkeypatch.setattr(tp.events, "DATA_DIR", tmp_path / "data")

    report = perfect_forecast(series, "rs_2024", ISSUE, days=(1,),
                              metrics_path=tmp_path / "m" / "metrics.json")
    out = tmp_path / "data" / "rs_2024" / "cog" / "rs_2024_tpi_forecast_d1_20240502.tif"
    assert out.exists()
    assert report["written"] == [str(out)]


def test_forecast_preserves_nodata(tmp_path):
    dates = [START + timedelta(days=i) for i in range(16)]
    series = _write_series(tmp_path / "series.tif", dates)
    with rasterio.open(series, "r+") as src:
        target_band = list(src.descriptions).index("20240504") + 1
        arr = src.read(target_band)
        arr[0, 0] = np.nan
        src.write(arr, target_band)  # band of issue+2

    perfect_forecast(series, "rs-2024-05", ISSUE, days=(2,),
                     out_dir=tmp_path / "cog",
                     metrics_path=tmp_path / "m" / "metrics.json")
    with rasterio.open(tmp_path / "cog" / "rs_2024_tpi_forecast_d2_20240502.tif") as src:
        assert np.isnan(src.read(1)[0, 0])


def test_cli_help():
    import subprocess
    import sys
    from pathlib import Path

    script = Path(__file__).resolve().parents[1] / "scripts" / "07_forecast.py"
    proc = subprocess.run([sys.executable, str(script), "--help"],
                          check=False, capture_output=True, text=True)
    assert proc.returncode == 0
    assert "--issue" in proc.stdout and "--days" in proc.stdout
