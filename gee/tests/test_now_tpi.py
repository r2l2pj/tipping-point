"""gee.now_tpi — tag-window logic, asset naming, source selection, merge shape.

Uses the recording Fake from gee/tests/conftest.py (installed per test via
monkeypatch) — no Earth Engine credentials needed anywhere in this file.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from gee import common, now_tpi, tpi
from gee.tests.conftest import Fake

ROOT = "projects/test-project/assets/tipping-point"

# IMERG daily ends 2026-11-13; GEOS-FP valid dates are D+1..D+3.
IMERG_TAGS = ["20261110", "20261111", "20261112", "20261113",
              "20261111_3day", "20261112_3day", "20261113_3day"]
SMAP_TAGS = ["20261110", "20261112"]
GEOS_TAGS = ["20261114", "20261115", "20261116"]
TAGS = {"imerg_early": IMERG_TAGS, "smap_l4": SMAP_TAGS, "geosfp": GEOS_TAGS}


@pytest.fixture
def fake_ee(monkeypatch):
    """Install the recording fake as `ee` in the modules now_tpi uses."""
    fake = Fake([])
    for mod in (now_tpi, tpi, common):
        monkeypatch.setattr(mod, "ee", fake)
    monkeypatch.setattr(common, "init", lambda project=None: "test-project")
    return fake


@pytest.fixture
def patched_tags(monkeypatch):
    monkeypatch.setattr(
        now_tpi, "collection_tags",
        lambda coll_id: TAGS[coll_id.rsplit("/", 1)[-1]])


def _called(log, path_suffix, args=None, **kw):
    for p, a, k in log:
        if not p.endswith(path_suffix):
            continue
        if args is not None and list(a) != args:
            continue
        if any(k.get(key) != val for key, val in kw.items()):
            continue
        return True
    return False


# ---------- pure tag / window logic ----------


def test_daily_tags_excludes_3day():
    assert now_tpi.daily_tags(IMERG_TAGS) == [
        "20261110", "20261111", "20261112", "20261113"]


def test_select_obs_rain3_prefers_3day_product():
    assert now_tpi.select_obs_rain3(IMERG_TAGS) == ["20261113_3day"]


def test_select_obs_rain3_falls_back_to_dailies():
    assert now_tpi.select_obs_rain3(["20261111", "20261112", "20261113"]) == [
        "20261111", "20261112", "20261113"]


def test_forecast_leads_are_three_newest_valid_dates():
    assert now_tpi.forecast_leads(GEOS_TAGS + ["20251001"]) == GEOS_TAGS


def test_day_plans_windows(patched_tags):
    plans = now_tpi.day_plans(IMERG_TAGS, SMAP_TAGS, GEOS_TAGS)
    assert [p["k"] for p in plans] == [0, 1, 2, 3]
    # d0: observed only — the newest _3day IMERG product, no forecast
    assert plans[0]["date_tag"] == "20261113"
    assert plans[0]["rain3"] == [("imerg_early", "20261113_3day")]
    # d1: last 2 observed dailies + first GEOS-FP lead
    assert plans[1]["rain3"] == [("imerg_early", "20261112"),
                                 ("imerg_early", "20261113"),
                                 ("geosfp", "20261114")]
    assert plans[1]["date_tag"] == "20261114"
    # d2: last observed daily + leads 1-2; d3: forecast only
    assert plans[2]["rain3"] == [("imerg_early", "20261113"),
                                 ("geosfp", "20261114"),
                                 ("geosfp", "20261115")]
    assert plans[3]["rain3"] == [("geosfp", t) for t in GEOS_TAGS]
    # SMAP L4 held at the latest tag on every day (not forecast)
    assert all(p["smap"] == "20261112" for p in plans)
    # DOY lookups match each day's product date
    assert plans[0]["doy"] == date(2026, 11, 13).timetuple().tm_yday
    assert plans[3]["doy"] == date(2026, 11, 16).timetuple().tm_yday


def test_day_plans_rejects_missing_forecasts():
    with pytest.raises(ValueError, match="geosfp"):
        now_tpi.day_plans(IMERG_TAGS, SMAP_TAGS, ["20261114"])


# ---------- asset naming ----------


def test_derived_asset_ids():
    ids = now_tpi.derived_asset_ids("test-project")
    assert len(ids) == 16
    assert ids["tpi_slide_d0"] == f"{ROOT}/now/tpi_slide_d0"
    assert ids["tpi_flood_d3"] == f"{ROOT}/now/tpi_flood_d3"
    assert ids["r_d1"] == f"{ROOT}/now/r_d1"
    assert ids["s_d3"] == f"{ROOT}/now/s_d3"


def test_input_asset_ids(monkeypatch):
    monkeypatch.setattr(common, "GEE_PROJECT", "")
    ids = now_tpi.input_asset_ids()
    root = "projects/<GEE_PROJECT>/assets/tipping-point"
    assert ids["imerg_early"] == f"{root}/now/imerg_early"
    assert ids["geosfp"] == f"{root}/now/geosfp"
    assert ids["clim_rain"] == f"{root}/static/south_brazil/clim_rain"
    assert ids["hand"] == f"{root}/static/south_brazil/hand"


def test_collection_tags(fake_ee, monkeypatch):
    ids = [f"{ROOT}/now/imerg_early/20261111",
           f"{ROOT}/now/imerg_early/20261113_3day"]
    stub = SimpleNamespace(
        aggregate_array=lambda key: SimpleNamespace(getInfo=lambda: ids))
    monkeypatch.setattr(fake_ee, "ImageCollection", lambda path: stub)
    assert now_tpi.collection_tags("x") == ["20261111", "20261113_3day"]


# ---------- stack math / source selection (fake ee) ----------


def test_day_stack_reuses_tpi_scoring(fake_ee):
    log = fake_ee.log
    clim = fake_ee.node("clim")
    now_tpi.day_stack(fake_ee.node("rain3"), fake_ee.node("smap"), clim,
                      fake_ee.node("t_slide"), fake_ee.node("t_flood"))
    renames = {c[1][0] for c in log
               if c[0].endswith(".rename") and c[1] and isinstance(c[1][0], str)}
    assert {"R", "S", "TPI_slide", "TPI_flood"} <= renames
    sel = {c[1][0] for c in log if c[0] == "clim.select"}
    assert sel == {"p90", "p95", "p99"}            # climatology band names
    weights = {c[0].split(".")[0]: c[1][0]
               for c in log if c[0].endswith(".pow")}
    assert weights["smap"] == pytest.approx(0.3333)


def test_compute_selects_d0_vs_forecast(fake_ee, patched_tags):
    out = now_tpi.compute("test-project")
    assert out["dates"] == ["2026-11-13", "2026-11-14",
                            "2026-11-15", "2026-11-16"]
    log = fake_ee.log
    # observed _3day product and latest SMAP read as single images
    assert _called(log, "ee.Image", [f"{ROOT}/now/imerg_early/20261113_3day"])
    assert _called(log, "ee.Image", [f"{ROOT}/now/smap_l4/20261112"])
    for tag in GEOS_TAGS:
        assert _called(log, "ee.Image", [f"{ROOT}/now/geosfp/{tag}"])
    # climatology filtered by DOY for each of the 4 days
    doys = {k.get("doy") for k in now_tpi.day_plans(
        IMERG_TAGS, SMAP_TAGS, GEOS_TAGS)}
    for doy in doys:
        assert _called(log, "ee.Filter.eq", ["doy", doy])
    # 16 stamped single-band images
    assert len(out["images"]) == 16
    stamps = [a[0] for p, a, k in log
              if p.endswith(".set") and a and isinstance(a[0], dict)]
    assert len(stamps) == 16
    assert all(s["updated_at"] and s["data_date"] for s in stamps)
    assert {s["data_date"] for s in stamps} == set(out["dates"])


# ---------- run: exports + metrics merge ----------


def test_run_exports_and_merges_under_now(
        tmp_path, fake_ee, patched_tags, monkeypatch):
    monkeypatch.setattr(now_tpi, "_utcnow", lambda: "2026-11-14T00:00:00Z")
    path = tmp_path / "metrics.json"
    out = now_tpi.run(project="test-project", metrics_path=path)
    log = fake_ee.log
    assert len(out["exports"]) == 16
    for aid in now_tpi.derived_asset_ids("test-project").values():
        assert _called(log, "ee.batch.Export.image.toAsset", assetId=aid)
    m = json.loads(path.read_text())["cases"]["now"]
    assert m["event"] == "now"
    assert m["tpi_now_computed_at"] == "2026-11-14T00:00:00Z"
    assert m["tpi_now_dates"] == ["2026-11-13", "2026-11-14",
                                "2026-11-15", "2026-11-16"]
    assert m["forecast_source"] == "geos-fp"
    cols = m["collections"]
    assert cols["imerg_early"] == f"{ROOT}/now/imerg_early"
    assert cols["clim_rain"] == f"{ROOT}/static/south_brazil/clim_rain"
    assert cols["tpi_slide_d0"] == f"{ROOT}/now/tpi_slide_d0"


# ---------- CLI ----------


def test_dry_run_offline(capsys, fake_ee):
    def _boom(*a, **k):
        raise AssertionError("ee.Initialize must not run under --dry-run")

    import gee.now_tpi as nt
    prev = nt.common.init
    nt.common.init = _boom
    try:
        assert now_tpi.main(["--dry-run"]) == 0
    finally:
        nt.common.init = prev
    out = capsys.readouterr().out
    assert "now/tpi_slide_d0" in out and "now/s_d3" in out
    assert "now/imerg_early" in out and "static/south_brazil/clim_rain" in out
    assert "cases['now']" in out
    assert fake_ee.log == []                  # plan() never touches ee


def test_dry_run_uses_project_arg(capsys):
    assert now_tpi.main(["--dry-run", "--project", "myproj"]) == 0
    out = capsys.readouterr().out
    assert "projects/myproj/assets/tipping-point/now/geosfp" in out
