"""Tests for gee/impact.py + gee/hand.py — fake `ee`, no creds needed.

`sys.modules['ee']` is replaced with a chainable call recorder BEFORE the
modules under test import it; `gee.common` is stubbed only when U4's module
is absent from this branch. The original sys.modules['ee'] entry is restored
right after the imports so other test files see the real package.
"""
from __future__ import annotations

import json
import sys
import types
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

LOG: list = []


class _FakeObj:
    """Stand-in for any ee object: attributes chain, calls are recorded.

    Callable arguments (ee .map() lambdas) are invoked on a fresh recorder so
    their inner calls land in LOG too. getInfo() returns a plain int so merged
    metrics stay JSON-serializable.
    """

    def __init__(self, path, ret=None):
        self._path, self._ret = path, ret

    def __getattr__(self, name):
        return _FakeObj(f"{self._path}.{name}")

    def __call__(self, *args, **kwargs):
        LOG.append((self._path, list(args), dict(kwargs)))
        for a in (*args, *kwargs.values()):
            if isinstance(a, types.FunctionType):  # ee .map() lambdas: run so inner calls log too
                a(_FakeObj(f"{self._path}()"))
        return _FakeObj(f"{self._path}()")

    def getInfo(self):
        return self._ret if self._ret is not None else 0


FAKE_EE = _FakeObj("ee")

_CASES = {
    "rs_2024": {
        "aoi": [-52.5, -30.5, -50.5, -29.0],
        "event_date": "2024-05-02",
        "window_before_days": 10,
        "window_after_days": 5,
    },
    "wa_2025": {
        "aoi": [-122.6, 47.0, -121.5, 48.5],
        "event_date": "2025-12-10",
        "window_before_days": 10,
        "window_after_days": 10,
    },
}


def _window_dates(cfg: dict) -> list:
    d = date.fromisoformat(str(cfg["event_date"]))
    lo, hi = -cfg["window_before_days"], cfg["window_after_days"]
    return [(d + timedelta(days=k)).isoformat() for k in range(lo, hi + 1)]


def _stub_common() -> types.ModuleType:
    m = types.ModuleType("gee.common")
    m.init = lambda project=None: "test-project"
    m.case_cfg = lambda cid: _CASES[cid]
    m.aoi_geom = lambda cid: FAKE_EE.Geometry.Rectangle(_CASES[cid]["aoi"])
    m.event_dates = lambda cid: _window_dates(_CASES[cid])
    m.COL_IMERG = "NASA/GPM_L3/IMERG_V07"
    m.COL_SMAP = "NASA/SMAP/SPL4SMGP/008"
    m.IMG_NASADEM = "NASA/NASADEM_HGT/001"
    m.BAND_DEM = "elevation"
    m.CLIM_YEARS = (2001, 2023)
    m.ASSET_ROOT = "projects/test-project/assets/tipping-point"
    m.GEE_PROJECT = "test-project"
    return m


_had_ee = "ee" in sys.modules
_orig_ee = sys.modules.get("ee")
sys.modules["ee"] = FAKE_EE
try:
    import gee.common  # noqa: F401  (U4's module when it has landed)
except ModuleNotFoundError:
    sys.modules["gee.common"] = _stub_common()

from gee import (
    hand,
    impact,
)

if _had_ee:
    sys.modules["ee"] = _orig_ee
else:
    sys.modules.pop("ee", None)


@pytest.fixture(autouse=True)
def _clear_log():
    LOG.clear()


@pytest.fixture
def commons(monkeypatch):
    """Deterministic gee.common surface on both modules (real or stubbed)."""
    for mod in (impact, hand):
        monkeypatch.setattr(mod, "init", lambda project=None: "test-project", raising=False)
        monkeypatch.setattr(
            mod, "aoi_geom", lambda cid: FAKE_EE.Geometry.Rectangle(_CASES[cid]["aoi"]), raising=False
        )
    monkeypatch.setattr(impact, "case_cfg", lambda cid: _CASES[cid], raising=False)
    monkeypatch.setattr(impact, "event_dates", lambda cid: _window_dates(_CASES[cid]), raising=False)
    monkeypatch.setattr(impact, "ASSET_ROOT", "projects/test-project/assets/tipping-point", raising=False)
    monkeypatch.setattr(hand, "IMG_NASADEM", "NASA/NASADEM_HGT/001", raising=False)
    monkeypatch.setattr(hand, "BAND_DEM", "elevation", raising=False)


def _called(path_suffix, args=None, **kw):
    for p, a, k in LOG:
        if not p.endswith(path_suffix):
            continue
        if args is not None and a != args:
            continue
        if any(k.get(key) != val for key, val in kw.items()):
            continue
        return True
    return False


