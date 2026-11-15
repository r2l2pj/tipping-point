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
    # bucket ending exactly at Apr30 00:00 belongs to Apr30
    assert fetch._gfs_day_label(base + dt.timedelta(hours=24)) == \
        dt.date(2024, 4, 30)
    # Apr29 06:00 (fh6) also belongs to Apr30 -> GEE d1 = fh 6..24
    assert fetch._gfs_day_label(base + dt.timedelta(hours=6)) == \
        dt.date(2024, 4, 30)
    assert fetch._gfs_day_label(base + dt.timedelta(hours=30)) == \
        dt.date(2024, 5, 1)


# ---- decode of a recorded byte-ranged APCP message -------------------------

def test_decode_recorded_apcp_message():
    raw = (FIX / "gfs_f006_apcp.grib").read_bytes()
    arr, lats, lons = fetch._gfs_decode_message(raw)
    assert arr.shape == (721, 1440)
    assert lats[0] == -90.0 and lats[-1] == 90.0      # ascending
    assert lons[0] == pytest.approx(-179.75)
    assert lons[-1] == pytest.approx(180.0)
    assert np.isfinite(arr).all()
    assert arr.max() > 0  # it rained somewhere in that 6h


# ---- run picking ------------------------------------------------------------

def test_pick_run_prefers_issued_00z(monkeypatch):
    monkeypatch.setattr(fetch, "_gfs_head_ok", lambda url: True)
    assert fetch._gfs_pick_run(ISSUED) == (ISSUED, 0)


def test_pick_run_falls_back_to_newest_available(monkeypatch):
    # issued 00Z + issued-day cycles are not yet published; yesterday 18Z is
    ok = {fetch._gfs_key(ISSUED - dt.timedelta(days=1), 18, 72)}
    monkeypatch.setattr(
        fetch, "_gfs_head_ok", lambda url: any(k in url for k in ok))
    assert fetch._gfs_pick_run(ISSUED) == \
        (ISSUED - dt.timedelta(days=1), 18)


def test_pick_run_raises_when_nothing_archived(monkeypatch):
    monkeypatch.setattr(fetch, "_gfs_head_ok", lambda url: False)
    with pytest.raises(RuntimeError, match="f072"):
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
    assert daily.sizes["time"] == 3
    got = [str(t)[:10] for t in np.datetime_as_string(daily.time.values)]
    assert got == ["2024-04-30", "2024-05-01", "2024-05-02"]
    # d1 = fh 6,12,18,24 -> 1+2+3+4 = 10 mm
    assert calls[:4] == [6, 12, 18, 24]
    np.testing.assert_allclose(daily.isel(time=0).values, 10.0)
    np.testing.assert_allclose(daily.isel(time=2).values,
                               sum(fh / 6.0 for fh in (54, 60, 66, 72)))
    # subset snapped to cell centres inside bounds (GEE-identical)
    np.testing.assert_allclose(daily.lon.values,
                               np.arange(-52.5, -50.49, 0.25))
    np.testing.assert_allclose(daily.lat.values,
                               np.arange(-30.5, -28.99, 0.25))


def test_fetch_gfs_daily_fills_partial_day_from_later_cycle(monkeypatch):
    # 06Z run on `issued`: D+1 window (D, D+1] gets buckets ending
    # D 12:00, D 18:00, D+1 00:00 -> fh 6,12,18 only
    monkeypatch.setattr(fetch, "_gfs_pick_run", lambda issued: (ISSUED, 6))
    calls = []
    monkeypatch.setattr(fetch, "_gfs_apcp_grid",
                        lambda day, hh, fh: calls.append(fh)
                        or _fake_grid(1.0))
    daily, issued_iso = fetch.fetch_gfs_daily(BOUNDS, ISSUED)
    assert issued_iso == "2024-04-29T06:00:00Z"
    assert calls[:3] == [6, 12, 18]
    np.testing.assert_allclose(daily.isel(time=0).values, 3.0)
