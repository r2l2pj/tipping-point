"""Hermetic tests for tp.upstream (L35-U1): synthetic basin tables and
in-memory grids only — no downloads, no data/ writes."""
import json

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box
from tp.upstream import build_case, rasterize_ids, upstream_sets

# 1<-2<-{3<-5, 4}; 6<-7 is a separate coastal chain; 8 is an island outlet.
BASINS = pd.DataFrame({
    "HYBAS_ID": [1, 2, 3, 4, 5, 6, 7, 8],
    "NEXT_DOWN": [0, 1, 2, 2, 3, 0, 6, 0],
    "SUB_AREA": [10.0, 5.0, 4.0, 3.0, 2.0, 7.0, 1.0, 9.0],
    "UP_AREA": [24.0, 14.0, 6.0, 3.0, 2.0, 8.0, 1.0, 9.0],
})


def test_upstream_sets():
    up = upstream_sets(BASINS)
    assert up[1] == [1, 2, 3, 4, 5]
    assert up[2] == [2, 3, 4, 5]
    assert up[5] == [5]
    assert up[6] == [6, 7]
    assert up[8] == [8]


def test_upstream_sets_ids_subset():
    up = upstream_sets(BASINS, ids=[4, 7])
    assert up == {4: [4], 7: [7]}


def _toy_gdf() -> gpd.GeoDataFrame:
    # two adjacent 2x2-degree basins inside a 4x2 grid, ids > uint32
    return gpd.GeoDataFrame(
        {"HYBAS_ID": np.array([6100000010, 7100000010], dtype="int64"),
         "NEXT_DOWN": np.array([0, 6100000010], dtype="int64"),
         "SUB_AREA": [4.0, 4.0], "UP_AREA": [8.0, 4.0]},
        geometry=[box(-4, -2, -2, 0), box(-2, -2, 0, 0)], crs="EPSG:4326")


def test_rasterize_ids(tmp_path):
    like = tmp_path / "like.tif"
    with rasterio.open(like, "w", driver="GTiff", height=4, width=4, count=1,
                       dtype="uint8", crs="EPSG:4326",
                       transform=from_origin(-4, 0, 1, 1)) as dst:
        dst.write(np.zeros((4, 4), np.uint8), 1)
    arr, prof = rasterize_ids(_toy_gdf(), like)
    assert arr.dtype == np.int64 and arr.shape == (4, 4)
    assert (arr[:2, :2] == 6100000010).all()
    assert (arr[:2, 2:] == 7100000010).all()
    assert (arr[2:, :] == 0).all()  # below the polygons -> ocean
    assert prof["transform"] == from_origin(-4, 0, 1, 1)
    assert prof["dtype"] == "int64" and prof["nodata"] == 0


def test_build_case(tmp_path):
    like = tmp_path / "like.tif"
    with rasterio.open(like, "w", driver="GTiff", height=4, width=4, count=1,
                       dtype="float32", crs="EPSG:4326",
                       transform=from_origin(-4, 0, 1, 1)) as dst:
        dst.write(np.zeros((4, 4), np.float32), 1)
    # AOI intersects only the left basin; its upstream chain adds the right.
    stats = build_case(_toy_gdf(), (-3.5, -1.5, -2.5, -0.5), like, tmp_path / "out")
    assert stats["n_aoi_basins"] == 1
    assert stats["n_union_basins"] == 2
    assert stats["largest_upstream_area_km2"] == 8.0
    assert stats["pct_cells_with_basin"] == 50.0
    doc = json.loads((tmp_path / "out" / "upstream.json").read_text())
    assert doc["union_bbox"] == [-4.0, -2.0, 0.0, 0.0]
    assert doc["6100000010"] == [6100000010, 7100000010]
    assert doc["upstream_area_km2"] == {"6100000010": 8.0}
    g = gpd.read_file(tmp_path / "out" / "basins.geojson")
    assert sorted(g.HYBAS_ID) == [6100000010, 7100000010]
    with rasterio.open(tmp_path / "out" / "basin_id_90m.tif") as src:
        assert (src.height, src.width) == (4, 4)
        assert src.transform == from_origin(-4, 0, 1, 1)
        arr = src.read(1)
        assert (arr[:2, :] > 0).all() and (arr[2:, :] == 0).all()
