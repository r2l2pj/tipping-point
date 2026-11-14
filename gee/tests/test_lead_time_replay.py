"""Offline latency-plan and graph-construction checks."""
from datetime import timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from gee import lead_time


def test_offline_plan(monkeypatch, capsys):
    monkeypatch.setattr(lead_time.common, "init", lambda *_: pytest.fail("must stay offline"))
    assert lead_time.main(["--case", "rs_2024", "--dry-run"]) == 0
    assert '"step_h": 3' in capsys.readouterr().out
    plan = lead_time.replay_plan("rs_2024")
    assert "system:time_start <= t-75h" in plan["lead_time_assumptions"]["smap"]
    assert "interval end <= t-72h" in plan["lead_time_assumptions"]["smap"]
    assert len(plan["frames"]) == 16 * 8
    first, second, last = plan["frames"][0], plan["frames"][1], plan["frames"][-1]
    assert first["band"] == "20240422T0000"
    assert last["band"] == "20240507T2100"
    assert pd.Timestamp(second["at"]) - pd.Timestamp(first["at"]) == timedelta(hours=3)
    assert pd.Timestamp(first["at"]) - pd.Timestamp(first["imerg_interval_end_lte"]) == timedelta(hours=4)
    assert pd.Timestamp(first["at"]) - pd.Timestamp(first["smap_interval_end_lte"]) == timedelta(hours=72)
    with pytest.raises(SystemExit):
        lead_time.main(["--case", "rs_2024"])


def test_replay_graph_cutoffs_and_no_geos(monkeypatch):
    calls = []

    class Image:
        def __init__(self, name):
            self.name = name

        def __getattr__(self, method):
            def call(*args):
                calls.append((self.name, method, args))
                return self
            return call

    class Date:
        def __init__(self, at):
            self.at = pd.Timestamp(at)

        def advance(self, amount, unit):
            return Date(self.at + pd.Timedelta(**{unit + "s": amount}))

        def millis(self):
            return self.at.value // 1_000_000

    monkeypatch.setattr(lead_time, "ee", SimpleNamespace(
        Date=Date, ImageCollection=lambda name: Image(name),
        Filter=SimpleNamespace(lte=lambda field, value: (field, value))))
    monkeypatch.setattr(lead_time.tpi, "rain_score", lambda *args: Image("rain"))
    monkeypatch.setattr(lead_time.tpi, "geomean", lambda *args: Image("tpi"))
    lead_time.replay_at("2024-05-01T12:00Z", Image("early"), Image("clim"), (Image("terrain"),) * 2)
    filters = [args for name, method, args in calls if name == "early" and method == "filterDate"]
    assert filters[0][0].at == pd.Timestamp("2024-04-28T08:00Z")
    assert filters[0][1].at == pd.Timestamp("2024-05-01T08:00Z")
    assert ("early", "eq", (144,)) in calls
    smap = lead_time.common.COL_SMAP
    cutoff = next(args[0] for name, method, args in calls if name == smap and method == "filter")
    assert cutoff == ("system:time_start", pd.Timestamp("2024-04-28T09:00Z").value // 1_000_000)
    assert (smap, "sort", ("system:time_start", False)) in calls
    assert (smap, "limit", (1,)) in calls
    assert not any("GEOS" in name for name, _, _ in calls)
