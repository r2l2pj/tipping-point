"""Offline tests for fetch.ingest + tools.ingest_assets.

A fake ee is injected through sys.modules — NO network, NO credentials,
NO GCS and NO real SDK anywhere below (L10: no bucket anywhere).
"""
import json
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "pipeline"))
sys.path.insert(0, str(REPO_ROOT / "tools"))

import ingest_assets
from fetch import ingest
from tp import events


def _fake_ee(calls, assets=None, get_error=None):
    """Module-type fake ee: getAsset raises a not-found error for missing
    ids (or `get_error` unconditionally); Feature/FeatureCollection/
    Export.table.toAsset calls are recorded."""
    ee = types.ModuleType("ee")
    store = dict(assets or {})

    def get_asset(asset_id):
        if get_error is not None:
            raise get_error
        if asset_id not in store:
            raise RuntimeError(f"{asset_id} not found")
        return store[asset_id]

    class Feature:
        def __init__(self, geojson):
            self.geojson = geojson

    class FeatureCollection:
        def __init__(self, arg):
            self.arg = arg
            calls.append(("fc", len(arg)))

        def flatten(self):
            calls.append(("flatten",))
            return self

    class _Task:
        id = "task-fake-1"

        def start(self):
            calls.append(("task_start",))

    def to_asset(collection, description, assetId=None, overwrite=False,
                 **kw):
        calls.append(("export_table", {
            "collection": collection,
            "description": description,
            "assetId": assetId,
            "overwrite": overwrite,
        }))
        return _Task()

    def create_asset(value, path=None, **kw):
        calls.append(("create", value, path))
        store[path] = value
        return {"name": path}

    ee.data = types.SimpleNamespace(
        getAsset=get_asset, createAsset=create_asset)
    ee.Feature = Feature
    ee.FeatureCollection = FeatureCollection
    ee.batch = types.SimpleNamespace(
        Export=types.SimpleNamespace(
            table=types.SimpleNamespace(toAsset=to_asset)))
    ee.Initialize = lambda **kw: calls.append(("init", kw))
    ee.ServiceAccountCredentials = (
        lambda email, key_file: types.SimpleNamespace(
            email=email, key_file=key_file))
    return ee


def _install_fakes(monkeypatch, assets=None, get_error=None):
    """Inject fake ee; return calls. SA-key envs are cleared so ambient
    credentials can't leak in."""
    calls = []
    monkeypatch.setitem(
        sys.modules, "ee", _fake_ee(calls, assets, get_error))
    # gee.common binds `ee` at its own import time — drop the cache so the
    # real helper re-imports under THIS test's fake ee.
    monkeypatch.delitem(sys.modules, "gee.common", raising=False)
    for var in ("GCP_SA_KEY", "GEE_SERVICE_ACCOUNT_KEY"):
        monkeypatch.delenv(var, raising=False)
    return calls


def _write(path: Path, content=b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def _geojson(path: Path, n: int = 1) -> Path:
    return _write(path, json.dumps({
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [-51.8, -29.7]},
             "properties": {}}
        ] * n,
    }).encode())


# ---------------------------------------------------------------- library


def test_ingest_table_exports_client_side_collection(monkeypatch, tmp_path):
    """L10: no gs:// staging — features become a client-side
    ee.FeatureCollection uploaded via Export.table.toAsset."""
    calls = _install_fakes(monkeypatch)
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/sa.json")
    aid = "projects/p/assets/tipping-point/wa_2025/landslide_catalog"
    geo = tmp_path / "landslide_catalog.geojson"
    geo.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [-51.8, -29.7]},
         "properties": {"name": "x"}},
    ]}))
    entry = ingest.ingest_table(geo, asset_id=aid)
    kinds = [c[0] for c in calls]
    assert kinds[0] == "init"
    assert "export_table" in kinds
    assert "task_start" in kinds
    assert "table" not in kinds        # no startTableIngestion, no bucket
    export = next(c[1] for c in calls if c[0] == "export_table")
    assert export["assetId"] == aid
    assert export["overwrite"] is False
    assert entry["kind"] == "table"
    assert entry["status"] == "ingesting"
    assert entry["features"] == 1
    assert entry["operation"] == "task-fake-1"


