"""Hermetic tests for tools/wa_nisar_timeseries.py pure functions."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
for p in (str(REPO_ROOT), str(REPO_ROOT / "pipeline")):
    if p not in sys.path:
        sys.path.insert(0, p)

from tp import response_nisar as rn
from tools import wa_nisar_timeseries as ts


class _Resp:
    def __init__(self, items):
        self._items = items
        self.headers = {}

    def raise_for_status(self):
        return None

    def json(self):
        return {"items": self._items}


def _umm(granule_id: str, h5: str = "https://example.com/x.h5") -> dict:
    return {"umm": {"GranuleUR": granule_id,
                    "RelatedUrls": [{"Type": "GET DATA", "URL": h5}]},
            "meta": {}}


def test_find_h5_urls_maps_roles():
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(params["temporal"])
        items = []
        for role, gid in ts.GRANULES.items():
            start = ts.WINDOWS[role][0]
            if params["temporal"].startswith(start):
                items.append(_umm(gid))
        return _Resp(items)

    urls = ts.find_h5_urls(get=fake_get)
    assert set(urls) == {"ref", "pre", "post"}
    assert all(u.endswith(".h5") for u in urls.values())
    assert len(calls) == 3


def test_find_h5_urls_missing_role_raises():
    def fake_get(url, params=None, headers=None, timeout=None):
        return _Resp([_umm(ts.GRANULES["ref"])])  # only ever the ref granule

    with pytest.raises(RuntimeError, match="roles"):
        ts.find_h5_urls(get=fake_get)


def test_area_stats_linear_mean():
    # two pixels per date: -20 dB and -10 dB -> linear mean -> back to dB
    vals = [np.array([-20.0, -10.0])] * 3
    out = ts.area_stats(vals)
    lin_mean = (10 ** -2.0 + 10 ** -1.0) / 2
    np.testing.assert_allclose(out["mean_db"], [10 * np.log10(lin_mean)] * 3)
    np.testing.assert_allclose(out["median_db"], [-15.0] * 3)
    assert out["p10_db"][0] < out["p90_db"][0]
    np.testing.assert_allclose(out["p10_db"], [-19.0] * 3, atol=0.05)
    np.testing.assert_allclose(out["p90_db"], [-11.0] * 3, atol=0.05)


def test_area_stats_dates_axis_and_nan():
    rng = np.random.default_rng(0)
    vals = [rng.normal(-8, 2, size=500) for _ in range(3)]
    vals[1] = np.concatenate([vals[1], [np.nan, np.nan]])
    out = ts.area_stats(vals)
    assert len(out["mean_db"]) == 3
    assert np.isfinite(out["mean_db"]).all()
    # mean-in-linear >= mean-in-dB (Jensen)
    assert (np.asarray(out["mean_db"])
            > np.asarray([np.nanmean(v) for v in vals])).all()
    # an all-NaN date -> nan, not a crash
    out2 = ts.area_stats([vals[0], np.array([np.nan])])
    assert np.isnan(out2["mean_db"][1])


def test_box_window_inside_and_clipped():
    r0, r1, c0, c1 = ts.box_window(ts.BBOX)
    # AOI corners project inside the granule∩AOI grid (snapped outward)
    assert 0 <= r0 < r1 <= ts.GRID.height and r1 - r0 > 2700
    assert 0 <= c0 < c1 <= ts.GRID.width and c1 - c0 > 1300
    r0, r1, c0, c1 = ts.box_window((-121.75, 48.15, -121.55, 48.35))
    assert 0 <= r0 < r1 <= ts.GRID.height
    assert 0 <= c0 < c1 <= ts.GRID.width
    # fully outside -> empty window
    r0, r1, c0, c1 = ts.box_window((-140.0, 20.0, -139.0, 21.0))
    assert r1 - r0 == 0 or c1 - c0 == 0


def test_grid_matches_committed_cog_extent():
    # snapped 60 m grid must equal the committed pre/post COG extent
    g = ts.GRID
    assert tuple(g.transform) == (60.0, 0.0, 529490.0, 0.0, -60.0, 5373020.0,
                                0.0, 0.0, 1.0)
    assert (g.width, g.height) == (1410, 2797)
    assert g.epsg == 32610
