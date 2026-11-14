"""Offline tests for fetch.ingest + tools.ingest_assets.

Fake ee and fake google.cloud.storage are injected through sys.modules —
NO network, NO credentials and NO real SDK anywhere below.
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
    ids (or `get_error` unconditionally); start*Ingestion calls are recorded
    as ('image'|'table', request_id, params)."""
    ee = types.ModuleType("ee")
    store = dict(assets or {})

    def get_asset(asset_id):
        if get_error is not None:
            raise get_error
        if asset_id not in store:
            raise RuntimeError(f"{asset_id} not found")
        return store[asset_id]

    def start_ingestion(request_id, params):
        calls.append(("image", request_id, params))
        return {"name": "op/image-1"}

    def start_table(request_id, params):
        calls.append(("table", request_id, params))
        return {"name": "op/table-1"}

    ee.data = types.SimpleNamespace(
        getAsset=get_asset,
        startIngestion=start_ingestion,
        startTableIngestion=start_table,
    )
    ee.Initialize = lambda **kw: calls.append(("init", kw))
    return ee


def _fake_storage(uploads):
    """Module-type fake google.cloud.storage: Client().bucket().blob()
    .upload_from_filename records (bucket, blob, filename)."""
    mod = types.ModuleType("google.cloud.storage")

    class _Blob:
        def __init__(self, bucket_name, blob_name):
            self._bucket, self._name = bucket_name, blob_name

        def upload_from_filename(self, filename):
            uploads.append((self._bucket, self._name, filename))

    class _Bucket:
        def __init__(self, name):
            self._name = name

        def blob(self, name):
            return _Blob(self._name, name)

    class Client:
        def bucket(self, name):
            return _Bucket(name)

    mod.Client = Client
    return mod


def _install_fakes(monkeypatch, assets=None, get_error=None):
    """Inject fake ee + fake google.cloud.storage; return (uploads, calls)."""
    uploads, calls = [], []
    monkeypatch.setitem(
        sys.modules, "ee", _fake_ee(calls, assets, get_error))
    monkeypatch.setitem(
        sys.modules, "google.cloud.storage", _fake_storage(uploads))
    return uploads, calls