def test_ingest_table_chunks_large_collections(monkeypatch, tmp_path):
    """>10k features: per-chunk ee.FeatureCollections, each with its OWN
    Export.table.toAsset to <asset_id>_partNN — no request ever carries
    more than chunk_size features (no flattened mega-collection)."""
    calls = _install_fakes(monkeypatch)
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/sa.json")
    aid = "projects/p/assets/tipping-point/rs_2024/landslide_catalog"
    features = [{"type": "Feature", "geometry": None, "properties": {}}
                for _ in range(12_345)]
    geo = tmp_path / "big.geojson"
    geo.write_text(json.dumps(
        {"type": "FeatureCollection", "features": features}))
    entry = ingest.ingest_table(geo, asset_id=aid)
    # 3 client-side collections, each bounded at 5000 features
    assert [c[1] for c in calls if c[0] == "fc"] == [5000, 5000, 2345]
    assert ("flatten",) not in calls
    # FOLDER parent created at asset_id; parts are assets INSIDE it
    created = [c for c in calls if c[0] == "create"]
    assert created == [("create", {"type": "FOLDER"}, aid)]
    exports = [c[1] for c in calls if c[0] == "export_table"]
    assert [e["assetId"] for e in exports] == [
        f"{aid}/part00", f"{aid}/part01", f"{aid}/part02"]
    assert [e["description"] for e in exports] == [
        "ingest_landslide_catalog_part00",
        "ingest_landslide_catalog_part01",
        "ingest_landslide_catalog_part02"]
    assert len([c for c in calls if c[0] == "task_start"]) == 3
    assert entry["features"] == 12_345
    assert entry["status"] == "ingesting"
    assert [p["gee_id"] for p in entry["parts"]] == [
        e["assetId"] for e in exports]
    assert [p["features"] for p in entry["parts"]] == [5000, 5000, 2345]


def test_ingest_table_skips_existing_parts(monkeypatch, tmp_path):
    """Idempotent per part: a part already in EE is not re-exported."""
    aid = "projects/p/assets/tipping-point/rs_2024/landslide_catalog"
    calls = _install_fakes(
        monkeypatch,
        assets={aid: {"type": "FOLDER"},
                f"{aid}/part00": {"type": "TABLE"}})
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/sa.json")
    geo = _geojson(tmp_path / "x.geojson", n=7)
    entry = ingest.ingest_table(geo, asset_id=aid, chunk_size=3)
    exports = [c[1] for c in calls if c[0] == "export_table"]
    assert [e["assetId"] for e in exports] == [
        f"{aid}/part01", f"{aid}/part02"]          # part00 skipped
    assert [c for c in calls if c[0] == "create"] == []
    assert entry["parts"][0]["status"] == "exists"
    assert entry["status"] == "ingesting"


def test_ingest_table_chunks_on_payload_bytes(monkeypatch, tmp_path):
    """Few but large features (wide boundaries): split so no EE export
    request approaches the 10 MB serialized-payload cap."""
    calls = _install_fakes(monkeypatch)
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/sa.json")
    monkeypatch.setattr(ingest, "TABLE_CHUNK_BYTES", 300)
    aid = "projects/p/assets/tipping-point/wa_2025/municipal_boundaries"
    features = [{"type": "Feature", "geometry": None,
                 "properties": {"pad": "x" * 120}} for _ in range(10)]
    geo = tmp_path / "wide.geojson"
    geo.write_text(json.dumps(
        {"type": "FeatureCollection", "features": features}))
    entry = ingest.ingest_table(geo, asset_id=aid)
    fc_sizes = [c[1] for c in calls if c[0] == "fc"]
    assert len(fc_sizes) > 2                      # split happened
    assert sum(fc_sizes) == 10
    assert max(fc_sizes) < 10
    assert len(entry["parts"]) == len(fc_sizes)


