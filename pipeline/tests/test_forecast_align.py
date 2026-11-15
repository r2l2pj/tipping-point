"""L28b alignment: GFS forecast frames must never use observed rain at or
after the issue time, and must count each calendar day exactly once."""
import datetime as dt

import numpy as np
import pytest
from tp.forecast_align import gfs_band_days, issue_rain3, window_days

D = dt.date(2024, 4, 29)


def _day(off):
    return D + dt.timedelta(days=off)


def _encoded(days, scale):
    # distinct power-of-two per day -> any duplicate/missing day is visible
    return {d: np.full((2, 2), float(scale * 2 ** (i + 4))) for i, d in
            enumerate(sorted(days))}


def test_band_mapping_d1_is_issue_day():
    assert gfs_band_days(D, 4) == {_day(0): 1, _day(1): 2, _day(2): 3, _day(3): 4}


@pytest.mark.parametrize("k", [1, 2, 3])
def test_each_window_day_counted_exactly_once(k):
    obs = _encoded([_day(-3), _day(-2), _day(-1)], 1)
    fc = _encoded([_day(0), _day(1), _day(2), _day(3)], 1000)
    total, used = issue_rain3(D, k, obs, fc)
    want_days = window_days(D, k)
    assert want_days[-1] == _day(k)                      # window ends D+k
    expect = sum((obs | fc)[d][0, 0] for d in want_days)
    assert total[0, 0] == expect                         # no dup, no gap
    assert [u.split(":")[0] for u in used] == [d.isoformat() for d in want_days]
    for u in used:                                      # obs strictly before D
        day, src = u.split(":")
        assert (src == "obs") == (dt.date.fromisoformat(day) < D)


def test_k1_uses_obs_d_minus_1_plus_forecast_d_and_d1():
    obs = {_day(-1): np.full((1,), 1.0)}
    fc = {_day(0): np.full((1,), 10.0), _day(1): np.full((1,), 100.0)}
    total, used = issue_rain3(D, 1, obs, fc)
    assert total[0] == 111.0
    assert used == [f"{_day(-1)}:obs", f"{_day(0)}:fc", f"{_day(1)}:fc"]


def test_k3_needs_fourth_forecast_day():
    fc = {_day(i): np.ones(1) for i in range(3)}         # d1..d3 only
    with pytest.raises(KeyError):
        issue_rain3(D, 3, {}, fc)


@pytest.mark.parametrize("late", [0, 1, 3])
def test_look_ahead_observed_data_rejected(late):
    obs = {_day(-1): np.ones(1), _day(late): np.ones(1)}
    fc = {_day(i): np.ones(1) for i in range(4)}
    with pytest.raises(ValueError, match="look-ahead"):
        issue_rain3(D, 1, obs, fc)


def test_forecast_before_issue_rejected():
    fc = {_day(i): np.ones(1) for i in range(-1, 4)}
    with pytest.raises(ValueError, match="precedes issue"):
        issue_rain3(D, 1, {_day(-1): np.ones(1)}, fc)
