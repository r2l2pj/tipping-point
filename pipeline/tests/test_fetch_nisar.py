"""Offline tests for fetch.nisar — fake asf_search + fake fetch.ingest."""
import json
import os
import sys
import types
from pathlib import Path

import fetch.nisar as nisar_update
import pytest
import tp.nisar as tp_nisar
from tp import events as events_mod


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    for var in ("EARTHDATA_TOKEN", "EDL_USER", "EDL_PASS",
                "EARTHDATA_USERNAME", "EARTHDATA_PASSWORD",
                "GOOGLE_APPLICATION_CREDENTIALS", "GEE_PROJECT",
                "NOW_GCS_BUCKET"):
        monkeypatch.setenv(var, os.environ.get(var, ""))
        monkeypatch.delenv(var, raising=False)

G_PRE = (
    "NISAR_L2_PR_GCOV_006_172_D_064_4005_DHDH_A_20251204T031815_"
    "20251204T031850_X05009_N_F_J_001"
)
G_POST = (
    "NISAR_L2_PR_GCOV_007_172_D_064_4005_DHDH_A_20251216T031815_"
    "20251216T031850_X05009_N_F_J_001"
)
G_NEW = (
    "NISAR_L2_PR_GCOV_008_172_D_064_4005_DHDH_A_20251228T031815_"
    "20251228T031850_X05009_N_F_J_001"
)
G_NEW2 = (
    "NISAR_L2_PR_GCOV_009_172_D_064_4005_DHDH_A_20260109T031815_"
    "20260109T031850_X05009_N_F_J_001"
)
G_DUP_DATE = (
    "NISAR_L2_PR_GCOV_008_172_D_064_4005_DHDH_A_20251228T051200_"
    "20251228T051235_X05009_N_F_J_001"
)

EVENT = {
    "id": "wa-2025-12",
    "reference_date": "2025-12-10",
    "window": {"start": "2025-11-01", "end": "2026-01-31"},
    "aoi": {
        "bbox": [-122.6, 47.0, -121.5, 48.5],
        "wkt": ("POLYGON((-122.6 47.0, -121.5 47.0, -121.5 48.5, "
                "-122.6 48.5, -122.6 47.0))"),
    },
    "nisar": {
        "maturity": "BETA",
        "frames": [{
            "product": "GCOV",
            "track": "172D",
            "frame": "064",
            "scenes": [
                {"date": "2025-12-04", "role": "pre", "granule_id": G_PRE},
                {"date": "2025-12-16", "role": "post", "granule_id": G_POST},
            ],
            "collections": {"BETA": "NISAR_L2_GCOV_BETA_V1"},
        }],
    },
}

NO_NISAR_EVENT = {"id": "xx-2024-01", "aoi": {"bbox": [0, 0, 1, 1]}}


class _FakeProduct:
    """Stand-in for asf_search.ASFProduct — only .properties is read."""

    def __init__(self, file_id, url=None):
        self.properties = {
            "fileID": file_id,
            "url": url or f"https://daac.example/{file_id}.h5",
        }


def _fake_asf(granules):
    """A stand-in 'asf_search' module for sys.modules."""
    mod = types.ModuleType("asf_search")
    mod.DATASET = types.SimpleNamespace(NISAR="NISAR")
    calls = []

    def search(**kw):
        calls.append(kw)
        return list(granules)

    mod.search = search
    mod.calls = calls
    return mod


def _fake_ingest(root: Path):
    """A stand-in 'fetch.ingest' module honouring the L10 contract —
    append_manifest only; there is no raster-ingest path to fake."""
    mod = types.ModuleType("fetch.ingest")
    calls = {"manifest": []}

    def append_manifest(case, entry):
        calls["manifest"].append({"case": case, "entry": entry})
        path = Path(root) / case / "assets.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = (json.loads(path.read_text()) if path.exists()
                else {"assets": []})
        data["assets"].append(entry)
        path.write_text(json.dumps(data))
        return path

    mod.append_manifest = append_manifest
    mod.calls = calls
    return mod


