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


@pytest.fixture
def production(tmp_path):
    layers = []
    for hazard in ("slide", "flood"):
        for day in range(4):
            name = f"tpi_{hazard}_d{day}"
            layers.append({"id": name, "date": f"2026-11-{14 + day}",
                           "asset": f"projects/test/assets/tipping-point/now/{name}",
                           "png": f"png/{name}.png",
                           "cog": f"cog/{name}.tif" if day < 2 else None})
    node = {"bounds": [-58, -34, -48, -22.5], "updated_at": "2026-11-14T00:00:00Z",
            "layers": layers, "products": {"summary": "summary.json", "alerts": "alerts.json",
                                           "critical_areas": "geojson/critical_areas.geojson"}}
    write(tmp_path, "now/manifest.json", json.dumps(node).encode())
    write(tmp_path, "manifest.json", json.dumps({"cases": {"now": node}}).encode())
    write(tmp_path, "now/alerts.json", b'{"candidates": []}')
    return tmp_path


def fake_render_clients(state="COMPLETED"):
    calls = []

    class Image:
        def __init__(self, name="pixelArea"):
            self.name = name

        def __getattr__(self, method):
            def operation(*args, **kwargs):
                calls.append((method, args, kwargs))
                if method == "toDictionary":
                    return SimpleNamespace(getInfo=lambda: {
                        "data_date": "2026-11-14", "updated_at": "2026-11-14T00:00:00Z"})
                if method == "reduceRegion":
                    return SimpleNamespace(getInfo=lambda: {"area": 12.5})
                if method == "reduceToVectors":
                    return SimpleNamespace(getInfo=lambda: {"features": [{
                        "type": "Feature", "geometry": {"type": "Polygon", "coordinates": []},
                        "properties": {"class": 2}}]})
                if method == "getThumbURL":
                    return "fake-png-url"
                return self
            return operation

    Image.pixelArea = lambda: Image()

    def export(**kwargs):
        calls.append(("export", (), kwargs))
        return SimpleNamespace(start=lambda: calls.append(("start", (), {})),
                               status=lambda: {"state": state})

    ee = SimpleNamespace(Image=Image, Geometry=SimpleNamespace(Rectangle=lambda bounds: bounds),
                         Reducer=SimpleNamespace(sum=lambda: "sum"),
                         batch=SimpleNamespace(Export=SimpleNamespace(image=SimpleNamespace(toCloudStorage=export))))

    def blob(key):
        return SimpleNamespace(download_to_filename=lambda path: write(Path(path).parent, Path(path).name, b"COG"),
                               upload_from_filename=lambda path: calls.append(("upload", (), {"key": key})))

    client = SimpleNamespace(bucket=lambda name: SimpleNamespace(blob=blob))
    return ee, client, calls


def test_render_now_missing_outputs_and_manifest_last(production):
    ee, client, calls = fake_render_clients()
    originals = {rel: (production / rel).read_bytes()
                 for rel in ("manifest.json", "now/manifest.json", "now/alerts.json")}
    entries = fallback.render_now(production, bucket="bucket", ee_client=ee, client=client,
                                  fetch_png=lambda url: b"PNG")
    assert sum(1 for call in calls if call[0] == "getThumbURL") == 8
    exports = [kw for method, _, kw in calls if method == "export"]
    assert len(exports) == 4
    assert all(e["formatOptions"] == {"cloudOptimized": True} and e["crs"] == "EPSG:4326" for e in exports)
    thumbs = [kw for method, args, kw in calls if method == "getThumbURL"]
    assert len(thumbs) == 8
    params = [args[0] for method, args, _ in calls if method == "getThumbURL"]
    assert all(p["crs"] == "EPSG:3857" for p in params)
    assert any(method == "gte" and args == (0.4,) for method, args, _ in calls)
    assert len([1 for method, _, _ in calls if method == "reduceToVectors"]) == 4
    summary = json.loads((production / "now/summary.json").read_text())
    assert summary["critical_km2"] == {"slide": 12.5, "flood": 12.5, "any": 12.5}
    critical = json.loads((production / "now/geojson/critical_areas.geojson").read_text())
    assert len(critical["features"]) == 4
    assert {f["properties"]["hazard"] for f in critical["features"]} == {"slide", "flood"}
    assert all(f["properties"]["label"] == "Alto" for f in critical["features"])
    assert all((production / rel).read_bytes() == content for rel, content in originals.items())
    fallback.upload(fallback.plan(production, now=True), bucket="bucket", client=client)
    assert [kw["key"] for method, _, kw in calls if method == "upload"][-2:] == [
        "fallback/now/manifest.json", "fallback/manifest.json"]
    assert all(path.is_file() for path, _ in entries)
    calls.clear()
    fallback.render_now(production, bucket="bucket", ee_client=ee, client=client, fetch_png=lambda url: b"PNG")
    assert not any(method in ("export", "getThumbURL", "reduceRegion", "reduceToVectors")
                   for method, _, _ in calls)


