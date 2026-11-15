"""Offline tests for fetch.reference — a fake fetch.ingest module (an
ingest_table + append_manifest stand-in) is injected through sys.modules.
L10: no gs:// staging anywhere — no network, no real ee calls, no bucket
env, no credentials needed."""
from __future__ import annotations

import importlib
import json
import sys
import types
from pathlib import Path

import pytest
from fetch import reference as ref


def _fake_ingest_module(calls):
    """A fake fetch.ingest implementing the L10 ingest contract."""
    mod = types.ModuleType("fetch.ingest")

    def ingest_table(geojson_path, *, asset_id, force=False, **kw):
        calls["tables"].append(
            {"path": Path(geojson_path), "asset_id": asset_id,
             "force": force, "kw": kw})
        return {"name": asset_id.rsplit("/", 1)[-1], "gee_id": asset_id,
                "kind": "table", "status": "ingesting",
                "ingested_at": "2026-11-14T00:00:00Z"}

    def append_manifest(case, entry):
        calls["manifests"].append((case, dict(entry)))
        path = ref.DATA_DIR / case / "assets.json"
        doc = {"schema": "tipping-point/gee-assets@1", "assets": []}
        if path.exists():
            doc = json.loads(path.read_text())
        assets = [a for a in doc.get("assets", [])
                  if a.get("name") != entry.get("name")]
        assets.append(entry)
        doc["assets"] = assets
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc, indent=2) + "\n")
        return path

    mod.ingest_table = ingest_table
    mod.append_manifest = append_manifest
    return mod


# --- fixtures / helpers ------------------------------------------------------
@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    d = tmp_path / "data"
    monkeypatch.setattr(ref, "DATA_DIR", d)
    monkeypatch.delenv("NOW_GCS_BUCKET", raising=False)
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    monkeypatch.setenv("GEE_PROJECT", "fake-proj")
    return d


@pytest.fixture
def fake_ingest(monkeypatch, data_dir):
    calls = {"tables": [], "manifests": []}
    monkeypatch.setitem(sys.modules, "fetch.ingest", _fake_ingest_module(calls))
    importlib.reload(ref)  # bind the fake contract functions via `from .ingest`
    monkeypatch.setattr(ref, "DATA_DIR", data_dir)  # reload reset it
    yield calls
    sys.modules.pop("fetch.ingest", None)
    importlib.reload(ref)  # restore real bindings (None while unit/ingest is out)


def _write_fc(data_dir, case, filename, features):
    path = data_dir / case / "geojson" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"type": "FeatureCollection", "features": features}))
    return path


@pytest.mark.parametrize("kind", ["glc", "inventory"])
def test_prepared_catalog_preserves_timestamp_precision(tmp_path, kind):
    props = {"t_utc": "2024-05-02T15:00:00Z", "t_precision": "date",
             "event_date": "2024-05-02", "source": "fixture"}
    features = [_point(props), _point({"t_precision": "none", "event_date": None})]
    src = _write_fc(tmp_path, "rs_2024", "landslide_catalog.geojson", features)
    prepared = ref.prepare_geojson(src, kind, tmp_path / "staging")
    assert json.loads(prepared.read_text())["features"] == features
    assert prepared == src


def _point(props):
    return {"type": "Feature",
            "geometry": {"type": "Point", "coordinates": [-51.8, -29.7]},
            "properties": props}


BOUNDARY = {
    "type": "Feature",
    "geometry": {"type": "Polygon",
                 "coordinates": [[[-52.0, -29.8], [-51.7, -29.8],
                                  [-51.7, -29.6], [-52.0, -29.6],
                                  [-52.0, -29.8]]]},
    "properties": {"name": "Encantado", "admin": "municipio",
                   "source": "IBGE",
                   "license": "IBGE open data (attribution required)",
                   "url": "https://example.test/ibge"},
}


# --- tests -------------------------------------------------------------------
def test_boundaries_maps_to_municipal_asset(data_dir, fake_ingest):
    calls = fake_ingest
    _write_fc(data_dir, "rs_2024", "municipal_boundaries.geojson", [BOUNDARY])
    results = ref.run(["--kind", "boundaries", "--case", "rs_2024"])

    assert results[0]["status"] == "ingesting"
    asset_id = ("projects/fake-proj/assets/tipping-point/"
                "rs_2024/municipal_boundaries")
    assert results[0]["asset_id"] == asset_id
    (table,), _ = calls["tables"], calls["manifests"]
    assert table["asset_id"] == asset_id
    assert table["path"].name == "municipal_boundaries.geojson"