@pytest.fixture
def workspace(tmp_path):
    """tmp events/ + data/ dirs holding the synthetic wa event."""
    events_dir = tmp_path / "events"
    events_dir.mkdir()
    (events_dir / "wa-2025-12.json").write_text(json.dumps(EVENT))
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return events_dir, data_dir


def test_plan_finds_only_new_scenes(monkeypatch, workspace):
    events_dir, data_dir = workspace
    fake = _fake_asf([_FakeProduct(g) for g in (G_PRE, G_POST, G_NEW)])
    monkeypatch.setitem(sys.modules, "asf_search", fake)

    plan = nisar_update.build_plan(events_dir=events_dir, data_dir=data_dir)

    case = plan["cases"]["wa_2025"]
    assert [s["granule_id"] for s in case["new_scenes"]] == [G_NEW]
    assert case["new_scenes"][0]["asset_id"] == "nisar_gcov_20251228"
    assert {s["granule_id"] for s in case["skipped"]} == {G_PRE, G_POST}
    assert case["since"] == "2025-12-16"   # newest known scene date
    assert fake.calls[0]["shortName"] == "NISAR_L2_GCOV_BETA_V1"
    assert fake.calls[0]["intersectsWith"] == EVENT["aoi"]["wkt"]


def test_plan_since_override_and_case_filter(monkeypatch, workspace):
    events_dir, data_dir = workspace
    fake = _fake_asf([_FakeProduct(G_NEW), _FakeProduct(G_NEW2)])
    monkeypatch.setitem(sys.modules, "asf_search", fake)

    plan = nisar_update.build_plan(
        case="wa-2025-12", since="2026-01-01",
        events_dir=events_dir, data_dir=data_dir)

    assert list(plan["cases"]) == ["wa_2025"]
    case = plan["cases"]["wa_2025"]
    assert case["since"] == "2026-01-01"
    assert fake.calls[0]["start"] == "2026-01-01"
    # two new dates -> two assets
    assert [s["asset_id"] for s in case["new_scenes"]] == [
        "nisar_gcov_20251228", "nisar_gcov_20260109"]


NO_SCENES_EVENT = {
    "id": "rs-2024-05",
    "reference_date": "2024-05-02",
    "window": {"start": "2024-04-20", "end": "2024-05-31"},
    "aoi": {"bbox": [-52.5, -30.5, -50.5, -29.0]},
    "nisar": {"maturity": None, "frames": []},
}


def test_plan_since_no_known_scenes(monkeypatch, tmp_path):
    """No known scenes -> since = event window.start (not future-dated
    window.end, which would drop passes between event and end)."""
    events_dir = tmp_path / "events"
    events_dir.mkdir()
    (events_dir / "rs-2024-05.json").write_text(json.dumps(NO_SCENES_EVENT))
    fake = _fake_asf([])
    monkeypatch.setitem(sys.modules, "asf_search", fake)

    plan = nisar_update.build_plan(
        events_dir=events_dir, data_dir=tmp_path / "data")

    case = plan["cases"]["rs_2024"]
    assert case["since"] == "2024-04-20"
    assert fake.calls[0]["start"] == "2024-04-20"


def test_plan_since_never_future_dated(monkeypatch, tmp_path):
    """A window.start in the future clamps to today — searching from a
    future date would return nothing."""
    import datetime as dt

    event = dict(NO_SCENES_EVENT)
    event["window"] = {"start": "2099-01-01", "end": "2099-12-31"}
    events_dir = tmp_path / "events"
    events_dir.mkdir()
    (events_dir / "rs-2024-05.json").write_text(json.dumps(event))
    fake = _fake_asf([])
    monkeypatch.setitem(sys.modules, "asf_search", fake)

    plan = nisar_update.build_plan(
        events_dir=events_dir, data_dir=tmp_path / "data")

    assert plan["cases"]["rs_2024"]["since"] == dt.date.today().isoformat()


