import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from tp import manifest

REPO_ROOT = Path(__file__).resolve().parents[2]
LEGEND = ["Baixo", "Moderado", "Alto", "Crítico"]

# data/SCHEMA.md per-case node + layer field lists.
NODE_KEYS = {"event_id", "mode", "bounds", "forecast_source", "data_basis", "layers",
             "metrics", "data_dates", "products", "_null_reasons"}
LAYER_KEYS = {"id", "date", "png", "cog", "legend", "bounds", "forecast",
              "issued", "forecast_source", "kind", "upper_bound",
              "critical_km2", "people_critical", "_null_reasons"}
PRODUCT_NAMES = {"municipal_boundaries", "landslide_catalog", "places",
                 "swot_reference", "swot_series",
                 "priority_zones", "permanent_water", "flood_confirmed",
                 "landslide_candidates"}


def _data_root(tmp_path, *case_ids):
    """Synthetic data/ tree: data/<case-id>/{png,geojson,series,cog}."""
    root = tmp_path / "data"
    for cid in case_ids:
        for sub in ("png", "geojson", "series", "cog"):
            (root / cid / sub).mkdir(parents=True, exist_ok=True)
    return root


def test_case_node_shape_rs(tmp_path):
    node = manifest.build_case_node("rs-2024-05", _data_root(tmp_path, "rs_2024"))
    assert set(node) == NODE_KEYS
    assert node["event_id"] == "rs-2024-05"           # hyphen id only as a field
    assert node["mode"] == "replay"
    assert node["bounds"] == [-52.5, -30.5, -50.5, -29.0]   # config.yaml aoi
    assert node["forecast_source"] == "perfect-forecast"


def test_case_node_shape_wa(tmp_path):
    node = manifest.build_case_node("wa-2025-12", _data_root(tmp_path, "wa_2025"))
    assert set(node) == NODE_KEYS
    assert node["event_id"] == "wa-2025-12"
    assert node["bounds"] == [-122.6, 47.0, -121.5, 48.5]
    # NISAR GCOV frame dates come from events/wa-2025-12.json.
    assert node["data_dates"]["nisar_pre"] == "2025-12-04"
    assert node["data_dates"]["nisar_post"] == "2025-12-16"


def test_expected_layers_without_index(tmp_path):
    node = manifest.build_case_node("rs-2024-05", _data_root(tmp_path, "rs_2024"))
    assert [l["id"] for l in node["layers"]] == manifest.EXPECTED_LAYERS
    for layer in node["layers"]:
        assert set(layer) <= LAYER_KEYS
        assert layer["legend"] == LEGEND
        assert layer["bounds"] is None          # no _index.json -> null bounds
    slide = next(l for l in node["layers"] if l["id"] == "tpi_slide")
    assert slide["date"] == "2024-05-02"
    assert slide["png"] == "rs_2024/png/tpi_slide_20240502.png"
    assert slide["cog"] == "rs_2024/cog/rs_2024_tpi_slide_20240502.tif"
    assert "forecast" not in slide


def test_forecast_layers_flag_and_paths(tmp_path):
    node = manifest.build_case_node("rs-2024-05", _data_root(tmp_path, "rs_2024"))
    d1 = next(l for l in node["layers"] if l["id"] == "tpi_slide_d1")
    assert d1["forecast"] is True               # depicted day = event + 1
    assert d1["date"] == "2024-05-03"
    assert d1["png"] == "rs_2024/png/tpi_slide_d1_20240503.png"
    # forecast COG is stamped with the issue date (SCHEMA example)
    assert d1["cog"] == "rs_2024/cog/rs_2024_tpi_forecast_d1_20240502.tif"
    forecast_ids = [l["id"] for l in node["layers"] if l.get("forecast")]
    assert forecast_ids == ["tpi_slide_d1", "tpi_slide_d2", "tpi_slide_d3",
                            "tpi_flood_d1", "tpi_flood_d2", "tpi_flood_d3"]


