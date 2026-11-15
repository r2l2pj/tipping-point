"""Hermetic tests for tools/imerg_vs_gauge.py — no network, no data/ writes."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools import imerg_vs_gauge as ivg

ASCII = """Dataset: 3B-DAY.MS.MRG.3IMERG.20240420-S000000-E235959.V07B.nc4
precipitation.lat, -30.15, -30.05, -29.95
precipitation.precipitation[precipitation.time=16176][precipitation.lon=-52.35], 1, 2, 3
precipitation.precipitation[precipitation.time=16176][precipitation.lon=-52.25], 4, 5, 6
"""

CSV = """REGIAO: S
UF: RS
ESTACAO: TEST STATION
CODIGO (WMO): A999
LATITUDE: -30,0
LONGITUDE: -51,0
ALTITUDE: 10
DATA DE FUNDACAO: 01/01/2000
Data;Hora UTC;PRECIPITAÇÃO TOTAL, HORÁRIO (mm);
2024-05-01;0000 UTC;1,0;
2024-05-01;0100 UTC;2,0;
2024-05-02;0000 UTC;0,5;
2024-05-02;0100 UTC;;
"""


def test_cell_index_known_point():
    # Porto Alegre ~(-51.17, -30.05) sits in cell centred (-51.15, -30.05)
    i, j = ivg.cell_index(-51.1747, -30.0536)
    assert (i, j) == (1288, 599)
    lon, lat = ivg.cell_center(i, j)
    assert abs(lon - (-51.15)) < 1e-9 and abs(lat - (-30.05)) < 1e-9


def test_parse_ascii_shape_and_orientation():
    arr = ivg.parse_ascii(ASCII, n_i=2, n_j=3)
    assert arr.shape == (2, 3)
    # line order = lon columns; csv order = lat values
    assert arr[0, 0] == 1 and arr[0, 2] == 3
    assert arr[1, 0] == 4 and arr[1, 2] == 6


def test_parse_ascii_rejects_wrong_shape():
    with pytest.raises(ValueError):
        ivg.parse_ascii(ASCII, n_i=3, n_j=3)


def test_parse_station_hours_counts():
    days = ivg.parse_station_hours(CSV)
    assert days["2024-05-01"] == {"mm": 3.0, "hours": 2}
    assert days["2024-05-02"] == {"mm": 0.5, "hours": 1}


def test_pair_days_missing_hour_skip_and_join():
    gauge = {
        "2024-05-01": {"mm": 10.0, "hours": 24},
        "2024-05-02": {"mm": 8.0, "hours": 21},   # 3 missing > 2 -> skip
        "2024-05-03": {"mm": 4.0, "hours": 22},   # 2 missing -> keep
        "2024-05-04": {"mm": 1.0, "hours": 24},   # imerg missing -> skip
        # 2024-05-05 absent -> 'no gauge data'
    }
    dates = [f"2024-05-0{d}" for d in range(1, 6)]
    imerg = {"2024-05-01": 9.0, "2024-05-03": 5.0, "2024-05-05": 1.0}
    pairs, skipped = ivg.pair_days(gauge, dates, imerg.get, 2)
    assert [p["date"] for p in pairs] == ["2024-05-01", "2024-05-03"]
    assert pairs[0] == {"date": "2024-05-01", "gauge_mm": 10.0,
                        "gauge_hours": 24, "imerg_mm": 9.0}
    reasons = {s["date"]: s["reason"] for s in skipped}
    assert "missing gauge hours" in reasons["2024-05-02"]
    assert reasons["2024-05-04"] == "no IMERG value"
    assert reasons["2024-05-05"] == "no gauge data"


def test_station_stats_exact_values():
    pairs = [
        {"date": "2024-04-27", "gauge_mm": 10.0, "imerg_mm": 12.0},
        {"date": "2024-04-28", "gauge_mm": 20.0, "imerg_mm": 24.0},
        {"date": "2024-05-02", "gauge_mm": 30.0, "imerg_mm": 36.0},
        {"date": "2024-05-10", "gauge_mm": 40.0, "imerg_mm": 48.0},
    ]
    st = ivg.station_stats(pairs, ("2024-04-27", "2024-05-06"))
    assert st["n_days"] == 4
    assert st["r"] == pytest.approx(1.0)
    # totals: gauge 100, imerg 120 -> +20%
    assert st["bias_pct"] == pytest.approx(20.0)
    # event window 04-27..05-06: gauge 60, imerg 72 -> 1.2
    assert st["event_total_gauge_mm"] == 60.0
    assert st["event_total_imerg_mm"] == 72.0
    assert st["event_total_ratio"] == pytest.approx(1.2)


def test_station_stats_nulls_carry_reasons():
    st = ivg.station_stats(
        [{"date": "2024-05-01", "gauge_mm": 0.0, "imerg_mm": 3.0}],
        ("2024-04-27", "2024-05-06"))
    assert st["r"] is None and st["bias_pct"] is None
    assert st["event_total_ratio"] is None
    assert st["stat_reasons"]["bias_pct"] == "zero gauge total in window"


def test_build_metrics_median_and_shape():
    stations = [
        {"id": "A1", "name": "n1", "lat": -30.0, "lon": -51.0, "n_days": 5,
         "r": 0.8, "bias_pct": 10.0, "event_total_gauge_mm": 50.0,
         "event_total_imerg_mm": 60.0, "event_total_ratio": 1.2},
        {"id": "A2", "name": "n2", "lat": -29.0, "lon": -52.0, "n_days": 7,
         "r": 0.6, "bias_pct": -5.0, "event_total_gauge_mm": 40.0,
         "event_total_imerg_mm": 20.0, "event_total_ratio": 0.5},
    ]
    m = ivg.build_metrics(stations, "2024-04-20", "2024-05-15")
    assert m["window"] == "2024-04-20..2024-05-15"
    assert m["n_stations"] == 2 and m["n_station_days"] == 12
    assert m["pearson_r_median"] == pytest.approx(0.7)
    assert m["bias_pct_median"] == pytest.approx(2.5)
    assert m["event_total_ratio_median"] == pytest.approx(0.85)
    assert m["caveat"] == "point gauge vs 0.1 deg cell"
    assert [s["id"] for s in m["stations"]] == ["A1", "A2"]


def test_merge_into_metrics_json(tmp_path):
    from tp.metrics import merge_metrics
    path = tmp_path / "metrics.json"
    merge_metrics("rs_2024", {"imerg_vs_gauge": {"n_stations": 7}},
                  path=path)
    doc = __import__("json").loads(path.read_text())
    assert doc["cases"]["rs_2024"]["imerg_vs_gauge"]["n_stations"] == 7
