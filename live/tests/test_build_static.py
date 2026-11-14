"""Source-level checks for build_static (GEE needs creds, so no live runs)."""
import types
from pathlib import Path

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


def _asset(name):
    return f"{ASSET_ROOT}/static/{STATIC_DOMAIN}/{name}"
