"""Offline tests for tools/wa_swot_series.py — fake fetcher, no network."""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

import wa_swot_series as tool

CSV_HEADER = "reach_id,time_str,wse,wse_u,reach_q,river_name,p_lon,p_lat"


def _rows(*lines):
    import csv
    import io
    text = CSV_HEADER + "\n" + "\n".join(lines) + "\n"
    return list(csv.DictReader(io.StringIO(text)))


RS_EVENT = {
    "id": "rs-2024-05",
    "start_date": "2024-04-27",
    "reference_date": "2024-05-02",
    "window": {"start": "2024-04-20", "end": "2024-05-31"},
    "swot": {
        "reach_ids": ["64120300041", "64120100045"],
        "window": {"start": "2024-04-01", "end": "2024-06-30"},
        "event_dates": {"start": "2024-04-29", "peak": "2024-05-05"},
    },
}

WA_EVENT = {
    "id": "wa-2025-12",
    "start_date": "2025-12-08",
    "reference_date": "2025-12-10",
    "window": {"start": "2025-11-01", "end": "2026-01-31"},
    "swot": {"reach_ids": ["78310800021"]},
}


def test_rs_uses_swot_window_and_event_dates_overrides():
    """swot.window/event_dates beat the event window + start/reference dates."""
    def fake(rid, start, end):
        assert (start, end) == ("2024-04-01", "2024-06-30")
        if rid == "64120100045":
            return None, "Hydrocron HTTP 400: not found"
        return _rows(
            "64120300041,2024-04-04T05:47:23Z,4.2507,0.10969,2,no_data,-52.02,-29.94",
            "64120300041,2024-05-06T11:46:38Z,14.2538,0.10084,2,no_data,-52.02,-29.94",
            "64120300041,2024-04-10T00:00:00Z,-999999999999.0,-999999999999.0,3,no_data,-52.02,-29.94",
        ), None

    doc = tool.build("rs_2024", RS_EVENT, fetcher=fake)
    assert doc["case"] == "rs_2024"
    assert doc["window"] == ["2024-04-01", "2024-06-30"]
    assert doc["event_dates"] == {"start": "2024-04-29", "peak": "2024-05-05"}
    reach = doc["reaches"][0]
    assert reach["name"] is None            # 'no_data' river_name never surfaces
    assert len(reach["series"]) == 2        # fill row dropped
    assert reach["dropped_fill"] == 1
    assert doc["reaches"][1]["series"] == []
    assert "64120100045" in doc["_null_reasons"]


def test_wa_falls_back_to_event_window_and_dates():
    """No swot.window -> event window + start_date/reference_date (wa_2025)."""
    def fake(rid, start, end):
        assert (start, end) == ("2025-11-01", "2026-01-31")
        return _rows(
            "78310800021,2025-12-10T10:37:59Z,3.137,0.10283,2,Skagit River,-122.4,48.4",
        ), None

    doc = tool.build("wa_2025", WA_EVENT, fetcher=fake)
    assert doc["window"] == ["2025-11-01", "2026-01-31"]
    assert doc["event_dates"] == {"start": "2025-12-08", "peak": "2025-12-10"}
    reach = doc["reaches"][0]
    assert reach["name"] == "Skagit River"
    assert reach["series"][0] == {"time": "2025-12-10T10:37:59Z", "wse_m": 3.137,
                                  "wse_u_m": 0.10283, "quality": 2}


def test_fill_quality_and_out_of_window():
    """reach_q fill -> quality null; obs outside the window are counted."""
    rows = _rows(
        "1,2024-05-06T11:46:38Z,14.25,-999999999999.0,-999999999999.0,no_data,-52.0,-29.9",
        "1,2024-07-15T00:00:00Z,5.0,0.1,1,no_data,-52.0,-29.9",
    )
    reach = tool.to_reach("1", rows, "2024-04-01", "2024-06-30")
    assert reach["series"] == [{"time": "2024-05-06T11:46:38Z", "wse_m": 14.25,
                                "wse_u_m": None, "quality": None}]
    assert reach["dropped_out_of_window"] == 1


def test_no_reach_ids_reason_names_event():
    doc = tool.build("rs_2024", {"id": "rs-2024-05",
                                 "window": {"start": "2024-04-01",
                                            "end": "2024-06-30"}},
                     fetcher=lambda *a: (_ for _ in ()).throw(AssertionError))
    assert doc["reaches"] == []
    assert "events/rs-2024-05.json" in doc["_null_reasons"]["reaches"]


def test_peak_rise_vs_pre_event_median():
    reach = {"series": [
        {"time": "2024-04-04T00:00:00Z", "wse_m": 4.0},
        {"time": "2024-04-15T00:00:00Z", "wse_m": 4.2},
        {"time": "2024-05-06T00:00:00Z", "wse_m": 14.25},
    ]}
    assert tool._peak_rise(reach, "2024-04-29") == 10.15
    assert tool._peak_rise({"series": []}, "2024-04-29") is None
    assert tool._peak_rise(reach, None) is None
