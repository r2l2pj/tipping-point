"""tp.gfs_series — GFS forecast rain fields for the case chart series (L28b)."""
from __future__ import annotations

import datetime as dt

import numpy as np
import rasterio
from rasterio.transform import from_origin

from tp.gfs_series import gfs_fields


def _cog(path, bands):
    with rasterio.open(path, "w", driver="GTiff", height=2, width=2, count=len(bands),
                       dtype="float32", crs="EPSG:4326", transform=from_origin(0, 2, 1, 1)) as dst:
        for i, v in enumerate(bands, start=1):
            dst.write(np.full((2, 2), v, np.float32), i)


def test_fields_from_issue_minus_1_and_minus_2(tmp_path):
    _cog(tmp_path / "rs_2024_gfs_20240429.tif", [1.0, 2.0, 3.0, 4.0])   # issued X-2
    _cog(tmp_path / "rs_2024_gfs_20240430.tif", [10.0, 20.0, 30.0, 40.0])  # issued X-1
    f = gfs_fields(tmp_path, "rs_2024", dt.date(2024, 5, 1))
    assert f["gfs_mm"] == 20.0 and f["gfs_issued"] == "2024-04-30"          # band d2 of X-1
    assert f["gfs_rain3_mm"] == 6.0 and f["gfs_rain3_issued"] == "2024-04-29"  # d1+d2+d3 of X-2
    assert f["gfs_mm_lead0"] is None                                       # no run issued X


def test_missing_runs_are_null(tmp_path):
    f = gfs_fields(tmp_path, "rs_2024", dt.date(2024, 5, 1))
    assert f == {"gfs_mm": None, "gfs_mm_lead0": None, "gfs_issued": None,
                 "gfs_rain3_mm": None, "gfs_rain3_issued": None}
