"""Offline tests for fetch.inventory — temp shapefiles + fake Zenodo payload."""
import json
import sys
from pathlib import Path

import geopandas as gpd
from shapely.geometry import Point

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fetch import inventory

RS_EVENT = {
    "id": "rs-2024-05",
    "country": "BR",
    "aoi": {"bbox": [-52.5, -30.5, -50.5, -29.0]},
    "window": {"start": "2024-04-20", "end": "2024-05-31"},
}


def _points_gdf():
    return gpd.GeoDataFrame(
        {
            "DATA": ["2024-05-02", "2024-05-10", "2020-01-01", "2024-05-03"],
            "geometry": [
                Point(-51.5, -29.7),   # inside aoi, inside window
                Point(-52.0, -30.0),   # inside aoi, inside window
                Point(-51.6, -29.8),   # inside aoi, outside window
                Point(-49.0, -28.0),   # outside aoi
            ],
        },
        crs="EPSG:4326",
    )


def test_catalog_filter_aoi_and_window(tmp_path):
    shp = tmp_path / "points.shp"
    _points_gdf().to_file(shp)
    gdf = gpd.read_file(shp)
    out = inventory.catalog_filter(gdf, RS_EVENT)

    assert len(out) == 2                      # aoi + window filter
    assert set(out["event_date"]) == {"2024-05-02", "2024-05-10"}
    assert out.crs.to_epsg() == 4326


def test_catalog_filter_no_date_column(tmp_path):
    gdf = _points_gdf().drop(columns=["DATA"])
    gdf.to_file(tmp_path / "nodate.shp")
    out = inventory.catalog_filter(gpd.read_file(tmp_path / "nodate.shp"), RS_EVENT)
    assert len(out) == 3                      # aoi only, no date column
    assert set(out["event_date"]) == {None}


class _FakeResponse:
    def __init__(self, payload=None, content=b"x"):
        self._payload = payload
        self.content = content

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_download_inventories_gets_all_sidecars(tmp_path, monkeypatch):
    record = {
        "files": [
            {"key": "a.shp", "links": {"self": "https://zenodo.example/a.shp"}},
            {"key": "a.shx", "links": {"self": "https://zenodo.example/a.shx"}},
            {"key": "a.dbf", "links": {"self": "https://zenodo.example/a.dbf"}},
            {"key": "a.prj", "links": {"self": "https://zenodo.example/a.prj"}},
        ]
    }

    def fake_get(url, **kwargs):
        if url.endswith("/content") or "zenodo.example" in url:
            return _FakeResponse(content=b"blob-" + url.encode())
        return _FakeResponse(payload=record)

    monkeypatch.setattr(inventory.requests, "get", fake_get)
    monkeypatch.setattr(
        inventory, "INVENTORIES",
        {"fake": {"record": "999", "doi": "10/x", "source": "Fake",
                  "point_stem": "a"}},
    )
    downloaded = inventory.download_inventories(tmp_path / "raw")

    paths = downloaded["fake"]
    assert {p.name for p in paths} == {"a.shp", "a.shx", "a.dbf", "a.prj"}
    for p in paths:
        assert (tmp_path / "raw" / "fake" / p.name).exists()
    # second call: files already on disk -> still listed, not re-fetched
    again = inventory.download_inventories(tmp_path / "raw")
    assert len(again["fake"]) == 4


def test_download_inventories_unreachable_returns_empty(tmp_path, monkeypatch):
    def boom(url, **kwargs):
        raise OSError("zenodo down")

    monkeypatch.setattr(inventory.requests, "get", boom)
    monkeypatch.setattr(
        inventory, "INVENTORIES",
        {"fake": {"record": "999", "doi": "10/x", "source": "Fake",
                  "point_stem": "a"}},
    )
    assert inventory.download_inventories(tmp_path / "raw") == {"fake": []}


def test_inventory_catalog_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(inventory, "event_data_dir", lambda eid: tmp_path / "data" / eid)
    monkeypatch.setattr(inventory, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(inventory, "download_inventories", lambda raw: {})
    raw = tmp_path / "data" / "raw" / "inventory"
    (raw / "ufrgs").mkdir(parents=True)
    stem = inventory.INVENTORIES["ufrgs"]["point_stem"]
    _points_gdf().to_file(raw / "ufrgs" / f"{stem}.shp")

    out = inventory.inventory_catalog(RS_EVENT)
    fc = json.loads(out.read_text())
    assert len(fc["features"]) == 2
    props = fc["features"][0]["properties"]
    assert props == {"source": "UFRGS", "license": "CC-BY-4.0",
                     "doi": "10.5281/zenodo.15618462",
                     "event_date": props["event_date"]}
    assert fc["features"][0]["geometry"]["type"] == "Point"
