"""Hermetic tests for tp/coherence_loss.py — pure arrays, no IO."""
from __future__ import annotations

import numpy as np
import pytest
from affine import Affine
from rasterio.crs import CRS

from tp.coherence_loss import (
    baseline_hit_pct,
    hit_stats,
    loss_polygons,
    point_distances_m,
)

# EPSG:32610 grid, 60 m pixels, origin at (500000, 5200000) top-left.
CRS_UTM = CRS.from_epsg(32610)
TR = Affine(60.0, 0.0, 500000.0, 0.0, -60.0, 5200000.0)


def _lonlat(x_utm, y_utm):
    from rasterio.warp import transform
    xs, ys = transform(CRS_UTM, CRS.from_epsg(4326), [x_utm], [y_utm])
    return xs[0], ys[0]


def test_point_distances_zero_inside_mask():
    mask = np.zeros((10, 10), dtype=bool)
    mask[5, 5] = True
    lon, lat = _lonlat(500000 + 5.5 * 60, 5200000 - 5.5 * 60)
    d = point_distances_m(mask, TR, CRS_UTM, [lon], [lat])
    assert d[0] == pytest.approx(0.0)


def test_point_distances_euclidean():
    mask = np.zeros((10, 10), dtype=bool)
    mask[0, 0] = True
    # point at cell (4,3): dx=3.5*60? centre-to-centre (0,0)->(4,3):
    # 4 cells east, 3 south -> dist_px 5 -> 300 m.
    lon, lat = _lonlat(500000 + 4.5 * 60, 5200000 - 3.5 * 60)
    d = point_distances_m(mask, TR, CRS_UTM, [lon], [lat])
    assert d[0] == pytest.approx(300.0)


def test_point_distances_outside_is_nan():
    mask = np.ones((10, 10), dtype=bool)
    lon, lat = _lonlat(1.0, 1.0)  # far outside UTM-10N grid
    d = point_distances_m(mask, TR, CRS_UTM, [lon], [lat])
    assert np.isnan(d[0])


def test_hit_stats_radii():
    mask = np.zeros((20, 20), dtype=bool)
    mask[10, 10] = True
    lon_hit, lat_hit = _lonlat(500000 + 10.5 * 60, 5200000 - 10.5 * 60)
    lon_far, lat_far = _lonlat(500000 + 19.5 * 60, 5200000 - 0.5 * 60)
    out = hit_stats(mask, TR, CRS_UTM, [lon_hit, lon_far],
                    [lat_hit, lat_far], radii_m=(100.0, 500.0))
    assert out["points_valid"] == 2
    assert out["hit_100m_pct"] == 50.0   # only the on-mask point
    # far point: (10,10)->(19,0) = 9 E + 10 N cells -> sqrt(181) px ~ 806 m
    assert out["hit_500m_pct"] == 50.0


def test_hit_stats_empty_mask_zero_pct():
    mask = np.zeros((10, 10), dtype=bool)
    lon, lat = _lonlat(500000 + 5.5 * 60, 5200000 - 5.5 * 60)
    out = hit_stats(mask, TR, CRS_UTM, [lon], [lat])
    assert out["hit_100m_pct"] == 0.0


def test_baseline_hit_pct_deterministic_and_bounded():
    rng_mask = np.zeros((20, 20), dtype=bool)
    steep = np.ones((20, 20), dtype=bool)
    steep[:2, :] = False
    lon, lat = _lonlat(500000 + 10.5 * 60, 5200000 - 10.5 * 60)
    a = baseline_hit_pct(steep, 4, TR, CRS_UTM, [lon], [lat],
                         radii_m=(100.0,), n_seeds=10, seed=7)
    b = baseline_hit_pct(steep, 4, TR, CRS_UTM, [lon], [lat],
                         radii_m=(100.0,), n_seeds=10, seed=7)
    assert a == b                                   # seeded -> deterministic
    assert 0.0 <= a["hit_100m_mean_pct"] <= 100.0
    assert a["n_seeds"] == 10 and a["draw_px"] == 4
    _ = rng_mask


def test_baseline_draw_capped_by_pool():
    steep = np.zeros((4, 4), dtype=bool)
    steep[0, 0] = steep[3, 3] = True    # pool of 2 cells
    lon, lat = _lonlat(500000 + 0.5 * 60, 5200000 - 0.5 * 60)
    out = baseline_hit_pct(steep, 999, TR, CRS_UTM, [lon], [lat],
                           radii_m=(100.0,), n_seeds=3, seed=1)
    assert out["draw_px"] == 2
    assert out["hit_100m_mean_pct"] == 100.0  # pool covers the point


def test_loss_polygons_min_px():
    mask = np.zeros((20, 20), dtype=bool)
    mask[2:6, 2:6] = True            # 16 px component
    mask[10, 10] = True              # 1 px component
    geoms, labels, sizes = loss_polygons(mask, TR, min_px=9)
    assert len(geoms) == 1
    lid, geom = geoms[0]
    assert sizes[lid] == 16
    # geometry sits at the component's UTM position (x ~ 500120..)
    xs = [c[0] for c in geom["coordinates"][0]]
    assert min(xs) == pytest.approx(500000 + 2 * 60)
    assert labels[10, 10] != 0       # counted but filtered from geoms
