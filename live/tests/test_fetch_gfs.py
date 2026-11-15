"""GFS NOAA-AWS fetcher — recorded .idx text + a recorded APCP GRIB message;
every HTTP call monkeypatched, no network."""
import datetime as dt
from pathlib import Path

import numpy as np
import pytest

from live import fetch

FIX = Path(__file__).parent / "fixtures"
ISSUED = dt.date(2024, 4, 29)
BOUNDS = (-52.5, -30.5, -50.5, -29.0)


def test_gfs_key_layout():
    assert fetch._gfs_key(ISSUED, 0, 6) == (
        "gfs.20240429/00/atmos/gfs.t00z.pgrb2.0p25.f006")
    assert fetch._gfs_key(ISSUED, 18, 72, "0p50") == (
        "gfs.20240429/18/atmos/gfs.t18z.pgrb2.0p50.f072")


# ---- idx parsing / range selection ----------------------------------------

def test_apcp_range_f006_first_bucket():
    idx = (FIX / "gfs_f006.idx").read_text()
    # first 'APCP:surface:0-6 hour acc fcst' message; ends at next offset
    assert fetch._gfs_apcp_range(idx, 6) == (432566154, 432933561)


def test_apcp_range_picks_trailing_not_since_issuance():
    idx = (FIX / "gfs_f024.idx").read_text()
    # f024 has '18-24 hour acc fcst' (want) and '0-1 day acc fcst' (reject)
    assert fetch._gfs_apcp_range(idx, 24) == (437053045, 437417956)
    assert fetch._gfs_apcp_range(idx, 18) is None   # no 12-18 bucket in f024
    assert fetch._gfs_apcp_range(idx, 5) is None


# ---- day labelling / bucketing ---------------------------------------------

def test_gfs_day_label_midnight_boundary():
    base = dt.datetime(2024, 4, 29)
    # L28b: bucket ending exactly at Apr30 00:00 covers Apr29 18:00..24:00
    assert fetch._gfs_day_label(base + dt.timedelta(hours=24)) == \
        dt.date(2024, 4, 29)
    # bucket ending Apr30 06:00 covers Apr30 00:00..06:00
    assert fetch._gfs_day_label(base + dt.timedelta(hours=30)) == \
        dt.date(2024, 4, 30)
    assert fetch._gfs_day_label(base + dt.timedelta(hours=48)) == \
        dt.date(2024, 4, 30)
    assert fetch._gfs_day_label(base + dt.timedelta(hours=54)) == \
        dt.date(2024, 5, 1)


# ---- decode of a recorded byte-ranged APCP message -------------------------

def test_decode_recorded_apcp_message():
    raw = (FIX / "gfs_f006_apcp.grib").read_bytes()
    arr, lats, lons = fetch._gfs_decode_message(raw)
    assert arr.shape == (721, 1440)
    assert lats[0] == -90.0 and lats[-1] == 90.0      # ascending
    # GDAL GRIB driver wraps to -180..179.75 (same cells as NCEP 0..359.75)
    assert lons[0] == pytest.approx(-180.0)
    assert lons[-1] == pytest.approx(179.75)
    assert np.allclose(np.diff(lons), 0.25) and np.allclose(np.diff(lats), 0.25)
    assert np.isfinite(arr).all()
    assert arr.max() > 0  # it rained somewhere in that 6h


# ---- run picking ------------------------------------------------------------

def test_pick_run_prefers_issued_00z(monkeypatch):
    monkeypatch.setattr(fetch, "_gfs_head_ok", lambda url: True)
    assert fetch._gfs_pick_run(ISSUED) == (ISSUED, 0)


def test_pick_run_falls_back_to_newest_available(monkeypatch):
    # issued 00Z + issued-day cycles are not yet published; yesterday 18Z is
    # (its last needed fh = bucket ending issued+4 00:00 -> fh102)
    ok = {fetch._gfs_key(ISSUED - dt.timedelta(days=1), 18, 102)}
    monkeypatch.setattr(
        fetch, "_gfs_head_ok", lambda url: any(k in url for k in ok))
    assert fetch._gfs_pick_run(ISSUED) == \
        (ISSUED - dt.timedelta(days=1), 18)


