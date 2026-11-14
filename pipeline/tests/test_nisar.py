import json
import os
import subprocess
import sys
import types
from pathlib import Path
from typing import ClassVar

import numpy as np
import pytest
import tp.nisar as nisar_mod
from tp import events as events_mod
from tp.events import load_event
from tp.nisar import (
    _granule_meta,
    download_plan,
    execute_plan,
    gdal_subset_cmd,
    pick_pairs,
)

GCOV_NAMES = [
    "NISAR_L2_PR_GCOV_006_172_D_064_4005_DHDH_A_20251204T031815_D0",
    "NISAR_L2_PR_GCOV_007_172_D_064_4005_DHDH_A_20251216T031815_D0",
    "NISAR_L2_PR_GCOV_005_172_D_064_4005_DHDH_A_20251122T031815_D0",
    "NISAR_L2_PR_GCOV_001_005_A_026_4005_DHDH_A_20251204T000000_D0",
    "NISAR_L2_PR_GCOV_002_005_A_026_4005_DHDH_A_20251228T000000_D0",
]


def test_granule_meta_parse():
    m = _granule_meta(GCOV_NAMES[0])
    assert m == {
        "product": "GCOV", "track": 172, "look": "D",
        "frame": 64, "date": "2025-12-04",
    }
    assert _granule_meta("not_a_granule") is None


def test_pick_pairs_same_frame():
    pairs = pick_pairs(GCOV_NAMES, "2025-12-12")
    assert set(pairs) == {"172D_064", "005A_026"}
    pre, post = pairs["172D_064"]["pre"], pairs["172D_064"]["post"]
    assert "20251204" in pre and "20251216" in post


def test_pick_pairs_no_post():
    pairs = pick_pairs(GCOV_NAMES, "2026-06-01")
    assert pairs == {}


class _G:
    def __init__(self, native_id):
        self.meta = {"native-id": native_id}


def test_pick_pairs_objects():
    pairs = pick_pairs([_G(n) for n in GCOV_NAMES[:2]], "2025-12-12")
    assert pairs["172D_064"]["pre"].meta["native-id"] == GCOV_NAMES[0]


def test_gdal_subset_cmd():
    cmd = gdal_subset_cmd(
        "https://example.com/x.h5",
        "/science/LSAR/GCOV/grids/frequencyA/HHHH",
        (-122.6, 47.0, -121.5, 48.5),
        Path("out.tif"),
    )
    assert "gdalwarp" in cmd
    assert "NETCDF:" in cmd and "/vsicurl/" in cmd
    assert "-te -122.6 47.0 -121.5 48.5" in cmd


def test_download_plan_wa():
    plan = download_plan(load_event("wa-2025-12"))
    gcov = [s for s in plan if s["product"] == "GCOV"]
    gunw = [s for s in plan if s["product"] == "GUNW"]
    wm = [s for s in plan if s["product"] == "WATERMASK"]
    assert len(gcov) == 9      # HHHH, HVHV, mask x ref/pre/post
    assert len(gunw) == 0      # wa is GCOV-only — no pair crosses the event
    assert len(wm) == 1
    assert {s["which"] for s in gcov} == {"ref", "pre", "post"}
    # scenes-list shape: declared granule_ids ride along on the plan
    assert {s["granule_id"] for s in gcov} == {
        WA_GCOV_REF, WA_GCOV_PRE, WA_GCOV_POST
    }


GUNW_EVENT = {
    "id": "syn-2025-01",
    "aoi": {"bbox": [-122.6, 47.0, -121.5, 48.5]},
    "nisar": {
        "frames": [
            {
                "product": "GUNW",
                "track": "172D",
                "frame": "064",
                "reference_pair": "2025-11-22_to_2025-12-04",
                "event_pair": "2025-12-04_to_2025-12-16",
                "collections": {"BETA": "NISAR_L2_GUNW_BETA_V1"},
            }
        ],
    },
}


def test_download_plan_gunw_synthetic():
    plan = download_plan(GUNW_EVENT)
    gunw = [s for s in plan if s["product"] == "GUNW"]
    assert len(gunw) == 2
    assert {s["which"] for s in gunw} == {"ref", "evt"}
    assert all("wrappedInterferogram" in s["h5_path"] for s in gunw)


# --- execute_plan -----------------------------------------------------------

