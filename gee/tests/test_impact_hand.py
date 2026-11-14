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
    impact.run("rs_2024", metrics_path=tmp_path / "m.json", exports=False)
    assert _called("ee.ImageCollection", [impact.COL_GPW])
    assert _called("ee.Image", [f"{impact.ASSET_ROOT}/rs_2024_tpi_slide_series"])
    # epoch closest to the event year: sort by abs(date difference in years)
    assert _called(".difference")
    assert any("year" in a for p, a, k in LOG if p.endswith(".difference"))
    assert _called(".sort", ["d"])
    assert _called(".first")
    assert _called(".abs")


def test_ntl_reducer_params(tmp_path, commons):
    impact.run("rs_2024", metrics_path=tmp_path / "m.json", exports=False)
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


def test_night_light_exports(tmp_path, commons):
    impact.run("rs_2024", metrics_path=tmp_path / "m.json")
    exports = [(p, a, k) for p, a, k in LOG if p.endswith("Export.image.toDrive")]
    assert len(exports) == 2
    assert {k["fileNamePrefix"] for _, _, k in exports} == {
        "rs_2024_night_lights_pre_20240502",
        "rs_2024_night_lights_post_20240502",
    }
    assert all(k["formatOptions"] == {"cloudOptimized": True} for _, _, k in exports)
    assert sum(1 for p, a, k in LOG if p.endswith(".start")) == 2


# ---------- impact: flood-asset gate ----------


def test_flood_gate_failure_writes_null_reasons(tmp_path, commons, monkeypatch):
    seen = []

    def _missing(path):
        seen.append(path)
        raise RuntimeError("asset not found")

    monkeypatch.setattr(impact.ee, "data", SimpleNamespace(getAsset=_missing))
    path = tmp_path / "metrics.json"
    impact.run("wa_2025", metrics_path=path, exports=False)
    m = json.loads(path.read_text())
    assert seen == [f"{impact.ASSET_ROOT}/wa_2025_flood_nisar"]
    assert m["people_flooded"] is None and m["flooded_km2"] is None
    assert {"people_flooded", "flooded_km2"} <= set(m["_null_reasons"])
    assert "flood_nisar" in m["_null_reasons"]["people_flooded"]
    assert m["people_critical"] == 0 and m["gpw_year"] == 0
    assert m["night_light_drop_pct"] == 0


def test_flood_gate_pass_wa(tmp_path, commons):
    # default fake ee.data.getAsset returns a truthy object -> gate opens
    out = impact.run("wa_2025", metrics_path=tmp_path / "m.json", exports=False)
    assert out["metrics"]["people_flooded"] == 0
    assert out["metrics"]["flooded_km2"] == 0
    assert _called("ee.data.getAsset")


def test_flood_keys_null_for_rs(tmp_path, commons):
    out = impact.run("rs_2024", metrics_path=tmp_path / "m.json", exports=False)
    m = out["merged"]
    assert m["people_flooded"] is None
    assert "predates launch" in m["_null_reasons"]["people_flooded"]
    assert not _called("ee.data.getAsset")  # rs never checks the gate


# ---------- hand: radii + best pick ----------


def test_hand_three_radii(tmp_path, commons):
    hand.run("rs_2024", metrics_path=tmp_path / "m.json")
    radii = {
        k["radius"] for p, a, k in LOG if p.endswith(".focalMin") and k.get("units") == "meters"
    }
    assert radii == {1000, 1500, 3000}
    assert _called("ee.Image", [hand.IMG_NASADEM])
    assert _called("ee.Image", ["MERIT/Hydro/v1_0_1"])
    assert _called("ee.Reducer.pearsonsCorrelation")
    assert _called(".sample", scale=90, numPixels=5000, seed=1)
    m = json.loads((tmp_path / "m.json").read_text())
    assert set(m["hand_validation"]) == {"1000", "1500", "3000"}
    assert m["collections"]["MERIT_Hydro"] == "MERIT/Hydro/v1_0_1"


def test_best_radius_picks_highest_combined(tmp_path, commons, monkeypatch):
    canned = {
        1000: {"r": 0.5, "iou": 0.4},
        1500: {"r": 0.7, "iou": 0.6},
        3000: {"r": 0.75, "iou": 0.3},
    }
    assert hand.best_radius(canned) == 1500
    monkeypatch.setattr(hand, "_radius_stats", lambda dem, merit, aoi, r: canned[r])
    out = hand.run("rs_2024", metrics_path=tmp_path / "m.json")
    assert out["metrics"]["hand_best_radius_m"] == 1500
    assert out["merged"]["hand_validation"]["1500"] == {"r": 0.7, "iou": 0.6}


# ---------- dry-run: plan without init ----------


def test_dry_run_impact_no_init(capsys, monkeypatch):
    monkeypatch.setattr(impact, "init", lambda *a, **k: pytest.fail("init in dry-run"), raising=False)
    assert impact.main(["--case", "wa_2025", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "wa_2025_night_lights_pre_20251210" in out
    assert "wa_2025_night_lights_post_20251210" in out
    assert "wa_2025_flood_nisar" in out


def test_dry_run_hand_prints_radii(capsys, monkeypatch):
    monkeypatch.setattr(hand, "init", lambda *a, **k: pytest.fail("init in dry-run"), raising=False)
    assert hand.main(["--case", "rs_2024", "--dry-run"]) == 0
    out = capsys.readouterr().out
    for r in ("1000", "1500", "3000"):
        assert r in out
    assert "MERIT" in out
