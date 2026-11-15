"""Hermetic tests for tools/replay_v2.py — synthetic cubes only, no data/
reads, no network, no GEE."""
import datetime as dt
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
for d in (REPO / "tools", REPO / "pipeline"):
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))
import replay_v2 as rv


def _days(a, n):
    return [a + dt.timedelta(days=i) for i in range(n)]


def test_observed_p3_is_d3_to_d1():
    days = _days(dt.date(2024, 4, 1), 10)
    mm = np.arange(10 * 4, dtype=np.float64).reshape(10, 2, 2)
    got = rv.observed_p3(mm, days, dt.date(2024, 4, 6))
    np.testing.assert_array_equal(got, mm[2] + mm[3] + mm[4])  # D-3..D-1


def test_observed_p3_missing_day_is_nan():
    days = _days(dt.date(2024, 4, 1), 3)
    mm = np.ones((3, 2, 2))
    assert np.isnan(rv.observed_p3(mm, days, dt.date(2024, 4, 1))).all()


def test_cell_climatology_percentiles():
    # 23y x 21 offsets complete -> percentile of the stacked 3-day sums.
    days = _days(dt.date(2001, 1, 1), 23 * 366 + 60)
    rng = np.random.default_rng(0)
    mm = rng.gamma(1.5, 4.0, (len(days), 3, 5)).astype(np.float32)
    target = dt.date(2024, 5, 2)
    idx = {d: i for i, d in enumerate(days)}
    vals = []
    for y in range(2001, 2024):
        ref = dt.date(y, target.month, target.day)
        for o in range(-30, 31, 3):
            e = ref + dt.timedelta(days=o)
            vals.append(sum(mm[idx[e - dt.timedelta(days=k)]]
                            for k in (1, 2, 3)))
    st = np.stack(vals)
    want = np.percentile(st, [90, 95, 99], axis=0)  # all finite -> nan==percentile
    got = rv.cell_climatology(mm, days, target)
    np.testing.assert_allclose(got, want, rtol=1e-6, atol=1e-6)


def test_cell_climatology_min_n_is_nan():
    days = _days(dt.date(2024, 4, 1), 10)
    mm = np.ones((10, 2, 2))
    got = rv.cell_climatology(mm, days, dt.date(2024, 4, 5))
    assert np.isnan(got).all()  # far fewer than 300 windows


def test_frame_day_source_l28b():
    issue = dt.date(2024, 4, 27)   # k=1 -> E=I+1 -> days I-1, I, I+1
    got = rv.frame_day_source(issue, 1)
    assert [d for d, _, _ in got] == [dt.date(2024, 4, 26),
                                    dt.date(2024, 4, 27),
                                    dt.date(2024, 4, 28)]
    assert [s for _, s, _ in got] == ["obs", "gfs", "gfs"]
    assert [k for _, _, k in got] == [-1, 0, 1]
    assert [d for d, _, _ in rv.frame_day_source(issue, 3)] == [
        dt.date(2024, 4, 28), dt.date(2024, 4, 29), dt.date(2024, 4, 30)]
    assert [s for _, s, _ in rv.frame_day_source(issue, 3)] == ["gfs"] * 3


def test_combine_tpi_mask_and_geomean():
    r = np.array([[0.5, np.nan], [1.0, 0.0]])
    s = np.full((2, 2), 1.0)
    t = np.full((2, 2), 0.125)
    water = np.array([[False, False], [True, False]])
    got = rv.combine_tpi(r, s, t, water, (1 / 3, 1 / 3, 1 / 3))
    assert np.isnan(got[0, 1]) and np.isnan(got[1, 0])
    np.testing.assert_allclose(got[0, 0], (0.5 * 0.125) ** (1 / 3),
                               rtol=1e-6)
    assert got[1, 1] == 0.0


def test_cube_to_grid_bilinear():
    # 2x2 source cells centred at (lat 0.05/-0.05, lon 0.05/0.15); a target
    # point midway between the two centres interpolates linearly.
    from affine import Affine

    class _Like:
        height = width = None

    lats = np.array([-0.05, 0.05])
    lons = np.array([0.05, 0.15])
    arr = np.array([[1.0, 3.0], [5.0, 9.0]])  # [lat asc, lon asc]
    # dst grid: single row of cells at y=0.0 (between the two lats) and
    # x=0.10 (between the two lons).
    like = type("L", (), {})()
    like.height, like.width = 1, 1
    like.transform = Affine(0.2, 0.0, 0.0, 0.0, -0.2, 0.1)  # cell 0.2x0.2, centre (0.1, 0.0)
    like.crs = "EPSG:4326"
    got = rv.cube_to_grid(arr, lats, lons, like)
    np.testing.assert_allclose(got[0, 0], (1 + 3 + 5 + 9) / 4, rtol=1e-6)
