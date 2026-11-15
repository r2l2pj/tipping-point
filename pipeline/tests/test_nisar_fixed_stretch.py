"""NISAR gray frames: one fixed -25..0 dB stretch (gray = 1 + (dB+25)/25*254, alpha 0 = nodata)."""
import io

import numpy as np
from rasterio.transform import from_origin
from tp.bundle import NISAR_DB_STRETCH, NISAR_LEGEND, _gray_png_3857, nisar_stretch_meta


def _rgba(png: bytes) -> np.ndarray:
    from PIL import Image
    return np.asarray(Image.open(io.BytesIO(png)).convert("RGBA"))


def test_fixed_stretch_is_date_independent_and_maps_ends():
    tr = from_origin(-57.0, -29.0, 0.01, 0.01)
    a = np.full((40, 40), -25.0)
    a[:, 20:] = 0.0
    a[:5, :5] = np.nan
    b = a - 3.0          # a darker scene: a per-image stretch would rescale it, a fixed one must not
    b[:, 20:] = 0.0
    pa, _ = _gray_png_3857(a, tr, "EPSG:4326", float("nan"), stretch=NISAR_DB_STRETCH)
    pb, _ = _gray_png_3857(b, tr, "EPSG:4326", float("nan"), stretch=NISAR_DB_STRETCH)
    ra, rb = _rgba(pa), _rgba(pb)
    va = ra[..., 3] > 0
    assert set(np.unique(ra[..., 0][va])) <= {1, 255}
    assert (ra[..., 3] == 0).any()                     # nodata transparent
    assert np.array_equal(ra[..., 0][:, -5:], rb[..., 0][:, -5:])   # 0 dB -> 255 in both
    assert rb[..., 0][va & (rb[..., 3] > 0)].min() == 1             # < -25 dB clips to 1, not 0


def test_meta():
    assert NISAR_DB_STRETCH == (-25.0, 0.0)
    m = nisar_stretch_meta()
    assert m["stretch_db"] == {"db_min": -25.0, "db_max": 0.0}
    assert m["legend"] is NISAR_LEGEND and NISAR_LEGEND["unit"] == "dB"