# ---------- impact: GPW nearest epoch + reducer params ----------


def test_gpw_nearest_epoch_pick(tmp_path, commons):
    impact.run("rs_2024", metrics_path=tmp_path / "m.json", pull=False)
    assert _called("ee.ImageCollection", [impact.COL_GPW])
    assert _called("ee.Image", [f"{impact.ASSET_ROOT}/rs_2024/tpi_slide_series"])
    # epoch closest to the event year: sort by abs(date difference in years)
    assert _called(".difference")
    assert any("year" in a for p, a, k in LOG if p.endswith(".difference"))
    assert _called(".sort", ["d"])
    assert _called(".first")
    assert _called(".abs")


def test_ntl_reducer_params(tmp_path, commons):
    impact.run("rs_2024", metrics_path=tmp_path / "m.json", pull=False)
    assert _called(".advance", [-30, "day"])  # 30 nights before
    assert _called(".advance", [7, "day"])  # 7 nights after
    assert sum(1 for p, a, k in LOG if p.endswith(".filterDate")) >= 2
    assert _called(".eq", [0])  # Mandatory_Quality_Flag == 0
    assert any(
        "Mandatory_Quality_Flag" in str(a) for p, a, k in LOG if p.endswith(".select")
    )
    assert _called(".reduceRegion", scale=500)  # VNP46A2 scale
    assert _called(".reduceRegion", scale=1000)  # TPI/GPW scale
    assert _called("ee.Reducer.mean")
    assert _called("ee.Reducer.sum")
    assert _called(".gt", [0.6])  # TPI_slide > 0.6 critical cut


def _zip_tif() -> bytes:
    import io
    import zipfile

    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    with rasterio.io.MemoryFile() as mem:
        with mem.open(
            driver="GTiff", height=4, width=6, count=1, dtype="float32",
            crs="EPSG:4326", transform=from_origin(-51, -29, 0.01, 0.01),
        ) as dst:
            dst.write(np.arange(24, dtype="float32").reshape(4, 6), 1)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("download.tif", mem.read())
    return buf.getvalue()


def test_night_light_pull(tmp_path, commons, monkeypatch):
    monkeypatch.setattr(impact, "_fetch", lambda url: _zip_tif())
    out = impact.run("rs_2024", metrics_path=tmp_path / "m.json", out_dir=tmp_path / "cog")
    downloads = [(p, a, k) for p, a, k in LOG if p.endswith(".getDownloadURL")]
    assert len(downloads) == 2
    assert all(a[0]["format"] == "GEO_TIFF" and a[0]["scale"] == 500 for _, a, _ in downloads)
    assert {p.name for p in out["files"]} == {
        "rs_2024_night_lights_pre_20240502.tif",
        "rs_2024_night_lights_post_20240502.tif",
    }
    import rasterio

    with rasterio.open(out["files"][0]) as src:  # COG driver is write-only: reads back as GTiff
        assert src.tags(ns="IMAGE_STRUCTURE")["LAYOUT"] == "COG" and src.shape == (4, 6)


# ---------- impact: flood-asset gate ----------


def test_flood_gate_failure_writes_null_reasons(tmp_path, commons, monkeypatch):
    seen = []

    def _missing(path):
        seen.append(path)
        raise RuntimeError("asset not found")

    monkeypatch.setattr(impact.ee, "data", SimpleNamespace(getAsset=_missing))
    # no real assets.json in scope -> convention path
    monkeypatch.setattr(impact, "DATA_DIR", tmp_path, raising=False)
    path = tmp_path / "metrics.json"
    impact.run("wa_2025", metrics_path=path, pull=False)
    blob = json.loads(path.read_text())["cases"]["wa_2025"]
    assert seen == [f"{impact.ASSET_ROOT}/wa_2025/flood_nisar"]
    assert blob["people_flooded"] is None and blob["flooded_km2"] is None
    assert {"people_flooded", "flooded_km2"} <= set(blob["_null_reasons"])
    assert "flood_nisar" in blob["_null_reasons"]["people_flooded"]
    assert blob["people_critical"] == 0 and blob["gpw_year"] == 0
    assert blob["night_light_drop_pct"] == 0


