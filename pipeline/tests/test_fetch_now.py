"""Offline tests for the Modo Agora input fetchers.

fetch.imerg / fetch.smap / fetch.geosfp — every remote surface is faked
(earthaccess, rasterio, requests, the unit/ingest contract callables); the
aggregation/gridding math runs on real numpy/xarray/scipy. No network, no
credentials, no writes outside tmp_path.
"""
import datetime as dt
import json
import os
import re
import subprocess
import sys
import types
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fetch import geosfp, imerg, smap

REPO = Path(__file__).resolve().parents[2]

_CREDS = ("EARTHDATA_TOKEN", "EARTHDATA_USERNAME", "EARTHDATA_PASSWORD",
          "EDL_USER", "EDL_PASS")
_ENV = (*_CREDS, "GEE_PROJECT", "NOW_GCS_BUCKET")
BOUNDS = (-58.0, -34.0, -48.0, -22.5)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))     # no ~/.netrc during tests


# --------------------------------------------------------------------------- #
# fakes
# --------------------------------------------------------------------------- #
def _fake_earthaccess(monkeypatch, granules=(), download_files=None):
    mod = types.ModuleType("earthaccess")
    calls = {"search": [], "login": 0, "login_strategies": [], "download": []}

    def search_data(**kw):
        calls["search"].append(kw)
        return list(granules)

    def login(**kw):
        calls["login"] += 1
        calls["login_strategies"].append(kw.get("strategy"))
        return object()

    def download(gs, dest):
        calls["download"].append((list(gs), str(dest)))
        return [str(f) for f in (download_files or [])]

    mod.search_data = search_data
    mod.login = login
    mod.download = download
    mod.calls = calls
    monkeypatch.setitem(sys.modules, "earthaccess", mod)
    return mod


class _FakeDst:
    def __init__(self, rec):
        self.rec = rec

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self.rec["mode"] == "w":
            Path(self.rec["path"]).parent.mkdir(parents=True, exist_ok=True)
            Path(self.rec["path"]).touch()     # fake a completed write
        return False

    def write(self, arr, idx):
        self.rec["bands"][idx] = np.asarray(arr, dtype=np.float32).tolist()


def _fake_rasterio(monkeypatch):
    """Module-level rasterio stub: records open() kwargs + band writes."""
    mod = types.ModuleType("rasterio")
    mod.writes = []

    def open_(path, mode="r", **kw):
        rec = {"path": str(path), "mode": mode, "kw": kw, "bands": {}}
        mod.writes.append(rec)
        return _FakeDst(rec)

    class _Env:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def drivers(self):
            return set()                      # force the GTiff fallback

    class _CRS:
        @staticmethod
        def from_user_input(v):
            return v

    mod.open = open_
    mod.Env = _Env
    crs = types.ModuleType("rasterio.crs")
    crs.CRS = _CRS
    tr = types.ModuleType("rasterio.transform")
    tr.from_origin = lambda w, n, x, y: ("from_origin", w, n, x, y)
    monkeypatch.setitem(sys.modules, "rasterio", mod)
    monkeypatch.setitem(sys.modules, "rasterio.crs", crs)
    monkeypatch.setitem(sys.modules, "rasterio.transform", tr)
    return mod


def _fake_requests(monkeypatch, handler):
    mod = types.ModuleType("requests")
    mod.calls = []

    class _Resp:
        def __init__(self, text):
            self.text = text

        def raise_for_status(self):
            pass

    def get(url, timeout=None):
        mod.calls.append(url)
        return _Resp(handler(url))

    mod.get = get
    monkeypatch.setitem(sys.modules, "requests", mod)
    return mod


def _fake_ingest(monkeypatch, module, manifest=None):
    """Patch the unit/ingest append_manifest on a fetch module.

    L10: there is no raster-ingest path — only the manifest contract is
    left; assets are recorded 'local-only'.
    """
    calls = {"manifest": []}

    def append(case, entry):
        calls["manifest"].append((case, entry))
        if manifest is not None:
            entries = []
            if manifest.exists():
                entries = json.loads(manifest.read_text())["entries"]
            entries.append(entry)
            manifest.write_text(json.dumps({"entries": entries}))
        return manifest

    monkeypatch.setattr(module, "append_manifest", append)
    return calls


def _halfhourly(start="2026-11-10", days=3, rate=2.0):
    """Synthetic IMERG half-hourly (time,lat,lon) mm/h DataArray."""
    times = np.arange(np.datetime64(start),
                      np.datetime64(start) + np.timedelta64(days, "D"),
                      np.timedelta64(30, "m"))
    return xr.DataArray(
        np.full((len(times), 2, 3), rate),
        dims=("time", "lat", "lon"),
        coords={"time": times, "lat": [-30.0, -29.0],
                "lon": [-52.0, -51.0, -50.0]},
        name="precipitation")