WA_GCOV_REF = (
    "NISAR_L2_PR_GCOV_004_172_D_064_4005_DHDH_A_20251110T031814_"
    "20251110T031849_X05009_N_F_J_001"
)
WA_GCOV_PRE = (
    "NISAR_L2_PR_GCOV_006_172_D_064_4005_DHDH_A_20251204T031815_"
    "20251204T031850_X05009_N_F_J_001"
)
WA_GCOV_POST = (
    "NISAR_L2_PR_GCOV_007_172_D_064_4005_DHDH_A_20251216T031815_"
    "20251216T031850_X05009_N_F_J_001"
)
GCOV_WRONG_TRACK = "NISAR_L2_PR_GCOV_003_005_A_026_4005_DHDH_A_20251204T000000_D0"
GUNW_REF = "NISAR_L2_PR_GUNW_001_172_D_064_4005_DHDH_A_20251204T000000_D0"
GUNW_EVT = "NISAR_L2_PR_GUNW_002_172_D_064_4005_DHDH_A_20251216T000000_D0"


class _FakeGranule:
    def __init__(self, native_id):
        self.meta = {"native-id": native_id}

    def data_links(self, access=None):
        return [f"https://daac.example/{self.meta['native-id']}.h5"]


class _FakeEarthaccess:
    def __init__(self):
        self.logged_in = False
        self.logins = []
        self.calls = []

    def login(self, **kw):
        self.logged_in = True
        self.logins.append(kw)

    def search_data(self, short_name, bounding_box, temporal):
        self.calls.append((short_name, temporal))
        if "GCOV" in short_name:
            return [
                _FakeGranule(WA_GCOV_REF),
                _FakeGranule(WA_GCOV_PRE),
                _FakeGranule(WA_GCOV_POST),
                _FakeGranule(GCOV_WRONG_TRACK),
            ]
        if "GUNW" in short_name:
            return [_FakeGranule(GUNW_REF), _FakeGranule(GUNW_EVT)]
        return [_FakeGranule("WM_TILE")]


def _fake_rasterio():
    """A stand-in 'rasterio' package for sys.modules: Env/open/WarpedVRT/
    windows.from_bounds, writing empty files instead of real GeoTIFFs."""
    mod = types.ModuleType("rasterio")
    mod.envs = []

    class _Env:
        def __init__(self, **kw):
            mod.envs.append(kw)
            self.kw = kw

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Window:
        height = width = 4

        def round_offsets(self):
            return self

        def round_lengths(self):
            return self

    class _Src:
        crs = "EPSG:32610"
        count = 1
        nodata = float("nan")
        transform = "src-transform"
        profile: ClassVar[dict] = {
            "driver": "HDF5", "dtype": "float32", "crs": "EPSG:32610",
            "width": 100, "height": 100, "count": 1, "nodata": float("nan"),
        }

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _VRT:
        def __init__(self, src, crs=None):
            self.crs = crs or src.crs
            self.count = src.count
            self.nodata = src.nodata
            self.transform = "vrt-transform"
            self.profile = dict(src.profile)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, window=None):
            return np.zeros(
                (self.count, int(window.height), int(window.width)),
                dtype="float32",
            )

        def window_transform(self, win):
            return self.transform

    class _Writer:
        def __init__(self, path, **profile):
            self.path, self.profile = Path(path), profile

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.path.write_bytes(b"COG")
            return False

        def write(self, data):
            self.data = data

    def _open(path, mode="r", **profile):
        if mode == "w":
            return _Writer(path, **profile)
        assert str(path).startswith('NETCDF:"')
        return _Src()

    vrt_mod = types.ModuleType("rasterio.vrt")
    vrt_mod.WarpedVRT = _VRT
    win_mod = types.ModuleType("rasterio.windows")
    win_mod.from_bounds = lambda w, s, e, n, transform: _Window()
    mod.Env = _Env
    mod.open = _open
    return mod, vrt_mod, win_mod