def test_flood_gate_pass_wa(tmp_path, commons, monkeypatch):
    # default fake ee.data.getAsset returns a truthy object -> gate opens
    monkeypatch.setattr(impact, "DATA_DIR", tmp_path, raising=False)
    out = impact.run("wa_2025", metrics_path=tmp_path / "m.json", pull=False)
    assert out["metrics"]["people_flooded"] == 0
    assert out["metrics"]["flooded_km2"] == 0
    assert _called("ee.data.getAsset")
    # L8: concrete asset ids recorded in collections
    cols = out["merged"]["cases"]["wa_2025"]["collections"]
    assert cols["tpi_slide_series"] == f"{impact.ASSET_ROOT}/wa_2025/tpi_slide_series"
    assert cols["flood_nisar"] == f"{impact.ASSET_ROOT}/wa_2025/flood_nisar"


@pytest.mark.parametrize(
    ("asset", "expected"),
    [
        ("projects/p/assets/tipping-point/wa_2025/flood_nisar", "nisar"),
        ("projects/p/assets/tipping-point/rs_2024/opera_dswx", "opera"),
        ("projects/p/assets/tipping-point/rs_2024/flood_observed", "observed"),
        (None, None),
    ],
)
def test_emitted_flood_source(tmp_path, commons, monkeypatch, asset, expected):
    monkeypatch.setattr(impact, "_flood_asset", lambda cid: asset)
    monkeypatch.setattr(impact, "_flood_mask", lambda cid: FAKE_EE.Image(asset) if asset else None)
    out = impact.run("wa_2025", metrics_path=tmp_path / "metrics.json", pull=False)
    assert out["metrics"]["flood_source"] == expected
    assert out["merged"]["cases"]["wa_2025"]["flood_source"] == expected


def test_null_flood_source_preserves_written_value(tmp_path, commons):
    path = tmp_path / "metrics.json"
    path.write_text(json.dumps({"cases": {"rs_2024": {"flood_source": "opera"}}}))
    out = impact.run("rs_2024", metrics_path=path, pull=False)
    assert out["metrics"]["flood_source"] is None
    assert out["merged"]["cases"]["rs_2024"]["flood_source"] == "opera"


def test_asset_prefers_assets_json(tmp_path, commons, monkeypatch):
    monkeypatch.setattr(impact, "DATA_DIR", tmp_path)
    assert impact._asset("wa_2025", "flood_nisar") == f"{impact.ASSET_ROOT}/wa_2025/flood_nisar"
    (tmp_path / "wa_2025").mkdir()
    (tmp_path / "wa_2025" / "assets.json").write_text(
        json.dumps({"flood_nisar": "projects/p/assets/custom/flood"})
    )
    assert impact._asset("wa_2025", "flood_nisar") == "projects/p/assets/custom/flood"
    # missing key falls back to the convention path
    assert impact._asset("wa_2025", "tpi_slide_series") == f"{impact.ASSET_ROOT}/wa_2025/tpi_slide_series"


def test_flood_keys_null_for_rs(tmp_path, commons):
    out = impact.run("rs_2024", metrics_path=tmp_path / "m.json", pull=False)
    blob = out["merged"]["cases"]["rs_2024"]
    assert blob["people_flooded"] is None
    assert "predates launch" in blob["_null_reasons"]["people_flooded"]
    assert not _called("ee.data.getAsset")  # rs never checks the gate
    assert "flood_nisar" not in blob["collections"]  # gate closed: no asset id recorded
    assert blob["collections"]["tpi_slide_series"].endswith("/rs_2024/tpi_slide_series")


# ---------- hand: radii + best pick ----------


def _hand_npy() -> bytes:
    """Canned NPY tile: merit 0..15, pR = merit scaled so IoU falls with radius."""
    import io

    import numpy as np

    a = np.zeros((4, 4), dtype=np.dtype(
        [("merit", "<f4"), ("p1000", "<f4"), ("p1500", "<f4"), ("p3000", "<f4")]))
    a["merit"] = np.arange(16, dtype="float32").reshape(4, 4)
    a["p1000"] = a["merit"] / 2
    a["p1500"] = a["merit"] / 3
    a["p3000"] = a["merit"] / 10
    buf = io.BytesIO()
    np.save(buf, a)
    return buf.getvalue()


class _GeomStub:
    """aoi stand-in whose bounds().getInfo() returns real bbox coordinates."""

    def __init__(self, coords):
        self._coords = coords

    def bounds(self):
        return SimpleNamespace(getInfo=lambda: {"coordinates": [self._coords]})


