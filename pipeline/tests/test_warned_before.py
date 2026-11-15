"""tools/warned_before.py — window selection and shares (hermetic)."""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import warned_before as wb  # noqa: E402

T = from_origin(-50.0, -29.0, 0.01, 0.01)


def _write(path, bands: dict[str, np.ndarray]):
    names = list(bands)
    with rasterio.open(path, "w", driver="GTiff", height=4, width=4, count=len(names), dtype="float32",
                       crs="EPSG:4326", transform=T, nodata=np.nan) as dst:
        for i, n in enumerate(names, 1):
            dst.write(bands[n].astype("float32"), i)
            dst.set_band_description(i, n)
    return path


def test_shares_alto_crit_and_nodata():
    tpi = np.array([0.1, 0.45, 0.7, np.nan])
    s = wb.shares(tpi, np.array([0, 1, 2, 3]), np.array([1.0, 1.0, 1.0, 1.0]))
    assert s == {"alto_or_critico_pct": 50.0, "critico_pct": 25.0, "no_tpi_pct": 25.0}


def test_window_uses_24_48h_frames_only(tmp_path):
    z = np.zeros((4, 4))
    hot = np.full((4, 4), 0.8)
    series = _write(tmp_path / "c_tpi_slide_x.tif",
                    {"20240428": z, "20240429": z, "20240430": hot, "20240501": hot})
    cog = tmp_path
    # issued 04-29 (48 h): d1 -> 04-30 hot ; d3 -> 05-02 is after the anchor day and must be ignored
    _write(cog / "c_tpi_gfs_20240429.tif", {"d1_slide": z, "d1_flood": hot, "d2_slide": z, "d2_flood": hot,
                                            "d3_slide": hot, "d3_flood": hot})
    with rasterio.open(series) as g:
        idx = wb._cells_from_xy([-49.995, -49.985], [-29.005, -29.015], g)
    anchor = dt.datetime(2024, 5, 1, tzinfo=dt.UTC)
    r = wb.evaluate(cog, "c", "slide", series, idx, np.ones(2), anchor)
    assert r["observed"]["frames"] == ["20240429", "20240430"]
    assert r["observed"]["critico_pct"] == 100.0           # 04-30 band (24 h) is hot
    assert r["forecast_gfs"]["alto_or_critico_pct"] == 0.0  # d3 (05-02) excluded, d1/d2 slide cold
    assert [c["date"] for c in r["by_date_observed"]][0] == "2024-04-28"
