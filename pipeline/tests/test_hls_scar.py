import inspect

import numpy as np
import rasterio
from rasterio.transform import from_origin
from tp import hls_scar
from tp.hls_scar import _drop_small, detect

SHAPE = (20, 20)


def _write_bands(path, bands, nodata=np.nan):
    h, w = bands[0].shape
    with rasterio.open(
        path, "w", driver="GTiff", height=h, width=w, count=len(bands),
        dtype="float32", crs="EPSG:32610",
        transform=from_origin(500000, 5300000, 10, 10), nodata=nodata,
    ) as dst:
        for i, b in enumerate(bands, 1):
            dst.write(np.asarray(b, np.float32), i)
    return path


def _pair(tmp_path, scar_box=(2, 6, 2, 6), fmask=None):
    """pre/post [NIR, SWIR2] rasters; vegetation NBR ~0.43, scar NBR ~-0.33."""
    nir_pre = np.full(SHAPE, 0.5, np.float32)
    swir2_pre = np.full(SHAPE, 0.2, np.float32)
    nir_post, swir2_post = nir_pre.copy(), swir2_pre.copy()
    r0, r1, c0, c1 = scar_box
    nir_post[r0:r1, c0:c1] = 0.2          # scar: NIR drops, SWIR2 rises
    swir2_post[r0:r1, c0:c1] = 0.4
    pre_p = _write_bands(tmp_path / "pre.tif", [nir_pre, swir2_pre])
    post_bands = [nir_post, swir2_post]
    if fmask is not None:
        post_bands.append(fmask)
    post_p = _write_bands(tmp_path / "post.tif", post_bands)
    return pre_p, post_p


def _slope(tmp_path, fill=30.0):
    return _write_bands(tmp_path / "slope.tif", [np.full(SHAPE, fill, np.float32)])


def test_dnbr_detection_fires(tmp_path):
    pre_p, post_p = _pair(tmp_path)
    slope_p = _slope(tmp_path)
    stats = detect(pre_p, post_p, tmp_path / "out.tif", slope_tif=slope_p)
    assert stats["candidate_patches"] == 1
    assert stats["candidate_px"] == 16     # 4x4 scar
    assert stats["fmask"] == "absent"
    with rasterio.open(tmp_path / "out.tif") as src:
        arr = src.read(1)
        assert src.crs.to_string() == "EPSG:32610"
    assert arr[2:6, 2:6].sum() == 16
    assert arr.sum() == 16


def test_fmask_cloud_and_shadow_bits_mask(tmp_path):
    fmask = np.zeros(SHAPE, np.float32)
    fmask[2, 2] = 2                       # bit1 cloud
    fmask[3, 3] = 4                       # bit2 cloud shadow
    fmask[4, 4] = 8                       # bit3 snow -> NOT masked
    pre_p, post_p = _pair(tmp_path, fmask=fmask)
    slope_p = _slope(tmp_path)
    stats = detect(pre_p, post_p, tmp_path / "out.tif", slope_tif=slope_p)
    assert stats["fmask"] == "applied"
    assert stats["fmask_masked_px"] == 2
    assert stats["candidate_px"] == 14     # 16 - 2 masked


def test_slope_gate_excludes(tmp_path):
    pre_p, post_p = _pair(tmp_path)
    slope = np.full(SHAPE, 30.0, np.float32)
    slope[2:6, 2:4] = 5.0                  # flat half of the scar
    slope_p = _write_bands(tmp_path / "slope.tif", [slope])
    stats = detect(pre_p, post_p, tmp_path / "out.tif",
                   slope_tif=slope_p, min_px=1)
    assert stats["candidate_px"] == 8      # only the steep half remains


def test_min_blob_drops_small_patch(tmp_path):
    pre_p, post_p = _pair(tmp_path, scar_box=(2, 4, 2, 4))  # 2x2 = 4 px
    slope_p = _slope(tmp_path)
    stats = detect(pre_p, post_p, tmp_path / "out.tif",
                   slope_tif=slope_p, min_px=10)
    assert stats["candidate_patches"] == 0
    assert stats["candidate_px"] == 0


def test_one_band_precomputed_nbr(tmp_path):
    nbr_pre = np.full(SHAPE, 0.4, np.float32)
    nbr_post = nbr_pre.copy()
    nbr_post[2:8, 2:8] = -0.2              # dNBR 0.6
    pre_p = _write_bands(tmp_path / "pre.tif", [nbr_pre])
    post_p = _write_bands(tmp_path / "post.tif", [nbr_post])
    stats = detect(pre_p, post_p, tmp_path / "out.tif")  # no slope gate
    assert stats["slope_gate"] is False
    assert stats["candidate_px"] == 36


def test_drop_small():
    mask = np.zeros(SHAPE, bool)
    mask[2:6, 2:6] = True                  # 16 px kept
    mask[10, 10] = True                    # 1 px dropped
    out = _drop_small(mask, 5)
    assert out.sum() == 16
    assert out[10, 10] == 0


def test_candidates_wording_and_limitation():
    doc = hls_scar.__doc__
    assert "candidate" in doc.lower()
    assert "LIMITATION" in doc
    assert "GUNW" in doc
    src = inspect.getsource(hls_scar)
    assert "confirmed" not in src          # never 'confirmed' for landslides
