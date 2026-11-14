"""GEOS-FP GrADS-OPeNDAP .ascii fetch — every request monkeypatched, no HTTP."""
import datetime as dt
import os
import re

import numpy as np
import pytest

from live import fetch

ISSUED = dt.date(2026, 11, 10)
LATS = np.arange(-90.0, 90.25, 0.25)
LONS = np.arange(-180.0, 180.0, 0.3125)
BOUNDS = (-58.0, -34.0, -48.0, -22.5)


def _ord(t: dt.datetime) -> float:
    """GDS 'days since 1-1-1' stamp: ordinal+1 plus day fraction."""
    secs = t.hour * 3600 + t.minute * 60 + t.second
    return t.date().toordinal() + 1 + secs / 86400.0


def _hourly(start: dt.datetime, hours: int) -> list[float]:
    return [_ord(start + dt.timedelta(hours=i)) for i in range(hours)]


def _fmt(v) -> str:
    return repr(float(v))


def _time_block(times) -> str:
    return f"time, [{len(times)}]\n" + ", ".join(_fmt(v) for v in times) + "\n"


def _vec_block(name, vals) -> str:
    return f"{name}, [{len(vals)}]\n" + ", ".join(_fmt(v) for v in vals) + "\n"


def _grid_block(arr) -> str:
    nt, nla, nlo = arr.shape
    lines = [f"prectot, [{nt}][{nla}][{nlo}]"]
    for t in range(nt):
        for la in range(nla):
            lines.append(f"[{t}][{la}], " + ", ".join(_fmt(v) for v in arr[t, la]))
        lines.append("")
    return "\n".join(lines) + "\n"


def _fake_get(runs):
    """runs: {run_name: (times_list, arr3d_or_callable)} → fake _gds_get."""
    def get(url):
        if url.endswith("tavg1_2d_flx_Nx/"):
            return "".join(f'<a href="{r}.info">{r}</a>\n' for r in runs)
        run = url.rsplit("/", 1)[1].split(".ascii")[0]
        ce = url.split("?", 1)[1]
        times, arr = runs[run]
        if ce == "prectot.time":
            return _time_block(times)
        if "prectot.lat" in ce:
            return _vec_block("lat", LATS) + _vec_block("lon", LONS)
        m = re.fullmatch(
            r"prectot\.prectot\[(\d+):1:(\d+)\]"
            r"\[(\d+):1:(\d+)\]\[(\d+):1:(\d+)\]", ce)
        assert m, f"unexpected CE {ce!r}"
        t0, t1, la0, la1, lo0, lo1 = (int(g) for g in m.groups())
        grid = arr(t0, t1, la0, la1, lo0, lo1) if callable(arr) else \
            arr[t0:t1 + 1, la0:la1 + 1, lo0:lo1 + 1]
        return _grid_block(grid)
    return get


def _span(start: dt.datetime, hours: int):
    """(times, full-grid arr) for a run starting `start` (first step :30)."""
    times = _hourly(start, hours)
    arr = np.full((hours, len(LATS), len(LONS)), 1.0e-3)
    return times, arr


# ---- parser ---------------------------------------------------------------

def test_gds_parse_vectors():
    text = _vec_block("lat", [-90.0, -89.75, -89.5]) + \
        _vec_block("lon", [-180.0, -179.6875])
    out = fetch._gds_parse_vectors(text)
    assert out["lat"].tolist() == [-90.0, -89.75, -89.5]
    assert out["lon"].tolist() == [-180.0, -179.6875]


def test_gds_parse_grid_shape_and_values():
    arr = np.arange(2 * 2 * 3, dtype=float).reshape(2, 2, 3) * 0.01
    out = fetch._gds_parse_grid(_grid_block(arr))
    assert out.shape == (2, 2, 3)
    np.testing.assert_allclose(out, arr)


def test_gds_times_decode():
    vals = np.array([_ord(dt.datetime(2026, 11, 13, 0, 30)),
                     _ord(dt.datetime(2026, 11, 13, 23, 30))])
    ts = fetch._gds_times(vals)
    assert ts == [dt.datetime(2026, 11, 13, 0, 30),
                  dt.datetime(2026, 11, 13, 23, 30)]


