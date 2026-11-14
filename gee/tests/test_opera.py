"""Tests for gee/opera.py — fake `ee`, no creds needed.

`sys.modules['ee']` is replaced with a chainable call recorder BEFORE the
module under test imports it; `gee.common` is stubbed only when U4's module
is absent from this branch (same pattern as gee/tests/test_impact_hand.py).
The original sys.modules['ee'] entry is restored right after the imports so
other test files see the real package.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # gee/ has no __init__.py yet

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


def _stub_common() -> types.ModuleType:
    m = types.ModuleType("gee.common")
    m.init = lambda project=None: "test-project"
    m.case_cfg = lambda cid: _CASES[cid]
    m.aoi_geom = lambda cid: FAKE_EE.Geometry.Rectangle(_CASES[cid]["aoi"])
    m.data_dir = lambda cid: Path("data") / cid
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

from gee import opera

if _had_ee:
    sys.modules["ee"] = _orig_ee
else:
    sys.modules.pop("ee", None)


@pytest.fixture(autouse=True)
def _clear_log():
    LOG.clear()


@pytest.fixture
def commons(monkeypatch):
    """Deterministic gee.common surface (real or stubbed)."""
    monkeypatch.setattr(opera, "init", lambda project=None: "test-project", raising=False)
    monkeypatch.setattr(
        opera, "aoi_geom", lambda cid: FAKE_EE.Geometry.Rectangle(_CASES[cid]["aoi"]), raising=False
    )
    monkeypatch.setattr(opera, "case_cfg", lambda cid: _CASES[cid], raising=False)
    monkeypatch.setattr(opera, "data_dir", lambda cid: Path("data") / cid, raising=False)
    monkeypatch.setattr(
        opera, "ASSET_ROOT", "projects/test-project/assets/tipping-point", raising=False
    )


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


# ---------- asset resolution ----------


def test_assets_json_wins(tmp_path, commons):
    (tmp_path / "assets.json").write_text(json.dumps({
        "water_nisar": "projects/x/assets/nisar_wtr",
        "opera_wtr": "projects/x/assets/opera_mos",
    }))
    opera.compute("rs_2024", data_root=tmp_path)
    assert _called("ee.Image", ["projects/x/assets/nisar_wtr"])
    assert _called("ee.Image", ["projects/x/assets/opera_mos"])
    # no getAsset probes — the map is authoritative
    assert not _called("ee.data.getAsset")


def test_default_asset_paths(tmp_path, commons):
    # no assets.json -> <root>/<case>/<name>, confirmed via ee.data.getAsset
    root = "projects/test-project/assets/tipping-point"
    opera.compute("rs_2024", data_root=tmp_path)
    assert _called("ee.data.getAsset", [f"{root}/rs_2024/water_nisar"])
    assert _called("ee.data.getAsset", [f"{root}/rs_2024/opera_wtr"])
    assert _called("ee.Image", [f"{root}/rs_2024/water_nisar"])
    assert _called("ee.Image", [f"{root}/rs_2024/opera_wtr"])


def test_catalog_fallback(tmp_path, commons, monkeypatch):
    # only the catalog collection "exists" -> OPERA image built from it
    col = opera.OPERA_CATALOG["rs_2024"]

    def _ga(path):
        if path != col:
            raise RuntimeError("asset not found")
        return {}

    monkeypatch.setattr(opera.ee, "data", SimpleNamespace(getAsset=_ga))
    res = opera.compute("rs_2024", data_root=tmp_path)
    assert res["opera"] == col
    assert res["opera_source"] == f"catalog:{col}"
    assert res["nisar"] is None
    assert _called("ee.ImageCollection", [col])
    assert _called(".filterDate", ["2024-05-02", "2024-05-07"])  # event..+5d
    assert _called(".select", [opera.WTR_BAND])
    assert _called(".mosaic")


def test_nothing_resolves(tmp_path, commons, monkeypatch):
    def _ga(path):
        raise RuntimeError("asset not found")

    monkeypatch.setattr(opera.ee, "data", SimpleNamespace(getAsset=_ga))
    res = opera.compute("rs_2024", data_root=tmp_path)
    assert res["opera"] is None and res["nisar"] is None
    assert _called("ee.Dictionary", [{}])      # empty stats, no bands


# ---------- band math ----------


def test_stats_band_math(tmp_path, commons):
    opera.compute("rs_2024", data_root=tmp_path)
    for val in (1, 2, 253, 255):
        assert any(
            p.endswith(".eq") and a == [val] or p.endswith(".neq") and a == [val]
            for p, a, k in LOG
        )
    for name in ("tp", "fp", "fn", "cloud", "valid"):
        assert _called(".rename", [name])
    assert _called("ee.Reducer.sum")
    assert _called(".reduceRegion", scale=opera.SCALE)
    assert _called(".selfMask") and _called(".And") and _called(".Or")


# ---------- run + merge ----------


def test_merge_hand_computed(tmp_path, commons, monkeypatch):
    counts = {"tp": 3, "fp": 2, "fn": 1, "cloud": 2, "valid": 14}
    monkeypatch.setattr(_FakeObj, "getInfo", lambda self: dict(counts))
    path = tmp_path / "metrics.json"
    out = opera.run("rs_2024", metrics_path=path, data_root=tmp_path)
    m = json.loads(path.read_text())["cases"]["rs_2024"]
    assert m["iou"] == pytest.approx(0.5)
    assert m["f1"] == pytest.approx(6 / 9)
    assert m["cloud_pct"] == pytest.approx(200 / 14)
    assert m["collections"]["opera_dswx"] == "OPERA_L3_DSWX-HLS_V1"
    assert m["collections"]["cloud_pct_source"].startswith("asset:")
    assert out["metrics"]["iou"] == m["iou"]


def test_nisar_gate_null_reasons(tmp_path, commons, monkeypatch):
    # OPERA resolves, water_nisar does not -> iou/f1 null + reason, cloud_pct merged
    def _ga(path):
        if path.endswith("water_nisar"):
            raise RuntimeError("asset not found")
        return {}

    monkeypatch.setattr(opera.ee, "data", SimpleNamespace(getAsset=_ga))
    monkeypatch.setattr(_FakeObj, "getInfo", lambda self: {"cloud": 5, "valid": 10})
    path = tmp_path / "metrics.json"
    opera.run("rs_2024", metrics_path=path, data_root=tmp_path)
    m = json.loads(path.read_text())["cases"]["rs_2024"]
    assert m["iou"] is None and m["f1"] is None
    assert "NISAR" in m["_null_reasons"]["iou"]
    assert m["cloud_pct"] == 50.0
    # no tp/fp/fn bands were built — the nisar gate closed first
    assert not _called(".rename", ["tp"])


def test_all_null_when_no_opera(tmp_path, commons, monkeypatch):
    def _ga(path):
        raise RuntimeError("asset not found")

    monkeypatch.setattr(opera.ee, "data", SimpleNamespace(getAsset=_ga))
    path = tmp_path / "metrics.json"
    opera.run("rs_2024", metrics_path=path, data_root=tmp_path)
    m = json.loads(path.read_text())["cases"]["rs_2024"]
    assert m["iou"] is None and m["f1"] is None and m["cloud_pct"] is None
    assert {"iou", "f1", "cloud_pct"} <= set(m["_null_reasons"])


# ---------- CLI ----------


def test_dry_run_needs_no_ee(capsys, commons):
    assert opera.main(["--case", "rs_2024", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "[opera] case=rs_2024" in out
    assert "water_nisar" in out and "opera_wtr" in out
    assert LOG == []          # plan() never touches ee


def test_tasks_skips_merge(tmp_path, commons, monkeypatch, capsys):
    monkeypatch.setattr(_FakeObj, "getInfo", lambda self: {"tp": 1, "fp": 1, "fn": 0})
    path = tmp_path / "metrics.json"
    assert opera.main(
        ["--case", "rs_2024", "--tasks", "--metrics-path", str(path)]
    ) == 0
    out = capsys.readouterr().out
    assert "metrics not merged" in out
    assert not path.exists()
