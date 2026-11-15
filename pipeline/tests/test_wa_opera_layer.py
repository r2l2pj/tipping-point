"""Hermetic tests for tools/wa_opera_layer.py — tiny synthetic grid, no EE."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # pipeline/
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # repo root (tools/, live/)

import numpy as np
import pytest
from rasterio.transform import from_origin

from tools import wa_opera_layer as wol

# 4x5 class grid: two tp, three fp, one fn, rest nodata
GRID = np.array([[0, 0, 1, 0, 0],
                 [0, 2, 2, 0, 1],
                 [0, 0, 3, 0, 0],
                 [0, 0, 2, 0, 0]], dtype=np.uint8)
TRANSFORM = from_origin(-122.6, 48.5, 0.0003, 0.0003)


def test_recount():
    assert wol.recount(GRID) == {"tp": 2, "fp": 3, "fn": 1}


def test_class_km2_positive_and_monotonic_with_px():
    km2 = wol.class_km2(GRID, TRANSFORM)
    assert set(km2) == {"tp", "fp", "fn"}
    assert km2["fp"] > km2["tp"] > km2["fn"] > 0
    # 0.0003 deg cells at lat ~48.4 are ~0.0008 km2 each
    assert 0.0005 < km2["tp"] / 2 < 0.001


def test_confusion_metrics():
    m = wol.confusion_metrics({"tp": 8, "fp": 3, "fn": 6})
    assert m["confusion"] == {"tp": 8, "fp": 3, "fn": 6}
    assert m["iou"] == pytest.approx(8 / 17)
    assert m["f1"] == pytest.approx(16 / 25)
    assert wol.confusion_metrics({"tp": 0, "fp": 0, "fn": 0})["iou"] == 0.0


def test_palette_png_colors():
    import io

    import rasterio

    valid = GRID != 0
    blob = wol.palette_png_bytes(GRID, valid, wol.OPERA_CONFUSION_PALETTE)
    with rasterio.open(io.BytesIO(blob)) as ds:
        rgba = ds.read()
    assert rgba.shape == (4, 4, 5)
    px = {(int(rgba[0, r, c]), int(rgba[1, r, c]), int(rgba[2, r, c]),
           int(rgba[3, r, c])) for r in range(4) for c in range(5)}
    assert (26, 152, 80, 255) in px    # tp  #1a9850
    assert (244, 109, 67, 255) in px   # fp  #f46d43
    assert (69, 117, 180, 255) in px   # fn  #4575b4
    assert rgba[3, 0, 0] == 0          # nodata transparent


def test_png_3857_warps_and_reports_bounds():
    blob, bounds = wol.png_3857(GRID, TRANSFORM)
    assert blob[:8] == b"\x89PNG\r\n\x1a\n"
    w, s, e, n = bounds
    assert w < e and s < n
    # 3857 image extent back in lon/lat ~= source extent
    assert w == pytest.approx(-122.6, abs=0.05)
    assert e == pytest.approx(-122.6 + 5 * 0.0003, abs=0.05)


def test_legend_rows_shape():
    rows = wol.legend_rows({"tp": 2, "fp": 3, "fn": 1},
                           {"tp": 0.02, "fp": 0.03, "fn": 0.01})
    assert [r["class"] for r in rows] == [1, 2, 3]
    assert rows[0]["color"] == "#1a9850" and rows[0]["px"] == 2
    assert all(set(r) == {"class", "label", "color", "px", "km2"} for r in rows)


def test_upsert_index_replaces_same_id_and_date():
    layers = [{"id": "nisar_post", "date": "2025-12-16", "png": "x"},
              {"id": "opera_validation", "date": "2025-12-16", "png": "old"}]
    entry = wol.index_entry("wa_2025/png/opera_validation_20251216.png",
                            "wa_2025/cog/wa_2025_opera_validation_20251216.tif",
                            [-122.6, 47.0, -121.5, 48.5])
    out = wol.upsert_index(layers, entry)
    assert len(out) == 2
    assert out[0]["id"] == "nisar_post"          # other layers preserved
    assert out[1]["png"] == "wa_2025/png/opera_validation_20251216.png"
    assert out[1]["legend"] == list(wol.OPERA_CONFUSION_LABELS)
    assert out[1]["forecast"] is False and out[1]["kind"] == "observed"


def test_upsert_index_appends_when_new():
    layers = [{"id": "opera_validation", "date": "2025-12-15"}]
    entry = wol.index_entry("p.png", "c.tif", [0, 0, 1, 1])
    assert len(wol.upsert_index(layers, entry)) == 2


def test_manifest_forwards_index_legend():
    from tp.manifest import _layer_entry

    entry = _layer_entry("wa_2025", "opera_validation", None,
                         wol.index_entry("p.png", "c.tif", [0, 0, 1, 1]))
    assert entry["legend"] == list(wol.OPERA_CONFUSION_LABELS)
    assert entry["png"] == "p.png" and entry["cog"] == "c.tif"
    # default layers still get the TPI legend
    other = _layer_entry("wa_2025", "tpi_slide", None,
                         {"id": "tpi_slide", "date": "2025-12-10"})
    assert other["legend"] == ["Baixo", "Moderado", "Alto", "Crítico"]


def test_build_json_schema():
    doc = wol.build_json(
        {"tp": 2, "fp": 3, "fn": 1}, {"tp": 0.02, "fp": 0.03, "fn": 0.01},
        [-122.6, 47.0, -121.5, 48.5], 0.0003, (5, 4),
        {"opera_dswx_s1": "catalog:x", "nisar_gcov": "y"}, 30,
        {"tp": 8, "fp": 38, "fn": 60, "iou": 0.1, "f1": 0.18})
    for key in ("legend", "definition", "sources", "bounds", "iou", "f1"):
        assert key in doc
    assert doc["ratios_vs_metrics"]["tp"] == pytest.approx(2 / 8)
    assert doc["classes"]["0"] == "nodata"


def test_write_cog_roundtrip(tmp_path):
    import rasterio

    out = wol.write_cog(GRID, TRANSFORM, "EPSG:4326", tmp_path / "cls.tif")
    with rasterio.open(out) as ds:
        assert ds.crs.to_epsg() == 4326 and ds.dtypes == ("uint8",)
        assert ds.nodata == 0
        np.testing.assert_array_equal(ds.read(1), GRID)
