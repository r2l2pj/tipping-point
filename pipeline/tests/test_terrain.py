import subprocess
import sys
from pathlib import Path

import numpy as np
import rasterio
from conftest import write_tif
from rasterio.transform import from_origin
from tp.terrain import slope_deg

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "00_terrain.py"
ANGLE = 20.0
R_M = 6_371_000.0  # spherical-earth radius used to lay out the synthetic planes


def _plane(h, w, res, angle=ANGLE):
    """Height field of a plane tilted `angle` degrees along the diagonal."""
    g = np.tan(np.deg2rad(angle)) / np.sqrt(2.0)
    y, x = np.meshgrid(np.arange(h) * res, np.arange(w) * res, indexing="ij")
    return (g * (x + y)).astype(np.float32)


def test_slope_projected(tmp_path):
    dem = _plane(64, 64, 10.0)
    p = write_tif(tmp_path / "dem.tif", dem, res=10.0, crs="EPSG:32610")
    with rasterio.open(p) as ds:
        slope, profile = slope_deg(ds)
    assert slope.dtype == np.float32
    inner = slope[2:-2, 2:-2]
    assert np.abs(inner - ANGLE).max() < 0.5
    assert profile["dtype"] == "float32"


def test_slope_epsg4326(tmp_path):
    """Geographic grid: pixel size must come from WGS84 degree lengths, not raw degrees."""
    h, w = 64, 64
    res_deg = 0.001
    lon0, lat0 = -70.0, -30.0
    # metres east/north of each pixel centre on a sphere — close enough to
    # WGS84 (<0.3 %) that the +/-0.5 deg check still discriminates degree-vs-metre bugs
    lon = lon0 + (np.arange(w) + 0.5) * res_deg
    lat = lat0 - (np.arange(h) + 0.5) * res_deg
    x_m = np.deg2rad(lon - lon0) * R_M * np.cos(np.deg2rad(lat))[:, None]
    y_m = np.deg2rad(lat0 - lat)[:, None] * R_M
    g = np.tan(np.deg2rad(ANGLE)) / np.sqrt(2.0)
    dem = (g * (x_m + y_m)).astype(np.float32)

    transform = from_origin(lon0, lat0, res_deg, res_deg)
    p = tmp_path / "dem4326.tif"
    with rasterio.open(
        p, "w", driver="GTiff", height=h, width=w, count=1, dtype="float32",
        crs="EPSG:4326", transform=transform, nodata=np.nan,
    ) as dst:
        dst.write(dem, 1)

    with rasterio.open(p) as ds:
        slope, _ = slope_deg(ds)
    inner = slope[2:-2, 2:-2]
    assert np.abs(inner - ANGLE).max() < 0.5


def test_slope_nodata(tmp_path):
    dem = _plane(48, 48, 10.0)
    dem[20:26, 20:26] = -9999.0
    p = write_tif(tmp_path / "dem_hole.tif", dem, res=10.0,
                  crs="EPSG:32610", nodata=-9999.0)
    with rasterio.open(p) as ds:
        slope, _ = slope_deg(ds)
    assert np.isnan(slope[20:26, 20:26]).all()
    assert np.isfinite(slope[10, 10])
    assert abs(slope[10, 10] - ANGLE) < 0.5
    # gdaldem rule: neighbours of the hole still get a finite slope
    assert np.isfinite(slope[19, 19])


def test_slope_array():
    dem = _plane(32, 32, 5.0)
    slope, profile = slope_deg(dem, cellsize=5.0)
    assert profile is None
    assert np.abs(slope[2:-2, 2:-2] - ANGLE).max() < 0.5


def test_script_help():
    r = subprocess.run([sys.executable, str(SCRIPT), "--help"],
                       check=False, capture_output=True, text=True)
    assert r.returncode == 0
    assert "--dem" in r.stdout and "--event" in r.stdout


def test_script_end_to_end(tmp_path):
    dem = _plane(48, 48, 10.0)
    p = write_tif(tmp_path / "dem.tif", dem, res=10.0, crs="EPSG:32610")
    out = tmp_path / "slope.tif"
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--dem", str(p), "--out", str(out)],
        check=False, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    with rasterio.open(out) as ds:
        assert ds.dtypes[0] == "float32"
        slope = ds.read(1)
    assert np.abs(slope[4:-4, 4:-4] - ANGLE).max() < 0.5
