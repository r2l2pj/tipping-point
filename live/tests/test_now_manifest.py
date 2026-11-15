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


def test_observed_floods_grouped_never_tpi(tmp_path):
    sq = lambda x0: {"type": "Polygon", "coordinates": [[[x0, -29.9], [x0 + 0.01, -29.9],
                     [x0 + 0.01, -29.89], [x0, -29.89], [x0, -29.9]]]}
    _write(tmp_path / "geojson/municipal_boundaries.geojson", {
        "type": "FeatureCollection", "features": [{
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[[-52, -30], [-51, -30],
                         [-51, -29], [-52, -29], [-52, -30]]]},
            "properties": {"name": "Canoas",
                           "url": "https://x/api/v3/malhas/estados/RS"}}]})
    _write(tmp_path / "response/flood_dswx.geojson", {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": sq(-51.5), "properties": {
            "source": "opera-dswx-s1", "acquired": "2026-11-10T08:00:00Z", "area_km2": 1.0, "people": 3}},
        {"type": "Feature", "geometry": sq(-51.4), "properties": {
            "source": "opera-dswx-s1", "acquired": "2026-11-14T09:00:00Z", "area_km2": 2.0, "people": None}}]})
    _write(tmp_path / "alerts.json", {"candidates": [], "paused": False})
    now_manifest.add_observed_to_alerts(tmp_path)
    a = json.loads((tmp_path / "alerts.json").read_text())
    assert a["candidates"] == []
    (o,) = a["observed"]
    assert o["kind"] == "observed" and o["hazard"] == "flood"
    assert o["confidence"] == "standard"
    assert o["municipality"] == "Canoas" and o["uf"] == "RS"
    assert o["area_km2"] == 3.0 and o["people"] == 3 and o["polygons"] == 2
    assert o["acquired"] == "2026-11-14T09:00:00Z"
    assert "class" not in o and "EMERGENCY" not in json.dumps(o).upper()


def test_observed_split_by_confidence(tmp_path):
    sq = lambda x0: {"type": "Polygon", "coordinates": [[[x0, -29.9], [x0 + 0.01, -29.9],
                     [x0 + 0.01, -29.89], [x0, -29.89], [x0, -29.9]]]}
    feats = [{"type": "Feature", "geometry": sq(-51.5 + 0.02 * i), "properties": {
        "source": "opera-dswx-s1", "area_km2": a, "confidence": c}}
        for i, (a, c) in enumerate([(1.0, "standard"), (2.0, "lower"), (4.0, "lower")])]
    _write(tmp_path / "response/flood_dswx.geojson", {"type": "FeatureCollection", "features": feats})
    obs = now_manifest.observed_floods(tmp_path)
    by = {o["confidence"]: o["area_km2"] for o in obs}
    assert by == {"standard": 1.0, "lower": 6.0}