# --------------------------------------------------------------------------- #
# auth split — anonymous CMR search, credentialed download (imerg + smap)
# --------------------------------------------------------------------------- #
def test_imerg_search_is_anonymous(monkeypatch):
    ea = _fake_earthaccess(monkeypatch, granules=[{"id": "g1"}])
    out = imerg.search_granules(BOUNDS, dt.date(2026, 11, 14), days=3)
    assert out == [{"id": "g1"}]
    kw = ea.calls["search"][0]
    assert kw["short_name"] == "GPM_3IMERGHHE"
    assert kw["version"] == "07"
    assert kw["temporal"] == ("2026-11-11", "2026-11-15")
    assert kw["bounding_box"] == BOUNDS
    assert ea.calls["login"] == 0              # search never authenticates


def test_smap_search_is_anonymous(monkeypatch):
    ea = _fake_earthaccess(monkeypatch, granules=[{"id": "s1"}])
    out = smap.search_granules(BOUNDS, dt.date(2026, 11, 14), days_back=5)
    assert out == [{"id": "s1"}]
    kw = ea.calls["search"][0]
    assert kw["short_name"] == "SPL4SMGP"
    assert kw["version"] == "008"
    assert ea.calls["login"] == 0


def test_download_requires_credentials(monkeypatch, tmp_path):
    for mod in (imerg, smap):
        ea = _fake_earthaccess(monkeypatch,
                               download_files=[str(tmp_path / "g.h5")])
        with pytest.raises(RuntimeError, match="EARTHDATA_TOKEN"):
            mod.download_granules(["g"], tmp_path)
        assert ea.calls["login"] == 0
        assert ea.calls["download"] == []


def test_download_with_token(monkeypatch, tmp_path):
    for mod in (imerg, smap):
        ea = _fake_earthaccess(monkeypatch,
                               download_files=[str(tmp_path / "g.h5")])
        monkeypatch.setenv("EARTHDATA_TOKEN", "tok")
        out = mod.download_granules(["g"], tmp_path)
        assert out == [tmp_path / "g.h5"]
        assert ea.calls["login"] == 1
        assert ea.calls["download"][0][0] == ["g"]


def test_download_edl_fallback(monkeypatch, tmp_path):
    ea = _fake_earthaccess(monkeypatch, download_files=[str(tmp_path / "g.h5")])
    monkeypatch.setenv("EDL_USER", "u")
    monkeypatch.setenv("EDL_PASS", "p")
    imerg.download_granules(["g"], tmp_path)
    assert ea.calls["login"] == 1
    assert os.environ["EARTHDATA_USERNAME"] == "u"   # mapped for earthaccess
    assert os.environ["EARTHDATA_PASSWORD"] == "p"


def test_download_partial_creds_rejected(monkeypatch, tmp_path):
    """EDL_USER without EDL_PASS (or a lone EARTHDATA_USERNAME) is not enough."""
    ea = _fake_earthaccess(monkeypatch,
                           download_files=[str(tmp_path / "g.h5")])
    monkeypatch.setenv("EDL_USER", "u")                     # EDL_PASS missing
    with pytest.raises(RuntimeError, match="EDL_USER"):
        imerg.download_granules(["g"], tmp_path)
    monkeypatch.delenv("EDL_USER")
    monkeypatch.setenv("EARTHDATA_USERNAME", "u")           # password missing
    with pytest.raises(RuntimeError, match="EDL_USER"):
        smap.download_granules(["g"], tmp_path)
    assert ea.calls["login"] == 0
    assert ea.calls["download"] == []


def test_download_netrc_fallback(monkeypatch, tmp_path):
    (tmp_path / ".netrc").write_text("machine urs.earthdata.nasa.gov\n")
    ea = _fake_earthaccess(monkeypatch,
                           download_files=[str(tmp_path / "g.h5")])
    imerg.download_granules(["g"], tmp_path)
    assert ea.calls["login_strategies"] == ["netrc"]


# --------------------------------------------------------------------------- #
# IMERG aggregation math
# --------------------------------------------------------------------------- #
def test_imerg_aggregation_math():
    daily, rain3, steps = imerg.daily_and_3day(_halfhourly(rate=2.0))
    # 2 mm/h * 0.5 * 48 steps = 48 mm/day; 3-day total = 144 mm
    assert daily.sizes["time"] == 3
    np.testing.assert_allclose(daily.values, np.full((3, 2, 3), 48.0))
    assert np.isnan(rain3.values[:2]).all()
    np.testing.assert_allclose(rain3.values[2], np.full((2, 3), 144.0))
    np.testing.assert_array_equal(steps.values, np.full(3, 48))


