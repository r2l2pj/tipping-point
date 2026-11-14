"""Offline tests for fetch.boundaries — synthetic features, no network."""
import json
import sys
from pathlib import Path

import pytest
from shapely.geometry import box, mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fetch import boundaries

RS_EVENT = {
    "id": "rs-2024-05",
    "country": "BR",
    "aoi": {"bbox": [-52.5, -30.5, -50.5, -29.0]},
}


def _poly_feature(w, s, e, n, **props):
    return {"type": "Feature", "geometry": mapping(box(w, s, e, n)),
            "properties": props}


def test_intersects_aoi_filters_features():
    aoi = box(-52.5, -30.5, -50.5, -29.0)
    inside = _poly_feature(-52.0, -30.0, -51.9, -29.9)
    edge = _poly_feature(-50.6, -29.1, -50.4, -29.0)      # touches the boundary
    outside = _poly_feature(-49.0, -28.0, -48.9, -27.9)
    null_geom = {"type": "Feature", "geometry": None, "properties": {}}

    assert boundaries.intersects_aoi(inside, aoi)
    assert boundaries.intersects_aoi(edge, aoi)
    assert not boundaries.intersects_aoi(outside, aoi)
    assert not boundaries.intersects_aoi(null_geom, aoi)


def test_municipal_boundaries_filters_and_writes(tmp_path, monkeypatch):
    monkeypatch.setattr(boundaries, "event_data_dir", lambda eid: tmp_path / eid)
    malhas_fc = {
        "type": "FeatureCollection",
        "features": [
            _poly_feature(-52.0, -30.0, -51.9, -29.9, codarea="4300034"),
            _poly_feature(-49.0, -28.0, -48.9, -27.9, codarea="4300000"),
        ],
    }
    municipios = [{"id": 4300034, "nome": "Aceguá"},
                  {"id": 4300000, "nome": "Fora"}]
    calls = []

    def fake_get_json(url, **params):
        calls.append(url)
        return malhas_fc if "malhas" in url else municipios

    monkeypatch.setattr(boundaries, "_get_json", fake_get_json)
    out = boundaries.municipal_boundaries(RS_EVENT)

    assert out == tmp_path / "rs_2024" / "geojson" / "municipal_boundaries.geojson"
    fc = json.loads(out.read_text())
    assert fc["type"] == "FeatureCollection"
    assert len(fc["features"]) == 1
    props = fc["features"][0]["properties"]
    assert props["name"] == "Aceguá"
    assert props["admin"] == "municipio"
    assert props["source"] == "IBGE"
    assert props["license"]
    assert props["url"].startswith("https://servicodados.ibge.gov.br")
    assert len(calls) == 2  # malhas + localidades


def test_municipal_boundaries_fetch_error_stub(tmp_path, monkeypatch):
    monkeypatch.setattr(boundaries, "event_data_dir", lambda eid: tmp_path / eid)

    def boom(url, **params):
        raise OSError("no route to host")

    monkeypatch.setattr(boundaries, "_get_json", boom)
    out = boundaries.municipal_boundaries(RS_EVENT)

    fc = json.loads(out.read_text())
    assert fc["features"] == []
    assert "fetch_error" in fc
    assert "no route to host" in fc["fetch_error"]


@pytest.mark.parametrize("layer_admin", boundaries.TIGERWEB_LAYERS.items())
def test_tigerweb_layers_map_to_admin(tmp_path, monkeypatch, layer_admin):
    # one-layer sanity check of the TIGERweb response handling
    monkeypatch.setattr(boundaries, "event_data_dir", lambda eid: tmp_path / eid)
    layer, admin = layer_admin
    fc_in = {"type": "FeatureCollection",
             "features": [_poly_feature(-122.0, 47.5, -121.9, 47.6,
                                      BASENAME="X", NAME="X city", GEOID="53")]}

    def fake_get_json(url, **params):
        if f"/{layer}/query" in url:
            return fc_in
        return {"type": "FeatureCollection", "features": []}

    monkeypatch.setattr(boundaries, "_get_json", fake_get_json)
    event = {"id": "wa-2025-12", "country": "US",
             "aoi": {"bbox": [-122.6, 47.0, -121.5, 48.5]}}
    out = boundaries.municipal_boundaries(event)
    fc = json.loads(out.read_text())
    assert [f["properties"]["admin"] for f in fc["features"]] == [admin]
    assert fc["features"][0]["properties"]["source"] == "US Census TIGER"
