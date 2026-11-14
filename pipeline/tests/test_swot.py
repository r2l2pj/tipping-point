import pandas as pd
import pytest
from tp import swot
from tp.events import artifact_stem

CSV_A = """reach_id,time_str,wse
71224100223,2025-12-01T00:11:00Z,3.21
71224100223,2025-12-22T00:12:30Z,4.56
"""

CSV_B = """reach_id,time_str,wse
71224100231,2025-12-10T00:05:00Z,1.02
71224100231,2025-12-05T00:05:00Z,0.98
"""

CSV_FILL = """reach_id,time_str,wse
71224100223,2025-12-01T00:11:00Z,3.21
71224100223,2025-12-08T00:11:00Z,-999999999999
71224100223,2025-12-22T00:12:30Z,4.56
"""


class _Resp:
    def __init__(self, csv: str):
        self._csv = csv

    def raise_for_status(self):
        pass

    def json(self):
        return {"results": {"csv": self._csv}}


def _patch_get(monkeypatch, by_reach: dict[str, str]):
    def fake_get(url, params=None, timeout=None):
        return _Resp(by_reach[params["feature_id"]])

    monkeypatch.setattr(swot.requests, "get", fake_get)


def test_fetch_reach_parses_csv(monkeypatch):
    _patch_get(monkeypatch, {"71224100223": CSV_A})
    df = swot.fetch_reach("71224100223", "2025-12-01", "2025-12-31")
    assert list(df.columns) == ["reach_id", "time_str", "river_m"]
    assert df["river_m"].tolist() == [3.21, 4.56]
    assert df["time_str"].tolist()[0] == "2025-12-01T00:11:00Z"


def test_fetch_reach_drops_fill_values(monkeypatch):
    _patch_get(monkeypatch, {"71224100223": CSV_FILL})
    df = swot.fetch_reach("71224100223", "2025-12-01", "2025-12-31")
    assert len(df) == 2
    assert (df["river_m"] > -1e9).all()


def test_river_series_merges_reaches_sorted(monkeypatch, tmp_path):
    _patch_get(monkeypatch, {"71224100223": CSV_A, "71224100231": CSV_B})
    out = tmp_path / "s.json"
    df = swot.river_series(["71224100223", "71224100231"],
                           "2025-12-01", "2025-12-31", out)
    assert df["time_str"].is_monotonic_increasing
    assert set(df["reach_id"]) == {71224100223, 71224100231}
    records = pd.read_json(out)
    assert records["time_str"].tolist() == df["time_str"].tolist()


def test_series_path_uses_artifact_stem():
    p = swot.series_path("wa-2025-12")
    assert p.name == f"{artifact_stem('wa-2025-12', 'swot')}.json"
    assert p.name == "wa_2025_swot_20251210.json"
    assert p.parent.name == "series"


def test_resolve_reach_ids_missing():
    with pytest.raises(ValueError, match="reach"):
        swot.resolve_reach_ids(None, {"id": "fake-1"})
    with pytest.raises(ValueError):
        swot.resolve_reach_ids(None, {"swot": {"reach_ids": []}})
    with pytest.raises(ValueError):
        swot.resolve_reach_ids(None, {"swot": None})


def test_resolve_reach_ids_default_and_override():
    event = {"swot": {"reach_ids": ["1", "2"]}}
    assert swot.resolve_reach_ids(None, event) == ["1", "2"]
    assert swot.resolve_reach_ids(["3"], event) == ["3"]
