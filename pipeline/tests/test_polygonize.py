import geopandas as gpd
import numpy as np
import pytest
from conftest import write_tif
from tp.polygonize import mask_to_geojson


def test_mask_to_geojson(tmp_path):
    arr = np.zeros((20, 20), np.uint8)
    arr[2:12, 2:12] = 1            # 100 px at 10 m = 0.01 km2
    arr[15, 15] = 2
    p = write_tif(tmp_path / "mask.tif", arr.astype(np.float32), nodata=None)

    out = tmp_path / "flood.geojson"
    stats = mask_to_geojson(p, out, values=(1,))
    assert stats["features"] == 1

    gdf = gpd.read_file(out)
    assert str(gdf.crs).endswith("4326")
    assert gdf["area_km2"].iloc[0] == pytest.approx(0.01, abs=1e-4)
    assert gdf["value"].iloc[0] == 1


def test_mask_to_geojson_min_area(tmp_path):
    arr = np.zeros((10, 10), np.float32)
    arr[0, 0] = 1                  # 1 px = 0.0001 km2
    p = write_tif(tmp_path / "m.tif", arr, nodata=None)
    stats = mask_to_geojson(p, tmp_path / "o.geojson", min_area_km2=0.001)
    assert stats["features"] == 0