def test_ingest_table_small_collection_single_chunk(monkeypatch, tmp_path):
    calls = _install_fakes(monkeypatch)
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/sa.json")
    geo = tmp_path / "small.geojson"
    geo.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": None, "properties": {}},
    ] * 2}))
    ingest.ingest_table(geo, asset_id="projects/p/assets/a/b/c")
    assert [c[1] for c in calls if c[0] == "fc"] == [2]
    exports = [c[1] for c in calls if c[0] == "export_table"]
    assert [e["assetId"] for e in exports] == ["projects/p/assets/a/b/c"]


def test_existing_asset_is_skipped(monkeypatch, tmp_path):
    aid = "projects/p/assets/tipping-point/rs_2024/municipal_boundaries"
    calls = _install_fakes(monkeypatch, assets={aid: {"type": "TABLE"}})
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/sa.json")
    geo = tmp_path / "x.geojson"
    geo.write_text(json.dumps({"type": "FeatureCollection", "features": []}))
    entry = ingest.ingest_table(geo, asset_id=aid)
    assert entry["status"] == "exists"
    assert [c[0] for c in calls] == ["init"]  # no ingestion started


def test_force_reingests_existing(monkeypatch, tmp_path):
    aid = "projects/p/assets/tipping-point/rs_2024/municipal_boundaries"
    calls = _install_fakes(monkeypatch, assets={aid: {"type": "TABLE"}})
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/sa.json")
    geo = tmp_path / "x.geojson"
    geo.write_text(json.dumps({"type": "FeatureCollection", "features": []}))
    entry = ingest.ingest_table(geo, asset_id=aid, force=True)
    assert entry["status"] == "ingesting"
    export = next(c[1] for c in calls if c[0] == "export_table")
    assert export["overwrite"] is True


def test_getAsset_non_404_errors_propagate(monkeypatch, tmp_path):
    calls = _install_fakes(
        monkeypatch, get_error=RuntimeError("PERMISSION_DENIED"))
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/sa.json")
    with pytest.raises(RuntimeError, match="PERMISSION_DENIED"):
        ingest.ingest_table(_geojson(tmp_path / "x.geojson"),
                            asset_id="projects/p/assets/tipping-point/c/x")
    assert [c[0] for c in calls] == ["init"]  # no ingestion started


def test_getAsset_404_status_means_absent(monkeypatch, tmp_path):
    err = RuntimeError("weird message")
    err.status_code = 404
    calls = _install_fakes(monkeypatch, get_error=err)
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/sa.json")
    entry = ingest.ingest_table(_geojson(tmp_path / "x.geojson"),
                                asset_id="projects/p/assets/a/b/c")
    assert entry["status"] == "ingesting"
    assert calls[-1][0] == "task_start"


def test_getAsset_403_status_propagates(monkeypatch, tmp_path):
    err = RuntimeError("forbidden")
    err.status_code = 403
    _install_fakes(monkeypatch, get_error=err)
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/sa.json")
    with pytest.raises(RuntimeError, match="forbidden"):
        ingest.ingest_table(_geojson(tmp_path / "x.geojson"),
                            asset_id="projects/p/assets/a/b/c")


def test_getAsset_non_404_status_with_not_found_text_propagates(
        monkeypatch, tmp_path):
    err = RuntimeError("requested entity was not found in project")
    err.status_code = 403
    _install_fakes(monkeypatch, get_error=err)
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/sa.json")
    with pytest.raises(RuntimeError, match="not found"):
        ingest.ingest_table(_geojson(tmp_path / "x.geojson"),
                            asset_id="projects/p/assets/a/b/c")


def test_getAsset_auth_text_with_not_found_propagates(monkeypatch, tmp_path):
    err = RuntimeError("Permission denied: asset project not found")
    _install_fakes(monkeypatch, get_error=err)
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/sa.json")
    with pytest.raises(RuntimeError, match="not found"):
        ingest.ingest_table(_geojson(tmp_path / "x.geojson"),
                            asset_id="projects/p/assets/a/b/c")


