"""Hermetic tests for tools/upstream_rain.py — no network, no data/ writes."""
import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools import upstream_rain as ur


# --- grid ----------------------------------------------------------------- #
def test_imerg_index_window_rs():
    i0, i1, j0, j1 = ur.imerg_index_window(ur.BOXES["rs_2024"])
    assert (i0, i1, j0, j1) == (1252, 1301, 590, 621)
    lats, lons = ur.imerg_centers(i0, i1, j0, j1)
    assert len(lons) == 50 and len(lats) == 32
    assert lons[0] == -54.75 and lons[-1] == -49.85   # cells cover -54.8..-49.8
    assert lats[0] == -30.95 and lats[-1] == -27.85   # cells cover -31.0..-27.8
    np.testing.assert_allclose(np.diff(lats), 0.1)


def test_imerg_index_window_wa():
    i0, i1, j0, j1 = ur.imerg_index_window(ur.BOXES["wa_2025"])
    assert (i0, i1, j0, j1) == (570, 595, 1366, 1397)
    lats, lons = ur.imerg_centers(i0, i1, j0, j1)
    assert len(lons) == 26 and len(lats) == 32
    assert lons[0] == -122.95 and lons[-1] == -120.45
    assert lats[0] == 46.65 and lats[-1] == 49.75


# --- day lists -------------------------------------------------------------- #
def test_replay_window():
    assert ur.replay_window("rs_2024") == (dt.date(2024, 4, 18),
                                         dt.date(2024, 5, 10))
    assert ur.replay_window("wa_2025") == (dt.date(2025, 11, 27),
                                           dt.date(2025, 12, 23))


def test_clim_days_rs_bounds_and_count():
    days = ur.clim_days("rs_2024")
    # window 04-18..05-10 -> Mar 13 .. Jun 9 of every year 2001..2023
    assert len(days) == 89 * 23
    assert min(days) == dt.date(2001, 3, 13)
    assert max(days) == dt.date(2023, 6, 9)
    assert dt.date(2007, 4, 18) in days
    assert dt.date(2007, 6, 10) not in days


def test_clim_days_wa_year_wrap():
    days = ur.clim_days("wa_2025")
    # window 11-27..12-23 -> Oct 22 y .. Jan 22 y+1
    assert len(days) == 93 * 23
    assert min(days) == dt.date(2001, 10, 22)
    assert max(days) == dt.date(2024, 1, 22)
    assert dt.date(2010, 1, 15) in days        # Jan days belong to y+1
    assert dt.date(2010, 9, 1) not in days


@pytest.mark.parametrize("case", ["rs_2024", "wa_2025"])
def test_clim_days_cover_exact_need(case):
    """For every target day D in the window and offset -30..30 step 3 the
    3-day sum over [end-3d, end) must have all its days in clim_days."""
    have = set(ur.clim_days(case))
    ws, we = ur.replay_window(case)
    for y in (2001, 2012, 2023):
        d = ws
        while d <= we:
            ref = ur._from_ymd(y, d.month, d.day)
            for o in ur.OFFSETS:
                end = ref + dt.timedelta(days=o)
                for k in range(1, ur.WIN_DAYS + 1):
                    assert end - dt.timedelta(days=k) in have
            d += dt.timedelta(days=7)


def test_needed_days_union():
    days = ur.needed_days("rs_2024")
    assert len(days) == 26 + 89 * 23
    assert dt.date(2024, 4, 15) in days and dt.date(2024, 5, 10) in days
    assert days == sorted(days)


# --- imerg cube ------------------------------------------------------------- #
def test_fetch_imerg_case_missing_becomes_nan(tmp_path):
    n_i = ur.imerg_index_window(ur.BOXES["rs_2024"])[1] - \
        ur.imerg_index_window(ur.BOXES["rs_2024"])[0] + 1
    n_j = ur.imerg_index_window(ur.BOXES["rs_2024"])[3] - \
        ur.imerg_index_window(ur.BOXES["rs_2024"])[2] + 1

    def fake(day, i0, i1, j0, j1, cache, token):
        if day == dt.date(2024, 4, 20):
            raise RuntimeError("boom")
        assert (i1 - i0 + 1, j1 - j0 + 1) == (n_i, n_j)
        return np.full((n_i, n_j), day.day, np.float32)

    monkey_days = ur.needed_days  # keep a handle before patching
    try:
        ur.needed_days = lambda c: list(
            ur._daterange(dt.date(2024, 4, 18), dt.date(2024, 4, 22)))
        mm, _lats, _lons, _fetched, missing = ur.fetch_imerg_case(
            "rs_2024", tmp_path, "tok", workers=4, fetch_day=fake)
    finally:
        ur.needed_days = monkey_days
    assert mm.shape == (5, 32, 50)
    assert missing == [dt.date(2024, 4, 20)]
    assert np.isnan(mm[2]).all()
    assert mm[0, 0, 0] == 18 and mm[4, 0, 0] == 22
    # day 19 came back [lon, lat] filled with 19; transpose keeps it uniform
    assert mm[1].shape == (32, 50)


