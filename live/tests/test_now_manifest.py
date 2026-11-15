import json

from live import now_manifest


def _write(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj) if not isinstance(obj, str) else obj)


def test_finalize_merges_fragment_and_prunes(tmp_path):
    _write(tmp_path / "manifest.json", {
        "updated_at": "2026-11-15T06:00:00Z",
        "data_dates": {"imerg": "2026-11-14"},
        "products": {"critical_areas": "geojson/critical_areas.geojson",
                     "summary": "summary.json",
                     "permanent_water": "geojson/permanent_water.geojson"},
    })
    _write(tmp_path / "geojson/critical_areas.geojson", "{}")
    _write(tmp_path / "summary.json", "{}")
    _write(tmp_path / "response/flood_nisar.geojson", "{}")
    _write(tmp_path / "response/nisar_meta.json", "{}")
    _write(tmp_path / "geojson/priority_zones.geojson", "{}")
    _write(tmp_path / "response/manifest_node.json", {
        "data_dates": {"nisar-gcov": "2026-11-06T21:16:03Z"},
        "products": {"flood_dswx": "response/flood_dswx.geojson",
                     "flood_nisar": "response/flood_nisar.geojson"},
        "_null_reasons": {"opera-dswx-s1": "no granules"},
        "response": {"schema": "tipping-point/response@1"},
    })
    m = now_manifest.finalize(tmp_path)
    assert m["products"] == {
        "critical_areas": "geojson/critical_areas.geojson",
        "summary": "summary.json",
        "flood_nisar": "response/flood_nisar.geojson",
        "nisar_meta": "response/nisar_meta.json",
        "priority_zones": "geojson/priority_zones.geojson",
    }  # permanent_water + flood_dswx absent on disk -> dropped
    assert m["data_dates"] == {"imerg": "2026-11-14",
                               "nisar-gcov": "2026-11-06T21:16:03Z"}
    assert m["_null_reasons"] == {"opera-dswx-s1": "no granules"}
    assert m["response"]["schema"] == "tipping-point/response@1"
    assert json.loads((tmp_path / "manifest.json").read_text()) == m
    assert not (tmp_path / "manifest.json.tmp").exists()


def test_finalize_without_fragment_keeps_manifest(tmp_path):
    _write(tmp_path / "manifest.json", {"products": {"summary": "summary.json"}})
    _write(tmp_path / "summary.json", "{}")
    m = now_manifest.finalize(tmp_path)
    assert m["products"] == {"summary": "summary.json"}
    assert "response" not in m
