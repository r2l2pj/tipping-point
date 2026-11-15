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


def test_m4_scorer_matches_m1_function():
    import m4_cut_calibration as m4
    rng = np.random.default_rng(0)
    t = np.round(rng.random((30, 40)), 1)  # coarse T -> many ties
    tpi = np.clip(t * rng.random((30, 40)) + 0.2, 0, 1)
    tpi[0, :5] = np.nan
    ref = rng.integers(0, t.size, 200)
    dom = np.ones(t.shape, bool)
    dom[-3:] = False
    s = m4.Scorer(tpi, t, ref, dom)
    for cut in (0.3, 0.5, 0.7):
        a, b = s.at(cut), lvt.lift_vs_terrain(tpi, t, ref, dom, cut)
        assert a["flagged_cells"] == b["flagged_cells"] and a["hit_pct"] == b["hit_pct"]
        assert a["ratio"] == b["ratio"] and a["lift"] == b["lift"]


def test_m4_calibrate_picks_lowest_cut_meeting_both_rules():
    import m4_cut_calibration as m4
    t = np.zeros((10, 10))
    t[0, 0] = 1.0  # terrain points at cell 0; the truth is cell 99, which only the TPI ranks high
    tpi = np.linspace(0, 0.98, 100).reshape(10, 10)
    c = m4.calibrate(m4.Scorer(tpi, t, np.array([99])))
    assert c["critico_area_only"] == c["critico_area_and_ratio"]
    assert c["curve"][0]["area_pct"] > 2.0


def test_ablation_compose_reproduces_tpi_and_masks_wetland(tmp_path):
    import tpi_v3_ablation as ab
    rng = np.random.default_rng(1)
    r, s = rng.random((3, 4, 5)).astype(np.float32), rng.random((3, 4, 5)).astype(np.float32)
    t2, t3 = rng.random((4, 5)).astype(np.float32), rng.random((4, 5)).astype(np.float32)
    tpi2 = np.cbrt(r * s * t2)
    tpi2[:, 0, 0] = np.nan  # water
    wet = np.zeros((4, 5), bool)
    wet[1, 1] = True
    tpi3 = np.cbrt(r * s * t3)
    tpi3[:, 0, 0] = np.nan
    tpi3[:, 1, 1] = np.nan
    np.savez(tmp_path / "f.npz", tpi_v2_flood=tpi2, tpi_v3_flood=tpi3, t_v2_flood=t2, t_v3_flood=t3, wetland=wet)
    np.savez(tmp_path / "c.npz", r_v2=r, r_v3=r, s_v2=s, s_v3=s)
    fr, co = np.load(tmp_path / "f.npz"), np.load(tmp_path / "c.npz")
    sel = np.array([True, True, False])
    chk = ab.check_reproduces(fr, co, "flood", sel)
    assert chk["v2"]["max_abs_err"] < 1e-6 and chk["v2"]["nan_mismatch_cells"] == 0
    assert chk["v3"]["max_abs_err"] < 1e-6 and chk["v3"]["nan_mismatch_cells"] == 0
