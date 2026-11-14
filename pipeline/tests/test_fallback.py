import io
import json
import subprocess
import sys
import zipfile
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
    node = {"layers": [{"id": "tpi_slide_d0", "png": "png/day.png", "cog": "cog/day.tif"}],
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


def test_now_plan_lists_local_files_manifest_last(data):
    entries = fallback.plan(data, now=True)
    expected = {"now/" + p.relative_to(data / "now").as_posix()
                for p in (data / "now").rglob("*") if p.is_file()}
    assert set(entries) == expected | {"manifest.json"}
    assert entries[-2:] == ["now/manifest.json", "manifest.json"]
    assert all((data / rel).is_file() for rel in entries)


@pytest.mark.parametrize("case", fallback.CASES)
def test_case_plan(data, case):
    entries = fallback.plan(data, case=case)
    assert set(entries) == {
        f"{case}/png/day.png", f"{case}/cog/day.tif",
        f"{case}/geojson/places.geojson", f"{case}/series/daily.json",
        "metrics.json", "manifest.json"}
    assert entries[-1] == "manifest.json"
    assert all((data / rel).is_file() for rel in entries)


def test_plan_rejects_bad_selection(data):
    with pytest.raises(ValueError, match="either now or a canonical case"):
        fallback.plan(data)
    with pytest.raises(ValueError, match="either now or a canonical case"):
        fallback.plan(data, now=True, case="rs_2024")


def test_dry_run_offline_no_ee_or_storage_imports(data, monkeypatch, capsys):
    import builtins

    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        assert not (name == "ee" or name.startswith("ee.") or "storage" in name
                    or name in ("fetch", "gee"))
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    export_fallback.main(["--now", "--root", str(data), "--dry-run"])
    assert capsys.readouterr().out.splitlines() == fallback.plan(data, now=True)


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


def test_cli_subprocess_dry_run(data):
    result = subprocess.run([sys.executable, str(REPO / "tools/export_fallback.py"),
                             "--case", "wa_2025", "--root", str(data), "--dry-run"],
                            check=False, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == fallback.plan(data, case="wa_2025")


def test_cli_rejects_bucket_arg(data):
    result = subprocess.run([sys.executable, str(REPO / "tools/export_fallback.py"),
                             "--now", "--root", str(data), "--bucket", "x", "--dry-run"],
                            check=False, capture_output=True, text=True)
    assert result.returncode != 0


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


def fake_render_clients(cog_bytes=b"COG"):
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
                        "properties": {"class": 3}}]})
                if method == "getThumbURL":
                    return "fake-png-url"
                if method == "getDownloadURL":
                    return "fake-cog-url"
                return self
            return operation

    Image.pixelArea = lambda: Image()
    ee = SimpleNamespace(Image=Image, ImageCollection=lambda name: Image(name),
                         Geometry=SimpleNamespace(Rectangle=lambda bounds: bounds),
                         Reducer=SimpleNamespace(sum=lambda: "sum"))

    def fetch(url, limit=None):
        calls.append(("fetch", (url,), {"limit": limit}))
        if limit is not None and len(cog_bytes) > limit:
            return None
        return cog_bytes

    return ee, calls, fetch


def test_render_now_writes_local_files(production):
    ee, calls, fetch = fake_render_clients()
    originals = {rel: (production / rel).read_bytes()
                 for rel in ("manifest.json", "now/manifest.json", "now/alerts.json")}
    entries = fallback.render_now(production, ee_client=ee, fetch=fetch,
                                  fetch_png=lambda url: b"PNG")
    assert [args for method, args, _ in calls if method == "select"] == [
        ("TPI_slide",)] * 4 + [("TPI_flood",)] * 4 + [("population_count",)]
    thumbs = [args[0] for method, args, _ in calls if method == "getThumbURL"]
    assert len(thumbs) == 8
    assert all(p["crs"] == "EPSG:3857" for p in thumbs)
    downloads = [args[0] for method, args, _ in calls if method == "getDownloadURL"]
    assert len(downloads) == 4
    assert all(d["crs"] == "EPSG:4326" and d["format"] == "GEO_TIFF" for d in downloads)
    assert all((production / f"now/cog/tpi_{h}_d{d}.tif").read_bytes() == b"COG"
               for h in ("slide", "flood") for d in range(2))
    assert any(method == "gte" and args == (0.4,) for method, args, _ in calls)
    assert len([1 for method, _, _ in calls if method == "reduceToVectors"]) == 4
    summary = json.loads((production / "now/summary.json").read_text())
    assert summary["critical_km2"] == {"slide": 12.5, "flood": 12.5, "any": 12.5}
    critical = json.loads((production / "now/geojson/critical_areas.geojson").read_text())
    assert len(critical["features"]) == 4
    assert {f["properties"]["hazard"] for f in critical["features"]} == {"slide", "flood"}
    assert all(f["properties"]["label"] == "Crítico" for f in critical["features"])
    assert len([1 for method, args, _ in calls if method == "gt" and args == (0.6,)]) == 6
    assert summary["people_exposed"] == summary["people_flooded"] == 12.5
    assert summary["_null_reasons"] == {}
    assert all((production / rel).read_bytes() == content for rel, content in originals.items())
    assert entries[-2:] == ["now/manifest.json", "manifest.json"]
    assert all((production / rel).is_file() for rel in entries)
    calls.clear()
    fallback.render_now(production, ee_client=ee, fetch=fetch, fetch_png=lambda url: b"PNG")
    assert not any(method in ("getDownloadURL", "getThumbURL", "reduceRegion", "reduceToVectors")
                   for method, _, _ in calls)


