import numpy as np
import pandas as pd
import pytest
from conftest import write_tif
from rasterio.warp import transform as warp_transform
from tp.landslide_hit import landslide_hit, read_points

RES = 10.0


def _class_cog(tmp_path):
    cls = np.zeros((10, 10), np.uint8)
    cls[1, 1] = 2                        # Alto -> hit
    cls[2, 2] = 3                        # Critico -> hit
    cls[3, 3] = 3                        # Critico -> hit
    cls[4, 4] = 1                        # Moderado -> valid miss
    cls[5, 5] = 255                      # nodata -> skipped
    return write_tif(tmp_path / "tpi_cls.tif", cls, res=RES, nodata=255)


def _lonlat(rows, cols):
    xs = [500000 + (c + 0.5) * RES for c in cols]
    ys = [5300000 - (r + 0.5) * RES for r in rows]
    return warp_transform("EPSG:32610", "EPSG:4326", xs, ys)


def test_hit_pct_hand_computed(tmp_path):
    cog = _class_cog(tmp_path)
    xs, ys = _lonlat([1, 2, 3, 4, 5], [1, 2, 3, 4, 5])
    # + one point ~0.5 deg east of the raster -> skipped as out of bounds
    xs.append(xs[0] + 0.5)
    ys.append(ys[0])
    df = pd.DataFrame({"lat": ys, "lon": xs, "source": ["GLC"] * 6})
    csv = tmp_path / "pts.csv"
    df.to_csv(csv, index=False)

    stats = landslide_hit(csv, cog)
    assert stats["points_total"] == 6
    assert stats["points_valid"] == 4      # nodata + out-of-bounds skipped
    assert stats["points_hit"] == 3
    assert stats["points_skipped"] == 2
    assert stats["hit_pct"] == 75.0
    assert stats["collections"]["landslide_catalog"] == "GLC"
    assert stats["collections"]["tpi"] == "tpi_cls"


@pytest.mark.parametrize("lat_col,lon_col", [
    ("lat", "lon"), ("Latitude", "Longitude"), ("y", "x"), ("LAT", "LNG"),
])
def test_column_aliases(tmp_path, lat_col, lon_col):
    cog = _class_cog(tmp_path)
    xs, ys = _lonlat([1, 4], [1, 4])       # one hit (Alto), one miss
    df = pd.DataFrame({lat_col: ys, lon_col: xs})
    csv = tmp_path / "pts.csv"
    df.to_csv(csv, index=False)

    stats = landslide_hit(csv, cog)
    assert stats["points_valid"] == 2
    assert stats["hit_pct"] == 50.0


def test_source_column_mixed(tmp_path):
    cog = _class_cog(tmp_path)
    xs, ys = _lonlat([1, 4], [1, 4])
    df = pd.DataFrame({"lat": ys, "lon": xs,
                       "source": ["GLC", "inventory"]})
    csv = tmp_path / "pts.csv"
    df.to_csv(csv, index=False)
    stats = landslide_hit(csv, cog)
    assert stats["collections"]["landslide_catalog"] == "GLC+inventory"


def test_no_source_column_uses_stem(tmp_path):
    cog = _class_cog(tmp_path)
    xs, ys = _lonlat([1], [1])
    csv = tmp_path / "wa_catalog.csv"
    pd.DataFrame({"lat": ys, "lon": xs}).to_csv(csv, index=False)
    stats = landslide_hit(csv, cog)
    assert stats["collections"]["landslide_catalog"] == "wa_catalog"


def test_missing_lat_column_raises(tmp_path):
    csv = tmp_path / "pts.csv"
    pd.DataFrame({"foo": [1.0], "lon": [-122.0]}).to_csv(csv, index=False)
    with pytest.raises(ValueError, match="latitude"):
        read_points(csv)


def test_all_nodata_gives_zero_pct(tmp_path):
    cog = _class_cog(tmp_path)
    xs, ys = _lonlat([5, 5], [5, 5])       # both on nodata
    csv = tmp_path / "pts.csv"
    pd.DataFrame({"lat": ys, "lon": xs}).to_csv(csv, index=False)
    stats = landslide_hit(csv, cog)
    assert stats["points_valid"] == 0
    assert stats["hit_pct"] == 0.0