# ---- run picker -----------------------------------------------------------

def test_picks_newest_run_reaching_need_by(monkeypatch):
    # freshest is still being published (ends Nov 12, short of D+3=Nov 13);
    # the next-older reaches well past it, and an even older one is ignored
    runs = {
        "tavg1_2d_flx_Nx.20261112_00": _span(dt.datetime(2026, 11, 12, 0, 30), 20),
        "tavg1_2d_flx_Nx.20261111_00": _span(dt.datetime(2026, 11, 11, 0, 30), 200),
        "tavg1_2d_flx_Nx.20261110_00": _span(dt.datetime(2026, 11, 10, 0, 30), 240),
    }
    monkeypatch.setattr(fetch, "_gds_get", _fake_get(runs))
    daily, issued_iso = fetch.fetch_geosfp_daily(BOUNDS, ISSUED)
    assert issued_iso == "2026-11-11T00:00:00Z"
    assert daily.sizes["time"] == 3


def test_skips_run_ending_mid_d3(monkeypatch):
    # freshest ends 05:30 on D+3 (partial day) -> must fall back to the
    # older run that covers D+3 through 23:30
    runs = {
        "tavg1_2d_flx_Nx.20261112_00": _span(dt.datetime(2026, 11, 12, 0, 30), 29),
        "tavg1_2d_flx_Nx.20261111_00": _span(dt.datetime(2026, 11, 11, 0, 30), 200),
    }
    monkeypatch.setattr(fetch, "_gds_get", _fake_get(runs))
    daily, issued_iso = fetch.fetch_geosfp_daily(BOUNDS, ISSUED)
    assert issued_iso == "2026-11-11T00:00:00Z"
    assert daily.sizes["time"] == 3


def test_raises_when_no_run_reaches(monkeypatch):
    runs = {
        "tavg1_2d_flx_Nx.20261112_00": _span(dt.datetime(2026, 11, 12, 0, 30), 20),
        "tavg1_2d_flx_Nx.20261111_00": _span(dt.datetime(2026, 11, 11, 0, 30), 40),
    }
    monkeypatch.setattr(fetch, "_gds_get", _fake_get(runs))
    with pytest.raises(RuntimeError, match="covers"):
        fetch.fetch_geosfp_daily(BOUNDS, ISSUED)


def test_raises_when_fewer_than_3_daily_steps(monkeypatch):
    # run "reaches" D+3 but only starts there -> a single usable day
    runs = {
        "tavg1_2d_flx_Nx.20261113_00": _span(dt.datetime(2026, 11, 13, 0, 30), 24),
    }
    monkeypatch.setattr(fetch, "_gds_get", _fake_get(runs))
    with pytest.raises(RuntimeError, match="fewer than 3"):
        fetch.fetch_geosfp_daily(BOUNDS, ISSUED)


# ---- accumulation ---------------------------------------------------------

def test_daily_sum_is_hours_x3600_not_x86400(monkeypatch):
    start = dt.datetime(2026, 11, 9, 0, 30)
    times = _hourly(start, 24 * 7)
    # hourly value varies by step so a wrong factor can't cancel out
    steps = np.arange(len(times), dtype=float) * 1.0e-4 + 1.0e-3
    arr = np.repeat(steps[:, None, None], len(LATS), axis=1)
    arr = np.repeat(arr, len(LONS), axis=2)
    runs = {"tavg1_2d_flx_Nx.20261109_00": (times, arr)}
    monkeypatch.setattr(fetch, "_gds_get", _fake_get(runs))

    daily, issued_iso = fetch.fetch_geosfp_daily(BOUNDS, ISSUED)
    assert issued_iso == "2026-11-09T00:00:00Z"

    got_dates = [str(t)[:10] for t in np.datetime_as_string(daily.time.values)]
    assert got_dates == ["2026-11-11", "2026-11-12", "2026-11-13"]

    decoded = fetch._gds_times(np.array(times))
    for k, d in enumerate((dt.date(2026, 11, 11), dt.date(2026, 11, 12),
                           dt.date(2026, 11, 13))):
        idx = [i for i, t in enumerate(decoded) if t.date() == d]
        want = float(steps[idx].sum() * 3600.0)
        assert float(daily.isel(time=k).mean()) == pytest.approx(want)
        # the old bug (PRECTOT*86400 then daily sum) would give 24x this
        assert float(daily.isel(time=k).mean()) < want * 2


