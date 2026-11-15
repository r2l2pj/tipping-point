"""Published metrics critical_* scalars == the series peak row (one definition,
tp.bundle._critical_stats). Guards against concurrent-merge clobbering
(seen 2026-11-15: rs critical_km2 430.1 from an old run vs series 10870.6).
Read-only on the repo data/."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

DATA = Path(__file__).resolve().parents[2] / "data"
KEYS = ("critical_km2", "critical_slide_km2", "critical_flood_km2", "people_critical")


@pytest.mark.parametrize("case", ["rs_2024", "wa_2025"])
def test_metrics_critical_match_series_peak(case):
    m = json.loads((DATA / "metrics.json").read_text())["cases"][case]
    days = json.loads((DATA / case / "series" / "imerg_smap_daily.json").read_text())["days"]
    rows = [r for r in days if r.get("critical_km2") is not None]
    peak = max(rows, key=lambda r: r["critical_km2"])
    assert m["critical_km2_date"] == peak["date"]
    for k in KEYS:
        assert m[k] == pytest.approx(peak[k]), k