def test_imerg_partial_day_counted():
    da = _halfhourly(days=3).isel(time=slice(None, -24))   # last day half in
    daily, _, steps = imerg.daily_and_3day(da)
    assert steps.values.tolist() == [48, 48, 24]
    np.testing.assert_allclose(daily.values[2], np.full((2, 3), 24.0))


def test_imerg_open_halfhourly_subsets(monkeypatch):
    """Group='Grid' open, 'precipitation' var, bounds clip."""
    seen = {}

    def fake_open_dataset(path, group=None, engine=None, decode_times=True):
        seen["group"] = group
        lats = np.linspace(-60, -20, 9)     # ascending, 5 deg step
        lons = np.linspace(-60, -40, 5)
        da = xr.DataArray(
            np.ones((1, 9, 5)), dims=("time", "lat", "lon"),
            coords={"time": [np.datetime64("2026-11-10")],
                    "lat": lats, "lon": lons})
        return xr.Dataset({"precipitation": da})

    fake_xr = types.ModuleType("xarray")
    fake_xr.open_dataset = fake_open_dataset
    monkeypatch.setitem(sys.modules, "xarray", fake_xr)
    out = imerg.open_halfhourly(["a.h5"], (-55.0, -50.0, -51.0, -40.0))
    assert seen["group"] == "Grid"
    assert out.lat.values.tolist() == [-50.0, -45.0, -40.0]
    assert out.lon.values.tolist() == [-55.0]


# --------------------------------------------------------------------------- #
# COG writer + asset plumbing
# --------------------------------------------------------------------------- #
def test_write_cog_north_up(tmp_path, monkeypatch):
    rio = _fake_rasterio(monkeypatch)
    vals = np.array([[1, 2], [3, 4]], dtype=np.float32)   # rows: lat -30, -29
    imerg.write_cog(tmp_path / "x.tif", vals, [-30.0, -29.0], [-52.0, -51.0])
    rec = rio.writes[0]
    assert rec["kw"]["driver"] == "GTiff"                 # Env drivers was empty
    assert rec["kw"]["transform"] == ("from_origin", -52.5, -28.5, 1.0, 1.0)
    assert rec["bands"][1] == [[3.0, 4.0], [1.0, 2.0]]    # flipped north-up
    assert rec["kw"]["crs"] == "EPSG:4326"
    assert rec["kw"]["count"] == 1
    assert rec["kw"]["dtype"] == "float32"
    assert np.isnan(rec["kw"]["nodata"])


def test_now_bounds_resolution(monkeypatch, capsys):
    """Ruling L9: default AOI = config.yaml domains.now via tp.config;
    --bounds always wins."""
    import tp.config
    # config domains.now is the source of truth
    fake = (-55.5, -33.8, -48.0, -27.0)
    monkeypatch.setattr(tp.config, "now_bounds", lambda: fake, raising=False)
    for mod in (imerg, smap, geosfp):
        assert mod.now_bounds() == fake
    # --bounds still overrides the config source
    geosfp.main(["--date", "2026-11-14", "--bounds",
                 "-60", "-35", "-50", "-25", "--dry-run"])
    plan = json.loads(capsys.readouterr().out)
    assert plan["bounds"] == [-60.0, -35.0, -50.0, -25.0]


def test_asset_ids():
    assert imerg.asset_id("p", "20261114") == (
        "projects/p/assets/tipping-point/now/imerg_early/20261114")
    assert smap.asset_id("p", "20261114") == (
        "projects/p/assets/tipping-point/now/smap_l4/20261114")
    assert geosfp.asset_id("p", "20261115") == (
        "projects/p/assets/tipping-point/now/geosfp/20261115")


def test_manifest_asset_ids(tmp_path):
    p = tmp_path / "assets.json"
    assert imerg.manifest_asset_ids(p) == set()
    p.write_text(json.dumps({"entries": [
        {"asset_id": "projects/x/assets/tipping-point/now/imerg_early/20261114"},
        {"nested": {"id": "other"}},
    ]}))
    assert imerg.manifest_asset_ids(p) == {
        "projects/x/assets/tipping-point/now/imerg_early/20261114", "other"}


