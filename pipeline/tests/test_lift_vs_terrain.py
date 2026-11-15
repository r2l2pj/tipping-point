"""tools/lift_vs_terrain.py — M1 terrain-only baseline (hermetic)."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import lift_vs_terrain as lvt


def test_tpi_equal_to_terrain_ranking_gives_ratio_one():
    t = np.arange(16, dtype=float).reshape(4, 4) / 15
    tpi = t.copy()  # same ranking: TPI flags exactly the top-T cells
    r = lvt.lift_vs_terrain(tpi, t, np.array([15, 14, 0]), cut=0.85)
    assert r["flagged_cells"] == 3 and r["ratio"] == 1.0
    assert r["lift"] == r["lift_terrain"] == round((2 / 3) / (3 / 16), 2)


def test_terrain_beats_tpi_and_missing_tpi_counts_as_miss():
    t = np.array([[1.0, 0.9], [0.1, 0.0]])
    tpi = np.array([[0.1, 0.1], [0.9, np.nan]])
    r = lvt.lift_vs_terrain(tpi, t, np.array([0, 3]), cut=0.4)  # ref 3 has no TPI
    assert r["n_ref"] == 2 and r["hit_pct"] == 0.0 and r["ratio"] == 0.0
    assert r["terrain_hit_pct"] == 50.0


def test_ties_use_expected_hit_not_raster_order():
    t = np.ones((2, 2))  # a T plateau: every cell tied
    tpi = np.array([[0.0, 0.0], [0.0, 0.9]])
    r = lvt.lift_vs_terrain(tpi, t, np.array([3]), cut=0.4)
    assert r["terrain_hit_pct"] == 25.0 and r["ratio"] == 4.0  # 1 of 4 tied cells picked
    assert r["ratio_stable"] is None  # §M1 raster order picks cell 0 and misses


def test_domain_and_no_flag():
    t = np.ones((2, 2))
    tpi = np.full((2, 2), 0.1)
    dom = np.array([[True, False], [True, True]])
    r = lvt.lift_vs_terrain(tpi, t, np.array([1, 2]), dom, cut=0.4)
    assert r["n_ref"] == 1 and r["flagged_cells"] == 0 and r["lift"] is None and "reason" in r
