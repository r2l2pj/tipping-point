"""Hermetic tests for tools/alarm_precision.py — synthetic units/geoms only,
no data/ reads, no network."""
import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pytest
from affine import Affine
from shapely.geometry import box

REPO = Path(__file__).resolve().parents[2]
for d in (REPO / "tools", REPO / "pipeline"):
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))
import alarm_precision as ap

ONSET = dt.datetime(2024, 4, 29, 3, tzinfo=dt.UTC)   # L37 rs_2024 onset
WIN = (dt.date(2024, 4, 29), dt.date(2024, 5, 2))


def test_issue_files_sorted_and_parsed(tmp_path):
    for d in ("20240425", "20240423", "20240424"):
        (tmp_path / f"rs_2024_tpi_gfs_{d}.tif").touch()
    (tmp_path / "rs_2024_tpi_slide_20240502.tif").touch()   # not a gfs replay
    got = ap.iter_issue_files(tmp_path, "rs_2024")
    assert [d.isoformat() for d, _ in got] == [
        "2024-04-23", "2024-04-24", "2024-04-25"]


def test_slide_bands_only_slide_sorted():
    desc = ("d1_slide", "d1_flood", "d3_flood", "d2_slide", "d3_slide", "d2_flood")
    assert ap.slide_bands(desc) == [(1, 1), (2, 4), (3, 5)]


def test_depicted_window_primary_and_variant():
    issue = dt.date(2024, 4, 27)
    assert ap.depicted_window(issue, [1, 2, 3], 0) == (
        dt.date(2024, 4, 28), dt.date(2024, 4, 30))
    assert ap.depicted_window(issue, [1, 2, 3], -1) == (
        dt.date(2024, 4, 27), dt.date(2024, 4, 29))


@pytest.mark.parametrize("issue,expected", [
    (dt.date(2024, 4, 25), False),   # depicts 04-26..04-28, before trigger
    (dt.date(2024, 4, 26), True),    # depicts 04-27..04-29
    (dt.date(2024, 5, 1), True),     # depicts 05-02..05-04
    (dt.date(2024, 5, 2), False),    # depicts 05-03..05-05
])
def test_overlap_edges_primary(issue, expected):
    dw = ap.depicted_window(issue, [1, 2, 3], 0)
    assert ap.windows_overlap(dw, WIN) is expected


def test_overlap_edges_variant_shifts_one_day_earlier():
    dw = ap.depicted_window(dt.date(2024, 5, 2), [1, 2, 3], -1)
    assert ap.windows_overlap(dw, WIN) is True      # depicts 05-02..05-04
    dw = ap.depicted_window(dt.date(2024, 4, 25), [1, 2, 3], -1)
    assert ap.windows_overlap(dw, WIN) is False     # depicts 04-25..04-27


def test_lead_h_sign():
    assert ap.lead_h(dt.date(2024, 4, 28), ONSET) == 27.0
    assert ap.lead_h(dt.date(2024, 4, 29), ONSET) == 3.0
    assert ap.lead_h(dt.date(2024, 4, 30), ONSET) == -21.0


def _unit(muni, issue, alarm, overlap, slide_munis=(0,)):
    return {"muni": muni, "issue": issue, "alarm": alarm, "overlap": overlap,
            "hit": muni in slide_munis and overlap}


def test_summarize_synthetic_units():
    # 2 municipalities (0 = has slides), 4 issue days; overlap on d26,d27 only
    d25, d26, d27, d28 = [dt.date(2024, 4, 25 + i) for i in range(4)]
    overlap = {d25: False, d26: True, d27: True, d28: False}
    units = []
    alarms = {  # (muni, issue) -> alarmed
        (0, d25): True,   # alarmed, but window misses trigger -> false alarm
        (0, d26): True,   # correct alarm, lead 75 h
        (0, d27): True,   # correct alarm, lead 51 h
        (0, d28): False,
        (1, d26): True,   # alarmed non-slide muni -> false alarm
        (1, d27): False,
        (1, d25): False,
        (1, d28): True,   # false alarm (window outside trigger)
    }
    for m in (0, 1):
        for issue in (d25, d26, d27, d28):
            units.append(_unit(m, issue, alarms[(m, issue)], overlap[issue]))
    r = ap.summarize(units, n_munis=2, n_slide_munis=1, onset=ONSET)
    assert r["n_units"] == 8
    assert r["n_hit_units"] == 2               # muni0 x {d26, d27}
    assert r["n_alarms"] == 5
    assert r["n_hits"] == 2
    assert r["false_alarms"] == 3
    assert r["precision_pct"] == 40.0
    assert r["baseline_pct"] == 25.0
    assert r["lift"] == 1.6
    assert r["pod_before_onset_pct"] == 100.0   # muni0 detected by d26/d27
    assert r["pod_before_window_end_pct"] == 100.0
    assert r["alarm_lead_h_all"]["median"] == 63.0
    assert r["alarm_lead_h_all"]["iqr"] == [57.0, 69.0]
    assert r["alarm_lead_h_issued_before_onset"]["n"] == 2
    by = {r_["issue"]: r_ for r_ in r["by_issue_day"]}
    assert by["2024-04-26"]["n_alarms"] == 2 and by["2024-04-26"]["n_hits"] == 1
    assert by["2024-04-27"]["window_overlaps_trigger"] is True
    assert by["2024-04-25"]["window_overlaps_trigger"] is False
    assert by["2024-04-28"]["n_hits"] == 0


def test_summarize_pod_before_onset_requires_pre_onset_run():
    # slide muni alarmed only by a post-onset in-window run -> not detected pre
    d28, d30 = dt.date(2024, 4, 28), dt.date(2024, 4, 30)   # lead 27 / -21 h
    units = [_unit(0, d28, False, True), _unit(0, d30, True, True)]
    r = ap.summarize(units, n_munis=1, n_slide_munis=1, onset=ONSET)
    assert r["pod_before_onset_pct"] == 0.0
    assert r["pod_before_window_end_pct"] == 100.0
    assert r["alarm_lead_h_all"]["median"] == -21.0
    assert r["alarm_lead_h_issued_before_onset"]["median"] is None


def test_summarize_zero_alarms_nulls():
    units = [_unit(0, dt.date(2024, 4, 27), False, True)]
    r = ap.summarize(units, n_munis=1, n_slide_munis=1, onset=ONSET)
    assert r["n_alarms"] == 0 and r["precision_pct"] is None
    assert r["lift"] is None and r["baseline_pct"] == 100.0


def test_municipality_ids_partition():
    # 4x4 grid, two side-by-side boxes; cell centres assign each cell once
    transform = Affine(1.0, 0.0, 0.0, 0.0, -1.0, 4.0)   # 1-deg cells, origin NW
    g = [box(0, 0, 2, 4), box(2, 0, 4, 4)]
    ids = ap.municipality_ids(g, transform, (4, 4))
    assert ids.shape == (4, 4)
    assert (ids[:, :2] == 1).all() and (ids[:, 2:] == 2).all()


def test_slide_municipalities_intersects():
    munis = [box(0, 0, 1, 1), box(1, 0, 2, 1)]
    point = box(0.5, 0.5, 0.5, 0.5)                      # inside muni 0
    spanning = box(0.9, 0.4, 1.1, 0.6)                   # touches both
    assert ap.slide_municipalities([point], munis) == {0}
    assert ap.slide_municipalities([spanning], munis) == {0, 1}
    assert ap.slide_municipalities([], munis) == set()