def test_plan_dedups_against_assets_json(monkeypatch, workspace):
    events_dir, data_dir = workspace
    (data_dir / "wa_2025").mkdir()
    (data_dir / "wa_2025" / "assets.json").write_text(json.dumps({
        "assets": [{
            "asset_id": "nisar_gcov_20251228",
            "gee_asset": "projects/p/assets/tipping-point/wa_2025/nisar/"
                         "nisar_gcov_20251228",
        }],
    }))
    fake = _fake_asf([_FakeProduct(G_NEW), _FakeProduct(G_NEW2)])
    monkeypatch.setitem(sys.modules, "asf_search", fake)

    plan = nisar_update.build_plan(events_dir=events_dir, data_dir=data_dir)

    case = plan["cases"]["wa_2025"]
    assert [s["granule_id"] for s in case["new_scenes"]] == [G_NEW2]
    assert case["skipped"] == [
        {"granule_id": G_NEW, "reason": "asset-exists"}]


G_OTHER_FRAME = (
    "NISAR_L2_PR_GCOV_008_071_D_065_4005_DHDH_A_20251228T031850_"
    "20251228T031925_X05009_N_F_J_001"
)


def test_plan_dedups_same_date_granules(monkeypatch, workspace):
    events_dir, data_dir = workspace
    fake = _fake_asf([_FakeProduct(G_NEW), _FakeProduct(G_DUP_DATE)])
    monkeypatch.setitem(sys.modules, "asf_search", fake)

    plan = nisar_update.build_plan(events_dir=events_dir, data_dir=data_dir)

    case = plan["cases"]["wa_2025"]
    assert len(case["new_scenes"]) == 1     # one asset per date
    assert case["skipped"] == [
        {"granule_id": G_DUP_DATE, "reason": "duplicate-date"}]


def test_plan_prefers_declared_frame(monkeypatch, workspace):
    """Same date, two frames: the event's declared 172D/064 wins even when
    the search lists the other frame first."""
    events_dir, data_dir = workspace
    fake = _fake_asf([_FakeProduct(G_OTHER_FRAME), _FakeProduct(G_NEW)])
    monkeypatch.setitem(sys.modules, "asf_search", fake)

    plan = nisar_update.build_plan(events_dir=events_dir, data_dir=data_dir)

    case = plan["cases"]["wa_2025"]
    assert [s["granule_id"] for s in case["new_scenes"]] == [G_NEW]
    assert case["skipped"] == [
        {"granule_id": G_OTHER_FRAME, "reason": "duplicate-date"}]


def test_plan_json_shape(monkeypatch, workspace):
    events_dir, data_dir = workspace
    fake = _fake_asf([_FakeProduct(G_NEW)])
    monkeypatch.setitem(sys.modules, "asf_search", fake)
    monkeypatch.setenv("GEE_PROJECT", "test-proj")

    plan = nisar_update.build_plan(events_dir=events_dir, data_dir=data_dir)

    assert plan["schema"] == "tipping-point/nisar-update@1"
    assert plan["generated_at"].endswith("Z")
    case = plan["cases"]["wa_2025"]
    for key in ("event_id", "collection", "since", "bbox", "search_aois",
                "known_dates", "found", "new_scenes", "skipped"):
        assert key in case
    scene = case["new_scenes"][0]
    for key in ("granule_id", "date", "track", "look", "frame", "asset_id",
                "gee_asset", "variable", "h5_path", "h5_url", "cog"):
        assert key in scene
    assert scene["gee_asset"] == (
        "projects/test-proj/assets/tipping-point/wa_2025/nisar/"
        "nisar_gcov_20251228")
    assert scene["h5_path"] == tp_nisar.HDF5_PATHS["GCOV"]["HHHH"]
    assert scene["track"] == 172 and scene["frame"] == 64


def test_events_without_nisar_block_skipped(monkeypatch, tmp_path):
    events_dir = tmp_path / "events"
    events_dir.mkdir()
    (events_dir / "xx-2024-01.json").write_text(json.dumps(NO_NISAR_EVENT))
    fake = _fake_asf([])
    monkeypatch.setitem(sys.modules, "asf_search", fake)

    plan = nisar_update.build_plan(
        events_dir=events_dir, data_dir=tmp_path / "data")
    assert plan["cases"] == {}
    assert fake.calls == []