def test_glc_catalog_t_utc_and_attribution(data_dir, fake_ingest):
    calls = fake_ingest
    _write_fc(data_dir, "wa_2025", "landslide_catalog.geojson", [
        _point({"event_date": "2025-12-09", "event_title": "SR 530",
                "source": "NASA COOLR Global Landslide Catalog",
                "url": "https://example.test/coolr"}),
        _point({"source_name": "news report",
                "landslide_trigger": "atmospheric river"}),
    ])
    results = ref.run(["--kind", "glc", "--case", "wa_2025"])

    assert results[0]["asset_id"].endswith(
        "tipping-point/wa_2025/landslide_catalog")
    (table,) = calls["tables"]
    staged = table["path"]
    assert staged.name == "landslide_catalog.geojson"
    assert staged.parent == data_dir / "wa_2025" / "staging"
    dated, undated = json.loads(staged.read_text())["features"]
    assert dated["properties"]["t_utc"] == "2025-12-09T00:00:00Z"
    assert dated["properties"]["source"].startswith("NASA COOLR")
    assert dated["properties"]["url"] == "https://example.test/coolr"
    assert "t_utc" not in undated["properties"]


def test_undated_catalog_stages_source_unchanged(data_dir, fake_ingest):
    calls = fake_ingest
    src = _write_fc(data_dir, "rs_2024", "landslide_catalog.geojson", [
        _point({"source": "UFRGS", "license": "CC-BY-4.0",
                "doi": "10.5281/zenodo.15618462", "event_date": None}),
        _point({"source": "Cemaden", "license": "CC-BY-4.0",
                "doi": "10.5281/zenodo.13225473"}),
    ])
    results = ref.run(["--kind", "inventory", "--case", "rs_2024"])

    assert results[0]["status"] == "ingesting"
    (table,) = calls["tables"]
    assert table["path"] == src  # the source file itself was ingested
    # no staging copy was needed
    assert not (data_dir / "rs_2024" / "staging").exists()
    for f in json.loads(src.read_text())["features"]:
        assert "t_utc" not in f["properties"]


def test_idempotent_skip_via_assets_json(data_dir, fake_ingest):
    calls = fake_ingest
    _write_fc(data_dir, "rs_2024", "municipal_boundaries.geojson", [BOUNDARY])
    (data_dir / "rs_2024" / "assets.json").write_text(json.dumps({
        "schema": "tipping-point/gee-assets@1",
        "assets": [{"name": "municipal_boundaries",
                    "gee_id": "projects/p/assets/tipping-point/rs_2024/x",
                    "kind": "table", "status": "ingested"}]}))

    results = ref.run(["--kind", "boundaries", "--case", "rs_2024"])
    assert results[0]["status"] == "skipped"
    assert calls["tables"] == []

    results = ref.run(
        ["--kind", "boundaries", "--case", "rs_2024", "--force"])
    assert results[0]["status"] == "ingesting"
    assert calls["tables"][0]["force"] is True


def test_manifest_appended_after_ingest(data_dir, fake_ingest):
    _write_fc(data_dir, "rs_2024", "municipal_boundaries.geojson", [BOUNDARY])
    ref.run(["--kind", "boundaries", "--case", "rs_2024"])

    doc = json.loads((data_dir / "rs_2024" / "assets.json").read_text())
    entry, = doc["assets"]
    assert entry["name"] == "municipal_boundaries"
    assert entry["kind"] == "table"
    assert entry["status"] == "ingesting"
    assert entry["gee_id"].endswith("rs_2024/municipal_boundaries")
    assert "gcs_uri" not in entry


@pytest.mark.parametrize("kind", ["boundaries", "glc", "inventory"])
def test_refresh_calls_the_matching_fetcher(data_dir, fake_ingest,
                                            monkeypatch, kind):
    fetched = []

    def fake_fetcher(k):
        return lambda event, **kw: fetched.append((k, event["id"], kw))

    monkeypatch.setattr(ref, "_fetcher", fake_fetcher)
    _write_fc(data_dir, "rs_2024", ref.KINDS[kind]["filename"], [_point({})])
    ref.run(["--kind", kind, "--case", "rs_2024", "--refresh"])

    assert fetched == [(kind, "rs-2024-05", {})]


