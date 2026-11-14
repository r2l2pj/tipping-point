"""Offline OPERA catalog validation tests with a recording EE fake."""
from __future__ import annotations

import json

import pytest

from gee import opera


class Fake:
    def __init__(self, log, path="ee", counts=None):
        self.log, self.path, self.counts = log, path, counts

    def __getattr__(self, name):
        return Fake(self.log, f"{self.path}.{name}", self.counts)

    def __call__(self, *args, **kwargs):
        self.log.append((self.path, args, kwargs))
        return Fake(self.log, self.path + "()", self.counts)

    def getInfo(self):
        if self.path.endswith(".bandNames()"):
            return [opera.WTR_BAND]
        return self.counts


@pytest.fixture
def fake(monkeypatch):
    log = []
    counts = {"tp": 3, "fp": 2, "fn": 1, "valid": 14, "cloud": 2,
              "observed": 14, "opera_scenes": 2, "hls_scenes": 2}
    monkeypatch.setattr(opera, "ee", Fake(log, counts=counts))
    monkeypatch.setattr(opera, "init", lambda: None)
    monkeypatch.setattr(opera, "aoi_geom", lambda cid: "AOI")
    monkeypatch.setattr(opera, "case_cfg", lambda cid: {"event_date": "2024-05-02"})
    monkeypatch.setattr(opera, "ASSET_ROOT", "projects/test/assets/tipping-point")
    return log, counts


def called(fake, suffix, args=None):
    return any(path.endswith(suffix) and (args is None or actual == args)
               for path, actual, kw in fake[0])


def manifest(tmp_path, rows):
    (tmp_path / "assets.json").write_text(json.dumps({"assets": rows}))


@pytest.mark.parametrize("case_id,collection,water", [
    ("rs_2024", "OPERA/DSWX/L3_V1/HLS", (1, 2)),
    ("wa_2025", "OPERA/DSWX/L3_V1/S1", (1, 3)),
])
def test_catalog_only_and_product_classes(tmp_path, fake, case_id, collection, water):
    manifest(tmp_path, [
        {"name": "flood_nisar", "gee_id": "flood-raster", "kind": "image"},
        {"name": "opera_wtr", "gee_id": "DO-NOT-USE"},
    ])
    res = opera.compute(case_id, tmp_path)
    assert res["opera"] == collection
    assert called(fake, "ee.ImageCollection", (collection,))
    assert called(fake, ".select", ("WTR_Water_classification",))
    assert called(fake, ".filterDate", ("2024-05-02", "2024-05-07"))
    assert all(called(fake, ".eq", (value,)) for value in water)
    assert called(fake, "ee.ImageCollection", ("OPERA/DSWX/L3_V1/HLS",))
    assert not called(fake, "ee.Image", ("DO-NOT-USE",))
    for name in ("cloud", "valid", "tp", "fp", "fn", "observed"):
        assert called(fake, ".rename", (name,))
    assert res["collections"]["cloud_pct_source"] == "catalog:OPERA/DSWX/L3_V1/HLS"


def test_local_only_raster_is_not_used(tmp_path, fake, monkeypatch):
    # L10: manifest image rows are 'local-only' placeholders — no such asset
    # can exist, so they must never reach ee.Image().
    monkeypatch.setattr(opera, "_asset_exists", lambda path: False)
    manifest(tmp_path, [
        {"name": "flood_nisar", "gee_id": "local-cog", "kind": "image",
         "status": "local-only"},
    ])
    res = opera.resolve_inputs("wa_2025", tmp_path)
    assert res["nisar"] is None
    assert not called(fake, "ee.Image", ("local-cog",))


def test_polygons_preferred_and_painted_over_background(tmp_path, fake):
    manifest(tmp_path, [
        {"name": "flood_nisar", "gee_id": "raster", "kind": "image",
         "status": "local-only"},
        {"name": "flood_nisar_20251210", "gee_id": "polygons", "kind": "table"},
    ])
    res = opera.compute("wa_2025", tmp_path)
    assert res["nisar_kind"] == "polygons"
    assert called(fake, "ee.FeatureCollection", ("polygons",))
    assert called(fake, "ee.Image.constant", (0,))
    assert called(fake, ".byte().paint")
    assert not called(fake, "ee.Image", ("raster",))


def test_legacy_flat_map_and_no_water_mask(tmp_path, fake):
    (tmp_path / "assets.json").write_text(json.dumps({
        "flood_polygons": "polygons", "water_nisar": "permanent-water", "opera_wtr": "old-opera",
    }))
    assert opera.resolve_inputs("wa_2025", tmp_path)["nisar"] == "polygons"


def test_raster_fallback(tmp_path, fake):
    res = opera.resolve_inputs("wa_2025", tmp_path)
    assert res["nisar"].endswith("/wa_2025/flood_nisar")
    assert called(fake, "ee.data.getAsset", ("projects/test/assets/tipping-point/wa_2025/flood_nisar",))


def test_merge_hand_computed(tmp_path, fake):
    path = tmp_path / "metrics.json"
    opera.run("wa_2025", metrics_path=path, data_root=tmp_path)
    case = json.loads(path.read_text())["cases"]["wa_2025"]
    assert case["iou"] == .5
    assert case["f1"] == pytest.approx(6 / 9)
    assert case["cloud_pct"] == pytest.approx(200 / 14)
    assert case["collections"]["opera_dswx"] == "OPERA_L3_DSWX-S1_V1"
    assert called(fake, "ee.ImageCollection", ("OPERA/DSWX/L3_V1/HLS",))
    assert called(fake, "ee.ImageCollection", ("OPERA/DSWX/L3_V1/S1",))
    assert sum(path.endswith(".bandNames") for path, a, kw in fake[0]) == 2


def test_missing_nisar_keeps_hls_cloud(tmp_path, fake, monkeypatch):
    monkeypatch.setattr(opera, "_asset_exists", lambda path: False)
    out = opera.run("rs_2024", merge=False, data_root=tmp_path)
    assert out["metrics"]["iou"] is None
    assert out["metrics"]["cloud_pct"] == pytest.approx(200 / 14)
    assert not called(fake, ".rename", ("tp",))


def test_empty_catalog_and_no_valid_pixels_are_null(tmp_path, fake):
    fake[1].update(valid=0, observed=0, opera_scenes=0, hls_scenes=0)
    path = tmp_path / "metrics.json"
    opera.run("wa_2025", metrics_path=path, data_root=tmp_path)
    case = json.loads(path.read_text())["cases"]["wa_2025"]
    assert all(case[key] is None for key in ("iou", "f1", "cloud_pct"))
    assert set(case["_null_reasons"]) >= {"iou", "f1", "cloud_pct"}
    assert called(fake, "ee.Algorithms.If")


def test_schema_mismatch_fails(fake, monkeypatch):
    monkeypatch.setattr(Fake, "getInfo", lambda self: ["B01_WTR"])
    with pytest.raises(RuntimeError, match="lacks WTR_Water_classification"):
        opera.confirm_catalog_schema()


def test_dry_run_is_offline(capsys, fake):
    assert opera.main(["--case", "rs_2024", "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "OPERA/DSWX/L3_V1/HLS" in output
    assert "flood polygons" in output and "flood_nisar" in output
    assert fake[0] == []


def test_tasks_skips_merge(tmp_path, fake, monkeypatch):
    monkeypatch.setattr(opera, "data_dir", lambda case: tmp_path)
    path = tmp_path / "metrics.json"
    assert opera.main(["--case", "wa_2025", "--tasks", "--metrics-path", str(path)]) == 0
    assert not path.exists()
