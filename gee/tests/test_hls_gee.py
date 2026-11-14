"""Tests for gee/hls_scar.py — fake `ee`, no creds needed.

`sys.modules['ee']` is replaced with a chainable call recorder BEFORE the
module under test imports it; `gee.common` is stubbed only when U4's module
is absent from this branch. The original sys.modules['ee'] entry is
restored right after the import so other test files see the real package.
"""
from __future__ import annotations

import inspect
import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root

LOG: list = []


class _FakeObj:
    """Stand-in for any ee object: attributes chain, calls are recorded.

    Callable arguments (ee .map() lambdas) are invoked on a fresh recorder
    so their inner calls land in LOG too. getInfo() returns a plain int so
    merged metrics stay JSON-serializable.
    """

    def __init__(self, path, ret=None):
        self._path, self._ret = path, ret

    def __getattr__(self, name):
        return _FakeObj(f"{self._path}.{name}")

    def __call__(self, *args, **kwargs):
        LOG.append((self._path, list(args), dict(kwargs)))
        for a in (*args, *kwargs.values()):
            if isinstance(a, types.FunctionType):
                a(_FakeObj(f"{self._path}()"))
        return _FakeObj(f"{self._path}()")

    def getInfo(self):
        return self._ret if self._ret is not None else 0


FAKE_EE = _FakeObj("ee")


def _stub_common() -> types.ModuleType:
    m = types.ModuleType("gee.common")
    m.init = lambda project=None: "test-project"
    m.aoi_geom = lambda cid: FAKE_EE.Geometry.Rectangle([0, 0, 1, 1])
    m.asset_path = (lambda name, project=None:
                    f"projects/test-project/assets/tipping-point/{name}")
    m.ASSET_ROOT = "projects/test-project/assets/tipping-point"
    m.GEE_PROJECT = "test-project"
    m.IMG_NASADEM = "NASA/NASADEM_HGT/001"
    m.BAND_DEM = "elevation"
    return m


_had_ee = "ee" in sys.modules
_orig_ee = sys.modules.get("ee")
sys.modules["ee"] = FAKE_EE
try:
    import gee.common  # noqa: F401  (U4's module when it has landed)
except ModuleNotFoundError:
    sys.modules["gee.common"] = _stub_common()

from gee import hls_scar

if _had_ee:
    sys.modules["ee"] = _orig_ee
else:
    sys.modules.pop("ee", None)


@pytest.fixture(autouse=True)
def _clear_log():
    LOG.clear()


@pytest.fixture
def commons(monkeypatch):
    """Deterministic gee.common surface on the module (real or stubbed)."""
    monkeypatch.setattr(
        hls_scar, "init", lambda project=None: "test-project", raising=False)
    monkeypatch.setattr(
        hls_scar, "aoi_geom",
        lambda cid: FAKE_EE.Geometry.Rectangle([0, 0, 1, 1]), raising=False)
    monkeypatch.setattr(
        hls_scar, "asset_path",
        lambda name, project=None: f"projects/test-project/assets/tipping-point/{name}",
        raising=False)


def _called(path_suffix, args=None, **kw):
    for p, a, k in LOG:
        if not p.endswith(path_suffix):
            continue
        if args is not None and a != args:
            continue
        if any(k.get(key) != val for key, val in kw.items()):
            continue
        return True
    return False


def test_hls_collections_and_band_rename(commons):
    hls_scar.compute("wa_2025")
    assert _called("ee.ImageCollection", ["NASA/HLS/HLSL30/v002"])
    assert _called("ee.ImageCollection", ["NASA/HLS/HLSS30/v002"])
    # L30: red B04, NIR B05, SWIR2 B07; S30: red B04, NIR B8A, SWIR2 B12
    assert _called(".select", [["B04", "B05", "B07", "Fmask"],
                               ["RED", "NIR", "SWIR2", "Fmask"]])
    assert _called(".select", [["B04", "B8A", "B12", "Fmask"],
                               ["RED", "NIR", "SWIR2", "Fmask"]])
    assert _called(".merge") and _called(".filterBounds") and _called(".filterDate")