def test_stage_and_ingest_records_local_only(tmp_path, monkeypatch,
                                             capsys):
    """L10: no raster-ingest path — assets are recorded 'local-only' with
    a warning, still idempotent via assets.json."""
    cog = tmp_path / "imerg_early_20261114.tif"
    cog.write_bytes(b"x")
    manifest = tmp_path / "assets.json"
    calls = _fake_ingest(monkeypatch, imerg, manifest)

    a1 = [{"tag": "20261114", "cog": cog}]
    imerg.stage_and_ingest(a1, project="proj", assets_json=manifest)
    assert a1[0]["status"] == "local-only"
    assert "not supported without a GCS bucket (L10)" in \
        capsys.readouterr().err
    case, entry = calls["manifest"][0]
    assert case == "now"
    assert (entry["asset_id"] ==
            "projects/proj/assets/tipping-point/now/imerg_early/20261114")
    assert entry["status"] == "local-only"
    assert "gcs_uri" not in entry

    a2 = [{"tag": "20261114", "cog": cog}]
    imerg.stage_and_ingest(a2, project="proj", assets_json=manifest)
    assert a2[0]["status"] == "skipped_existing"     # idempotent via manifest
    assert len(calls["manifest"]) == 1

    a3 = [{"tag": "20261114", "cog": cog}]
    imerg.stage_and_ingest(a3, project="proj", force=True,
                           assets_json=manifest)
    assert a3[0]["status"] == "local-only"
    assert len(calls["manifest"]) == 2

    a4 = [{"tag": "20261115", "cog": cog}]
    imerg.stage_and_ingest(a4, project="proj", ingest=False,
                           assets_json=manifest)
    assert a4[0]["status"] == "planned"


def test_stage_and_ingest_missing_cog(tmp_path):
    miss = [{"tag": "t", "cog": tmp_path / "nope.tif"}]
    smap.stage_and_ingest(miss, project="p",
                          assets_json=tmp_path / "m.json")
    assert miss[0]["status"] == "missing_cog"


def test_scan_cogs(tmp_path):
    (tmp_path / "imerg_early_20261101.tif").write_bytes(b"x")
    (tmp_path / "imerg_early_20261102_3day.tif").write_bytes(b"x")
    (tmp_path / "smap_l4_20261102.tif").write_bytes(b"x")
    got = imerg.scan_cogs(tmp_path)
    assert [a["tag"] for a in got] == ["20261101", "20261102_3day"]
    assert [a["product"] for a in got] == ["daily", "3day"]


# --------------------------------------------------------------------------- #
# end-to-end run() per module (fetch patched, rasterio + ingest faked)
# --------------------------------------------------------------------------- #
def test_imerg_run_end_to_end(tmp_path, monkeypatch):
    rio = _fake_rasterio(monkeypatch)
    manifest = tmp_path / "assets.json"
    calls = _fake_ingest(monkeypatch, imerg, manifest)
    monkeypatch.setattr(
        imerg, "fetch_daily",
        lambda b, e, d, w: imerg.daily_and_3day(_halfhourly()))
    assets = imerg.run(dt.date(2026, 11, 12), BOUNDS, 3,
                       out_dir=tmp_path / "cog", project="proj",
                       assets_json=manifest)
    assert [a["tag"] for a in assets] == [
        "20261110", "20261111", "20261112", "20261112_3day"]
    assert all(a["status"] == "local-only" for a in assets)
    assert assets[0]["source"]["hh_steps"] == 48
    names = sorted(Path(w["path"]).name for w in rio.writes)
    assert names == ["imerg_early_20261110.tif", "imerg_early_20261111.tif",
                     "imerg_early_20261112.tif", "imerg_early_20261112_3day.tif"]
    assert len(calls["manifest"]) == 4
    written = json.loads(manifest.read_text())["entries"]
    assert written[-1]["asset_id"].endswith("imerg_early/20261112_3day")

    # second run: same asset ids -> everything skipped
    assets2 = imerg.run(dt.date(2026, 11, 12), BOUNDS, 3,
                        out_dir=tmp_path / "cog",
                        project="proj", assets_json=manifest)
    assert {a["status"] for a in assets2} == {"skipped_existing"}
    assert len(calls["manifest"]) == 4


def test_smap_open_rootzone_real_layout(tmp_path):
    """SPL4SMGP v008 layout: sm under Geophysical_Data, cell_lat/lon at
    root (the KeyError hit on a real granule, u15/live/fetch.py fix)."""
    h5py = pytest.importorskip("h5py")
    f = tmp_path / "SMAP_L4_SM_gph_fake.h5"
    with h5py.File(f, "w") as h:
        g = h.create_group("Geophysical_Data")
        g.create_dataset("sm_rootzone_wetness",
                         data=np.array([[0.4, 0.5], [0.6, np.nan]],
                                       dtype=np.float32))
        h.create_dataset("cell_lat", data=np.array(
            [[-30.0, -30.0], [-31.0, -31.0]], dtype=np.float64))
        h.create_dataset("cell_lon", data=np.array(
            [[-52.0, -51.0], [-52.0, -51.0]], dtype=np.float64))
    lats, _lons, vals = smap.open_rootzone(f, BOUNDS)
    assert lats.size == 4 and np.isfinite(vals).sum() == 3
    # outside the bbox -> nothing
    far = smap.open_rootzone(f, (10.0, 10.0, 20.0, 20.0))
    assert far[0].size == 0