def test_getAsset_grpc_not_found_code_means_absent(monkeypatch, tmp_path):
    err = RuntimeError("rpc error")
    err.code = lambda: "NOT_FOUND"  # grpc-style callable status
    calls = _install_fakes(monkeypatch, get_error=err)
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/sa.json")
    entry = ingest.ingest_table(_geojson(tmp_path / "x.geojson"),
                                asset_id="projects/p/assets/a/b/c")
    assert entry["status"] == "ingesting"
    assert calls[-1][0] == "task_start"


def test_getAsset_eeexception_does_not_exist_means_absent(
        monkeypatch, tmp_path):
    # real earthengine-api getAsset 404 text carries no status code
    err = RuntimeError(
        "Asset 'projects/p/assets/a/b/c' does not exist or doesn't "
        "allow this operation.")
    calls = _install_fakes(monkeypatch, get_error=err)
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/sa.json")
    entry = ingest.ingest_table(_geojson(tmp_path / "x.geojson"),
                                asset_id="projects/p/assets/a/b/c")
    assert entry["status"] == "ingesting"
    assert calls[-1][0] == "task_start"


def test_invalid_case_and_name_rejected(tmp_path):
    # real filenames keep their dots; separators/traversal are rejected
    assert (ingest.asset_id("wa_2025", "wa_2025_nisar_gcov_20251228.tif")
            .endswith("/wa_2025/wa_2025_nisar_gcov_20251228.tif"))
    for bad in ("a/b", "..", "a..b", "a\\b", "", " has space", "trail."):
        with pytest.raises(ValueError):
            ingest.asset_id(bad, "x")
        with pytest.raises(ValueError):
            ingest.asset_id("ok", bad)
    with pytest.raises(ValueError):
        ingest.ingest_table(_geojson(tmp_path / "x.geojson"),
                            asset_id="projects/p/assets/../evil")
    with pytest.raises(ValueError):
        ingest.append_manifest("../escape", {"name": "x"})


def test_ensure_collection_creates_container_once(monkeypatch):
    """ensure_collection: createAsset(IMAGE_COLLECTION) once, then exists."""
    calls = _install_fakes(monkeypatch)
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/sa.json")
    parent = "projects/p/assets/tipping-point/rs_2024/imerg_early"
    e1 = ingest.ensure_collection(parent)
    assert e1["status"] == "created"
    assert e1["type"] == "IMAGE_COLLECTION"
    create = [c for c in calls if c[0] == "create"]
    assert create == [("create", {"type": "IMAGE_COLLECTION"}, parent)]
    e2 = ingest.ensure_collection(parent)
    assert e2["status"] == "exists"
    assert len([c for c in calls if c[0] == "create"]) == 1   # idempotent


def test_ensure_collection_injected_ee_data():
    """ee_data injection skips creds/init entirely."""
    created = []
    store = {}

    def get_asset(aid):
        if aid not in store:
            raise RuntimeError(f"{aid} not found")
        return store[aid]

    def create_asset(value, path=None):
        created.append((value, path))
        store[path] = value
        return {"name": path}

    data = types.SimpleNamespace(getAsset=get_asset,
                                 createAsset=create_asset)
    parent = "projects/p/assets/tipping-point/c/x"
    assert ingest.ensure_collection(parent, ee_data=data)["status"] == "created"
    assert ingest.ensure_collection(parent, ee_data=data)["status"] == "exists"
    assert created == [({"type": "IMAGE_COLLECTION"}, parent)]