def test_alerts_geojson_adds_search_aois(monkeypatch, workspace, tmp_path):
    events_dir, data_dir = workspace
    alerts = tmp_path / "alerts.json"
    alerts.write_text(json.dumps({
        "candidates": [
            {"class": 3, "centroid": [-122.0, 48.0], "pixels": 5},
            {"class": 2, "centroid": [-122.3, 47.5], "pixels": 9},
        ],
    }))
    fake = _fake_asf([_FakeProduct(G_NEW)])
    monkeypatch.setitem(sys.modules, "asf_search", fake)

    plan = nisar_update.build_plan(
        events_dir=events_dir, data_dir=data_dir,
        alerts_geojson=alerts)

    assert len(fake.calls) == 3     # event AOI + one per alert candidate
    kinds = [a["kind"] for a in plan["cases"]["wa_2025"]["search_aois"]]
    assert kinds == ["event", "alert", "alert"]
    assert plan["alert_aois"] == 2


def test_dry_run_cli_needs_no_creds(monkeypatch, workspace, capsys):
    events_dir, data_dir = workspace
    for var in ("EDL_USER", "EDL_PASS", "EARTHDATA_USERNAME",
                "EARTHDATA_PASSWORD", "EARTHDATA_TOKEN", "NETRC"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(data_dir))    # no ~/.netrc
    monkeypatch.delenv("GEE_PROJECT", raising=False)
    fake = _fake_asf([_FakeProduct(G_NEW)])
    monkeypatch.setitem(sys.modules, "asf_search", fake)
    # dry-run must never touch the ingest contract at all
    for name in ("fetch.ingest", "pipeline.fetch.ingest", "ingest"):
        sys.modules.pop(name, None)
    monkeypatch.setattr(events_mod, "EVENTS_DIR", events_dir)
    monkeypatch.setattr(events_mod, "DATA_DIR", data_dir)

    assert nisar_update.main(["--case", "wa_2025", "--dry-run"]) == 0
    plan = json.loads(capsys.readouterr().out)
    scene = plan["cases"]["wa_2025"]["new_scenes"][0]
    assert scene["asset_id"] == "nisar_gcov_20251228"
    assert "<GEE_PROJECT>" in scene["gee_asset"]   # placeholder, no env


def _stub_execute(monkeypatch, workspace):
    """Fake asf + ingest + creds + env for execute_plan."""
    events_dir, data_dir = workspace
    fake_asf = _fake_asf([_FakeProduct(G_NEW)])
    monkeypatch.setitem(sys.modules, "asf_search", fake_asf)
    fake_ingest = _fake_ingest(data_dir)
    monkeypatch.setitem(sys.modules, "fetch.ingest", fake_ingest)
    monkeypatch.setitem(sys.modules, "pipeline.fetch.ingest", fake_ingest)
    assert nisar_update._load_ingest() is fake_ingest
    monkeypatch.setenv("EARTHDATA_TOKEN", "tok")
    monkeypatch.setenv("GEE_PROJECT", "test-proj")
    monkeypatch.setenv("NOW_GCS_BUCKET", "test-bucket")

    written = []

    def fake_subset(h5_url, h5_path, bbox, out_path, gdal_env):
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"COG")
        written.append(Path(out_path))
        return Path(out_path)

    monkeypatch.setattr(tp_nisar, "_subset_h5_variable", fake_subset)
    return events_dir, data_dir, fake_ingest, written


def test_execute_downloads_clips_and_records_local_only(
        monkeypatch, workspace):
    """L10 default: COG stays local, manifest gets status 'local-only',
    nothing touches the gs:// path."""
    events_dir, data_dir, _fake_ingest, written = _stub_execute(
        monkeypatch, workspace)

    plan = nisar_update.build_plan(events_dir=events_dir, data_dir=data_dir)
    results = nisar_update.execute_plan(plan, data_dir=data_dir)

    assert [Path(s["cog"]).name for s in plan["cases"]["wa_2025"]
            ["new_scenes"]] == ["wa_2025_nisar_gcov_20251228.tif"]
    assert [p.name for p in written] == ["wa_2025_nisar_gcov_20251228.tif"]
    res = results[0]
    assert res["asset_id"] == "nisar_gcov_20251228"
    assert res["status"] == "local-only"
    assert res["local_path"].endswith("wa_2025_nisar_gcov_20251228.tif")
    assert res["gee_asset"] == (
        "projects/test-proj/assets/tipping-point/wa_2025/nisar/"
        "nisar_gcov_20251228")

    manifest = json.loads(
        (data_dir / "wa_2025" / "assets.json").read_text())
    entry = manifest["assets"][0]
    assert entry["asset_id"] == "nisar_gcov_20251228"
    assert entry["status"] == "local-only"
    assert entry["granule_id"] == G_NEW
    assert entry["kind"] == "image"


