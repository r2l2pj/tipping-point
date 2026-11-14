"""manifest.json is always the last file written — the app never reads a
half-updated set."""
import json

from live.publish import write_outputs


def test_manifest_written_last(tmp_path):
    files = {
        "png/tpi_slide_d0.png": b"\x89PNG-fake",
        "geojson/critical_areas.geojson": {"type": "FeatureCollection", "features": []},
        "summary.json": {"critical_km2": 0.0},
    }
    manifest = {"updated_at": "2026-11-14T12:00:00Z", "layers": []}
    written = write_outputs(tmp_path, files, manifest)

    assert [p.name for p in written] == [
        "tpi_slide_d0.png", "critical_areas.geojson", "summary.json", "manifest.json"]
    assert written[-1] == tmp_path / "manifest.json"
    for p in written[:-1]:
        assert p.exists() and p.stat().st_size > 0
    loaded = json.loads((tmp_path / "manifest.json").read_text())
    assert loaded["updated_at"] == "2026-11-14T12:00:00Z"


def test_second_run_overwrites_manifest_last(tmp_path):
    files = {"a.bin": b"x"}
    w1 = write_outputs(tmp_path, files, {"updated_at": "t1"})
    w2 = write_outputs(tmp_path, files, {"updated_at": "t2"})
    assert w1[-1] == w2[-1]
    assert json.loads(w2[-1].read_text())["updated_at"] == "t2"