def test_fill_value_becomes_nan(monkeypatch):
    start = dt.datetime(2026, 11, 9, 0, 30)
    times = _hourly(start, 24 * 7)
    arr = np.full((len(times), len(LATS), len(LONS)), 1.0e-3)
    # GEOS-FP _FillValue, placed inside the bounds+padded fetch window
    arr[:, 230:232, 400:405] = 1.0e15
    runs = {"tavg1_2d_flx_Nx.20261109_00": (times, arr)}
    monkeypatch.setattr(fetch, "_gds_get", _fake_get(runs))

    daily, _ = fetch.fetch_geosfp_daily(BOUNDS, ISSUED)
    assert np.isnan(daily.isel(time=0).values).any()
    assert np.isfinite(daily.isel(time=0).values).any()


def test_subset_is_padded_one_point(monkeypatch):
    start = dt.datetime(2026, 11, 9, 0, 30)
    times, arr = _span(start, 24 * 7)
    runs = {"tavg1_2d_flx_Nx.20261109_00": (times, arr)}
    monkeypatch.setattr(fetch, "_gds_get", _fake_get(runs))

    daily, _ = fetch.fetch_geosfp_daily(BOUNDS, ISSUED)
    w, s, e, n = BOUNDS
    assert float(daily.lat.min()) <= s and float(daily.lat.max()) >= n
    assert float(daily.lon.min()) <= w and float(daily.lon.max()) >= e
    # exactly one grid point of padding beyond the bounds
    assert float(daily.lat.min()) == pytest.approx(
        LATS[np.searchsorted(LATS, s) - 1])
    assert float(daily.lon.max()) == pytest.approx(
        LONS[min(len(LONS) - 1, np.searchsorted(LONS, e, "right"))])


# ---- Earthdata bearer-token auth (ruling L6) ------------------------------

