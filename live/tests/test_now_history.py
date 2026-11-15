"""live/now_history.py — rolling snapshots of past Now runs (hermetic)."""
import datetime as dt
import json
from pathlib import Path

from live import now_history as nh


def _run(now: Path, when: dt.datetime, crit: float = 1.0) -> None:
    (now / "png").mkdir(parents=True, exist_ok=True)
    (now / "response" / "png").mkdir(parents=True, exist_ok=True)
    (now / "png" / "tpi_flood_d0.png").write_bytes(b"PNG" + when.isoformat().encode())
    (now / "response" / "png" / "nisar_hh.png").write_bytes(b"PNG")
    m = {"schema": "tipping-point/now@2", "mode": "live",
         "updated_at": when.strftime("%Y-%m-%dT%H:%M:%S.123456Z"),
         "data_dates": {"imerg": "2026-11-15T00Z", "gfs_issued": "2026-11-15T00:00:00Z"},
         "layers": [
             {"id": "tpi_flood_d0", "png": "png/tpi_flood_d0.png", "cog": "cog/x.tif"},
             {"id": "nisar_hh", "png": "response/png/nisar_hh.png"},
             {"id": "gone", "png": "png/missing.png"}],
         "products": {"summary": "summary.json", "places": "places.json"},
         "response": {"priority_zones": {"geojson": "geojson/priority_zones.geojson"}}}
    (now / "manifest.json").write_text(json.dumps(m))
    (now / "summary.json").write_text(json.dumps({"critical_km2": {"any": crit},
                                                  "alert_km2": {"any": 2 * crit}}))


def test_run_id():
    assert nh.run_id("2026-11-15T17:10:47.575597Z") == "20261115T171047Z"


def test_snapshot_layout_and_manifest(tmp_path: Path):
    now = tmp_path / "now"
    _run(now, dt.datetime(2026, 11, 15, 17, 10, 47), crit=3.5)
    idx = nh.update(now)
    d = now / "history" / "20261115T171047Z"
    assert (d / "png" / "tpi_flood_d0.png").is_file()
    assert (d / "response" / "png" / "nisar_hh.png").is_file()
    hm = json.loads((d / "manifest.json").read_text())
    assert hm["history"] is True and hm["run"] == "2026-11-15T17:10:47Z"
    assert [lay["id"] for lay in hm["layers"]] == ["tpi_flood_d0", "nisar_hh"]
    assert all("cog" not in lay for lay in hm["layers"])
    assert hm["products"] == {"summary": "summary.json"} and "response" not in hm
    # every path in the history manifest resolves under the run dir
    for lay in hm["layers"]:
        assert (d / lay["png"]).is_file()
    row = idx["runs"][0]
    assert row["manifest"] == "now/history/20261115T171047Z/manifest.json"
    assert row["critical_km2_any"] == 3.5 and row["alert_km2_any"] == 7.0
    on_disk = json.loads((now / "history" / "index.json").read_text())
    assert on_disk["schema"] == nh.SCHEMA and on_disk["keep"] == 8
    live = json.loads((now / "manifest.json").read_text())
    assert live["products"]["history"] == "history/index.json"
    assert "history" not in hm["products"]


def test_rolling_keeps_newest_and_rebuilds_restored_rows(tmp_path: Path):
    now = tmp_path / "now"
    t0 = dt.datetime(2026, 11, 14, 0, 17, 0)
    for i in range(10):
        _run(now, t0 + dt.timedelta(hours=3 * i), crit=float(i))
        nh.update(now, keep=8)
    runs = sorted(p.name for p in (now / "history").iterdir() if p.is_dir())
    assert len(runs) == 8 and runs[0] == "20261114T061700Z"
    idx = json.loads((now / "history" / "index.json").read_text())
    assert [r["id"] for r in idx["runs"]] == sorted(runs, reverse=True)
    # rows of restored (earlier) runs are rebuilt from their own files
    assert idx["runs"][1]["critical_km2_any"] == 8.0
    assert idx["runs"][-1]["critical_km2_any"] == 2.0


def test_rerun_same_run_replaces_and_junk_dropped(tmp_path: Path):
    now = tmp_path / "now"
    when = dt.datetime(2026, 11, 15, 18, 17, 0)
    _run(now, when, crit=1.0)
    nh.update(now)
    (now / "history" / "notarun").mkdir()
    (now / "history" / "20261101T000000Z").mkdir()          # unreadable restore
    _run(now, when, crit=9.0)
    idx = nh.update(now)
    assert [r["id"] for r in idx["runs"]] == ["20261115T181700Z"]
    assert idx["runs"][0]["critical_km2_any"] == 9.0
    assert not (now / "history" / "20261101T000000Z").exists()


def test_no_manifest_is_noop(tmp_path: Path):
    now = tmp_path / "now"
    now.mkdir()
    idx = nh.update(now)
    assert idx["runs"] == []