def test_refresh_window_arg_forwarded_for_glc(data_dir, fake_ingest,
                                              monkeypatch):
    """--window START,END is the explicit GLC filter — NOT the whole
    event['window']; without it the fetcher gets no window kwarg."""
    fetched = []

    def fake_fetcher(k):
        return lambda event, **kw: fetched.append((k, kw.get("window")))

    monkeypatch.setattr(ref, "_fetcher", fake_fetcher)
    _write_fc(data_dir, "wa_2025", "landslide_catalog.geojson", [_point({})])

    ref.run(["--kind", "glc", "--case", "wa_2025", "--refresh",
             "--window", "2025-11-01,2026-01-31"])
    assert fetched == [("glc", {"start": "2025-11-01",
                               "end": "2026-01-31"})]

    fetched.clear()
    ref.run(["--kind", "glc", "--case", "wa_2025", "--refresh", "--force"])
    assert fetched == [("glc", None)]  # event['glc_window'] applies inside glc


def test_window_arg_rejects_bad_input():
    for bad in ("2025-11-01", "not,dates", "2026-01-31,2025-11-01"):
        with pytest.raises(SystemExit):
            ref.run(["--kind", "glc", "--case", "wa_2025",
                     "--window", bad])


def test_dry_run_makes_no_calls(data_dir, fake_ingest):
    calls = fake_ingest
    _write_fc(data_dir, "rs_2024", "municipal_boundaries.geojson", [BOUNDARY])
    _write_fc(data_dir, "rs_2024", "landslide_catalog.geojson", [_point({})])

    results = ref.run(["--kind", "all", "--case", "rs_2024", "--dry-run"])
    assert {r["status"] for r in results} == {"dry-run"}
    assert calls == {"tables": [], "manifests": []}
    assert not (data_dir / "rs_2024" / "assets.json").exists()
    assert not (data_dir / "rs_2024" / "staging").exists()


def test_dry_run_when_ingest_unavailable(data_dir, monkeypatch):
    """--dry-run must work in a checkout without fetch.ingest (unit/ingest)."""
    monkeypatch.setattr(ref, "ingest_table", None)
    monkeypatch.setattr(ref, "append_manifest", None)
    _write_fc(data_dir, "rs_2024", "municipal_boundaries.geojson", [BOUNDARY])

    results = ref.run(
        ["--kind", "boundaries", "--case", "rs_2024", "--dry-run"])
    assert results[0]["status"] == "dry-run"
    with pytest.raises(RuntimeError, match="fetch.ingest"):
        ref.run(["--kind", "boundaries", "--case", "rs_2024"])


def test_missing_product_reports_missing(data_dir, fake_ingest):
    calls = fake_ingest
    results = ref.run(["--kind", "glc", "--case", "wa_2025"])
    assert results[0]["status"] == "missing"
    assert calls["tables"] == []


def test_no_bucket_env_needed(data_dir, fake_ingest):
    """L10: vector ingest works with no bucket env at all (fixture already
    deleted NOW_GCS_BUCKET/GCS_BUCKET)."""
    _write_fc(data_dir, "rs_2024", "municipal_boundaries.geojson", [BOUNDARY])
    results = ref.run(["--kind", "boundaries", "--case", "rs_2024"])
    assert results[0]["status"] == "ingesting"


def test_all_kind_all_cases(data_dir, fake_ingest):
    for case in ("rs_2024", "wa_2025"):
        _write_fc(data_dir, case, "municipal_boundaries.geojson", [BOUNDARY])
        _write_fc(data_dir, case, "landslide_catalog.geojson", [_point({})])

    results = ref.run(["--kind", "all", "--all"])
    got = [(r["case"], r["asset"]) for r in results if r["case"] in ("rs_2024", "wa_2025")]
    assert got == [
        ("rs_2024", "municipal_boundaries"),
        ("rs_2024", "landslide_catalog"),
        ("wa_2025", "municipal_boundaries"),
        ("wa_2025", "landslide_catalog"),
    ]


def test_all_kind_picks_glc_for_wa(data_dir, fake_ingest, monkeypatch):
    fetched = []

    def fake_fetcher(k):
        return lambda event, **kw: fetched.append((k, event["id"]))

    monkeypatch.setattr(ref, "_fetcher", fake_fetcher)
    _write_fc(data_dir, "wa_2025", "municipal_boundaries.geojson", [BOUNDARY])
    _write_fc(data_dir, "wa_2025", "landslide_catalog.geojson", [_point({})])

    ref.run(["--kind", "all", "--case", "wa_2025", "--refresh"])
    assert fetched == [("boundaries", "wa-2025-12"), ("glc", "wa-2025-12")]