def test_edl_login_uses_earthdata_token(monkeypatch):
    """EARTHDATA_TOKEN -> offline login, bearer session, GDAL header."""
    import earthaccess

    for var in ("EDL_USER", "EDL_PASS", "EARTHDATA_USERNAME",
                "EARTHDATA_PASSWORD", "GDAL_HTTP_HEADERS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("EARTHDATA_TOKEN", "fake")
    earthaccess.__auth__.authenticated = False
    earthaccess.__auth__.token = None

    fetch._edl_login()

    auth = earthaccess.__auth__
    assert auth.token["access_token"] == "fake"
    assert auth.get_session().headers["Authorization"] == "Bearer fake"
    assert os.environ["GDAL_HTTP_HEADERS"] == "Authorization: Bearer fake"


def test_edl_login_falls_back_to_edl_user_pass(monkeypatch):
    """No token -> EDL_USER/EDL_PASS mapped onto earthaccess env names."""
    import earthaccess

    for var in ("EARTHDATA_TOKEN", "EARTHDATA_USERNAME", "EARTHDATA_PASSWORD",
                "GDAL_HTTP_HEADERS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("EDL_USER", "u1")
    monkeypatch.setenv("EDL_PASS", "p1")
    calls = []
    monkeypatch.setattr(earthaccess, "login",
                        lambda **kw: calls.append(kw) or object())

    fetch._edl_login()

    assert os.environ["EARTHDATA_USERNAME"] == "u1"
    assert os.environ["EARTHDATA_PASSWORD"] == "p1"
    assert calls == [{"strategy": "environment"}]
    assert "GDAL_HTTP_HEADERS" not in os.environ


def test_nisar_recent_passes_is_anonymous(monkeypatch):
    import asf_search as asf

    monkeypatch.setenv("EARTHDATA_TOKEN", "fake")
    posts = []
    monkeypatch.setattr(asf.ASFSession, "post", lambda self, **kw: posts.append(kw))
    captured = {}
    monkeypatch.setattr(asf, "geo_search",
                        lambda **kw: captured.update(kw) or [])

    assert fetch.nisar_recent_passes(BOUNDS) == []
    assert "session" not in captured
    assert posts == []


def test_check_earthdata_token_exits_loudly_on_rejection(monkeypatch):
    import earthaccess

    monkeypatch.setattr(earthaccess, "login", lambda **kw: object())

    def _raise(**kw):
        raise RuntimeError("401 Unauthorized")

    monkeypatch.setattr(earthaccess, "search_data", _raise)
    with pytest.raises(SystemExit) as ei:
        fetch.check_earthdata_token()
    msg = str(ei.value)
    assert "EARTHDATA_TOKEN" in msg and "user_tokens" in msg


def test_check_earthdata_token_returns_on_success(monkeypatch):
    import earthaccess

    monkeypatch.setattr(earthaccess, "login", lambda **kw: object())
    monkeypatch.setattr(earthaccess, "search_data", lambda **kw: [object()])
    fetch.check_earthdata_token()  # returns quietly, no SystemExit


# ---- SMAP L4 --------------------------------------------------------------

def test_smap_l4_reads_root_coords_and_crops_bbox(monkeypatch, tmp_path):
    import earthaccess
    import xarray as xr

    for var in ("EARTHDATA_TOKEN", "EDL_USER", "EDL_PASS", "EARTHDATA_USERNAME",
                "EARTHDATA_PASSWORD", "GDAL_HTTP_HEADERS"):
        monkeypatch.delenv(var, raising=False)
    path = tmp_path / "SPL4SMGP.h5"
    lat, lon = (a.astype(np.float32) for a in np.indices((6, 8)))
    sm = np.arange(48, dtype=np.float32).reshape(6, 8)
    dims = ("y", "x")
    xr.Dataset({"cell_lat": (dims, lat), "cell_lon": (dims, lon)}).to_netcdf(
        path, engine="h5netcdf")
    xr.Dataset({"sm_rootzone_wetness": (dims, sm)}).to_netcdf(
        path, engine="h5netcdf", mode="a", group="Geophysical_Data")
    monkeypatch.setattr(earthaccess, "login", lambda **kw: object())
    monkeypatch.setattr(earthaccess, "search_data",
                        lambda **kw: [{"meta": {"native-id": "FAKE-SPL4SMGP"}}])
    monkeypatch.setattr(earthaccess, "open", lambda results: [str(path)])

    lats, lons, vals, pid = fetch.fetch_smap_l4((2, 2, 4, 3))
    assert lats.shape == lons.shape == vals.shape == (2, 3)
    np.testing.assert_array_equal(lats, lat[2:4, 2:5])
    np.testing.assert_array_equal(lons, lon[2:4, 2:5])
    np.testing.assert_array_equal(vals, sm[2:4, 2:5])
    assert pid == "FAKE-SPL4SMGP"


def test_gds_get_retries_readtimeout_then_succeeds(monkeypatch):
    """Two ReadTimeouts then a good response: returns text after 3 calls."""
    import time

    import requests

    monkeypatch.setattr(time, "sleep", lambda s: None)
    calls = []

    class _Resp:
        text = "payload"

        def raise_for_status(self):
            pass

    def _get(url, timeout):
        calls.append(url)
        if len(calls) < 3:
            raise requests.exceptions.ReadTimeout("slow")
        return _Resp()

    monkeypatch.setattr(requests, "get", _get)

    assert fetch._gds_get("http://x") == "payload"
    assert calls == ["http://x"] * 3


def test_gds_get_gives_up_after_three_connection_errors(monkeypatch):
    """Always-failing connection: raises ConnectionError after 3 calls."""
    import time

    import requests

    monkeypatch.setattr(time, "sleep", lambda s: None)
    calls = []

    def _get(url, timeout):
        calls.append(url)
        raise requests.exceptions.ConnectionError("down")

    monkeypatch.setattr(requests, "get", _get)

    with pytest.raises(requests.exceptions.ConnectionError):
        fetch._gds_get("http://x")
    assert len(calls) == 3