def _stub_execute(monkeypatch, tmp_path):
    """Fake rasterio + earthaccess + EDL creds + tmp DATA_DIR for execute_plan."""
    mod, vrt_mod, win_mod = _fake_rasterio()
    monkeypatch.setitem(sys.modules, "rasterio", mod)
    monkeypatch.setitem(sys.modules, "rasterio.vrt", vrt_mod)
    monkeypatch.setitem(sys.modules, "rasterio.windows", win_mod)
    fake_ea = _FakeEarthaccess()
    monkeypatch.setattr(nisar_mod, "earthaccess", fake_ea)
    # fetch.edl.earthaccess_login() resolves earthaccess lazily via sys.modules
    monkeypatch.setitem(sys.modules, "earthaccess", fake_ea)
    monkeypatch.delenv("EARTHDATA_TOKEN", raising=False)
    monkeypatch.setenv("EDL_USER", "u")
    monkeypatch.setenv("EDL_PASS", "p")
    # execute_plan maps EDL_* onto these for earthaccess; setting them via
    # monkeypatch keeps the leak contained at teardown.
    monkeypatch.setenv("EARTHDATA_USERNAME", "")
    monkeypatch.setenv("EARTHDATA_PASSWORD", "")
    monkeypatch.setattr(events_mod, "DATA_DIR", tmp_path)

    def _fake_download(url, dest, creds):
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"h5")
        return dest

    monkeypatch.setattr(nisar_mod, "_download_h5", _fake_download)
    return fake_ea


def test_execute_plan_writes_tifs_and_manifest(monkeypatch, tmp_path):
    fake_ea = _stub_execute(monkeypatch, tmp_path)
    written = execute_plan(load_event("wa-2025-12"))

    raw = tmp_path / "raw" / "nisar"
    names = {p.name for p in written}
    assert names == {
        "gcov_hhhh_ref.tif", "gcov_hvhv_ref.tif", "gcov_mask_ref.tif",
        "gcov_hhhh_pre.tif", "gcov_hvhv_pre.tif", "gcov_mask_pre.tif",
        "gcov_hhhh_post.tif", "gcov_hvhv_post.tif", "gcov_mask_post.tif",
    }
    assert all(p.exists() and raw in p.parents for p in written)
    assert fake_ea.logged_in
    assert len(fake_ea.calls) == 3  # one CMR hit per GCOV scene

    manifest = json.loads(
        (tmp_path / "wa_2025" / "nisar_products.json").read_text()
    )
    assert manifest["GCOV"]["collection"] == "NISAR_L2_GCOV_BETA_V1"
    assert manifest["GCOV"]["ref"] == WA_GCOV_REF
    assert manifest["GCOV"]["pre"] == WA_GCOV_PRE
    assert manifest["GCOV"]["post"] == WA_GCOV_POST
    assert "GUNW" not in manifest
    assert manifest["WATERMASK"]["collection"] == "NISAR_WATERMASK"


def test_execute_plan_gunw_synthetic(monkeypatch, tmp_path):
    fake_ea = _stub_execute(monkeypatch, tmp_path)
    written = execute_plan(GUNW_EVENT)
    assert {p.name for p in written} == {
        "gunw_coh_ref.tif",
        "gunw_coh_evt.tif",
    }
    assert all(p.exists() for p in written)
    assert len(fake_ea.calls) == 2  # ref + evt granules (date/track matching)
    manifest = json.loads(
        (tmp_path / "syn_2025" / "nisar_products.json").read_text()
    )
    assert manifest["GUNW"]["collection"] == "NISAR_L2_GUNW_BETA_V1"
    assert manifest["GUNW"]["ref"] == GUNW_REF
    assert manifest["GUNW"]["evt"] == GUNW_EVT


def test_execute_plan_token_uses_bearer(monkeypatch, tmp_path):
    """EARTHDATA_TOKEN -> environment login + Bearer in GDAL_HTTP_HEADERS."""
    mod, vrt_mod, win_mod = _fake_rasterio()
    monkeypatch.setitem(sys.modules, "rasterio", mod)
    monkeypatch.setitem(sys.modules, "rasterio.vrt", vrt_mod)
    monkeypatch.setitem(sys.modules, "rasterio.windows", win_mod)
    fake_ea = _FakeEarthaccess()
    monkeypatch.setattr(nisar_mod, "earthaccess", fake_ea)
    monkeypatch.setitem(sys.modules, "earthaccess", fake_ea)
    for var in (
        "EDL_USER", "EDL_PASS", "EARTHDATA_USERNAME", "EARTHDATA_PASSWORD",
        "NETRC",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))  # no ~/.netrc
    monkeypatch.setenv("EARTHDATA_TOKEN", "faketoken")
    monkeypatch.setattr(events_mod, "DATA_DIR", tmp_path)

    def _fake_download(url, dest, creds):
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"h5")
        return dest

    monkeypatch.setattr(nisar_mod, "_download_h5", _fake_download)
    execute_plan(load_event("wa-2025-12"))
    assert fake_ea.logins == [{"strategy": "environment"}]
    assert mod.envs
    assert all(
        e["GDAL_HTTP_HEADERS"] == "Authorization: Bearer faketoken"
        for e in mod.envs
    )
    assert all("GDAL_HTTP_USERPWD" not in e for e in mod.envs)


