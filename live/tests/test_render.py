"""PNG/GeoJSON rendering on synthetic class arrays."""
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from live.render import (
    classes_png_bytes,
    cog_bytes,
    critical_features,
    feature_collection,
    layer_png_3857,
)

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


def test_layer_png_3857():
    """PNGs for the app are warped to EPSG:3857; bounds come back in lon/lat."""
    cls = np.zeros((10, 20), dtype=np.uint8)
    cls[:5] = 3
    png, bounds = layer_png_3857(cls, TR)
    with rasterio.MemoryFile(png) as mf, mf.open() as src:
        assert src.count == 4
        assert src.read()[3].max() == 255   # alpha band
    # 3857 extent back in lon/lat ≈ source grid [-52,-29.1,-51.8,-29.0]
    # (10x20 px of 0.01 deg), within pixel-snapping tolerance
    assert bounds[0] == pytest.approx(-52.0, abs=0.01)
    assert bounds[1] == pytest.approx(-29.1, abs=0.01)
    assert bounds[2] == pytest.approx(-51.8, abs=0.01)
    assert bounds[3] == pytest.approx(-29.0, abs=0.01)
    # both classes survive the warp (class 3 -> bd0026, class 0 -> ffffb2)
    with rasterio.MemoryFile(png) as mf, mf.open() as src:
        red = src.read(1)
    assert (red == 0xBD).sum() > 0 and (red == 0xFF).sum() > 0


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


def test_cog_bytes_roundtrip():
    """Float TPI COGs: EPSG:4326, float32, values and NaNs preserved."""
    arr = np.linspace(0.0, 1.0, 100, dtype=np.float32).reshape(10, 10)
    arr[0, 0] = np.nan
    arr[5, 5] = np.nan
    data = cog_bytes(arr, TR)
    with rasterio.MemoryFile(data) as mf, mf.open() as src:
        assert src.crs.to_epsg() == 4326
        assert src.dtypes[0] == "float32"
        out = src.read(1)
    assert np.isnan(out[0, 0]) and np.isnan(out[5, 5])
    finite = np.isfinite(arr)
    np.testing.assert_allclose(out[finite], arr[finite], rtol=1e-6)
