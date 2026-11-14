"""GEOS-FP GrADS-OPeNDAP .ascii fetch — every request monkeypatched, no HTTP."""
import datetime as dt
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
