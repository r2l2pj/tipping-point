"""Source-level checks for build_static (GEE needs creds, so no live runs)."""
import datetime as dt
import io
import types
import zipfile
from pathlib import Path

import numpy as np

from live import build_static
from live.config import ASSET_ROOT, STATIC_DOMAIN

SRC = Path(build_static.__file__).read_text()


def test_population_source_is_gpw():
    assert build_static.POP_COLLECTION == "CIESIN/GPWv411/GPW_Population_Count"
    assert build_static.POP_BAND == "population_count"


def test_no_ghsl_references():
    assert "GHSL" not in SRC


def test_entry_points_exist():
    assert callable(build_static.main)
    assert callable(build_static._terrain)
    assert callable(build_static.build_assets)


# ---------------------------------------------------------------------------
# --assets mode: fake ee module (no creds, no network)


class _Img:
    def __init__(self, *a, **k):
        self.props = {}

    def set(self, k, v):
        out = _Img()
        out.props = {**self.props, k: v}
        return out

    def __getattr__(self, _name):
        return lambda *a, **k: self


class _Collection:
    def __init__(self, *a, **k):
        pass

    def __getattr__(self, _name):
        return lambda *a, **k: _Img()


class _Task:
    def __init__(self, image, kwargs, log):
        self.image = image
        self.kwargs = kwargs
        self.started = False
        self._log = log

    def start(self):
        self.started = True
        self._log.append(self)


def _fake_ee():
    created, exported = [], []
    ee = types.SimpleNamespace()
    ee.ImageCollection = _Collection
    ee.Image = _Img
    ee.Date = types.SimpleNamespace(fromYMD=lambda *a: _Img())
    ee.Reducer = types.SimpleNamespace(percentile=lambda *a: object())
    ee.Terrain = types.SimpleNamespace(slope=lambda img: _Img())
    ee.Geometry = types.SimpleNamespace(Rectangle=lambda b: ("rect", b))
    ee.EEException = Exception
    ee.data = types.SimpleNamespace(
        getAsset=lambda path: None,                       # nothing exists
        createAsset=lambda opts, path: created.append((opts, path)))
    ee.batch = types.SimpleNamespace(Export=types.SimpleNamespace(
        image=types.SimpleNamespace(
            toAsset=lambda image, **kw: _Task(image, kw, exported))))
    ee._created = created
    ee._exported = exported
    return ee


def test_build_assets_exports_clim_collection_and_terrain():
    ee = _fake_ee()
    tasks = build_static.build_assets(ee)

    coll = f"{ASSET_ROOT}/static/{STATIC_DOMAIN}/clim_rain"
    assert ee._created == [({"type": "ImageCollection"}, coll)]

    # 366 doy inserts into the collection + slope + hand
    assert len(tasks) == 368
    assert all(t.started for t in tasks)

    clim = tasks[:366]
    assert [t.kwargs["assetId"] for t in clim] == [
        f"{coll}/doy_{d:03d}" for d in range(1, 367)]
    assert [t.image.props["doy"] for t in clim] == list(range(1, 367))
    assert all(t.kwargs["scale"] == build_static.ASSET_CLIM_SCALE_M
               for t in clim)

    by_desc = {t.kwargs["description"]: t for t in tasks[366:]}
    assert set(by_desc) == {"slope", "hand"}
    assert by_desc["slope"].kwargs["assetId"] == _asset("slope")
    assert by_desc["hand"].kwargs["assetId"] == _asset("hand")
    assert all(t.kwargs["scale"] == build_static.ASSET_TERRAIN_SCALE_M
               for t in tasks[366:])
    # population is npz-only — never an asset
    assert not any("pop" in t.kwargs["assetId"] for t in tasks)


def test_build_assets_dry_run_starts_nothing():
    ee = _fake_ee()
    tasks = build_static.build_assets(ee, dry_run=True)
    assert len(tasks) == 368
    assert ee._exported == []
    assert ee._created == []


def test_build_assets_clim_windows_are_per_doy():
    """clim_rain must sample +-30d around EACH doy's month/day (01_tpi_index.js),
    not reuse one monthly image for every doy of that month."""
    ee = _fake_ee()
    calls = []

    def fromYMD(y, m, d):
        calls.append((y, m, d))
        return _Img()

    ee.Date = types.SimpleNamespace(fromYMD=fromYMD)
    build_static.build_assets(ee, dry_run=True)

    expected_md = {
        (d.month, d.day) for d in
        (dt.date(2000, 1, 1) + dt.timedelta(days=i) for i in range(366))}
    assert {(m, d) for _, m, d in calls} == expected_md
    assert {y for y, _, _ in calls} == set(build_static.YEARS)
    # one fromYMD per (year, offset) per doy: 366 * 23 * 21
    assert len(calls) == 366 * len(list(build_static.YEARS)) * len(
        list(build_static.OFFSETS))


def _asset(name):
    return f"{ASSET_ROOT}/static/{STATIC_DOMAIN}/{name}"


# ---------------------------------------------------------------------------
# _download_band: GEE returns a zip OR a raw GeoTIFF — both must decode


class _DlImg:
    def select(self, _band):
        return self

    def getDownloadURL(self, _params):
        return "http://fake.example/tif"


def _tif_bytes(arr):
    from rasterio.io import MemoryFile

    with MemoryFile() as mf:
        with mf.open(driver="GTiff", height=arr.shape[0],
                     width=arr.shape[1], count=1, dtype="float64") as ds:
            ds.write(arr, 1)
        return mf.read()


def _run_download(monkeypatch, payload):
    import requests

    ee = _fake_ee()
    monkeypatch.setattr(
        requests, "get",
        lambda *a, **k: types.SimpleNamespace(content=payload))
    return build_static._download_band(ee, _DlImg(), "band")


def test_download_band_accepts_zip_payload(monkeypatch):
    arr = np.arange(12, dtype=np.float64).reshape(3, 4)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("band.tif", _tif_bytes(arr))
    out = _run_download(monkeypatch, buf.getvalue())
    np.testing.assert_array_equal(out, arr)
    assert out.dtype == np.float64


def test_download_band_accepts_raw_tiff_payload(monkeypatch):
    arr = np.arange(12, dtype=np.float64).reshape(3, 4) + 0.5
    out = _run_download(monkeypatch, _tif_bytes(arr))
    np.testing.assert_array_equal(out, arr)
    assert out.dtype == np.float64
