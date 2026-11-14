"""Source-level checks for build_static (GEE needs creds, so no live runs)."""
import datetime as dt
import io
import sys
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


def _expected_clim():
    """(collection, [doys]) pairs build_assets should export today."""
    pairs = [
        (f"{ASSET_ROOT}/static/{c['id']}/clim_rain", c["doys"], c["id"],
         list(c["bbox"]))
        for c in build_static._clim_cases()]
    now_doys = sorted(build_static._doys_around(
        dt.date.today(), build_static.NOW_PAD_DAYS))
    pairs.append((_asset("clim_rain"), now_doys, STATIC_DOMAIN,
                  list(build_static.NOW_BOUNDS)))
    return pairs


def test_build_assets_exports_clim_collections_and_terrain():
    ee = _fake_ee()
    tasks = build_static.build_assets(ee)

    expected = _expected_clim()
    n_clim = sum(len(doys) for _, doys, _, _ in expected)

    colls = [(coll, doys) for coll, doys, _, _ in expected]
    assert ee._created == [
        ({"type": "ImageCollection"}, coll) for coll, _ in colls]

    # clim subset + slope + hand
    assert len(tasks) == n_clim + 2
    assert all(t.started for t in tasks)

    clim = tasks[:n_clim]
    expected_tasks = [
        (f"{coll}/doy_{d:03d}", d, f"{domain}_clim_rain_doy_{d:03d}", bbox)
        for coll, doys, domain, bbox in expected for d in doys]
    assert [(t.kwargs["assetId"], t.image.props["doy"],
             t.kwargs["description"], t.kwargs["region"][1])
            for t in clim] == expected_tasks
    # int16 millimetres*10: image expression does .toInt16(), props flag it
    assert all(t.image.props["scale"] == 10 for t in clim)
    assert ".toInt16()" in SRC
    assert all(t.kwargs["scale"] == build_static.ASSET_CLIM_SCALE_M
               for t in clim)

    by_desc = {t.kwargs["description"]: t for t in tasks[n_clim:]}
    assert set(by_desc) == {"slope", "hand"}
    assert by_desc["slope"].kwargs["assetId"] == _asset("slope")
    assert by_desc["hand"].kwargs["assetId"] == _asset("hand")
    assert all(t.kwargs["scale"] == build_static.ASSET_TERRAIN_SCALE_M
               for t in tasks[n_clim:])
    # population is npz-only — never an asset
    assert not any("pop" in t.kwargs["assetId"] for t in tasks)


def test_build_assets_dry_run_starts_nothing():
    ee = _fake_ee()
    tasks = build_static.build_assets(ee, dry_run=True)
    n_clim = sum(len(doys) for _, doys, _, _ in _expected_clim())
    assert len(tasks) == n_clim + 2
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

    n_clim = sum(len(doys) for _, doys, _, _ in _expected_clim())
    expected_md = {
        (d.month, d.day) for _, doys, _, _ in _expected_clim() for d in
        (dt.date(2000, 1, 1) + dt.timedelta(days=doy - 1) for doy in doys)}
    assert {(m, d) for _, m, d in calls} == expected_md
    assert {y for y, _, _ in calls} == set(build_static.YEARS)
    # one fromYMD per (year, offset) per exported doy
    assert len(calls) == n_clim * len(list(build_static.YEARS)) * len(
        list(build_static.OFFSETS))


def test_clim_cases_reads_window_doys(tmp_path):
    """_clim_cases: doys = doy(window.start)..doy(window.end) inclusive,
    wrapping across the year boundary, region = aoi.bbox."""
    import json

    (tmp_path / "a.json").write_text(json.dumps({
        "id": "x-case", "aoi": {"bbox": [1, 2, 3, 4]},
        "window": {"start": "2024-04-20", "end": "2024-05-31"}}))
    (tmp_path / "b.json").write_text(json.dumps({
        "id": "y-wrap", "aoi": {"bbox": [5, 6, 7, 8]},
        "window": {"start": "2025-11-30", "end": "2026-01-15"}}))
    (tmp_path / "c.json").write_text(json.dumps({
        "id": "z-sameday", "aoi": {"bbox": [0, 0, 0, 0]},
        "window": {"start": "2024-06-10", "end": "2024-06-10"}}))

    cases = build_static._clim_cases(tmp_path)
    assert [c["id"] for c in cases] == ["x-case", "y-wrap", "z-sameday"]
    assert cases[0]["bbox"] == [1, 2, 3, 4]
    # Apr20..May31 = 42 doys, contiguous on the leap-year calendar
    assert cases[0]["doys"] == list(range(111, 153))
    # Nov30..Jan15 wraps: 335..366 then 1..15
    assert cases[1]["doys"] == list(range(335, 367)) + list(range(1, 16))
    assert cases[2]["doys"] == [162]


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


def test_ee_init_returns_ee_module_not_project_string(monkeypatch):
    calls = []
    fake_common = types.ModuleType("gee.common")
    fake_common.ee_init = lambda: calls.append("ee_init") or "fake-project"
    fake_ee = types.ModuleType("ee")
    monkeypatch.setitem(sys.modules, "gee", types.ModuleType("gee"))
    monkeypatch.setitem(sys.modules, "gee.common", fake_common)
    monkeypatch.setitem(sys.modules, "ee", fake_ee)

    assert build_static._ee_init() is fake_ee
    assert calls == ["ee_init"]

    init_calls = []
    fake_ee.ServiceAccountCredentials = lambda email, key_file: ("creds", email)
    fake_ee.Initialize = lambda creds, project=None: init_calls.append((creds, project))
    monkeypatch.setitem(sys.modules, "gee.common", None)
    monkeypatch.setenv("GCP_SA_KEY", '{"client_email": "sa@example.com"}')
    monkeypatch.setenv("GEE_PROJECT", "p")

    assert build_static._ee_init() is fake_ee
    assert init_calls == [(("creds", "sa@example.com"), "p")]
