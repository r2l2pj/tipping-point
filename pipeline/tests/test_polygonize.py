import importlib.util
import json
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
from conftest import write_tif
from tp.polygonize import mask_to_geojson


def test_mask_to_geojson(tmp_path):
    arr = np.zeros((20, 20), np.uint8)
    arr[2:12, 2:12] = 1            # 100 px at 10 m = 0.01 km2
    arr[15, 15] = 2
    p = write_tif(tmp_path / "mask.tif", arr.astype(np.float32), nodata=None)

    out = tmp_path / "flood.geojson"
    stats = mask_to_geojson(p, out, values=(1,))
    assert stats["features"] == 1

    gdf = gpd.read_file(out)
    assert str(gdf.crs).endswith("4326")
    assert gdf["area_km2"].iloc[0] == pytest.approx(0.01, abs=1e-4)
    assert gdf["value"].iloc[0] == 1


def test_mask_to_geojson_min_area(tmp_path):
    arr = np.zeros((10, 10), np.float32)
    arr[0, 0] = 1                  # 1 px = 0.0001 km2
    p = write_tif(tmp_path / "m.tif", arr, nodata=None)
    stats = mask_to_geojson(p, tmp_path / "o.geojson", min_area_km2=0.001)
    assert stats["features"] == 0


@pytest.mark.parametrize("product", ["flood_nisar", "water_nisar", "landslide_candidates"])
def test_product_contract(tmp_path, product):
    arr = np.zeros((6, 6), np.uint8)
    arr[1:4, 1:5] = 2
    raster = write_tif(tmp_path / f"wa_2025_{product}_20251210.tif", arr, nodata=None)
    output = tmp_path / "product.geojson"
    mask_to_geojson(raster, output, values=(2,))
    doc = json.loads(output.read_text())
    assert doc["type"] == "FeatureCollection"
    feature, = doc["features"]
    assert feature["properties"]["class"] == 2
    assert feature["properties"]["source"] == raster.name
    assert feature["properties"]["date"] == "2025-12-10"
    assert feature["properties"]["area_km2"] == pytest.approx(0.0012)
    assert len(feature["geometry"]["coordinates"][0]) == 5
    assert gpd.read_file(output).is_valid.all()


def test_empty_and_nodata(tmp_path):
    raster = write_tif(tmp_path / "empty.tif", np.ones((2, 2), np.uint8), nodata=1)
    output = tmp_path / "empty.geojson"
    mask_to_geojson(raster, output)
    assert json.loads(output.read_text()) == {"type": "FeatureCollection", "features": []}


def test_invalid_geometry_repaired():
    from shapely.geometry import Polygon
    from tp.polygonize import polygon_parts

    bowtie = Polygon([(0, 0), (2, 2), (0, 2), (2, 0), (0, 0)])
    assert not bowtie.is_valid
    parts = list(polygon_parts(bowtie))
    assert len(parts) == 2
    assert all(p.is_valid and p.geom_type == "Polygon" for p in parts)
    assert sum(p.area for p in parts) == 2


def test_missing_raster(tmp_path):
    with pytest.raises(FileNotFoundError, match="input mask raster not found"):
        mask_to_geojson(tmp_path / "absent.tif", tmp_path / "out.geojson")


def cli(monkeypatch, *args):
    script = Path(__file__).parents[1] / "scripts" / "06_polygonize.py"
    spec = importlib.util.spec_from_file_location("polygonize_cli", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(sys, "argv", [str(script), *map(str, args)])
    module.main()


def test_cli_dry_run_offline(tmp_path, monkeypatch, capsys):
    from tp import events

    monkeypatch.setattr(events, "DATA_DIR", tmp_path)
    cli(monkeypatch, "--case", "wa_2025", "--dry-run")
    text = capsys.readouterr().out
    assert all(product in text for product in ("flood_confirmed", "permanent_water", "landslide_candidates"))
    assert list(tmp_path.iterdir()) == []


def test_cli_config_and_missing(tmp_path, monkeypatch):
    raster = write_tif(tmp_path / "mask.tif", np.ones((2, 2), np.uint8), nodata=None)
    output = tmp_path / "result.geojson"
    settings = tmp_path / "settings.yaml"
    settings.write_text("polygonize:\n  min_area_km2: 1\n  simplify_m: 0\n")
    cli(monkeypatch, "--case", "wa_2025", "--raster", raster, "--out", output, "--config", settings)
    assert json.loads(output.read_text())["features"] == []
    with pytest.raises(SystemExit) as exc:
        cli(monkeypatch, "--case", "wa_2025", "--raster", tmp_path / "missing.tif")
    assert exc.value.code == 2


def test_cli_discovers_products_and_preserves_classes(tmp_path, monkeypatch):
    from tp import events

    monkeypatch.setattr(events, "DATA_DIR", tmp_path)
    cog = tmp_path / "wa_2025" / "cog"
    cog.mkdir(parents=True)
    for product in ("flood_nisar", "water_nisar", "landslide_candidates"):
        arr = np.array([[1, 0], [0, 2]], np.uint8)
        write_tif(cog / f"wa_2025_{product}_20251210.tif", arr, nodata=None)
    cli(monkeypatch, "--case", "wa_2025", "--min-area-km2", "0")
    outputs = sorted((tmp_path / "wa_2025" / "geojson").glob("*.geojson"))
    assert len(outputs) == 3
    for output in outputs:
        classes = {f["properties"]["class"] for f in json.loads(output.read_text())["features"]}
        assert classes == ({1, 2} if "landslide" in output.name else {1})