def test_smap_run_end_to_end(tmp_path, monkeypatch):
    _fake_rasterio(monkeypatch)
    manifest = tmp_path / "assets.json"
    calls = _fake_ingest(monkeypatch, smap, manifest)
    monkeypatch.setattr(
        smap, "fetch_latest", lambda *a: (
            np.array([-30.0, -29.0]), np.array([-52.0, -51.0]),
            np.full((2, 2), 0.66),
            {"product_date": "2026-11-13", "native_id": "SMAP_L4_SM_x"}))
    assets = smap.run(dt.date(2026, 11, 14), BOUNDS, 10,
                      out_dir=tmp_path / "cog", project="proj",
                      assets_json=manifest)
    assert [a["tag"] for a in assets] == ["20261113"]
    assert assets[0]["status"] == "local-only"
    assert calls["manifest"][0][1]["asset_id"].endswith("smap_l4/20261113")


def test_geosfp_run_end_to_end(tmp_path, monkeypatch):
    _fake_rasterio(monkeypatch)
    manifest = tmp_path / "assets.json"
    calls = _fake_ingest(monkeypatch, geosfp, manifest)
    days = [dt.date(2026, 11, 15), dt.date(2026, 11, 16), dt.date(2026, 11, 17)]
    monkeypatch.setattr(
        geosfp, "fetch_daily", lambda *a: (
            np.array([-30.0, -29.75]), np.array([-52.0, -51.75]),
            [np.full((2, 2), k + 1.0) for k in range(3)], days,
            "2026-11-14T00:00:00Z", "tavg1_2d_flx_Nx.20261114_00"))
    assets = geosfp.run(dt.date(2026, 11, 14), BOUNDS, 3,
                        out_dir=tmp_path / "cog", project="proj",
                        assets_json=manifest)
    assert [a["tag"] for a in assets] == ["20261115", "20261116", "20261117"]
    assert [a["source"]["lead"] for a in assets] == [1, 2, 3]
    assert all(a["status"] == "local-only" for a in assets)
    assert len(calls["manifest"]) == 3


def test_no_download_scans_cogs(tmp_path, monkeypatch):
    _fake_rasterio(monkeypatch)
    manifest = tmp_path / "assets.json"
    _fake_ingest(monkeypatch, geosfp, manifest)
    out = tmp_path / "cog"
    out.mkdir()
    (out / "geosfp_20261115.tif").write_bytes(b"x")
    (out / "geosfp_20261116.tif").write_bytes(b"x")
    assets = geosfp.run(dt.date(2026, 11, 14), BOUNDS, 3, out_dir=out,
                        project="p", download=False,
                        assets_json=manifest)
    assert [a["tag"] for a in assets] == ["20261115", "20261116"]
    assert all(a["status"] == "local-only" for a in assets)


# --------------------------------------------------------------------------- #
# --case hindcast: per-granule half-hourly images under a case collection
# --------------------------------------------------------------------------- #
def _fake_ee_storage(monkeypatch, store=None):
    """Fake ee (data.getAsset/createAsset/startIngestion backed by `store`)
    + fake google.cloud.storage; returns (calls, uploads, store)."""
    calls = {"getAsset": [], "createAsset": [], "startIngestion": [],
             "init": []}
    store = store if store is not None else {}
    ee = types.ModuleType("ee")

    def get_asset(aid):
        calls["getAsset"].append(aid)
        if aid not in store:
            raise RuntimeError(f"{aid} not found")
        return store[aid]

    def create_asset(value, path=None, **kw):
        calls["createAsset"].append((value, path))
        store[path] = value
        return {"name": path}

    def start_ingestion(rid, params):
        calls["startIngestion"].append(params)
        store[params["name"]] = {"type": "IMAGE"}
        return {"name": "op-1"}

    ee.data = types.SimpleNamespace(
        getAsset=get_asset, createAsset=create_asset,
        startIngestion=start_ingestion,
        startTableIngestion=lambda *a, **kw: None)
    ee.Initialize = lambda **kw: calls["init"].append(kw)
    ee.ServiceAccountCredentials = (
        lambda *a, **kw: types.SimpleNamespace(args=(a, kw)))
    monkeypatch.setitem(sys.modules, "ee", ee)
    monkeypatch.delitem(sys.modules, "gee.common", raising=False)

    uploads = []
    stor = types.ModuleType("google.cloud.storage")

    class _Blob:
        def __init__(self, bucket, name):
            self._b, self._n = bucket, name

        def upload_from_filename(self, filename):
            uploads.append((self._b, self._n, filename))

    class _Bucket:
        def __init__(self, name):
            self._n = name

        def blob(self, name):
            return _Blob(self._n, name)

    class Client:
        def bucket(self, name):
            return _Bucket(name)

    stor.Client = Client
    monkeypatch.setitem(sys.modules, "google.cloud.storage", stor)
    return calls, uploads, store


