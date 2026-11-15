import json

import numpy as np
import rasterio
from rasterio.transform import from_origin

from live import now_geojson


def test_permanent_water_polygonises_class_17(tmp_path):
    lc = np.zeros((40, 40), dtype=np.uint8)
    lc[5:25, 5:25] = 17          # ~20x20 px of 0.005 deg water -> kept
    lc[30, 30] = 17              # single-pixel speck -> dropped
    lc[0:3, 35:40] = 11          # wetland: not permanent water
    tif = tmp_path / "lc.tif"
    with rasterio.open(tif, "w", driver="GTiff", width=40, height=40, count=1,
                       dtype="uint8", crs="EPSG:4326",
                       transform=from_origin(-52.0, -30.0, 0.005, 0.005)) as ds:
        ds.write(lc, 1)
    out = now_geojson.ensure_permanent_water(
        tmp_path / "gj", (-52.0, -30.2, -51.8, -30.0), lc_path=tif)
    fc = json.loads(out.read_text())
    assert len(fc["features"]) == 1
    f = fc["features"][0]
    assert f["properties"]["class"] == "permanent_water"
    assert 100 < f["properties"]["area_km2"] < 115  # 0.1 x 0.1 deg at 30S
    assert "MCD12Q1" in fc["source"]


def test_permanent_water_missing_input_is_soft(tmp_path):
    assert now_geojson.ensure_permanent_water(
        tmp_path, (-52, -31, -51, -30), lc_path=tmp_path / "nope.tif") is None