def test_execute_plan_user_fallback_gdal(monkeypatch, tmp_path):
    """EDL_USER/EDL_PASS -> environment login + GDAL_HTTP_USERPWD, no Bearer."""
    fake_ea = _stub_execute(monkeypatch, tmp_path)
    execute_plan(load_event("wa-2025-12"))
    envs = sys.modules["rasterio"].envs
    assert envs
    assert all(e["GDAL_HTTP_USERPWD"] == "u:p" for e in envs)
    assert all("GDAL_HTTP_HEADERS" not in e for e in envs)
    assert fake_ea.logins == [{"strategy": "environment"}]


def test_gdal_subset_cmd_token(monkeypatch):
    """With EARTHDATA_TOKEN the printed cmd references the env var, not the value."""
    monkeypatch.setenv("EARTHDATA_TOKEN", "faketoken")
    cmd = gdal_subset_cmd(
        "https://example.com/x.h5", "/p", (-1.0, -1.0, 1.0, 1.0), Path("o.tif")
    )
    assert "Authorization: Bearer $EARTHDATA_TOKEN" in cmd
    assert "faketoken" not in cmd
    assert "NETRC" not in cmd
    monkeypatch.delenv("EARTHDATA_TOKEN")
    cmd = gdal_subset_cmd(
        "https://example.com/x.h5", "/p", (-1.0, -1.0, 1.0, 1.0), Path("o.tif")
    )
    assert "GDAL_HTTP_NETRC=YES" in cmd


def test_event_data_dir_uses_case_key(monkeypatch, tmp_path):
    monkeypatch.setattr(events_mod, "DATA_DIR", tmp_path)
    d = events_mod.event_data_dir("wa-2025-12")
    assert d == tmp_path / "wa_2025"
    assert (d / "cog").is_dir()
    assert events_mod.case_key("rs-2024-05") == "rs_2024"


def test_execute_plan_requires_creds(monkeypatch, tmp_path):
    for var in (
        "EARTHDATA_TOKEN", "EDL_USER", "EDL_PASS", "EARTHDATA_USERNAME",
        "EARTHDATA_PASSWORD", "NETRC",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))  # no ~/.netrc
    with pytest.raises(RuntimeError, match="Earthdata credentials"):
        execute_plan(load_event("wa-2025-12"))


def test_execute_plan_dry_run_needs_nothing(monkeypatch, tmp_path):
    for var in ("EDL_USER", "EDL_PASS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(events_mod, "DATA_DIR", tmp_path)
    event = load_event("wa-2025-12")
    paths = execute_plan(event, dry_run=True)
    expected = [s["out"] for s in download_plan(event) if s["h5_path"]]
    assert [p.name for p in paths] == expected
    assert all(tmp_path in p.parents for p in paths)
    assert not any(p.exists() for p in paths)


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "01_get_nisar.py"
REPO = Path(__file__).resolve().parents[2]


def _no_cred_env(home: Path) -> dict:
    drop = {
        "EARTHDATA_TOKEN", "EDL_USER", "EDL_PASS", "EARTHDATA_USERNAME",
        "EARTHDATA_PASSWORD", "NETRC",
    }
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env["HOME"] = str(home)
    return env


def test_plan_flag_runs_offline(tmp_path):
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--event", "wa-2025-12", "--plan"],
        check=False, capture_output=True, text=True, cwd=REPO,
        env=_no_cred_env(tmp_path),
    )
    assert r.returncode == 0, r.stderr
    assert "gdalwarp" in r.stdout
    assert "gcov_hhhh_pre.tif" in r.stdout


def test_execute_flag_refuses_without_creds(tmp_path):
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--event", "wa-2025-12", "--execute"],
        check=False, capture_output=True, text=True, cwd=REPO,
        env=_no_cred_env(tmp_path),
    )
    assert r.returncode != 0
    assert "Earthdata credentials" in r.stderr