def _granule_da(rate=1.5):
    """One-step (time,lat,lon) mm/h array standing in for a granule."""
    return xr.DataArray(
        np.full((1, 2, 3), rate), dims=("time", "lat", "lon"),
        coords={"time": [np.datetime64("2024-05-01T00:00")],
                "lat": [-30.0, -29.0], "lon": [-52.0, -51.0, -50.0]})


def _case_setup(monkeypatch, tmp_path, n=3):
    """N fake IMERG granules (half-hourly names), fake ee + storage, real
    ingest callables under fake creds. Returns (calls, uploads, store, ea)."""
    names = [
        f"3B-HHR-E.MS.MRG.3IMERG.20240501-S{hh:02d}{mm:02d}00-E{hh:02d}{mm + 29:02d}59.{i:04d}.V07B.HDF5"
        for i, (hh, mm) in enumerate([(0, 0), (0, 30), (1, 0)][:n])]
    granules = [{"producer_granule_id": nm} for nm in names]
    files = [tmp_path / "raw" / f"g{i}.h5" for i in range(n)]
    ea = _fake_earthaccess(monkeypatch, granules=granules,
                           download_files=[str(f) for f in files])
    monkeypatch.setenv("EARTHDATA_TOKEN", "tok")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/sa.json")
    monkeypatch.setenv("GEE_PROJECT", "proj")
    for var in ("GCP_SA_KEY", "GEE_SERVICE_ACCOUNT_KEY"):
        monkeypatch.delenv(var, raising=False)
    _fake_rasterio(monkeypatch)
    calls, uploads, store = _fake_ee_storage(monkeypatch)
    monkeypatch.setattr(imerg, "open_halfhourly", lambda paths, b: _granule_da())
    import tp.events
    monkeypatch.setattr(tp.events, "DATA_DIR", tmp_path / "data")
    return calls, uploads, store, ea


def test_granule_start_tag():
    assert imerg.granule_start_tag({"producer_granule_id":
        "3B-HHR-E.MS.MRG.3IMERG.20240501-S003000-E005959.0030.V07B.HDF5"}
        ) == "20240501T0030"
    assert imerg.granule_start_tag({"umm": {"TemporalExtent": {"RangeDateTime":
        {"BeginningDateTime": "2024-05-01T01:30:00.000Z"}}}}
        ) == "20240501T0130"
    with pytest.raises(ValueError):
        imerg.granule_start_tag({"id": "no-time-here"})


def test_imerg_case_run_creates_collection_and_children(tmp_path, monkeypatch):
    calls, uploads, _, ea = _case_setup(monkeypatch, tmp_path)
    assets = imerg.run_case(
        "rs-2024-05", dt.date(2024, 5, 1), dt.date(2024, 5, 2),
        out_dir=tmp_path / "cog",
        assets_json=tmp_path / "assets.json")
    parent = "projects/proj/assets/tipping-point/rs-2024-05/imerg_early"
    # --start/--end window honored (+1 day end-exclusive convention)
    assert ea.calls["search"][0]["temporal"] == ("2024-05-01", "2024-05-03")
    # aoi read from events/rs-2024-05.json (no --bounds given)
    assert ea.calls["search"][0]["bounding_box"] == (-52.5, -30.5, -50.5, -29.0)
    # IMAGE_COLLECTION container created exactly once, before children
    assert calls["createAsset"] == [({"type": "IMAGE_COLLECTION"}, parent)]
    # L10: no raster ingest and no GCS staging — children stay local-only
    assert calls["startIngestion"] == []
    assert uploads == []
    # per-granule asset ids are the granule start time YYYYMMDDThhmm
    assert [a["tag"] for a in assets] == [
        "20240501T0000", "20240501T0030", "20240501T0100"]
    assert [a["asset_id"] for a in assets] == [
        f"{parent}/20240501T0000",
        f"{parent}/20240501T0030",
        f"{parent}/20240501T0100"]
    assert all(a["status"] == "local-only" for a in assets)


def test_imerg_case_run_idempotent_second_run(tmp_path, monkeypatch):
    calls, _, _, _ = _case_setup(monkeypatch, tmp_path)
    kw = {"out_dir": tmp_path / "cog",
          "assets_json": tmp_path / "assets.json"}
    imerg.run_case("rs-2024-05", dt.date(2024, 5, 1), dt.date(2024, 5, 2), **kw)
    imerg.run_case("rs-2024-05", dt.date(2024, 5, 1), dt.date(2024, 5, 2), **kw)
    assert len(calls["createAsset"]) == 1        # collection made once
    assert calls["startIngestion"] == []


