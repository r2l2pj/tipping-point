"""Offline tests for ml.local.sources_static — synthetic DEMs/arrays only.

No network: CMR/downloads/OPeNDAP are monkeypatched. A real .hgt byte
file exercises the SRTMHGT driver end-to-end through build_terrain.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import rasterio
from affine import Affine

from ml.local import settings
from ml.local import sources_static as ss


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
    return tmp_path


def _tile_tr(res=1.0 / 1200.0, lon=0.0, lat=0.0, halo=0):
    return Affine(res, 0, lon - halo * res, 0, -res, lat + 1 + halo * res)


def _metric_plane(shape=(60, 60), gx=0.15, gy=-0.10, res=1.0 / 1200.0,
                  lat0=-30.0):
    """Elevation (m) of a metric plane z = gx*E + gy*N at row latitudes."""
    tr = _tile_tr(res, 0.0, lat0)
    lat = tr.f + (np.arange(shape[0]) + 0.5) * tr.e
    lat_m, lon_m = ss._deg_len_m(lat)
    dx = res * lon_m[:, None]               # east metres per col, per row
    dy = res * lat_m[:, None]               # metres per row (south->down)
    X = np.arange(shape[1])[None, :] * dx   # east metres
    Y = -np.arange(shape[0])[:, None] * dy  # north metres
    return gx * X + gy * Y, tr


def test_horn_grad_matches_tp_terrain():
    from tp.terrain import _horn
    rng = np.random.default_rng(0)
    dem = rng.normal(500, 80, (40, 50))
    dem[10, 10] = np.nan
    dzdx, dzdy = ss._horn_grad(dem, 90.0, 90.0)
    mine = np.degrees(np.arctan(np.hypot(dzdx, dzdy)))
    ref = _horn(dem, 90.0, 90.0)
    np.testing.assert_allclose(mine, ref, atol=1e-6, equal_nan=True)


def test_tilted_plane_slope_aspect():
    gx, gy = 0.15, -0.10
    dem, tr = _metric_plane(gx=gx, gy=gy)
    bands = ss._terrain_bands(dem, tr)
    want_slope = np.degrees(np.arctan(np.hypot(gx, gy)))
    inner = bands["slope"][3:-3, 3:-3]
    assert np.all(np.isfinite(inner))
    assert abs(np.nanmean(inner) - want_slope) < 0.05
    # downhill direction: gradient rises east(gx>0)/south-ish(gy<0 -> rises
    # northward? gy*N with gy<0 falls northward -> dips south => aspect ~S-W
    want_aspect = np.degrees(np.arctan2(-gx, -gy)) % 360.0
    sin, cos = np.nanmean(bands["aspect_sin"][3:-3, 3:-3]), np.nanmean(
        bands["aspect_cos"][3:-3, 3:-3])
    got = np.degrees(np.arctan2(sin, cos)) % 360.0
    diff = abs((got - want_aspect + 180) % 360 - 180)
    assert diff < 0.5


def test_flat_plane_zero_slope_zero_aspect():
    dem, tr = _metric_plane(gx=0.0, gy=0.0)
    bands = ss._terrain_bands(dem, tr)
    assert np.nanmax(bands["slope"]) == 0.0
    assert np.all(bands["aspect_sin"][3:-3, 3:-3] == 0.0)
    assert np.all(bands["aspect_cos"][3:-3, 3:-3] == 0.0)
    assert np.nanmax(np.abs(bands["curvature"])) == 0.0
    assert np.nanmax(bands["relief"]) == 0.0


def test_bowl_curvature():
    res = 1.0 / 1200.0
    k = 1e-5                                    # z = k*(E^2 + N^2) m
    tr = _tile_tr(res, 0.0, -30.0)
    lat = tr.f + (np.arange(60) + 0.5) * tr.e
    lat_m, lon_m = ss._deg_len_m(lat)
    X = np.arange(60)[None, :] * (res * lon_m[:, None])
    Y = -np.arange(60)[:, None] * (res * lat_m[:, None])
    dem = k * (X**2 + Y**2)
    bands = ss._terrain_bands(dem, tr)
    # Laplacian(z) = 4k -> stored x100 units
    inner = bands["curvature"][5:-5, 5:-5]
    assert abs(np.nanmean(inner) - 4 * k * 100) / (4 * k * 100) < 0.05


def test_relief_step():
    res = 1.0 / 1200.0
    dem = np.zeros((40, 40))
    dem[:, 20:] = 100.0                         # cliff at col 20
    bands = ss._terrain_bands(dem, _tile_tr(res, 0.0, -30.0))
    # relief in a 5x5 window centred on the cliff = full step
    assert np.nanmax(bands["relief"]) == pytest.approx(100.0)
    assert bands["relief"][20, 5] == pytest.approx(0.0)   # far from cliff


def test_nan_propagation():
    dem, tr = _metric_plane()
    dem[20, 20] = np.nan                        # hole
    bands = ss._terrain_bands(dem, tr)
    assert np.isnan(bands["slope"][20, 20])
    assert np.isnan(bands["elevation"][20, 20])
    assert np.isfinite(bands["slope"][20, 30])  # hole doesn't poison others


def test_mean_blocks():
    a = np.arange(36.0).reshape(6, 6)
    m = ss._mean_blocks(a, 3)
    assert m.shape == (2, 2)
    assert m[0, 0] == pytest.approx(np.mean([0, 1, 2, 6, 7, 8, 12, 13, 14]))
    a_nan = a.copy()
    a_nan[:3, :3] = np.nan
    m2 = ss._mean_blocks(a_nan, 3)
    assert np.isnan(m2[0, 0])
    a_part = a.copy()
    a_part[0, 0] = np.nan
    assert ss._mean_blocks(a_part, 3)[0, 0] == pytest.approx(
        np.mean([1, 2, 6, 7, 8, 12, 13, 14]))


def _write_hgt(path: Path, dem_i16: np.ndarray) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(dem_i16.astype(">i2").tobytes())
    return path


def test_build_terrain_and_sample(data_dir, monkeypatch):
    """Synthetic .hgt -> build_terrain -> real GeoTIFF -> sample_static."""
    n = 3601
    # gentle hill: elev = 800 - dist^2-ish -> positive slopes, finite relief
    yy, xx = np.mgrid[0:n, 0:n]
    dem = (800.0 - ((xx - 1800)**2 + (yy - 1800)**2) * 1e-4).astype(">i2")
    hdir = data_dir / "nasadem" / "hgt"
    _write_hgt(hdir / "s01e000.hgt", dem)
    _write_hgt(hdir / "s01e001.hgt", dem)
    _write_hgt(hdir / "s02e000.hgt", dem)
    _write_hgt(hdir / "s02e001.hgt", dem)
    monkeypatch.setattr(ss, "fetch_nasadem", lambda bbox, workers=4: [])

    tiles = ss.build_terrain(bbox=(0.0, -2.0, 2.0, -1.0))
    assert len(tiles) == 2                    # cells (0,-2) and (1,-2)
    with rasterio.open(tiles[0]) as ds:
        assert ds.count == 6
        assert ds.width == 1200 and ds.height == 1200
        assert ds.crs.to_epsg() == 4326
        slope = ds.read(2)
        assert np.nanmax(slope) > 0.05
        assert np.isfinite(slope).mean() > 0.95   # interior mostly finite

    # fake landcover + ndvi on the same bbox grid
    lc_tr = Affine(1 / 240, 0, 0.0, 0, -1 / 240, -1.0)
    lc = np.full((240, 480), 5, np.uint8)       # mixed forest
    lc[:, :100] = 17                            # water patch on the west edge
    lc_path = data_dir / "static90" / "landcover_mcd12q1_2023.tif"
    lc_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(lc_path, "w", driver="GTiff", height=240, width=480,
                       count=1, dtype="uint8", crs="EPSG:4326",
                       transform=lc_tr, nodata=255) as ds:
        ds.write(lc, 1)
    nd_tr = Affine(0.05, 0, -180.0, 0, -0.05, 90.0)
    nd = np.full((3600, 7200), 0.7, np.float32)
    nd_path = data_dir / "static90" / "ndvi_vnp13c2_2023.tif"
    with rasterio.open(nd_path, "w", driver="GTiff", height=3600, width=7200,
                       count=1, dtype="float32", crs="EPSG:4326",
                       transform=nd_tr, nodata=np.nan) as ds:
        ds.write(nd, 1)
    ss._write_index((0.0, -2.0, 2.0, -1.0), settings.RES_STATIC_DEG,
                    tiles, lc_path, nd_path, None)
    idx = json.loads((data_dir / "static90" / "index.json").read_text())
    assert len(idx["terrain"]) == 2
    vrt = ss._write_vrt(idx)
    assert vrt is not None and vrt.exists()
    with rasterio.open(vrt) as ds:
        assert ds.count == 8                    # 6 terrain + lc + ndvi
        arr = ds.read(2, window=Window_(500, 500, 10, 10))
        assert np.isfinite(arr).all()

    df = ss.sample_static([0.5, 1.5], [-1.5, -1.5])
    assert list(df.columns) == ["lon", "lat"] + ss.BANDS_ALL
    assert np.isfinite(df["slope"]).all()
    assert (df["landcover"] == 5).all()
    assert np.allclose(df["ndvi"], 0.7)
    assert df["hand_m"].isna().all()

    land = ss.is_land([0.5, 0.1], [-1.5, -1.5])   # 0.1 -> water patch col<100
    assert land.tolist() == [True, False]

    lo, la = ss.random_land_points(50, (0.0, -2.0, 2.0, -1.0),
                                   np.random.default_rng(1))
    assert len(lo) == 50
    assert ((lo >= 0) & (lo <= 2) & (la >= -2) & (la <= -1)).all()

    # static_on_grid: 0.01 deg window subset of the domain
    tr = Affine(0.01, 0, 0.0, 0, -0.01, -1.0)
    g = ss.static_on_grid(tr, (100, 200))
    assert set(g) == set(ss.BANDS_ALL)
    assert g["slope"].shape == (100, 200)
    assert np.isfinite(g["slope"]).mean() > 0.9
    assert np.isfinite(g["ndvi"]).all()
    assert g["hand_m"].dtype == np.float32


def Window_(c, r, w, h):
    from rasterio.windows import Window
    return Window(c, r, w, h)


def test_dmr_paths():
    xml = """<Dataset xmlns="http://xml.opendap.org/ns/DAP/4.0#">
      <Group name="MCD12Q1">
        <Float64 name="XDim"/><Float64 name="YDim"/>
        <Group name="Data_Fields"><Byte name="LC_Type1"/></Group>
      </Group></Dataset>"""
    p = ss._dmr_paths(xml)
    assert p["LC_Type1"] == ["/MCD12Q1/Data_Fields/LC_Type1"]
    assert p["XDim"] == ["/MCD12Q1/XDim"]


def test_opendap_vars_mocked(monkeypatch):
    import io as _io

    import h5py
    buf = _io.BytesIO()
    with h5py.File(buf, "w") as f:
        f.create_dataset("MCD12Q1/Data_Fields/LC_Type1",
                         data=np.arange(16, dtype=np.uint8).reshape(4, 4))
        f.create_dataset("MCD12Q1/XDim", data=np.linspace(0, 1111950, 4))
        f.create_dataset("MCD12Q1/YDim", data=np.linspace(0, -1111950, 4))
    dmr = """<Dataset xmlns="http://xml.opendap.org/ns/DAP/4.0#">
      <Group name="MCD12Q1"><Float64 name="XDim"/><Float64 name="YDim"/>
        <Group name="Data_Fields"><Byte name="LC_Type1"/></Group>
      </Group></Dataset>"""

    class R:
        status_code = 200
        text = dmr
        content = buf.getvalue()

        def raise_for_status(self):
            pass

    calls = []
    monkeypatch.setattr(ss, "_http_get",
                        lambda url, **kw: (calls.append(url), R())[1])
    out = ss._opendap_vars("http://x/g", [("LC_Type1", "[0:1][0:1]"),
                                        ("XDim", None), ("YDim", None)])
    assert calls[0].endswith(".dmr.xml")
    assert ".dap.nc4" in calls[1]
    assert out["LC_Type1"].shape == (4, 4)
    assert out["XDim"].shape == (4,)
    tr = ss._grid_transform(out["XDim"], out["YDim"])
    assert tr.a == pytest.approx(1111950 / 3)


def test_modis_cell():
    # h13v11 UL=(-5559752.598,-2223901.039) per StructMetadata — a point at
    # the tile centre must map to h=13, v=11
    xc, yc = -5559752.598 + MODIS_T / 2, -2223901.039 - MODIS_T / 2
    lo, la = rasterio.warp.transform(ss.SINUSOIDAL, ss.CRS.from_epsg(4326),
                                     [xc], [yc])
    h, v, r, c = ss._modis_cell(np.array(lo), np.array(la))
    assert (h[0], v[0]) == (13, 11)
    assert 0 <= r[0] < 2400 and 0 <= c[0] < 2400


MODIS_T = 1111950.5196667


def test_points_mode_end_to_end(data_dir, monkeypatch):
    """sample_static_points: mocked NASADEM zip + OPeNDAP slab + ndvi grid,
    parquet cache reuse on second call."""
    n = 3601
    xx = np.mgrid[0:n, 0:n][1]
    dem = (600.0 + (xx - 1800) * 0.2).astype(">i2")   # east-rising plane
    calls = {"ensure": 0}

    def fake_ensure(cell, keep=True, tmp=None):
        calls["ensure"] += 1
        h = _write_hgt(data_dir / "nasadem" / "hgt"
                       / f"{ss._cell_id(*cell)}.hgt", dem)
        z = data_dir / "nasadem" / "zip" / (
            f"NASADEM_HGT_{ss._cell_id(*cell)}.zip")
        z.parent.mkdir(parents=True, exist_ok=True)
        z.write_bytes(b"PKfake")
        return h, z

    monkeypatch.setattr(ss, "_ensure_hgt", fake_ensure)
    monkeypatch.setattr(ss, "_mcd12q1_tiles_year",
                        lambda year: {"h13v11": {"title": "x.h13v11"}})
    lc = np.full((2400, 2400), 12, np.uint8)
    monkeypatch.setattr(ss, "_opendap_vars",
                        lambda base, wants, **kw: {"LC_Type1": lc})
    monkeypatch.setattr(ss, "_modis_cell",
                        lambda lo, la: (np.full(len(lo), 13),
                                        np.full(len(lo), 11),
                                        np.full(len(lo), 100),
                                        np.full(len(lo), 200)))
    monkeypatch.setattr(ss, "_hand_path", lambda: None)
    nd_tr = Affine(0.05, 0, -180.0, 0, -0.05, 90.0)
    nd_path = data_dir / "static90" / "ndvi_vnp13c2_2023.tif"
    nd_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(nd_path, "w", driver="GTiff", height=3600, width=7200,
                       count=1, dtype="float32", crs="EPSG:4326",
                       transform=nd_tr, nodata=np.nan) as ds:
        ds.write(np.full((3600, 7200), 0.5, np.float32), 1)

    df = ss.sample_static_points([0.5, 0.6], [-0.5, -0.6])
    assert calls["ensure"] == 1                # same 1-deg cell, one fetch
    assert list(df.columns) == ["lon", "lat"] + ss.BANDS_ALL
    assert np.isfinite(df["slope"]).all()
    assert df["slope"].mean() > 0.3            # 0.2 m per ~92.7 m ≈ 12 deg
    assert (df["landcover"] == 12).all()
    assert np.allclose(df["ndvi"], 0.5)
    assert df["hand_m"].isna().all()
    # dem deleted (keep_dem=False)
    assert not (data_dir / "nasadem" / "hgt"
                / "s01e000.hgt").exists()
    cache = data_dir / ss.POINT_CACHE_NAME
    assert cache.exists()
    ss.sample_static_points([0.5, 0.55], [-0.5, -0.55])
    assert calls["ensure"] == 2                # one cached, one new point
    assert len(pd.read_pickle(cache)) == 3


def test_points_keep_dem(data_dir, monkeypatch):
    n = 3601
    dem = np.full((n, n), 300, ">i2")

    def fake_ensure(cell, keep=True, tmp=None):
        h = _write_hgt(data_dir / "nasadem" / "hgt"
                       / f"{ss._cell_id(*cell)}.hgt", dem)
        z = data_dir / "nasadem" / "zip" / (
            f"NASADEM_HGT_{ss._cell_id(*cell)}.zip")
        z.parent.mkdir(parents=True, exist_ok=True)
        z.write_bytes(b"PKfake")
        return h, z

    monkeypatch.setattr(ss, "_ensure_hgt", fake_ensure)
    monkeypatch.setattr(ss, "_points_landcover",
                        lambda lo, la, year=0: np.full(len(lo), np.nan))
    monkeypatch.setattr(ss, "_points_ndvi_hand",
                        lambda lo, la, year=0: (np.full(len(lo), np.nan),
                                                np.full(len(lo), np.nan)))
    monkeypatch.setattr(ss, "_hand_path", lambda: None)
    ss.sample_static_points([0.5], [-0.5], keep_dem=True)
    assert (data_dir / "nasadem" / "hgt"
            / "s01e000.hgt").exists()      # kept


def test_cell_id_and_lonlat():
    assert ss._cell_id(-52, -30) == "s30w052"
    assert ss._cell_id(5, 3) == "n03e005"
    assert ss._cell_lonlat("s30w052") == (-52, -30)
    assert ss._cell_lonlat("n03e005") == (5, 3)