@pytest.mark.parametrize("state", ["FAILED", "CANCELLED", "RUNNING"])
def test_cog_failure_never_publishes_manifests(production, state):
    ee, client, calls = fake_render_clients(state)
    with pytest.raises(RuntimeError, match="GEE COG export"):
        fallback.render_now(production, bucket="bucket", ee_client=ee, client=client,
                            fetch_png=lambda url: b"PNG", sleep=lambda n: None, max_polls=1)
    assert not any(method == "upload" for method, _, _ in calls)


def test_render_preserves_existing_local_products(production):
    ee, client, calls = fake_render_clients()
    local = {"now/summary.json": b'{"producer": true}',
             "now/geojson/critical_areas.geojson": b'{"features": []}',
             "now/png/tpi_slide_d0.png": b"producer PNG"}
    for rel, content in local.items():
        write(production, rel, content)
    fallback.render_now(production, bucket="bucket", ee_client=ee, client=client, fetch_png=lambda url: b"PNG")
    assert all((production / rel).read_bytes() == content for rel, content in local.items())
    assert len([1 for method, _, _ in calls if method == "getThumbURL"]) == 7
    assert not any(method in ("reduceRegion", "reduceToVectors") for method, _, _ in calls)


def test_render_missing_asset_metadata_errors(production):
    ee, client, calls = fake_render_clients()
    image = ee.Image()
    image.toDictionary = lambda fields: SimpleNamespace(getInfo=dict)
    image.select = lambda band: image
    ee.Image = lambda name: image
    with pytest.raises(ValueError, match="missing required asset timestamps"):
        fallback.render_now(production, bucket="bucket", ee_client=ee, client=client)
    assert not any(method == "export" for method, _, _ in calls)


def test_render_cli_error_hides_sdk_secrets(production, monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise RuntimeError("secret-sdk-detail")

    monkeypatch.setattr(fallback, "render_now", fail)
    with pytest.raises(SystemExit) as exc:
        export_fallback.main(["--now", "--root", str(production), "--bucket", "gs://bucket"])
    assert exc.value.code == 1
    message = capsys.readouterr().err
    assert "secret-sdk-detail" not in message
    assert "GEE fallback rendering failed" in message


def test_now_dry_run_missing_renders_is_offline(production, monkeypatch, capsys):
    monkeypatch.setattr(fallback, "render_now", lambda *a, **kw: pytest.fail("render in dry run"))
    export_fallback.main(["--now", "--root", str(production), "--dry-run"])
    keys = capsys.readouterr().out.splitlines()
    assert "fallback/now/summary.json" in keys
    assert keys[-2:] == ["fallback/now/manifest.json", "fallback/manifest.json"]
    assert not (production / "now/summary.json").exists()


@pytest.mark.parametrize("rel", ["now/alerts.json", "now/manifest.json", "manifest.json"])
def test_now_missing_producer_file_errors(production, rel):
    (production / rel).unlink()
    with pytest.raises(FileNotFoundError):
        fallback.now_plan(production)