def test_imerg_case_bounds_override(tmp_path, monkeypatch):
    _, _, _, ea = _case_setup(monkeypatch, tmp_path)
    b = (-53.0, -30.0, -51.0, -29.0)
    imerg.run_case("rs-2024-05", dt.date(2024, 5, 1), dt.date(2024, 5, 1),
                   bounds=b, out_dir=tmp_path / "cog",
                   assets_json=tmp_path / "assets.json")
    assert ea.calls["search"][0]["bounding_box"] == b


def test_imerg_case_dry_run(tmp_path, monkeypatch, capsys):
    imerg.main(["--case", "rs-2024-05", "--start", "2024-05-01",
                "--end", "2024-05-02", "--out-dir", str(tmp_path),
                "--dry-run"])
    out = json.loads(capsys.readouterr().out)
    assert out["case"] == "rs-2024-05"
    assert out["collection"].endswith("tipping-point/rs-2024-05/imerg_early")
    assert out["window"] == ["2024-05-01", "2024-05-02"]
    assert out["bounds"] == [-52.5, -30.5, -50.5, -29.0]
    assert out["granule_asset"].endswith("/<yyyymmddThhmm>")


def test_imerg_case_defaults_to_events_window(tmp_path, monkeypatch, capsys):
    """--case alone uses events/<case>.json window + aoi."""
    imerg.main(["--case", "rs-2024-05", "--out-dir", str(tmp_path),
                "--dry-run"])
    out = json.loads(capsys.readouterr().out)
    assert out["window"] == ["2024-04-20", "2024-05-31"]


def test_start_end_without_case_rejected(capsys):
    with pytest.raises(SystemExit):
        imerg.main(["--start", "2024-05-01", "--end", "2024-05-02"])


# --------------------------------------------------------------------------- #
# SMAP helpers
# --------------------------------------------------------------------------- #
def test_smap_product_date():
    g = {"meta": {"native-id": "SMAP_L4_SM_gph_20261113T013000_Vv8030_001.h5"}}
    assert smap.product_date(g, dt.date(2000, 1, 1)) == dt.date(2026, 11, 13)
    assert smap.product_date({"meta": {}}, dt.date(2000, 1, 1)) == dt.date(2000, 1, 1)


def test_smap_grid_to_regular_marks_gaps():
    la = np.linspace(-32, -30, 5)
    lo = np.linspace(-53, -51, 5)
    LA, LO = np.meshgrid(la, lo, indexing="ij")
    vals = np.full((5, 5), 0.7)
    vals[2, 2] = np.nan                                  # interior hole
    glat, glon, grid = smap.grid_to_regular(
        LA.ravel(), LO.ravel(), vals.ravel(),
        (-54.0, -33.0, -50.0, -29.0), res=0.25)
    assert grid.shape == (glat.size, glon.size)
    assert np.isnan(grid).any()                          # gap propagates
    i = int(np.argmin(np.abs(glat - (-32.9))))
    j = int(np.argmin(np.abs(glon - (-53.9))))
    assert grid[i, j] == pytest.approx(0.7, abs=0.05)    # corner interpolates


# --------------------------------------------------------------------------- #
# GEOS-FP GDS '.ascii' client (fake HTTP, real parsers)
# --------------------------------------------------------------------------- #
def _gds_stamp(t: dt.datetime) -> float:
    return (t.date().toordinal() + 1
            + (t.hour * 3600 + t.minute * 60 + t.second) / 86400)


def _gds_vector(name, vals):
    return f"{name}, [{len(vals)}]\n" + ",".join(f"{v:.6f}" for v in vals) + "\n"


def _gds_grid(nt, nla, nlo, rate):
    lines = [f"prectot, [{nt}][{nla}][{nlo}]"]
    for t in range(nt):
        for la in range(nla):
            lines.append(f"[{t}][{la}] , " + ",".join([str(rate)] * nlo))
    return "\n".join(lines)


