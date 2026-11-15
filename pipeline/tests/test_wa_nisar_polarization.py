"""Hermetic tests for tools/wa_nisar_polarization.py — synthetic grids only."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # pipeline/
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # repo root

import numpy as np
import pytest

from tools import wa_nisar_polarization as wp


def test_diverging_rgba_endpoints():
    arr = np.array([[-6.0, 0.0, 6.0]])
    rgba = wp.diverging_rgba(arr)
    assert tuple(rgba[0, 0, :3]) == wp.DIV_LOW     # -clip -> deep blue
    assert tuple(rgba[0, 1, :3]) == wp.DIV_MID     # 0 -> near-white
    assert tuple(rgba[0, 2, :3]) == wp.DIV_HIGH    # +clip -> deep red
    assert rgba[0, :, 3].tolist() == [255, 255, 255]


def test_diverging_rgba_clip_and_nodata():
    arr = np.array([[-99.0, np.nan, 99.0]])
    rgba = wp.diverging_rgba(arr)
    assert tuple(rgba[0, 0, :3]) == wp.DIV_LOW     # clipped to the ends
    assert tuple(rgba[0, 2, :3]) == wp.DIV_HIGH
    assert rgba[0, 1, 3] == 0                      # nodata transparent


def test_diverging_rgba_monotonic():
    arr = np.linspace(-6, 6, 13)[None, :]
    rgba = wp.diverging_rgba(arr).astype(int)
    assert rgba[0, 0, 2] > rgba[0, 0, 0]      # blue dominant at -clip
    assert rgba[0, -1, 0] > rgba[0, -1, 2]    # red dominant at +clip
    # colour distance from the white midpoint falls to 0 at t=0 and rises
    # monotonically on both sides
    d = np.abs(rgba[..., :3] - np.array(wp.DIV_MID)).sum(axis=2)
    assert np.all(np.diff(d[0, :7]) <= 0)
    assert np.all(np.diff(d[0, 6:]) >= 0)


def test_iou():
    a = np.array([[1, 1, 0], [0, 0, 0]], bool)
    b = np.array([[1, 0, 0], [0, 1, 0]], bool)
    assert wp.iou(a, b) == pytest.approx(1 / 3)
    assert wp.iou(a & False, b & False) == 0.0


def test_region_stats():
    pre = np.array([[10.0, 20.0, np.nan]])
    post = np.array([[4.0, 22.0, 1.0]])
    region = np.array([[1, 1, 1]], bool)
    s = wp.region_stats(pre, post, region)
    assert s["px"] == 2                          # nan pair excluded
    assert s["mean_pre_db"] == pytest.approx(15.0)
    assert s["median_post_db"] == pytest.approx(13.0)
    assert s["mean_change_db"] == pytest.approx(-2.0)


def test_hv_candidate_mask():
    thr = -22.0
    pre = np.array([[-10.0, -10.0, -25.0, -10.0]])
    post = np.array([[-25.0, -15.0, -26.0, -23.0]])
    # px0: drops 15 dB into < -22 -> candidate
    # px1: not dark enough -> no
    # px2: was already HV-water-dark before -> no
    # px3: drops 13 dB into < -22 -> candidate
    cand = wp.hv_candidate_mask(pre, post, thr, water=None)
    assert cand.tolist() == [[True, False, False, True]]
    water = np.array([[1, 0, 0, 0]], bool)
    assert wp.hv_candidate_mask(pre, post, thr, water).tolist() == \
        [[False, False, False, True]]


def test_lc_group_arr_and_bright_table():
    lc = np.array([[1, 5, 12, 14, 13, 17, 255, 8]])
    grp = wp.lc_group_arr(lc)
    assert grp.tolist() == [[1, 1, 2, 2, 3, 0, 0, 0]]
    bright = np.array([[1, 0, 1, 0, 1, 1, 1, 1]], bool)
    t = wp.bright_class_table(lc, bright, 0.36)
    assert set(t) == {"forest", "cropland", "urban", "other"}
    assert t["forest"]["km2"] == pytest.approx(0.36)
    assert t["urban"]["share"] == pytest.approx(1 / 6, abs=1e-3)
    assert t["other"]["km2"] == pytest.approx(3 * 0.36)


def test_index_entry_shape():
    e = wp.index_entry("nisar_hv_pre", wp.PRE_DATE, "wa_2025/png/x.png",
                       "wa_2025/cog/y.tif", [-122.6, 47.0, -121.5, 48.5],
                       {"legend": wp.GRAY_LEGEND, "db_range": [-30.1, -9.4]})
    assert e["kind"] == "observed" and e["forecast"] is False
    assert e["date"] == "2025-12-04" and e["db_range"] == [-30.1, -9.4]
