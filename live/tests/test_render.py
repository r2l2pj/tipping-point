"""PNG/GeoJSON rendering on synthetic class arrays."""
import numpy as np
import rasterio
from rasterio.transform import from_origin

from live.render import classes_png_bytes, critical_features, feature_collection

TR = from_origin(-52.0, -29.0, 0.01, 0.01)


def test_png_rgba_and_alpha():
    cls = np.zeros((8, 8), dtype=np.uint8)
    cls[:4] = 3
    valid = np.ones((8, 8), dtype=bool)
    valid[0, 0] = False
    png = classes_png_bytes(cls, valid)
    with rasterio.MemoryFile(png) as mf, mf.open() as src:
        assert src.count == 4 and src.height == 8 and src.width == 8
        bands = src.read()
    assert bands[3, 0, 0] == 0          # invalid pixel is transparent
    assert bands[3, 4, 0] == 255
    assert bands[0, 0, 1] == 0xBD       # class 3 -> bd0026 red channel


def test_critical_features_polygons_and_min_size():
    cls = np.zeros((20, 20), dtype=np.uint8)
    cls[5:9, 5:9] = 3                   # 16 px blob
    cls[15, 15] = 2                     # 1 px blob -> filtered
    feats = critical_features(cls, TR, kind="slide", day="2026-11-14", min_pixels=10)
    assert len(feats) == 1
    props = feats[0]["properties"]
    assert props["class"] == 3 and props["label"] == "Crítico" and props["pixels"] == 16
    assert feats[0]["geometry"]["type"] in ("Polygon", "MultiPolygon")
    fc = feature_collection(feats)
    assert fc["type"] == "FeatureCollection" and len(fc["features"]) == 1