def test_index_supplies_verbatim_layer_fields(tmp_path):
    root = _data_root(tmp_path, "wa_2025")
    index = {"layers": [
        {"id": "tpi_slide", "date": "2025-12-10",
         "png": "wa_2025/png/tpi_slide_20251210.png",
         "cog": "wa_2025/cog/wa_2025_tpi_slide_20251210.tif",
         "bounds": [-123.0, 46.9, -121.0, 48.6], "forecast": False},
        {"id": "hillshade", "date": "2025-12-01", "png": None,
         "cog": "wa_2025/cog/wa_2025_hillshade_20251201.tif",
         "bounds": [-123.0, 46.9, -121.0, 48.6]},
    ]}
    (root / "wa_2025" / "png" / "_index.json").write_text(json.dumps(index))
    node = manifest.build_case_node("wa-2025-12", root)
    by_id = {l["id"]: l for l in node["layers"]}
    slide = by_id["tpi_slide"]
    assert slide["bounds"] == [-123.0, 46.9, -121.0, 48.6]
    assert slide["forecast"] is False           # index value verbatim
    assert slide["legend"] == LEGEND            # legend injected by builder
    assert by_id["tpi_flood"]["bounds"] is None          # missing from index
    hill = by_id["hillshade"]                   # extra id appended from index
    assert hill["png"] is None
    assert hill["cog"] == "wa_2025/cog/wa_2025_hillshade_20251201.tif"
    assert "forecast" not in hill


def test_gfs_layer_critical_stats_copied_from_index(tmp_path):
    root = _data_root(tmp_path, "rs_2024")
    gfs = {"date": "2024-04-24", "issued": "2024-04-23",
           "cog": "rs_2024/cog/rs_2024_tpi_gfs_20240423.tif",
           "bounds": [-52.5, -30.5, -50.5, -29.0], "forecast": True,
           "forecast_source": "noaa-gfs", "kind": "forecast"}
    index = {"layers": [
        {**gfs, "id": "tpi_slide_gfs_d1",
         "png": "rs_2024/png/tpi_slide_gfs_d1_20240424.png",
         "critical_km2": 183.962, "people_critical": 5976},
        {**gfs, "id": "tpi_flood_gfs_d1",
         "png": "rs_2024/png/tpi_flood_gfs_d1_20240424.png",
         "critical_km2": 11.557, "people_critical": None,
         "_null_reasons": {"people_critical": "no population COG (GPW)"}},
    ]}
    (root / "rs_2024" / "png" / "_index.json").write_text(json.dumps(index))
    node = manifest.build_case_node("rs-2024-05", root)
    by_id = {l["id"]: l for l in node["layers"]}
    slide, flood = by_id["tpi_slide_gfs_d1"], by_id["tpi_flood_gfs_d1"]
    assert set(slide) <= LAYER_KEYS and set(flood) <= LAYER_KEYS
    assert slide["critical_km2"] == 183.962
    assert slide["people_critical"] == 5976
    assert "_null_reasons" not in slide
    assert flood["people_critical"] is None
    assert flood["_null_reasons"] == {"people_critical": "no population COG (GPW)"}
    # non-GFS layers never pick the fields up
    assert "critical_km2" not in by_id["tpi_slide"]


def test_cog_only_layers_listed_when_cog_exists(tmp_path):
    root = _data_root(tmp_path, "rs_2024")
    cog = root / "rs_2024" / "cog"
    (cog / "rs_2024_r_series_20240502.tif").write_bytes(b"fake")
    (cog / "rs_2024_t_slide_20240502.tif").write_bytes(b"fake")
    node = manifest.build_case_node("rs-2024-05", root)
    by_id = {l["id"]: l for l in node["layers"]}
    r = by_id["r_series"]
    assert r == {"id": "r_series", "date": "2024-05-02", "png": None,
                 "cog": "rs_2024/cog/rs_2024_r_series_20240502.tif"}
    assert by_id["t_slide"]["cog"] == "rs_2024/cog/rs_2024_t_slide_20240502.tif"
    # missing ones: absent from layers[], reason at node level
    assert "s_series" not in by_id
    assert "t_flood" not in by_id
    assert node["_null_reasons"]["s_series"]
    assert node["_null_reasons"]["t_flood"]


def test_products_null_carry_node_level_reasons(tmp_path):
    node = manifest.build_case_node("rs-2024-05", _data_root(tmp_path, "rs_2024"))
    assert set(node["products"]) == PRODUCT_NAMES
    # values are strictly string | null; reasons live at the node level
    assert all(v is None or isinstance(v, str) for v in node["products"].values())
    assert "_null_reasons" not in node["products"]
    for name, value in node["products"].items():
        assert node["_null_reasons"][name]              # every null has a reason
        assert value is None
    assert "predates launch" in node["_null_reasons"]["flood_confirmed"]


def test_products_paths_when_geojson_exists(tmp_path):
    root = _data_root(tmp_path, "wa_2025")
    g = root / "wa_2025" / "geojson"
    (g / "places.geojson").write_text('{"type":"FeatureCollection","features":[]}')
    node = manifest.build_case_node("wa-2025-12", root)
    assert node["products"]["places"] == "wa_2025/geojson/places.geojson"
    assert "places" not in node["_null_reasons"]
    assert node["products"]["flood_confirmed"] is None