def test_execute_is_idempotent(monkeypatch, workspace):
    events_dir, data_dir, fake_ingest, _ = _stub_execute(
        monkeypatch, workspace)

    plan = nisar_update.build_plan(events_dir=events_dir, data_dir=data_dir)
    first = nisar_update.execute_plan(plan, data_dir=data_dir)
    second = nisar_update.execute_plan(plan, data_dir=data_dir)

    assert len(first) == 1 and second == []
    assert len(fake_ingest.calls["manifest"]) == 1  # never re-downloaded


def test_execute_requires_creds(monkeypatch, workspace):
    events_dir, data_dir, _, _ = _stub_execute(monkeypatch, workspace)
    for var in ("EDL_USER", "EDL_PASS", "EARTHDATA_USERNAME",
                "EARTHDATA_PASSWORD", "EARTHDATA_TOKEN", "NETRC"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(data_dir))    # no ~/.netrc
    plan = nisar_update.build_plan(events_dir=events_dir, data_dir=data_dir)
    with pytest.raises(RuntimeError, match="Earthdata credentials"):
        nisar_update.execute_plan(plan, data_dir=data_dir)


def test_execute_requires_ingest_module(monkeypatch, workspace):
    events_dir, data_dir = workspace
    monkeypatch.setitem(sys.modules, "asf_search",
                        _fake_asf([_FakeProduct(G_NEW)]))
    monkeypatch.setenv("EARTHDATA_TOKEN", "tok")
    # real fetch.ingest exists on merged main — block every import path
    # (sys.modules[name]=None raises ImportError on lookup)
    for name in ("fetch.ingest", "pipeline.fetch.ingest", "ingest"):
        monkeypatch.setitem(sys.modules, name, None)
    plan = nisar_update.build_plan(events_dir=events_dir, data_dir=data_dir)
    with pytest.raises(RuntimeError, match="ingest"):
        nisar_update.execute_plan(plan, data_dir=data_dir)


def test_execute_local_only_needs_no_bucket_or_project(
        monkeypatch, workspace):
    """Default execute works with NOW_GCS_BUCKET and GEE_PROJECT unset —
    the planned asset id keeps the <GEE_PROJECT> placeholder."""
    events_dir, data_dir = workspace
    _, _, _fake_ingest, _ = _stub_execute(monkeypatch, workspace)
    monkeypatch.delenv("NOW_GCS_BUCKET", raising=False)
    monkeypatch.delenv("GEE_PROJECT", raising=False)
    plan = nisar_update.build_plan(events_dir=events_dir, data_dir=data_dir)
    results = nisar_update.execute_plan(plan, data_dir=data_dir)
    assert len(results) == 1 and results[0]["status"] == "local-only"
    assert "<GEE_PROJECT>" in results[0]["gee_asset"]


def test_ingest_flag_warns_not_supported(monkeypatch, workspace, capsys):
    """--ingest is accepted but only warns (L10 — no raster path)."""
    events_dir, data_dir, _, _ = _stub_execute(monkeypatch, workspace)
    monkeypatch.setattr(events_mod, "EVENTS_DIR", events_dir)
    monkeypatch.setattr(events_mod, "DATA_DIR", data_dir)
    plan_rc = nisar_update.main(
        ["--case", "wa_2025", "--execute", "--ingest"])
    assert plan_rc == 0
    assert "not supported without a GCS bucket (L10)" in (
        capsys.readouterr().err)
    entry = json.loads(
        (data_dir / "wa_2025" / "assets.json").read_text())["assets"][0]
    assert entry["status"] == "local-only"