def _write(path: Path, content=b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


# ---------------------------------------------------------------- library


def test_stage_to_gcs_uploads_under_staging(monkeypatch, tmp_path):
    uploads, _ = _install_fakes(monkeypatch)
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/sa.json")
    local = _write(tmp_path / "rs_2024_dswx_wtr_20240502.tif")
    uri = ingest.stage_to_gcs(local, bucket="tp-data", case="rs_2024",
                              name="dswx_wtr_20240502")
    assert uri == "gs://tp-data/staging/rs_2024/dswx_wtr_20240502.tif"
    assert uploads == [(
        "tp-data", "staging/rs_2024/dswx_wtr_20240502.tif", str(local))]


def test_image_ingest_uses_startIngestion(monkeypatch):
    _, calls = _install_fakes(monkeypatch)
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/sa.json")
    monkeypatch.setenv("GEE_PROJECT", "proj-x")
    aid = "projects/proj-x/assets/tipping-point/rs_2024/dswx_wtr_20240502"
    entry = ingest.ingest_asset(
        "gs://b/staging/rs_2024/dswx_wtr_20240502.tif",
        asset_id=aid, kind="image")
    assert [c[0] for c in calls] == ["init", "image"]
    params = calls[1][2]
    assert params["name"] == aid
    assert params["tilesets"][0]["sources"][0]["uris"] == [
        "gs://b/staging/rs_2024/dswx_wtr_20240502.tif"]
    assert entry["status"] == "ingesting"
    assert entry["gee_id"] == aid
    assert entry["kind"] == "image"
    assert entry["ingested_at"]


def test_table_ingest_uses_startTableIngestion(monkeypatch):
    _, calls = _install_fakes(monkeypatch)
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/sa.json")
    aid = "projects/p/assets/tipping-point/wa_2025/landslide_catalog"
    entry = ingest.ingest_asset(
        "gs://b/staging/wa_2025/landslide_catalog.geojson",
        asset_id=aid, kind="table")
    assert [c[0] for c in calls] == ["init", "table"]
    assert calls[1][2]["sources"][0]["uris"] == [
        "gs://b/staging/wa_2025/landslide_catalog.geojson"]
    assert entry["kind"] == "table"
    assert entry["status"] == "ingesting"


def test_existing_asset_is_skipped(monkeypatch):
    aid = "projects/p/assets/tipping-point/rs_2024/municipal_boundaries"
    _, calls = _install_fakes(monkeypatch, assets={aid: {"type": "TABLE"}})
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/sa.json")
    entry = ingest.ingest_asset("gs://b/x.geojson", asset_id=aid, kind="table")
    assert entry["status"] == "exists"
    assert [c[0] for c in calls] == ["init"]  # no ingestion started


def test_force_reingests_existing(monkeypatch):
    aid = "projects/p/assets/tipping-point/rs_2024/municipal_boundaries"
    _, calls = _install_fakes(monkeypatch, assets={aid: {"type": "TABLE"}})
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/sa.json")
    entry = ingest.ingest_asset("gs://b/x.geojson", asset_id=aid,
                              kind="table", force=True)
    assert entry["status"] == "ingesting"
    assert calls[-1][0] == "table"


def test_getAsset_non_404_errors_propagate(monkeypatch):
    _, calls = _install_fakes(
        monkeypatch, get_error=RuntimeError("PERMISSION_DENIED"))
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/sa.json")
    with pytest.raises(RuntimeError, match="PERMISSION_DENIED"):
        ingest.ingest_asset("gs://b/x.tif",
                            asset_id="projects/p/assets/tipping-point/c/x",
                            kind="image")
    assert [c[0] for c in calls] == ["init"]  # no ingestion started


def test_getAsset_404_status_means_absent(monkeypatch):
    err = RuntimeError("weird message")
    err.status_code = 404
    _, calls = _install_fakes(monkeypatch, get_error=err)
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/sa.json")
    entry = ingest.ingest_asset("gs://b/x.tif",
                                asset_id="projects/p/assets/a/b/c",
                                kind="image")
    assert entry["status"] == "ingesting"
    assert calls[-1][0] == "image"


def test_getAsset_403_status_propagates(monkeypatch):
    err = RuntimeError("forbidden")
    err.status_code = 403
    _install_fakes(monkeypatch, get_error=err)
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/sa.json")
    with pytest.raises(RuntimeError, match="forbidden"):
        ingest.ingest_asset("gs://b/x.tif",
                            asset_id="projects/p/assets/a/b/c",
                            kind="image")


def test_invalid_case_and_name_rejected(tmp_path):
    for bad in ("a/b", "..", "a\\b", "a.b", "", " has space"):
        with pytest.raises(ValueError):
            ingest.asset_id(bad, "x")
        with pytest.raises(ValueError):
            ingest.asset_id("ok", bad)
    with pytest.raises(ValueError):
        ingest.staging_uri("bkt", "rs_2024", "../evil")
    with pytest.raises(ValueError):
        ingest.staging_uri("bad/bucket", "rs_2024", "x")
    local = _write(tmp_path / "x.tif")
    with pytest.raises(ValueError):
        ingest.stage_to_gcs(local, bucket="b", case="c", name="bad/name")
    with pytest.raises(ValueError):
        ingest.ingest_asset("gs://b/x",
                            asset_id="projects/p/assets/../evil",
                            kind="image")
    with pytest.raises(ValueError):
        ingest.append_manifest("../escape", {"name": "x"})


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
    _install_fakes(monkeypatch)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    local = _write(tmp_path / "x.tif")
    with pytest.raises(RuntimeError, match="GOOGLE_APPLICATION_CREDENTIALS"):
        ingest.stage_to_gcs(local, bucket="b", case="c", name="n")
    with pytest.raises(RuntimeError, match="GOOGLE_APPLICATION_CREDENTIALS"):
        ingest.ingest_asset("gs://b/x", asset_id="a", kind="image")


def test_staging_bucket_env_and_alias(monkeypatch):
    monkeypatch.delenv("NOW_GCS_BUCKET", raising=False)
    monkeypatch.delenv("GCS_BUCKET", raising=False)
    with pytest.raises(RuntimeError, match="NOW_GCS_BUCKET"):
        ingest.staging_bucket()
    monkeypatch.setenv("GCS_BUCKET", "alias-bucket")
    assert ingest.staging_bucket() == "alias-bucket"
    monkeypatch.setenv("NOW_GCS_BUCKET", "primary-bucket")
    assert ingest.staging_bucket() == "primary-bucket"


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
    assert "staging/rs_2024/landslide_catalog.geojson" in out
    assert "tipping-point/rs_2024/landslide_catalog" in out
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
        "gcs_uri": "gs://<NOW_GCS_BUCKET>/staging/wa_2025/"
                   "municipal_boundaries.geojson",
        "kind": "table",
        "status": "pending",
        "ingested_at": None,
        "source": str(geo),
    }]


def test_cli_real_run_stages_ingests_and_manifests(monkeypatch, tmp_path):
    uploads, calls = _install_fakes(monkeypatch)
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/sa.json")
    monkeypatch.setenv("NOW_GCS_BUCKET", "tp-bucket")
    monkeypatch.setattr(events, "DATA_DIR", tmp_path)
    geo = _write(tmp_path / "rs_2024" / "geojson"
                 / "municipal_boundaries.geojson", b"{}")
    ingest_assets.main(["--case", "rs_2024"])
    assert uploads == [(
        "tp-bucket", "staging/rs_2024/municipal_boundaries.geojson",
        str(geo))]
    assert calls[-1][0] == "table"
    doc = json.loads((tmp_path / "rs_2024" / "assets.json").read_text())
    assert doc["assets"][0]["status"] == "ingesting"
    assert doc["assets"][0]["ingested_at"]
