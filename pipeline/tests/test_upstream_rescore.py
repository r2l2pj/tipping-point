"""tools/upstream_rescore.py — pure pieces of the L35 upstream-rain experiment (hermetic)."""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import upstream_rescore as ur


def test_grid_weights_inside_and_nearest_fallback():
    lat = np.array([1.05, 0.95, 0.85])
    lon = np.array([10.05, 10.15, 10.25])
    w = ur.grid_weights(box(10.0, 0.9, 10.2, 1.1), lat, lon)
    assert w.sum() == pytest.approx(1.0) and (w > 0).sum() == 4
    tiny = ur.grid_weights(box(10.24, 0.84, 10.26, 0.86), lat, lon)   # no centre inside
    assert tiny[2, 2] == 1.0


def test_wmean_ignores_nan_cells():
    cube = np.array([[[1.0, np.nan], [3.0, 5.0]]])
    w = np.full((2, 2), 0.25)
    assert ur.wmean(cube, w)[0] == pytest.approx(3.0)


def test_climatology_matches_definition():
    # constant 1 mm/day except one 30 mm day per year -> 3-day sums 3 or 32
    series = {}
    for y in range(2000, 2025):
        d = dt.date(y, 1, 1)
        while d.year == y:
            series[d] = 1.0
            d += dt.timedelta(days=1)
    p = ur.climatology(series, dt.date(2024, 5, 2))
    assert p == (3.0, 3.0, 3.0)
    assert ur.climatology({}, dt.date(2024, 5, 2)) is None


def test_new_tpi_takes_max_and_keeps_nodata():
    rl = np.array([0.0, 0.8, np.nan])
    ru = np.array([0.5, 0.2, 0.9])
    s = np.ones(3)
    t = np.ones(3)
    old = np.array([0.0, 0.9, np.nan])
    out = ur.new_tpi(rl, ru, s, t, old)
    assert out[0] == pytest.approx(0.5 ** (1 / 3), abs=1e-6)
    assert out[1] == pytest.approx(0.8 ** (1 / 3), abs=1e-6)
    assert np.isnan(out[2])


def test_lookup_maps_basins():
    ids = np.array([[1, 2], [0, 2]])
    assert ur.lookup(ids, {1: 0.3, 2: np.nan}).tolist() == [[0.3, 0.0], [0.0, 0.0]]
