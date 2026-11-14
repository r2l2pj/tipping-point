import json

import pytest
from fetch import featureserver, reference, wgs
from tp.events import load_event


@pytest.mark.parametrize(("raw", "clock", "expected", "precision"), [
    (1765324800000, None, "2025-12-10T20:00:00Z", "date"),
    ("2025-12-10", None, "2025-12-10T20:00:00Z", "date"),
    ("2025-07-10", None, "2025-07-10T19:00:00Z", "date"),
    ("2025-12-10", "08:30", "2025-12-10T16:30:00Z", "hour"),
    ("2025-12-10", "8:30 AM", "2025-12-10T16:30:00Z", "hour"),
    ("2025-12-10", "unknown", "2025-12-10T20:00:00Z", "date"),
    ("2025-12-10T08:30:00Z", None, "2025-12-10T08:30:00Z", "hour"),
    ("2025-12-10T08:30:00", None, "2025-12-10T16:30:00Z", "hour"),
    (None, "08:30", None, "none"),
    ("invalid", None, None, "none"),
])
def test_precision(raw, clock, expected, precision):
    props = wgs._clean_properties({"event_date": raw, "event_time": clock})
    assert props["t_precision"] == precision
    assert props.get("t_utc") == expected


@pytest.fixture
def fake_http(monkeypatch):
    calls = []
    pages = []

    def get(url, params, timeout):
        calls.append((url, params))
        payload = pages.pop(0)

        class Response:
            def raise_for_status(self):
                pass

            def json(self):
                return payload

        return Response()

    monkeypatch.setattr(featureserver.requests, "get", get)
    return calls, pages


def point(date):
    return {"geometry": {"type": "Point", "coordinates": [-122, 48]},
            "properties": {"event_date": date, "objectid": 1, "email": "private"}}


def test_bbox_all_dates_pagination_and_replacement(tmp_path, monkeypatch, fake_http):
    calls, pages = fake_http
    pages.extend([
        {"features": [point("2014-03-22"), point(None)], "exceededTransferLimit": True},
        {"features": [point("2025-12-10"), {"geometry": None}]},
    ])
    monkeypatch.setattr(wgs, "DATA_DIR", tmp_path)
    path = tmp_path / "wa_2025/geojson/landslide_catalog.geojson"
    path.parent.mkdir(parents=True)
    path.write_text('{"features": [{"properties": {"source": "GLC"}}]}')
    event = load_event("wa-2025-12")
    event["glc_window"] = event["window"]
    out = wgs.wgs_catalog(event)
    fc = json.loads(out.read_text())
    assert out == path
    assert len(fc["features"]) == 3
    for feature in fc["features"]:
        props = feature["properties"]
        assert feature["geometry"]["type"] == "Point"
        assert props["source"] == wgs.SOURCE_NAME
        assert props["url"] == wgs.SOURCE_URL
        assert props["license"] == wgs.LICENSE
        assert "email" not in props
    assert calls[0][1]["where"] == "1=1"
    assert calls[0][1]["geometry"] == "-122.6,47.0,-121.5,48.5"
    assert calls[1][1]["resultOffset"] == 2


def test_reference_fetches_and_ingests(tmp_path, monkeypatch, fake_http):
    _, pages = fake_http
    pages.append({"features": [point("2025-12-10")]})
    monkeypatch.setattr(wgs, "DATA_DIR", tmp_path)
    monkeypatch.setattr(reference, "DATA_DIR", tmp_path)
    calls = []
    monkeypatch.setattr(reference, "ingest_table", lambda path, **kw: calls.append((path, kw)))
    monkeypatch.setattr(reference, "append_manifest", lambda *args: None)
    result = reference.run(["--kind", "wgs", "--case", "wa_2025"])
    assert result[0]["status"] == "ingested"
    path, kw = calls[0]
    assert json.loads(path.read_text())["source"] == wgs.SOURCE_NAME
    assert kw["asset_id"].endswith("/wa_2025/landslide_catalog")


def test_reference_dry_run_and_case_guard(tmp_path, monkeypatch, fake_http):
    calls, _ = fake_http
    monkeypatch.setattr(reference, "DATA_DIR", tmp_path)
    result = reference.run(["--kind", "wgs", "--case", "wa_2025", "--dry-run"])
    assert result[0]["status"] == "dry-run"
    assert not calls
    assert not list(tmp_path.iterdir())
    with pytest.raises(ValueError, match="wa_2025"):
        reference.run(["--kind", "wgs", "--case", "rs_2024"])


def test_service_error(fake_http):
    _, pages = fake_http
    pages.append({"error": {"message": "unavailable"}})
    with pytest.raises(RuntimeError, match="unavailable"):
        wgs.wgs_catalog(load_event("wa-2025-12"))