def test_render_skips_cog_over_10mb(production, capsys):
    ee, _calls, fetch = fake_render_clients(cog_bytes=b"COG" * 5 * 1024 * 1024)
    fallback.render_now(production, ee_client=ee, fetch=fetch, fetch_png=lambda url: b"PNG")
    assert not list((production / "now/cog").glob("*.tif"))
    assert (production / "now/summary.json").is_file()
    assert "over" in capsys.readouterr().err


def test_render_unzips_getdownloadurl_result(production):
    ee, _calls, _ = fake_render_clients()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("layer.tif", b"INNER-TIF")
    payload = buffer.getvalue()
    fallback.render_now(production, ee_client=ee,
                        fetch=lambda url, limit=None: payload,
                        fetch_png=lambda url: b"PNG")
    assert (production / "now/cog/tpi_slide_d0.tif").read_bytes() == b"INNER-TIF"


def test_render_preserves_existing_local_products(production):
    ee, calls, fetch = fake_render_clients()
    local = {"now/summary.json": b'{"producer": true}',
             "now/geojson/critical_areas.geojson": b'{"features": []}',
             "now/png/tpi_slide_d0.png": b"producer PNG"}
    for rel, content in local.items():
        write(production, rel, content)
    fallback.render_now(production, ee_client=ee, fetch=fetch, fetch_png=lambda url: b"PNG")
    assert all((production / rel).read_bytes() == content for rel, content in local.items())
    assert len([1 for method, _, _ in calls if method == "getThumbURL"]) == 7
    assert not any(method in ("reduceRegion", "reduceToVectors") for method, _, _ in calls)


def test_render_rs_cog_only_layers(production):
    node = json.loads((production / "now/manifest.json").read_text())
    for prefix in ("r", "s"):
        node["layers"].append({"id": f"{prefix}_d0", "asset": f"projects/test/assets/now/{prefix}_d0",
                               "png": None, "cog": f"cog/{prefix}_d0.tif"})
    (production / "now/manifest.json").write_text(json.dumps(node))
    ee, calls, fetch = fake_render_clients()
    fallback.render_now(production, ee_client=ee, fetch=fetch, fetch_png=lambda url: b"PNG")
    assert [args for method, args, _ in calls if method == "select"][-3:-1] == [("R",), ("S",)]
    assert (production / "now/cog/r_d0.tif").read_bytes() == b"COG"
    assert (production / "now/cog/s_d0.tif").read_bytes() == b"COG"
    assert len([1 for method, _, _ in calls if method == "getThumbURL"]) == 8
    assert not (production / "now/png/r_d0.png").exists()
    assert not (production / "now/png/s_d0.png").exists()


def test_render_unknown_layer_prefix_errors(production):
    node = json.loads((production / "now/manifest.json").read_text())
    node["layers"].insert(0, {"id": "unknown_d0", "asset": "projects/test/assets/unknown_d0",
                               "png": None, "cog": "cog/unknown_d0.tif"})
    (production / "now/manifest.json").write_text(json.dumps(node))
    ee, calls, fetch = fake_render_clients()
    with pytest.raises(ValueError, match="unrecognized layer.*unknown_d0"):
        fallback.render_now(production, ee_client=ee, fetch=fetch, fetch_png=lambda url: b"PNG")
    assert not any(method in ("getDownloadURL", "getThumbURL") for method, _, _ in calls)


def test_render_missing_contracted_band_fails(production):
    ee, calls, fetch = fake_render_clients()

    def select(band):
        assert band == "TPI_slide"
        raise RuntimeError("required TPI_slide band absent")

    ee.Image = lambda asset: SimpleNamespace(select=select)
    with pytest.raises(RuntimeError, match="required TPI_slide band absent"):
        fallback.render_now(production, ee_client=ee, fetch=fetch)
    assert not any(method in ("getDownloadURL", "getThumbURL") for method, _, _ in calls)


def test_render_missing_asset_metadata_errors(production):
    ee, calls, fetch = fake_render_clients()
    image = ee.Image()
    image.toDictionary = lambda fields: SimpleNamespace(getInfo=dict)
    image.select = lambda band: image
    ee.Image = lambda name: image
    with pytest.raises(ValueError, match="missing required asset timestamps"):
        fallback.render_now(production, ee_client=ee, fetch=fetch)
    assert not any(method == "getDownloadURL" for method, _, _ in calls)


def test_render_cli_error_hides_sdk_secrets(production, monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise RuntimeError("secret-sdk-detail")

    monkeypatch.setattr(fallback, "render_now", fail)
    with pytest.raises(SystemExit) as exc:
        export_fallback.main(["--now", "--root", str(production)])
    assert exc.value.code == 1
    message = capsys.readouterr().err
    assert "secret-sdk-detail" not in message
    assert "GEE fallback rendering failed" in message


def test_now_dry_run_missing_renders_is_offline(production, monkeypatch, capsys):
    monkeypatch.setattr(fallback, "render_now", lambda *a, **kw: pytest.fail("render in dry run"))
    export_fallback.main(["--now", "--root", str(production), "--dry-run"])
    keys = capsys.readouterr().out.splitlines()
    assert "now/summary.json" in keys
    assert keys[-2:] == ["now/manifest.json", "manifest.json"]
    assert not (production / "now/summary.json").exists()


@pytest.mark.parametrize("rel", ["now/alerts.json", "now/manifest.json", "manifest.json"])
def test_now_missing_producer_file_errors(production, rel):
    (production / rel).unlink()
    with pytest.raises(FileNotFoundError):
        fallback.now_plan(production)