def test_fmask_clear_mask_bits_1_to_4(commons):
    hls_scar.compute("wa_2025")
    assert hls_scar.FMASK_BAD == 0b11110   # cloud|adjacent|shadow|snow
    assert _called(".bitwiseAnd", [hls_scar.FMASK_BAD])
    assert _called(".neq", [hls_scar.FMASK_FILL])    # Fmask fill 255 masked
    assert _called(".neq", [hls_scar.HLS_FILL])      # reflectance fill -9999


def test_cloud_fraction_bits_1_to_3(commons):
    hls_scar.compute("wa_2025")
    assert hls_scar.FMASK_CLOUDY == 0b1110           # "why radar" cloud bits
    assert _called(".bitwiseAnd", [hls_scar.FMASK_CLOUDY])
    assert _called("ee.Reducer.mean")
    assert _called(".multiply", [100])               # fraction -> pct


def test_dnbr_gates_and_min_blob(commons):
    hls_scar.compute("wa_2025")
    assert sum(1 for p, a, k in LOG
               if p.endswith(".normalizedDifference")) >= 2
    assert _called(".gt", [hls_scar.DNBR_MIN])       # dNBR > 0.1
    assert _called("ee.Image", ["NASA/NASADEM_HGT/001"])
    assert _called("ee.Terrain.slope")
    assert _called(".gt", [hls_scar.MIN_SLOPE_DEG])  # slope_range_deg[0]
    assert _called(".connectedPixelCount",
                   maxSize=1024, eightConnected=True)
    assert _called(".gte", [hls_scar.MIN_BLOB_PX])   # min_blob_pixels
    assert _called(".connectedComponents")


def test_ndvi_vote_adds_second_gate(commons):
    hls_scar.compute("wa_2025", ndvi_vote=True)
    # NDVI uses NIR+RED and requires NDVI_post - NDVI_pre < 0
    assert _called(".normalizedDifference", [["NIR", "RED"]])
    assert _called(".lt", [0])


def test_pre_post_windows_from_config(commons):
    hls_scar.compute("wa_2025")
    dates = [tuple(a) for p, a, k in LOG if p.endswith(".filterDate")]
    assert ("2025-11-30", "2025-12-10") in dates     # event - 10d .. event
    assert ("2025-12-10", "2025-12-20") in dates     # event .. event + 10d


def test_run_merges_metrics_and_starts_asset_export(tmp_path, commons):
    out = hls_scar.run("wa_2025", metrics_path=tmp_path / "m.json")
    m = json.loads((tmp_path / "m.json").read_text())
    assert {"cloud_pct", "landslide_hls_candidates",
            "landslide_hls_candidate_px"} <= set(m)
    assert "HLS" in m["collections"]
    assert "HLSL30" in m["collections"]["HLS"]
    exports = [(p, a, k) for p, a, k in LOG
               if p.endswith("Export.image.toAsset")]
    assert len(exports) == 1
    assert exports[0][2]["assetId"].endswith("wa_2025_landslide_candidates")
    assert exports[0][2]["scale"] == 30
    assert _called(".start")
    assert out["tasks"]


def test_dry_run_no_init(capsys, commons, monkeypatch):
    monkeypatch.setattr(
        hls_scar, "init",
        lambda *a, **k: pytest.fail("init called in dry-run"), raising=False)
    assert hls_scar.main(["--case", "wa_2025", "--dry-run"]) == 0
    out = capsys.readouterr().out
    for s in ("HLSL30", "HLSS30", "wa_2025_landslide_candidates",
              "cloud_pct", "candidate", "GUNW"):
        assert s in out
    assert "confirmed" not in out                     # never 'confirmed'


def test_candidates_wording_and_limitation():
    doc = hls_scar.__doc__
    assert "candidate" in doc.lower()
    assert "LIMITATION" in doc
    assert "GUNW" in doc
    src = inspect.getsource(hls_scar)
    assert "confirmed" not in src                     # never 'confirmed'