def test_write_imerg_npz_roundtrip(tmp_path, monkeypatch):
    days = [dt.date(2024, 4, 18), dt.date(2024, 4, 19)]
    monkeypatch.setattr(ur, "needed_days", lambda c: days)
    monkeypatch.setattr(ur, "OBS_DAYS", {"rs_2024": (days[0], days[-1])})
    mm = np.arange(2 * 3 * 4, dtype=np.float32).reshape(2, 3, 4)
    p = ur.write_imerg_npz("rs_2024", tmp_path, mm,
                           np.array([1.0, 2.0, 3.0]),
                           np.array([10.0, 20.0, 30.0, 40.0]),
                           [dt.date(2024, 4, 19)])
    z = np.load(p)
    assert z["dates"].tolist() == [20240418, 20240419]
    assert z["mm"].dtype == np.float32 and z["mm"].shape == (2, 3, 4)
    assert z["missing_dates"].tolist() == [20240419]
    assert z["lat"].tolist() == [1.0, 2.0, 3.0]


# --- gfs cube --------------------------------------------------------------- #
def test_gfs_day_fhs():
    assert ur.gfs_day_fhs(0) == [6, 12, 18, 24]
    assert ur.gfs_day_fhs(3) == [78, 84, 90, 96]


def _fake_gfs_grid(fh):
    lats = np.arange(-90.0, 90.0001, 0.25)
    lons = np.arange(-180.0, 180.0, 0.25)
    return (np.full((len(lats), len(lons)), fh, np.float32), lats, lons)


def test_fetch_gfs_issue_sums_buckets():
    apcp = lambda day, hh, fh: _fake_gfs_grid(fh)
    cube, lats, lons, miss = ur.fetch_gfs_issue(
        dt.date(2025, 11, 30), ur.BOXES["wa_2025"], workers=4,
        apcp_grid=apcp, head_ok=lambda url: True)
    assert cube.shape == (4, 13, 11)
    assert lats[0] == 46.75 and lats[-1] == 49.75
    assert lons[0] == -123.0 and lons[-1] == -120.5
    for k, total in enumerate((60, 156, 252, 348)):
        np.testing.assert_allclose(cube[k], total)
    assert miss == []


def test_fetch_gfs_issue_missing_fh_nan():
    def apcp(day, hh, fh):
        return None if fh in (30, 36) else _fake_gfs_grid(fh)
    cube, _, _, miss = ur.fetch_gfs_issue(
        dt.date(2025, 11, 30), ur.BOXES["wa_2025"], workers=4,
        apcp_grid=apcp, head_ok=lambda url: True)
    assert miss == [1]
    assert np.isnan(cube[1]).all() and np.isfinite(cube[0]).all()


def test_fetch_gfs_issue_run_absent_raises():
    with pytest.raises(RuntimeError):
        ur.fetch_gfs_issue(dt.date(2025, 11, 30), ur.BOXES["wa_2025"],
                           head_ok=lambda url: False)


def test_fetch_gfs_case_missing_issue():
    def apcp(day, hh, fh):
        return _fake_gfs_grid(fh)
    # issues whose f096 idx probe fails are 'missing'
    def head(url):
        return "20251130" not in url
    mm, _issues, _lats, _lons, missing, _partial = ur.fetch_gfs_case(
        "wa_2025", workers=4, apcp_grid=apcp, head_ok=head)
    assert dt.date(2025, 11, 30) in missing
    assert mm.shape == (21, 4, 13, 11)
    assert np.isnan(mm[0]).all() and np.isfinite(mm[1]).all()


# --- verify ------------------------------------------------------------------ #
def test_verify_gfs_cog_exact_and_diff(tmp_path):
    import rasterio
    from rasterio.transform import from_origin

    lats = np.array([46.75, 47.0, 47.25, 47.5])     # ascending in the cube
    lons = np.array([-123.0, -122.75, -122.5])
    issues = [dt.date(2025, 11, 30)]
    mm = np.arange(4 * 4 * 3, dtype=np.float32).reshape(1, 4, 4, 3)

    # COG covers lats 46.75..47.25 (north-up rows) x the same lons, 4 bands
    # identical to the cube's k bands -> max_abs_diff 0
    tif = tmp_path / "cog.tif"
    arr = mm[0, :, 2::-1, :].copy()                 # rows = lat 47.25,47.0,46.75
    arr[2, 1, 1] += 5.0                             # one differing cell, k=2
    with rasterio.open(
            tif, "w", driver="GTiff", height=3, width=3, count=4,
            dtype="float32", crs="EPSG:4326",
            transform=from_origin(-123.125, 47.375, 0.25, 0.25)) as ds:
        ds.write(arr)
    rep = ur.verify_gfs_cog(tif, issues[0], mm, issues, lats, lons)
    assert rep["bands"]["k0"]["cells"] == 9
    assert rep["bands"]["k0"]["max_abs_diff"] == 0.0
    assert rep["bands"]["k2"]["max_abs_diff"] == 5.0
    assert rep["max_abs_diff"] == 5.0