def test_metrics_fragment_into_root_file(tmp_path):
    root = _data_root(tmp_path, "rs_2024")
    assert manifest.build_case_node("rs-2024-05", root)["metrics"] is None
    assert manifest.build_case_node("rs-2024-05", root)["_null_reasons"]["metrics"]
    (root / "metrics.json").write_text('{"cases":{}}')
    node = manifest.build_case_node("rs-2024-05", root)
    assert node["metrics"] == "metrics.json#rs_2024"
    assert "metrics" not in node["_null_reasons"]


def test_data_dates_from_series(tmp_path):
    root = _data_root(tmp_path, "rs_2024")
    series = {"event": "rs-2024-05", "days": [
        {"date": "2024-05-01", "imerg_mm": 12.0, "smap": 0.6, "river_m": None},
        {"date": "2024-05-03", "imerg_mm": 8.0, "smap": None, "river_m": 4.1},
    ]}
    (root / "rs_2024" / "series" / "imerg_smap_daily.json").write_text(
        json.dumps(series))
    dates = manifest.build_case_node("rs-2024-05", root)["data_dates"]
    assert dates["imerg"] == "2024-05-03"
    assert dates["smap"] == "2024-05-01"
    assert dates["swot"] == "2024-05-03"


def test_root_shape_and_now_verbatim(tmp_path):
    root = _data_root(tmp_path, "rs_2024", "wa_2025")
    now_manifest = {"schema": "tipping-point/now@2", "mode": "live",
                    "updated_at": "2026-11-14T12:00:00Z", "layers": [{"id": "tpi_slide_d0"}],
                    "extra_field": "kept verbatim"}
    (root / "now").mkdir()
    (root / "now" / "manifest.json").write_text(json.dumps(now_manifest))
    out = manifest.build_root(root)
    assert set(out) == {"updated_at", "cases"}
    assert out["updated_at"].endswith("Z")
    assert set(out["cases"]) == {"rs_2024", "wa_2025", "now"}
    assert out["cases"]["now"] == now_manifest
    assert out["cases"]["rs_2024"]["event_id"] == "rs-2024-05"
    assert out["cases"]["wa_2025"]["event_id"] == "wa-2025-12"


def test_now_null_when_absent(tmp_path):
    root = _data_root(tmp_path)
    assert manifest.build_root(root)["cases"]["now"] is None


def test_manifest_written_last(tmp_path, monkeypatch):
    root = _data_root(tmp_path, "rs_2024", "wa_2025")
    writes = []
    real_write_text = Path.write_text

    def spy(self, data, *args, **kwargs):
        writes.append(self)
        return real_write_text(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", spy)
    # other data files land first, then the manifest write
    (root / "rs_2024" / "geojson" / "places.geojson").write_text("{}")
    manifest.write_root_manifest(root)
    assert writes[-1].name == "manifest.json.tmp"
    assert (root / "manifest.json").exists()
    assert not (root / "manifest.json.tmp").exists()


def test_write_is_atomic_via_os_replace(tmp_path, monkeypatch):
    root = _data_root(tmp_path)
    calls = []
    real_replace = os.replace

    def spy(src, dst):
        calls.append((Path(src), Path(dst)))
        real_replace(src, dst)

    monkeypatch.setattr("tp.manifest.os.replace", spy)
    manifest.write_root_manifest(root)
    assert calls == [(root / "manifest.json.tmp", root / "manifest.json")]


def test_dry_run_prints_valid_json_and_writes_nothing(tmp_path):
    root = _data_root(tmp_path, "rs_2024", "wa_2025")
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "build_manifest.py"),
         "--dry-run", "--data-root", str(root)],
        capture_output=True, text=True, check=True)
    out = json.loads(result.stdout)
    assert set(out["cases"]) == {"rs_2024", "wa_2025", "now"}
    assert not (root / "manifest.json").exists()


@pytest.mark.parametrize("event_id,case_id", [
    ("rs-2024-05", "rs_2024"),
    ("wa-2025-12", "wa_2025"),
])
def test_swot_series_product(tmp_path, event_id, case_id):
    root = _data_root(tmp_path, case_id)
    node = manifest.build_case_node(event_id, root)
    assert node["products"]["swot_series"] is None
    assert node["_null_reasons"]["swot_series"]
    (root / case_id / "series" / "swot.json").write_text("{}")
    node = manifest.build_case_node(event_id, root)
    assert node["products"]["swot_series"] == f"{case_id}/series/swot.json"
    assert "swot_series" not in node["_null_reasons"]
