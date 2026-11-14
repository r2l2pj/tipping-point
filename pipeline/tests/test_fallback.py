import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "pipeline"))
sys.path.insert(0, str(REPO / "tools"))

import export_fallback
from tp import fallback


def write(root, rel, content=b"producer bytes"):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


@pytest.fixture
def data(tmp_path):
    for case in fallback.CASES:
        for rel in ("png/day.png", "cog/day.tif", "geojson/places.geojson", "series/daily.json"):
            write(tmp_path, f"{case}/{rel}")
    node = {"layers": [{"png": "png/day.png", "cog": "cog/day.tif"}],
            "products": {"critical_areas": "geojson/critical_areas.geojson"}}
    for rel in ("png/day.png", "cog/day.tif", "geojson/critical_areas.geojson",
                "series/daily.json", "summary.json", "alerts.json", "prev_classes.npz"):
        write(tmp_path, f"now/{rel}")
    write(tmp_path, "now/manifest.json", json.dumps(node).encode())
    write(tmp_path, "metrics.json", b"{}")
    write(tmp_path, "manifest.json", json.dumps({"cases": {
        case: {"layers": [{"png": f"{case}/png/day.png", "cog": f"{case}/cog/day.tif"}],
               "products": {"places": f"{case}/geojson/places.geojson", "absent": None}}
        for case in fallback.CASES}}).encode())
    return tmp_path


def test_now_keys_and_order_copy_bytes(data):
    entries = fallback.plan(data, now=True)
    expected = {"fallback/now/" + p.relative_to(data / "now").as_posix()
                for p in (data / "now").rglob("*") if p.is_file()}
    assert {key for _, key in entries} == expected | {"fallback/manifest.json"}
    assert [key for _, key in entries][-2:] == ["fallback/now/manifest.json", "fallback/manifest.json"]
    uploads = []

    class Client:
        def bucket(self, name):
            assert name == "bucket"
            return SimpleNamespace(blob=lambda key: SimpleNamespace(
                upload_from_filename=lambda path: uploads.append((key, Path(path).read_bytes()))))

    fallback.upload(entries, bucket="gs://bucket", client=Client())
    assert uploads == [(key, path.read_bytes()) for path, key in entries]
    fallback.upload(entries, bucket="gs://bucket", client=Client())
    assert uploads[:len(entries)] == uploads[len(entries):]


@pytest.mark.parametrize("case", fallback.CASES)
def test_case_keys(data, case):
    entries = fallback.plan(data, case=case, prefix="static/")
    assert {key for _, key in entries} == {
        f"static/{case}/png/day.png", f"static/{case}/cog/day.tif",
        f"static/{case}/geojson/places.geojson", f"static/{case}/series/daily.json",
        "static/metrics.json", "static/manifest.json"}
    assert entries[-1][1] == "static/manifest.json"


def test_dry_run_no_storage_import(data, monkeypatch, capsys):
    import builtins

    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        assert name not in ("fetch", "google.cloud.storage")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    export_fallback.main(["--now", "--root", str(data), "--dry-run"])
    assert capsys.readouterr().out.splitlines() == [key for _, key in fallback.plan(data, now=True)]


@pytest.mark.parametrize("rel", ["manifest.json", "now/manifest.json", "now/summary.json",
                                   "now/alerts.json", "now/geojson/critical_areas.geojson",
                                   "now/png/day.png", "now/cog/day.tif"])
def test_missing_now_input(data, rel):
    (data / rel).unlink()
    with pytest.raises(FileNotFoundError, match="missing expected input"):
        fallback.plan(data, now=True)


@pytest.mark.parametrize("rel", ["metrics.json", "rs_2024/png/day.png"])
def test_missing_case_input(data, rel):
    (data / rel).unlink()
    with pytest.raises(FileNotFoundError):
        fallback.plan(data, case="rs_2024")


def test_missing_credentials_cli_nonzero(data, monkeypatch, capsys):
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    with pytest.raises(SystemExit) as exc:
        export_fallback.main(["--now", "--root", str(data), "--bucket", "gs://bucket"])
    assert exc.value.code == 1
    assert "GOOGLE_APPLICATION_CREDENTIALS" in capsys.readouterr().err


def test_reuses_ingest_storage_loader(data, monkeypatch):
    from fetch import ingest

    seen = []
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/unused/fake.json")
    monkeypatch.setenv("NOW_GCS_BUCKET", "gs://bucket")
    client = SimpleNamespace(bucket=lambda bucket: SimpleNamespace(
        blob=lambda key: SimpleNamespace(upload_from_filename=lambda path: seen.append(key))))

    def load(name):
        assert name == "google.cloud.storage"
        return SimpleNamespace(Client=lambda: client)

    monkeypatch.setattr(ingest, "_import", load)
    entries = fallback.plan(data, now=True)
    fallback.upload(entries)
    assert seen == [key for _, key in entries]


def test_upload_failure_aborts_before_manifests_and_redacts(data):
    seen = []

    def fail(path):
        raise RuntimeError("secret-credential-value")

    def blob(key):
        seen.append(key)
        return SimpleNamespace(upload_from_filename=fail)

    client = SimpleNamespace(bucket=lambda name: SimpleNamespace(blob=blob))
    with pytest.raises(RuntimeError, match="GCS upload failed") as exc:
        fallback.upload(fallback.plan(data, now=True), bucket="bucket", client=client)
    assert "secret-credential-value" not in str(exc.value)
    assert len(seen) == 1 and not seen[0].endswith("manifest.json")


def test_cli_subprocess_dry_run(data):
    result = subprocess.run([sys.executable, str(REPO / "tools/export_fallback.py"),
                             "--case", "wa_2025", "--root", str(data), "--dry-run"],
                            check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [key for _, key in fallback.plan(data, case="wa_2025")]