def test_pick_run_raises_when_nothing_archived(monkeypatch):
    monkeypatch.setattr(fetch, "_gfs_head_ok", lambda url: False)
    with pytest.raises(RuntimeError, match="covers D\\+3"):
        fetch._gfs_pick_run(ISSUED)


# ---- full daily fetch, offline ---------------------------------------------

def _fake_grid(val):
    lats = np.arange(-31.0, -28.75, 0.25)
    lons = np.arange(-53.0, -50.25, 0.25)
    return np.full((len(lats), len(lons)), val), lats, lons


def test_fetch_gfs_daily_buckets_and_subset(monkeypatch):
    monkeypatch.setattr(fetch, "_gfs_pick_run", lambda issued: (ISSUED, 0))
    calls = []

    def fake_grid(day, hh, fh):
        calls.append(fh)
        return _fake_grid(fh / 6.0)   # mm per 6h bucket encodes fh

    monkeypatch.setattr(fetch, "_gfs_apcp_grid", fake_grid)

    daily, issued_iso = fetch.fetch_gfs_daily(BOUNDS, ISSUED)

    assert issued_iso == "2024-04-29T00:00:00Z"
    assert daily.dims == ("time", "lat", "lon")
    assert daily.sizes["time"] == 4
    got = [str(t)[:10] for t in np.datetime_as_string(daily.time.values)]
    assert got == ["2024-04-29", "2024-04-30", "2024-05-01", "2024-05-02"]
    # L28b alignment: day D = buckets ending inside Apr29 = fh 6..24 of the
    # Apr29 00Z run (forecast; the Now job observes only through D-1);
    # D+1 = fh 30..48; D+3 = fh 78..96. Each bucket read exactly once.
    assert calls[:4] == [6, 12, 18, 24]
    assert sorted(calls) == list(range(6, 97, 6))
    np.testing.assert_allclose(daily.isel(time=0).values,
                               sum(fh / 6.0 for fh in (6, 12, 18, 24)))
    np.testing.assert_allclose(daily.isel(time=1).values,
                               sum(fh / 6.0 for fh in (30, 36, 42, 48)))
    np.testing.assert_allclose(daily.isel(time=3).values,
                               sum(fh / 6.0 for fh in (78, 84, 90, 96)))
    # subset snapped to cell centres inside bounds (GEE-identical)
    np.testing.assert_allclose(daily.lon.values,
                               np.arange(-52.5, -50.49, 0.25))
    np.testing.assert_allclose(daily.lat.values,
                               np.arange(-30.5, -28.99, 0.25))


def test_fetch_gfs_daily_later_cycle_day_alignment(monkeypatch):
    # previous-day 18Z run (fallback when D 00Z is not out yet): day D =
    # buckets ending Apr29 06:00..Apr30 00:00 -> fh 12,18,24,30.
    prev = ISSUED - dt.timedelta(days=1)
    monkeypatch.setattr(fetch, "_gfs_pick_run", lambda issued: (prev, 18))
    calls = []
    monkeypatch.setattr(fetch, "_gfs_apcp_grid",
                        lambda day, hh, fh: calls.append(fh)
                        or _fake_grid(1.0))
    daily, issued_iso = fetch.fetch_gfs_daily(BOUNDS, ISSUED)
    assert issued_iso == f"{prev}T18:00:00Z"
    assert calls[:4] == [12, 18, 24, 30]
    np.testing.assert_allclose(daily.isel(time=0).values, 4.0)


def test_gfs_candidates_never_later_than_issue_00z():
    cands = fetch._gfs_candidates(ISSUED)
    assert cands[0] == (ISSUED, 0)
    assert all(d < ISSUED for d, _ in cands[1:])   # later cycles miss part of D