def test_ensure_collection_create_race_returns_exists():
    """get-then-create race: getAsset misses, createAsset raises
    already-exists (text, 409 status, or grpc code) -> status 'exists'."""
    def get_asset(aid):
        raise RuntimeError(f"{aid} not found")

    def with_error(err):
        def create_asset(value, path=None):
            raise err
        return types.SimpleNamespace(getAsset=get_asset,
                                     createAsset=create_asset)

    parent = "projects/p/assets/tipping-point/c/x"
    for err in (RuntimeError("resource already exists"),
                RuntimeError("entity already exists in project")):
        e = ingest.ensure_collection(parent, ee_data=with_error(err))
        assert e["status"] == "exists"
    err409 = RuntimeError("conflict")
    err409.status_code = 409
    assert ingest.ensure_collection(
        parent, ee_data=with_error(err409))["status"] == "exists"
    grpc = RuntimeError("rpc error")
    grpc.code = lambda: "ALREADY_EXISTS"
    assert ingest.ensure_collection(
        parent, ee_data=with_error(grpc))["status"] == "exists"


def test_ensure_collection_create_other_errors_propagate():
    def get_asset(aid):
        raise RuntimeError(f"{aid} not found")

    def create_asset(value, path=None):
        raise RuntimeError("PERMISSION_DENIED on create")

    data = types.SimpleNamespace(getAsset=get_asset,
                                 createAsset=create_asset)
    with pytest.raises(RuntimeError, match="PERMISSION_DENIED"):
        ingest.ensure_collection("projects/p/assets/a/b", ee_data=data)
    # auth-flavoured 'already exists' text still propagates
    def create_auth(value, path=None):
        raise RuntimeError("permission denied: parent already exists")
    data = types.SimpleNamespace(getAsset=get_asset,
                                 createAsset=create_auth)
    with pytest.raises(RuntimeError, match="permission denied"):
        ingest.ensure_collection("projects/p/assets/a/b", ee_data=data)


def test_ensure_collection_validates_id(monkeypatch):
    _install_fakes(monkeypatch)
    with pytest.raises(ValueError):
        ingest.ensure_collection("projects/p/assets/../evil")
    with pytest.raises(ValueError):
        ingest.ensure_collection("")


def test_append_manifest_writes_and_upserts(monkeypatch, tmp_path):
    monkeypatch.setattr(events, "DATA_DIR", tmp_path)
    e1 = {"name": "a", "gee_id": "g/a", "gcs_uri": "gs://b/a.tif",
          "kind": "image", "status": "ingesting", "ingested_at": "t"}
    e2 = e1 | {"name": "b", "gee_id": "g/b", "kind": "table"}
    path = ingest.append_manifest("rs_2024", e1)
    assert path == tmp_path / "rs_2024" / "assets.json"
    ingest.append_manifest("rs_2024", e2)
    doc = json.loads(path.read_text())
    assert [a["name"] for a in doc["assets"]] == ["a", "b"]
    assert doc["updated_at"]
    ingest.append_manifest("rs_2024", e1 | {"status": "exists"})
    doc = json.loads(path.read_text())
    assert sorted(a["name"] for a in doc["assets"]) == ["a", "b"]  # no dup
    assert next(a for a in doc["assets"]
                if a["name"] == "a")["status"] == "exists"


def test_missing_creds_raise_naming_var(monkeypatch, tmp_path):
    _install_fakes(monkeypatch)  # SA key envs cleared
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    with pytest.raises(RuntimeError, match="GOOGLE_APPLICATION_CREDENTIALS"):
        ingest.ingest_table(_geojson(tmp_path / "x.geojson"),
                            asset_id="a/b")


def test_ee_init_via_common_with_sa_key(monkeypatch, tmp_path):
    """gee.common.init builds ServiceAccountCredentials from GCP_SA_KEY and
    passes them to ee.Initialize (real common.init, fake ee underneath)."""
    calls = _install_fakes(monkeypatch)
    key = _write(tmp_path / "sa.json", b'{"client_email": "a@b.iam"}')
    monkeypatch.setenv("GCP_SA_KEY", str(key))
    monkeypatch.setenv("GEE_PROJECT", "proj-x")
    ingest.ingest_table(_geojson(tmp_path / "x.geojson"),
                        asset_id="projects/p/assets/a/b/c")
    assert calls[0][0] == "init"
    assert calls[0][1]["project"] == "proj-x"
    assert calls[0][1]["credentials"].key_file == str(key)
    assert calls[-1][0] == "task_start"


