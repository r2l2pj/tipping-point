"""Offline tests for fetch.glc — requests.get is monkeypatched, no network."""
import json
import subprocess
import sys
from pathlib import Path

import pytest
from fetch import glc
from tp.events import load_event

REPO = Path(__file__).resolve().parents[2]


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _feature(props, geometry="__point__"):
    if geometry == "__point__":
        geometry = {"type": "Point", "coordinates": [-121.93, 48.27]}
    return {"type": "Feature", "geometry": geometry, "properties": props}


def _fixture_fc():
    return {
        "type": "FeatureCollection",
        "features": [
            _feature({
                "event_id": 5962,
                "event_date": 1395446400000,   # 2014-03-22 UTC (Oso)
                "event_title": "SR 530 Oso mudslide",
                "country_name": "United States",
                "country_code": "US",
                "latitude": 48.27,
                "longitude": -121.93,
                "source_name": "Seattle Times",
                "source_link": "http://example.com/oso",
                "landslide_trigger": "downpour",
                "fatality_count": 43,
                "location_description": "Oso, Snohomish County, WA",
            }),
            _feature({"event_id": 9999, "event_date": None}, geometry=None),
        ],
    }


def _patch(monkeypatch, tmp_path, payload):
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _Resp(payload)

    monkeypatch.setattr(glc.requests, "get", fake_get)
    monkeypatch.setattr(glc, "DATA_DIR", tmp_path)
    return captured


def test_query_params_wa():
    """Default query is bbox-only — no event_date filter for a reference
    catalog (community reports lag; a window would empty recent events)."""
    params = glc._query_params(load_event("wa-2025-12"))
    assert params["where"] == "1=1"
    assert "event_date" not in params["where"]
    assert params["geometry"] == "-122.6,47.0,-121.5,48.5"
    assert params["f"] == "geojson" and params["outFields"] == "*"


def test_query_params_window_opt_in():
    event = load_event("wa-2025-12")
    params = glc._query_params(event, window=event["window"])
    assert params["where"] == (
        "event_date >= DATE '2025-11-01' AND event_date <= DATE '2026-01-31'"
    )
    params = glc._query_params(event | {"glc_window": event["window"]})
    assert "event_date >= DATE '2025-11-01'" in params["where"]


def test_glc_catalog_parses_fixture(monkeypatch, tmp_path):
    captured = _patch(monkeypatch, tmp_path, _fixture_fc())
    out = glc.glc_catalog(load_event("wa-2025-12"))

    assert captured["url"] == glc.SERVICE_URL
    assert captured["params"]["where"] == "1=1"

    fc = json.loads(out.read_text())
    assert out == tmp_path / "wa_2025" / "geojson" / "landslide_catalog.geojson"
    assert fc["type"] == "FeatureCollection"
    assert fc["source"] == glc.SOURCE_NAME and fc["url"] == glc.SOURCE_URL
    assert len(fc["features"]) == 1           # null-geometry feature dropped

    props = fc["features"][0]["properties"]
    assert props["event_date"] == "2014-03-22"          # epoch ms -> ISO
    assert props["country_name"] == "United States"
    assert props["source_link"] == "http://example.com/oso"
    assert props["location_description"].startswith("Oso")
    assert props["latitude"] == 48.27 and props["longitude"] == -121.93
    assert props["source"] == glc.SOURCE_NAME and props["url"] == glc.SOURCE_URL
    assert fc["features"][0]["geometry"]["type"] == "Point"


@pytest.mark.parametrize(("raw", "expected", "precision"), [
    (1395446400000, "2014-03-22T19:00:00Z", "date"),
    ("2025-12-10", "2025-12-10T20:00:00Z", "date"),
    ("2025-12-10T08:30:00-08:00", "2025-12-10T16:30:00Z", "hour"),
    ("2025-12-10T08:30:00", "2025-12-10T16:30:00Z", "hour"),
    ("2025-12-10T08:30:00Z", "2025-12-10T08:30:00Z", "hour"),
    (None, None, "none"),
    ("invalid", None, "none"),
])
def test_glc_timestamp_properties(monkeypatch, tmp_path, raw, expected, precision):
    _patch(monkeypatch, tmp_path, {"features": [_feature({"event_date": raw})]})
    out = glc.glc_catalog(load_event("wa-2025-12"))
    props = json.loads(out.read_text())["features"][0]["properties"]
    assert props["t_precision"] == precision
    if expected is None:
        assert "t_utc" not in props
    else:
        assert props["t_utc"] == expected


def test_glc_catalog_empty_result(monkeypatch, tmp_path):
    _patch(monkeypatch, tmp_path, {"type": "FeatureCollection", "features": []})
    out = glc.glc_catalog(load_event("wa-2025-12"))
    fc = json.loads(out.read_text())
    assert fc["type"] == "FeatureCollection" and fc["features"] == []


def test_aux_script_help_no_siblings():
    """--help works even when fetch.boundaries / fetch.inventory are absent."""
    script = REPO / "pipeline" / "scripts" / "12_aux_products.py"
    r = subprocess.run(
        [sys.executable, str(script), "--help"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert r.returncode == 0
    assert "--event" in r.stdout and "--all" in r.stdout