def test_hand_three_radii(tmp_path, commons, monkeypatch):
    monkeypatch.setattr(
        hand, "aoi_geom",
        lambda cid: _GeomStub([[0, 0], [2, 0], [2, 1], [0, 1], [0, 0]]))
    monkeypatch.setattr(hand, "_fetch", lambda url: _hand_npy())
    hand.run("rs_2024", metrics_path=tmp_path / "m.json")
    radii = {
        k["radius"] for p, a, k in LOG if p.endswith(".focalMin") and k.get("units") == "meters"
    }
    assert radii == {1000, 1500, 3000}
    assert _called("ee.Image", [hand.IMG_NASADEM])
    assert _called("ee.Image", ["MERIT/Hydro/v1_0_1"])
    assert _called(".clip")  # stats clipped to the AOI, not the bbox
    downloads = [a[0] for p, a, k in LOG if p.endswith(".getDownloadURL")]
    assert len(downloads) == hand.GRID_TILES ** 2
    assert all(d["format"] == "NPY" and d["scale"] == 90
               and d["crs"] == "EPSG:4326" for d in downloads)
    blob = json.loads((tmp_path / "m.json").read_text())["cases"]["rs_2024"]
    assert set(blob["hand_validation_radii"]) == {"1000", "1500", "3000"}
    assert isinstance(blob["hand_validation_radii"]["1000"], float)
    per = blob["hand_validation_by_radius"]["1000"]
    assert set(per) == {"iou", "r", "r_5k", "n_px", "low_proxy_pct", "low_merit_pct"}
    # 9 identical 4x4 tiles: low_merit = 5/16 of px, iou(1000) = 5/10
    assert per["iou"] == 0.5 and per["r"] == pytest.approx(1.0) and per["n_px"] == 144
    assert per["low_merit_pct"] == 31.25
    assert blob["hand_validation_iou"] == 0.5
    assert blob["hand_validation"] == 0.5  # legacy key = IoU 0-1, not percent
    assert blob["hand_validation_r"] == pytest.approx(1.0)
    assert blob["hand_validation_radius_m"] == 1000
    assert "MERIT Hydro hnd" in blob["hand_validation_definition"]
    assert blob["collections"]["MERIT_Hydro"] == "MERIT/Hydro/v1_0_1"


def test_best_radius_picks_highest_iou(tmp_path, commons, monkeypatch):
    canned = {
        1000: {"r": 0.9, "iou": 0.3, "r_5k": 0.9, "n_px": 10,
               "low_proxy_pct": 1.0, "low_merit_pct": 2.0},
        1500: {"r": 0.1, "iou": 0.5, "r_5k": 0.1, "n_px": 10,
               "low_proxy_pct": 1.0, "low_merit_pct": 2.0},
        3000: {"r": 0.8, "iou": 0.2, "r_5k": 0.8, "n_px": 10,
               "low_proxy_pct": 1.0, "low_merit_pct": 2.0},
    }
    assert hand.best_radius(canned) == 1500  # highest IoU, not r + iou
    monkeypatch.setattr(hand, "compute", lambda _: canned)
    out = hand.run("rs_2024", metrics_path=tmp_path / "m.json")
    assert out["metrics"]["hand_validation"] == 0.5
    assert out["metrics"]["hand_validation_iou"] == 0.5
    assert out["metrics"]["hand_validation_r"] == 0.1
    assert out["metrics"]["hand_validation_radius_m"] == 1500
    assert out["merged"]["cases"]["rs_2024"]["hand_validation_by_radius"]["1500"]["iou"] == 0.5


# ---------- dry-run: plan without init ----------


def test_dry_run_impact_no_init(capsys, monkeypatch):
    monkeypatch.setattr(impact, "init", lambda *a, **k: pytest.fail("init in dry-run"), raising=False)
    assert impact.main(["--case", "wa_2025", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "wa_2025_night_lights_pre_20251210" in out
    assert "wa_2025_night_lights_post_20251210" in out
    assert "wa_2025/flood_nisar" in out


def test_hand_no_samples_is_null_with_reason(tmp_path, commons, monkeypatch):
    empty = {"iou": None, "r": None, "r_5k": None, "n_px": 0,
             "low_proxy_pct": None, "low_merit_pct": None}
    monkeypatch.setattr(hand, "compute", lambda _: {r: dict(empty) for r in hand.RADII_M})
    out = hand.run("rs_2024", metrics_path=tmp_path / "m.json")
    blob = out["merged"]["cases"]["rs_2024"]
    for k in ("hand_validation", "hand_validation_iou",
              "hand_validation_r", "hand_validation_radius_m"):
        assert blob[k] is None
        assert blob["_null_reasons"][k]


def test_dry_run_hand_prints_radii(capsys, monkeypatch):
    monkeypatch.setattr(hand, "init", lambda *a, **k: pytest.fail("init in dry-run"), raising=False)
    assert hand.main(["--case", "rs_2024", "--dry-run"]) == 0
    out = capsys.readouterr().out
    for r in ("1000", "1500", "3000"):
        assert r in out
    assert "MERIT" in out
