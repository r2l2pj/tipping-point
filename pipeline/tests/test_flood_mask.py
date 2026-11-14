import numpy as np
import pytest
import rasterio
from conftest import write_tif
from tp.flood_mask import (
    adaptive_threshold,
    compute_bright,
    compute_flood,
    flood_mask,
    min_mapping_unit,
    to_db,
)


def test_to_db():
    assert to_db(np.array([1.0])) == pytest.approx([0.0])
    assert to_db(np.array([0.01])) == pytest.approx([-20.0])
    assert np.isnan(to_db(np.array([np.nan]))[0])
    # zeros clip to 1e-6 -> -60 dB, never -inf
    assert to_db(np.array([0.0])) == pytest.approx([-60.0])


def _bimodal(rng, n=4096, water_frac=0.3):
    water = rng.normal(-22.0, 1.5, int(n * water_frac))
    land = rng.normal(-7.0, 1.5, n - water.size)
    return np.concatenate([land, water])


def test_adaptive_threshold_bimodal():
    rng = np.random.default_rng(0)
    img = _bimodal(rng).reshape(64, 64)
    thr, method = adaptive_threshold(img, tile_px=32)
    assert method == "adaptive"
    assert -20.0 < thr < -10.0


def test_adaptive_threshold_fallback_unimodal():
    rng = np.random.default_rng(1)
    img = rng.normal(-10.0, 1.0, (64, 64))
    thr, method = adaptive_threshold(img, tile_px=32, fallback=-15.0)
    assert method == "fallback"
    assert thr == -15.0


def test_adaptive_threshold_fallback_nodata():
    thr, method = adaptive_threshold(np.full((64, 64), np.nan), tile_px=32)
    assert method == "fallback"
    assert thr == -15.0


def test_min_mapping_unit():
    m = np.zeros((20, 20), bool)
    m[2:6, 2:6] = True      # 16 px blob kept
    m[10, 10] = True        # 1 px blob dropped
    out = min_mapping_unit(m, 5)
    assert out[10, 10] == 0
    assert out[2:6, 2:6].all()


def test_compute_flood_gates():
    pre = np.full((10, 10), -8.0)
    post = np.full((10, 10), -8.0)
    post[1:4, 1:4] = -20.0          # darkens into water
    post[5, 5] = -20.0              # darkens but in watermask
    post[6, 6] = -20.0              # darkens but HAND too high
    watermask = np.zeros((10, 10)); watermask[5, 5] = 1
    hand = np.full((10, 10), 2.0); hand[6, 6] = 20.0
    flood, water = compute_flood(pre, post, -15.0, watermask, hand)
    assert flood[1:4, 1:4].all()
    assert not flood[5, 5] and not flood[6, 6]
    assert water[5, 5] == 0          # permanent water excluded from water_now
    # pixels already water before are not "new" flood
    pre2, post2 = pre.copy(), post.copy()
    pre2[1:4, 1:4] = -20.0
    flood2, _ = compute_flood(pre2, post2, -15.0)
    assert not flood2[1:4, 1:4].any()


def test_compute_bright():
    pre = np.full((10, 10), -12.0)
    post = np.full((10, 10), -12.0)
    post[1:3, 1:3] = -8.0           # +4 dB brightening, low HAND
    post[5, 5] = -7.0               # brightens but HAND > 5 m
    hand = np.full((10, 10), 2.0); hand[5, 5] = 8.0
    bright = compute_bright(pre, post, -15.0, hand=hand)
    assert bright[1:3, 1:3].all()
    assert not bright[5, 5]


def test_flood_mask_io(tmp_path):
    rng = np.random.default_rng(2)
    shape = (96, 96)
    pre_lin = 10 ** (rng.normal(-8, 1.0, shape) / 10)
    post_lin = pre_lin.copy()
    # a flooded lake: darkens ~12 dB over a 20x20 block
    post_lin[10:30, 10:30] = 10 ** (rng.normal(-21, 1.0, (20, 20)) / 10)
    # a tiny 2-px blob that the MMU must remove
    post_lin[50, 50] = 1e-3
    post_lin[70:80, 70:80] = 10 ** (rng.normal(-2, 0.5, (10, 10)) / 10)  # brightens ~6 dB

    pre_p = write_tif(tmp_path / "pre.tif", pre_lin.astype(np.float32))
    post_p = write_tif(tmp_path / "post.tif", post_lin.astype(np.float32))
    wm = np.zeros(shape, np.float32); wm[0:5, 0:5] = 1
    wm_p = write_tif(tmp_path / "watermask.tif", wm)
    hand = np.full(shape, 3.0, np.float32)
    hand_p = write_tif(tmp_path / "hand.tif", hand)

    stats = flood_mask(
        pre_p, post_p, tmp_path / "out",
        watermask_path=wm_p, hand_path=hand_p, mmu_ha=0.05,
    )
    assert stats["threshold_method"] in ("adaptive", "fallback")
    assert stats["flood_km2"] > 0
    assert (tmp_path / "out" / "flood_nisar.tif").exists()
    assert (tmp_path / "out" / "water_nisar.tif").exists()
    assert (tmp_path / "out" / "flood_bright_nisar.tif").exists()

    with rasterio.open(tmp_path / "out" / "flood_nisar.tif") as src:
        flood = src.read(1)
    assert flood[10:30, 10:30].mean() > 0.8
    assert flood[50, 50] == 0          # MMU removed the speckle
    with rasterio.open(tmp_path / "out" / "flood_bright_nisar.tif") as src:
        bright = src.read(1)
    assert bright[70:80, 70:80].mean() > 0.5
