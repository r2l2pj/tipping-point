import numpy as np
import rasterio
from conftest import write_tif
from tp.landslide_coh import compute_candidates, landslide_candidates


def test_compute_candidates_basic():
    ref = np.full((20, 20), 0.7)
    evt = np.full((20, 20), 0.7)
    slope = np.full((20, 20), 30.0)
    evt[2:7, 2:6] = 0.2                      # coherence drop scar
    out = compute_candidates(ref, evt, slope)
    assert (out[2:7, 2:6] == 1).all()
    assert out.sum() == 20


def test_compute_candidates_gates():
    ref = np.full((10, 10), 0.7)
    evt = np.full((10, 10), 0.2)             # drop everywhere
    slope = np.full((10, 10), 30.0)
    slope[0:3, 0:3] = 5.0                    # flat -> excluded
    ref[3:5, 3:5] = 0.3                      # low ref coherence -> excluded
    out = compute_candidates(ref, evt, slope)
    assert out[0:3, 0:3].sum() == 0
    assert out[3:5, 3:5].sum() == 0
    assert out[6, 6] == 1


def test_compute_candidates_masks_and_hv():
    ref = np.full((10, 10), 0.7)
    evt = np.full((10, 10), 0.2)
    slope = np.full((10, 10), 30.0)
    flood = np.zeros((10, 10)); flood[1:3, 1:3] = 1
    snow = np.zeros((10, 10)); snow[4, 4] = 1
    elev = np.full((10, 10), 100.0); elev[6, 6] = 1000.0
    hv_drop = np.zeros((10, 10)); hv_drop[8, 8] = 3.0   # > 2 dB -> class 2
    out = compute_candidates(
        ref, evt, slope, flood=flood, snow=snow, elev=elev,
        hv_drop_db=hv_drop, max_elev_m=600.0,
    )
    assert out[1:3, 1:3].sum() == 0          # flood
    assert out[4, 4] == 0                    # snow
    assert out[6, 6] == 0                    # above elevation cap
    assert out[8, 8] == 2                    # high-confidence
    assert out[9, 9] == 1                    # plain candidate


def test_landslide_candidates_io(tmp_path):
    shape = (40, 40)
    ref = np.full(shape, 0.7, np.float32)
    evt = np.full(shape, 0.7, np.float32)
    slope = np.full(shape, 30.0, np.float32)
    evt[5:9, 5:9] = 0.2                      # 4x4 scar = 16 px, kept at >=10
    evt[20, 20] = 0.2                        # 1 px speck, dropped
    ref_p = write_tif(tmp_path / "ref.tif", ref, res=20.0)
    evt_p = write_tif(tmp_path / "evt.tif", evt, res=20.0)
    slope_p = write_tif(tmp_path / "slope.tif", slope, res=20.0)

    stats = landslide_candidates(
        ref_p, evt_p, slope_p, tmp_path / "cand.tif",
        max_elev_m=None,
    )
    assert stats["candidate_patches"] == 1
    assert stats["candidate_px"] == 16
    with rasterio.open(tmp_path / "cand.tif") as src:
        arr = src.read(1)
    assert arr[20, 20] == 0
