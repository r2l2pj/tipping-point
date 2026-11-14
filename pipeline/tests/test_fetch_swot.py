"""Offline tests for fetch.swot — fake storage/EE seams, fixture series JSON."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fetch import swot

CASE = "rs_2024"
RECORDS = [
    {"reach_id": 64120100045, "time_str": "2024-04-21T03:11:00Z", "river_m": 3.21},
    {"reach_id": 64120100021, "time_str": "2024-05-02T12:05:30Z", "river_m": 4.56},
]


def _case_data(tmp_path: Path, records=RECORDS) -> Path:
    """data/<case>/series/<case>_swot_<AAAAMMDD>.json fixture."""
    d = tmp_path / "data"
    sdir = d / CASE / "series"
    sdir.mkdir(parents=True)
    (sdir / f"{CASE}_swot_20240502.json").write_text(json.dumps(records))
    return d


class _FakeEE:
    """Stands in for ingest_table — the L10 bucket-free table export."""

    def __init__(self):
        self.ingestions = []

    def ingest(self, geojson_path, *, asset_id, force=False):
        self.ingestions.append({"path": Path(geojson_path),
                                "asset_id": asset_id, "force": force})
        return {"id": "OP123", "state": "READY", "asset_id": asset_id}


class _FakeManifest:
    def __init__(self):
        self.appended = []

    def append(self, case, entry):
        self.appended.append((case, entry))
        return Path(f"data/{case}/assets.json")


@pytest.fixture
def contract(monkeypatch):
    """Patch the ingest contract names on fetch.swot with fakes."""
    ee, manifest = _FakeEE(), _FakeManifest()
    monkeypatch.setattr(swot, "ingest_table", ee.ingest)
    monkeypatch.setattr(swot, "append_manifest", manifest.append)
    return ee, manifest


def test_fc_shape_and_props():
    fc = swot.build_feature_collection(RECORDS)
    assert fc["type"] == "FeatureCollection"
    assert len(fc["features"]) == len(RECORDS)
    feat = fc["features"][0]
    assert feat["type"] == "Feature"
    assert feat["properties"] == {
        "reach_id": "64120100045",
        "t_utc": "2024-04-21T03:11:00+00:00",
        "river_m": 3.21,
        "source": "SWOT Hydrocron",
    }
    assert feat["geometry"] is None  # plain-table ingest


def test_record_point_geometry():
    recs = [{"reach_id": "1", "time_str": "2024-04-21T00:00:00Z",
             "river_m": 1.0, "lon": -51.2, "lat": -30.0}]
    feat = swot.build_feature_collection(recs)["features"][0]
    assert feat["geometry"] == {"type": "Point", "coordinates": [-51.2, -30.0]}


def test_record_geometry_collapses_to_centroid():
    recs = [{"reach_id": "1", "time_str": "2024-04-21T00:00:00Z",
             "river_m": 1.0,
             "geometry": {"type": "LineString",
                          "coordinates": [[-51.0, -30.0], [-51.0, -30.2]]}}]
    geom = swot.build_feature_collection(recs)["features"][0]["geometry"]
    assert geom["type"] == "Point"
    assert geom["coordinates"] == pytest.approx([-51.0, -30.1])


def test_event_swot_endpoints_geometry():
    event = {"swot": {"reach_ids": ["1"],
                      "endpoints": {"1": [[-51.0, -30.0], [-51.0, -30.2]]}}}
    feat = swot.build_feature_collection(
        [{"reach_id": "1", "time_str": "2024-04-21T00:00:00Z", "river_m": 1.0}],
        event=event,
    )["features"][0]
    assert feat["geometry"] == {"type": "LineString",
                                "coordinates": [[-51.0, -30.0], [-51.0, -30.2]]}


def test_dry_run_writes_geojson_and_plan(tmp_path):
    data = _case_data(tmp_path)
    plan = swot.run(CASE, data_dir=data, dry_run=True)
    stage = Path(plan["staging"])
    fc = json.loads(stage.read_text())
    assert fc["type"] == "FeatureCollection"
    assert len(fc["features"]) == 2
    assert plan["kind"] == "table"
    assert plan["asset_id"].endswith(f"/{CASE}/{swot.ASSET_NAME}")
    assert plan["dry_run"] is True


def test_ingest_uses_table_contract(tmp_path, contract):
    ee, manifest = contract
    data = _case_data(tmp_path)
    plan = swot.run(CASE, data_dir=data, project="proj")
    assert ee.ingestions == [{
        "path": Path(plan["staging"]),
        "asset_id": "projects/proj/assets/tipping-point/rs_2024/swot_series",
        "force": False,
    }]
    (case, entry), = manifest.appended
    assert case == CASE
    assert entry["hash"] == plan["hash"]
    assert entry["kind"] == "table"
    assert entry["asset_id"].endswith("/swot_series")


def test_idempotent_skip_on_same_hash(tmp_path, contract):
    ee, manifest = contract
    data = _case_data(tmp_path)
    digest = swot.run(CASE, data_dir=data, dry_run=True)["hash"]
    (data / CASE / "assets.json").write_text(
        json.dumps({"swot_series": {"hash": digest}})
    )
    plan = swot.run(CASE, data_dir=data, project="p")
    assert plan["skipped"] is True
    assert ee.ingestions == manifest.appended == []


def test_changed_hash_reingests(tmp_path, contract):
    ee, _ = contract
    data = _case_data(tmp_path)
    (data / CASE / "assets.json").write_text(
        json.dumps({"swot_series": {"hash": "stale"}})
    )
    plan = swot.run(CASE, data_dir=data, project="p")
    assert plan["skipped"] is False
    assert len(ee.ingestions) == 1


def test_force_reingests_same_hash(tmp_path, contract):
    ee, _ = contract
    data = _case_data(tmp_path)
    digest = swot.run(CASE, data_dir=data, dry_run=True)["hash"]
    (data / CASE / "assets.json").write_text(
        json.dumps({"swot_series": {"hash": digest}})
    )
    swot.run(CASE, data_dir=data, project="p", force=True)
    assert len(ee.ingestions) == 1
    assert ee.ingestions[0]["force"] is True


def test_main_dry_run(monkeypatch, tmp_path, capsys):
    data = _case_data(tmp_path)
    monkeypatch.setattr(swot, "DATA_DIR", data)
    assert swot.main(["--case", CASE, "--dry-run"]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan["case"] == CASE
    assert plan["features"] == 2
    assert (data / CASE / "staging" / "swot_series.geojson").exists()


def test_no_records_errors(tmp_path):
    (tmp_path / "data" / CASE / "series").mkdir(parents=True)
    with pytest.raises(SystemExit, match="no SWOT records"):
        swot.run(CASE, data_dir=tmp_path / "data", dry_run=True)


def test_malformed_series_file_warns_and_skips(tmp_path, capsys):
    """One bad file among good ones: stderr warning, valid records kept."""
    data = _case_data(tmp_path)
    sdir = data / CASE / "series"
    bad = sdir / f"{CASE}_swot_20240503.json"
    bad.write_text("null")
    plan = swot.run(CASE, data_dir=data, dry_run=True)
    err = capsys.readouterr().err
    assert "skipping malformed series file" in err
    assert bad.name in err
    assert plan["features"] == len(RECORDS)


def test_invalid_json_series_file_warns_and_skips(tmp_path, capsys):
    data = _case_data(tmp_path)
    bad = data / CASE / "series" / f"{CASE}_swot_20240503.json"
    bad.write_text("{not json")
    plan = swot.run(CASE, data_dir=data, dry_run=True)
    assert bad.name in capsys.readouterr().err
    assert plan["features"] == len(RECORDS)


def test_all_malformed_series_files_clean_exit(tmp_path, capsys):
    """Every series file malformed -> clean SystemExit, no traceback."""
    data = _case_data(tmp_path, records=[])
    (data / CASE / "series" / f"{CASE}_swot_20240502.json").write_text("null")
    with pytest.raises(SystemExit, match="no SWOT records"):
        swot.run(CASE, data_dir=data, dry_run=True)
    assert "skipping malformed" in capsys.readouterr().err


def test_scalar_series_file_clean_error(tmp_path):
    """A JSON scalar/null in a matching series file reports, not crashes."""
    for raw in ("null", "42", '"str"', "true"):
        f = tmp_path / "s.json"
        f.write_text(raw)
        with pytest.raises(TypeError, match="expected a JSON list or object"):
            swot.load_records(f)


def test_feature_collection_series_file(tmp_path):
    """Dict with 'features' — GeoJSON Features flattened to records."""
    payload = {"features": [
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [-51.0, -30.0]},
         "properties": {"reach_id": "1",
                        "time_str": "2024-04-21T00:00:00Z", "river_m": 2.0}},
    ]}
    f = tmp_path / "s.json"
    f.write_text(json.dumps(payload))
    records, _ = swot.load_records(f)
    fc = swot.build_feature_collection(records)
    assert fc["features"][0]["properties"]["reach_id"] == "1"
    assert fc["features"][0]["geometry"]["type"] == "Point"


def test_scalar_assets_json_tolerated(tmp_path):
    """Malformed assets.json must not break the idempotency check."""
    data = _case_data(tmp_path)
    for raw in ("null", "42", '"str"',
                '{"swot_series": "scalar"}', '{"assets": 5}'):
        (data / CASE / "assets.json").write_text(raw)
        assert swot.recorded_hash(CASE, data) is None