def _gds_handler(times_by_run, lats, lons, rate):
    def handler(url):
        if url.endswith("/tavg1_2d_flx_Nx/"):
            return "\n".join(f'<a href="{r}">{r}</a>' for r in times_by_run)
        m = re.search(r"(tavg1_2d_flx_Nx\.\d{8}_\d{2})\.ascii\?(.*)", url)
        assert m, url
        run, q = m.groups()
        if q == "prectot.time":
            return _gds_vector("time",
                               [_gds_stamp(t) for t in times_by_run[run]])
        if q == "prectot.lat,prectot.lon":
            return _gds_vector("lat", lats) + _gds_vector("lon", lons)
        if q.startswith("prectot.prectot"):
            g = re.search(r"\[(\d+):1:(\d+)\]\[(\d+):1:(\d+)\]"
                          r"\[(\d+):1:(\d+)\]", q)
            t0, t1, la0, la1, lo0, lo1 = map(int, g.groups())
            return _gds_grid(t1 - t0 + 1, la1 - la0 + 1, lo1 - lo0 + 1, rate)
        raise AssertionError(url)
    return handler


def test_gds_parse_grid_and_times():
    arr = geosfp._gds_parse_grid(
        "prectot, [1][2][3]\n[0][0] , 1e15,0.5,0.5\n[0][1] , 0.5,0.5,0.5\n")
    assert arr.shape == (1, 2, 3)
    assert arr[0, 0, 0] == 1e15
    t = geosfp._gds_times(np.array([_gds_stamp(dt.datetime(2026, 11, 13, 0, 30))]))
    assert t == [dt.datetime(2026, 11, 13, 0, 30)]


def test_geosfp_fetch_daily(monkeypatch):
    issued = dt.date(2026, 11, 13)
    good = [dt.datetime(2026, 11, 13, 0, 30) + dt.timedelta(hours=h)
            for h in range(96)]                       # through 11-17 00:30
    short = [dt.datetime(2026, 11, 14, 0, 30) + dt.timedelta(hours=h)
             for h in range(24)]                      # ends 11-14: insufficient
    runs = {"tavg1_2d_flx_Nx.20261114_00": short,     # newest, but too short
            "tavg1_2d_flx_Nx.20261113_00": good}
    req = _fake_requests(monkeypatch,
                         _gds_handler(runs, [-34.0, -33.0, -32.0],
                                      [-53.0, -52.0, -51.0], 0.0001))
    _, _, slabs, days, issued_iso, run = geosfp.fetch_daily(
        BOUNDS, issued, 3)
    assert run == "tavg1_2d_flx_Nx.20261113_00"       # fell back past 11-14 run
    assert days == [dt.date(2026, 11, 14), dt.date(2026, 11, 15),
                    dt.date(2026, 11, 16)]
    for slab in slabs:
        np.testing.assert_allclose(slab, np.full((3, 3), 8.64), rtol=1e-6)
    assert issued_iso == "2026-11-13T00:00:00Z"
    assert not any("prectot.prectot" in u for u in req.calls
                   if "20261114_00" in u)             # rejected before grid pull


# --------------------------------------------------------------------------- #
# --dry-run: plan JSON only, no network/creds/writes
# --------------------------------------------------------------------------- #
def test_imerg_dry_run(capsys):
    imerg.main(["--date", "2026-11-14", "--days", "3", "--dry-run",
                "--out-dir", "/tmp/now-x"])
    plan = json.loads(capsys.readouterr().out)
    assert plan["collection"].endswith("tipping-point/now/imerg_early")
    assert plan["short_name"] == "GPM_3IMERGHHE.07"
    tags = [a["tag"] for a in plan["assets"]]
    assert tags == ["20261114", "20261114_3day", "20261113", "20261112"]
    assert plan["assets"][0]["asset_id"] == (
        "projects/<GEE_PROJECT>/assets/tipping-point/now/imerg_early/20261114")


def test_smap_dry_run(capsys):
    smap.main(["--date", "2026-11-13", "--dry-run"])
    plan = json.loads(capsys.readouterr().out)
    assert plan["collection"].endswith("tipping-point/now/smap_l4")
    assert [a["tag"] for a in plan["assets"]] == ["20261113"]


def test_geosfp_dry_run(capsys):
    geosfp.main(["--date", "2026-11-14", "--days", "3", "--dry-run"])
    plan = json.loads(capsys.readouterr().out)
    assert plan["collection"].endswith("tipping-point/now/geosfp")
    assert plan["auth"] == "none — public GDS HTTP"
    assert [a["lead"] for a in plan["assets"]] == [1, 2, 3]
    assert [a["tag"] for a in plan["assets"]] == [
        "20261115", "20261116", "20261117"]


def test_module_dry_run_subprocess():
    """The documented `python -m pipeline.fetch.* --dry-run` entry point."""
    r = subprocess.run(
        [sys.executable, "-m", "pipeline.fetch.geosfp",
         "--date", "2026-11-14", "--dry-run"],
        check=False, capture_output=True, text=True, cwd=REPO)
    assert r.returncode == 0, r.stderr
    plan = json.loads(r.stdout)
    assert plan["collection"].endswith("tipping-point/now/geosfp")