# ------------------------------------------------------------------- CLI


def test_registry_resolves_case_files_and_names(tmp_path):
    cog = tmp_path / "wa_2025" / "cog"
    _write(cog / "wa_2025_dswx_wtr_20251210.tif")
    _write(cog / "wa_2025_gcov_hhhh_20251204.tif")
    geo = tmp_path / "wa_2025" / "geojson"
    _write(geo / "landslide_catalog.geojson", b"{}")
    entries = {e["name"]: e
               for e in ingest_assets.resolve_registry("wa_2025", tmp_path)}
    assert entries["dswx_wtr_20251210"]["kind"] == "image"
    assert entries["dswx_wtr_20251210"]["product"] == "opera_dswx_wtr"
    assert entries["gcov_hhhh_20251204"]["product"] == "nisar_gcov"
    assert entries["landslide_catalog"]["kind"] == "table"


def test_dry_run_needs_no_env(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(events, "DATA_DIR", tmp_path)
    _write(tmp_path / "rs_2024" / "geojson" / "landslide_catalog.geojson",
           b"{}")
    for var in ("NOW_GCS_BUCKET", "GCS_BUCKET",
                "GOOGLE_APPLICATION_CREDENTIALS", "GEE_PROJECT"):
        monkeypatch.delenv(var, raising=False)
    ingest_assets.main(["--event", "rs-2024-05", "--dry-run"])
    out = capsys.readouterr().out
    assert "landslide_catalog.geojson" in out
    assert "tipping-point/rs_2024/landslide_catalog" in out
    assert "gs://" not in out
    assert not (tmp_path / "rs_2024" / "assets.json").exists()


def test_manifest_only_writes_pending(monkeypatch, tmp_path):
    monkeypatch.setattr(events, "DATA_DIR", tmp_path)
    monkeypatch.delenv("GEE_PROJECT", raising=False)
    geo = _write(tmp_path / "wa_2025" / "geojson"
                 / "municipal_boundaries.geojson", b"{}")
    ingest_assets.main(["--case", "wa_2025", "--manifest-only"])
    doc = json.loads(
        (tmp_path / "wa_2025" / "assets.json").read_text())
    assert doc["assets"] == [{
        "name": "municipal_boundaries",
        "gee_id": "projects/hermes-fesolucoes/assets/tipping-point/"
                  "wa_2025/municipal_boundaries",
        "kind": "table",
        "status": "pending",
        "ingested_at": None,
        "source": str(geo),
    }]


def test_cli_real_run_exports_and_manifests(monkeypatch, tmp_path):
    calls = _install_fakes(monkeypatch)
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/sa.json")
    monkeypatch.setattr(events, "DATA_DIR", tmp_path)
    _geojson(tmp_path / "rs_2024" / "geojson"
             / "municipal_boundaries.geojson")
    ingest_assets.main(["--case", "rs_2024"])
    export = next(c for c in calls if c[0] == "export_table")
    assert export[1]["assetId"].endswith(
        "tipping-point/rs_2024/municipal_boundaries")
    doc = json.loads((tmp_path / "rs_2024" / "assets.json").read_text())
    assert doc["assets"][0]["status"] == "ingesting"
    assert doc["assets"][0]["ingested_at"]


def test_cli_image_products_recorded_local_only(monkeypatch, tmp_path):
    """L10: raster registry entries keep their planned gee_id locally."""
    _install_fakes(monkeypatch)
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/sa.json")
    monkeypatch.setattr(events, "DATA_DIR", tmp_path)
    _write(tmp_path / "rs_2024" / "cog" / "rs_2024_dswx_wtr_20240502.tif")
    ingest_assets.main(["--case", "rs_2024"])
    doc = json.loads((tmp_path / "rs_2024" / "assets.json").read_text())
    entry = doc["assets"][0]
    assert entry["status"] == "local-only"
    assert entry["kind"] == "image"
    assert entry["gee_id"].endswith("tipping-point/rs_2024/dswx_wtr_20240502")
