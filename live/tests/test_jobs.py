"""Offline tests for live/jobs.py — subprocess.run is monkeypatched."""
import datetime as dt
import json
import sys
from types import SimpleNamespace

import pytest

from live import jobs


class _Proc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture
def calls(monkeypatch):
    seen = []
    monkeypatch.setattr(jobs, "_refresh_alerts", lambda *a, **k: None)

    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        return _Proc()
    monkeypatch.setattr(jobs.subprocess, "run", fake_run)
    return seen


def _tokens(cmd):
    """The module/script a step invokes (after '-m' or the tools path)."""
    if "-m" in cmd:
        return cmd[cmd.index("-m") + 1]
    return next(t for t in cmd if t.endswith(".py"))


def test_now_runs_steps_in_locked_order(calls):
    jobs.now()
    assert [_tokens(c) for c in calls] == [
        "pipeline.fetch.imerg",
        "pipeline.fetch.smap",
        "pipeline.fetch.geosfp",
        "live.fetch",
        "tools/now_tpi.py",
        "tools/build_manifest.py",
        "tools/export_fallback.py",
    ]
    w, s, e, n = (str(v) for v in jobs.NOW_BOUNDS)
    imerg = calls[0]
    assert imerg[0] == sys.executable
    assert imerg[imerg.index("--bounds") + 1:imerg.index("--bounds") + 5] == [w, s, e, n]
    assert imerg[imerg.index("--days") + 1] == "7"
    assert calls[2][calls[2].index("--days") + 1] == "3"
    nisar = calls[3]
    assert nisar[nisar.index("nisar-passes") - 2:nisar.index("nisar-passes")] == [
        "-m", "live.fetch"]
    assert nisar[nisar.index("--bounds") + 1:nisar.index("--bounds") + 5] == [w, s, e, n]
    assert nisar[nisar.index("--days") + 1] == str(jobs.NISAR_LOOKBACK_DAYS)
    assert nisar[nisar.index("--out") + 1].endswith("nisar_passes.json")
    assert nisar[nisar.index("--manifest") + 1].endswith("manifest.json")


def test_nisar_passes_cli_never_fails(tmp_path, monkeypatch):
    from live import fetch

    def boom(bounds, days=14):
        raise RuntimeError("asf_search offline")
    monkeypatch.setattr(fetch, "nisar_recent_passes", boom)
    out = tmp_path / "nisar_passes.json"
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"mode": "live", "nisar_passes": [{"old": 1}]}')
    rc = fetch.main(["nisar-passes", "--bounds", "-58", "-34", "-48", "-22.5",
                     "--days", "14", "--out", str(out),
                     "--manifest", str(manifest)])
    assert rc == 0
    assert json.loads(out.read_text()) == []
    assert json.loads(manifest.read_text())["nisar_passes"] == []


def test_nisar_passes_cli_writes_passes(tmp_path, monkeypatch):
    from live import fetch

    monkeypatch.setattr(fetch, "nisar_recent_passes",
                        lambda bounds, days=14: [{"granule": "NISA_g1"}])
    out = tmp_path / "nisar_passes.json"
    rc = fetch.main(["nisar-passes", "--bounds", "-58", "-34", "-48", "-22.5",
                     "--out", str(out)])
    assert rc == 0
    assert json.loads(out.read_text()) == [{"granule": "NISA_g1"}]


def test_now_export_fallback_is_last_after_alerts_and_manifest(monkeypatch):
    order = []
    monkeypatch.setattr(jobs, "_refresh_alerts", lambda *a, **k: order.append("alerts"))

    def fake_run(cmd, **kwargs):
        order.append(_tokens(cmd))
        return _Proc()
    monkeypatch.setattr(jobs.subprocess, "run", fake_run)
    jobs.now()
    assert order == [
        "pipeline.fetch.imerg",
        "pipeline.fetch.smap",
        "pipeline.fetch.geosfp",
        "live.fetch",
        "tools/now_tpi.py",
        "alerts",
        "tools/build_manifest.py",
        "tools/export_fallback.py",
    ]


def test_now_failure_before_alerts_skips_alerts_and_manifest(monkeypatch):
    order = []
    monkeypatch.setattr(jobs, "_refresh_alerts", lambda *a, **k: order.append("alerts"))

    def fake_run(cmd, **kwargs):
        order.append(_tokens(cmd))
        return _Proc(returncode=1 if _tokens(cmd) == "tools/now_tpi.py" else 0)
    monkeypatch.setattr(jobs.subprocess, "run", fake_run)
    with pytest.raises(SystemExit):
        jobs.now()
    assert order[-1] == "tools/now_tpi.py"
    assert "tools/export_fallback.py" not in order
    assert "alerts" not in order and "tools/build_manifest.py" not in order


def test_now_abort_on_nonzero_skips_later_steps(monkeypatch):
    seen = []

    def fake_run(cmd, **kwargs):
        seen.append(cmd)
        return _Proc(returncode=3, stderr="boom")
    monkeypatch.setattr(jobs.subprocess, "run", fake_run)
    with pytest.raises(SystemExit):
        jobs.now()
    assert len(seen) == 1
    assert _tokens(seen[0]) == "pipeline.fetch.imerg"


def test_now_dry_run_spawns_nothing(calls):
    jobs.now(dry_run=True)
    assert calls == []


def test_nisar_alerts_geojson_only_when_file_exists(tmp_path, monkeypatch, calls):
    monkeypatch.setattr(jobs, "ROOT_MANIFEST", tmp_path / "no_manifest.json")
    monkeypatch.setattr(jobs, "ALERTS_GEOJSON", tmp_path / "alerts.json")

    jobs.nisar()
    for cmd in calls:
        assert "--alerts-geojson" not in cmd
    assert [cmd[cmd.index("--case") + 1] for cmd in calls] == jobs.DEFAULT_CASES

    calls.clear()
    jobs.ALERTS_GEOJSON.write_text("{}")
    jobs.nisar()
    for cmd in calls:
        i = cmd.index("--alerts-geojson")
        assert cmd[i + 1] == str(tmp_path / "alerts.json")


def test_nisar_reads_case_keys_from_root_manifest(tmp_path, monkeypatch, calls):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        '{"cases": {"rs_2024": {}, "wa_2025": {}, "xx_2099": {}, "now": {}}}')
    monkeypatch.setattr(jobs, "ROOT_MANIFEST", manifest)
    monkeypatch.setattr(jobs, "ALERTS_GEOJSON", tmp_path / "alerts.json")
    jobs.nisar()
    assert [cmd[cmd.index("--case") + 1] for cmd in calls] == [
        "rs_2024", "wa_2025", "xx_2099"]


def test_validation_no_active_case(monkeypatch, calls, capsys):
    monkeypatch.setattr(jobs, "_event_dates",
                        lambda: {"ev-old": dt.date(2000, 1, 1)})
    jobs.validation(today=dt.date(2026, 11, 14))
    assert "no active case" in capsys.readouterr().out
    assert calls == []


def test_validation_active_case_runs_gee_opera_and_swot(monkeypatch, calls):
    monkeypatch.setattr(
        jobs, "_event_dates",
        lambda: {"zz-2030-01": dt.date(2030, 1, 10),
                 "ev-old": dt.date(2000, 1, 1)})
    monkeypatch.setattr(jobs, "_event_to_case", dict)
    jobs.validation(today=dt.date(2030, 1, 11))
    assert [_tokens(c) for c in calls] == ["gee.opera", "pipeline.fetch.swot"]
    for cmd in calls:
        assert cmd[cmd.index("--case") + 1] == "zz_2030"


def test_validation_window_edges(monkeypatch):
    monkeypatch.setattr(jobs, "_event_dates",
                        lambda: {"ev-x": dt.date(2026, 1, 1)})
    monkeypatch.setattr(jobs, "_event_to_case", dict)
    assert jobs.active_cases(dt.date(2025, 12, 30)) == ["ev_x"]
    assert jobs.active_cases(dt.date(2026, 2, 15)) == ["ev_x"]
    assert jobs.active_cases(dt.date(2025, 12, 29)) == []
    assert jobs.active_cases(dt.date(2026, 2, 16)) == []


def test_reference_kinds_and_cases(calls):
    jobs.reference()
    kinds = [cmd[cmd.index("--kind") + 1] for cmd in calls]
    assert kinds == ["glc", "inventory", "boundaries"]
    case_of = {cmd[cmd.index("--kind") + 1]:
               cmd[cmd.index("--case") + 1] if "--case" in cmd else None
               for cmd in calls}
    assert case_of == {"glc": "wa_2025", "inventory": "rs_2024",
                       "boundaries": None}


def test_main_dispatches_subcommand(monkeypatch):
    seen = []

    def fake_validation(dry_run=False):
        """fake."""
        seen.append(dry_run)
    monkeypatch.setattr(jobs, "validation", fake_validation)
    jobs.main(["validation", "--dry-run"])
    assert seen == [True]


@pytest.mark.parametrize("inject_module", [False, True])
def test_critical_areas_from_assets(monkeypatch, inject_module):
    from live import alerts

    calls = []
    geometry = {"type": "Polygon", "coordinates": []}

    class Image:
        def __init__(self, asset):
            calls.append(("image", asset))

        def gte(self, threshold):
            calls.append(("gte", threshold))
            self.threshold = threshold
            return self

        def selfMask(self):
            return self

        def reduceToVectors(self, **kwargs):
            calls.append(("vectors", kwargs))
            return self

        def getInfo(self):
            return {"features": [{"type": "Feature", "geometry": geometry,
                                  "properties": {"label": 1, "count": 12,
                                                 "threshold": self.threshold}}]}

    ee = SimpleNamespace(Image=Image, Geometry=SimpleNamespace(Rectangle=list))
    monkeypatch.setitem(sys.modules, "ee", ee)
    monkeypatch.setitem(sys.modules, "gee.common", SimpleNamespace(
        ee_init=lambda: calls.append(("init", None))))
    regions = alerts.critical_areas_from_assets(ee if inject_module else None)
    assert [region["kind"] for region in regions] == ["slide"] * 4 + ["flood"] * 4
    assert all(region["geometry"] == geometry for region in regions)
    assert [(region["threshold"], region["class"], region["label"])
            for region in regions] == [(0.6, 3, "Crítico"), (0.4, 2, "Alto")] * 4
    assert [value for op, value in calls if op == "image"] == [
        f"{alerts.ASSET_ROOT}/now/tpi_slide_d0",
        f"{alerts.ASSET_ROOT}/now/tpi_slide_d1",
        f"{alerts.ASSET_ROOT}/now/tpi_flood_d0",
        f"{alerts.ASSET_ROOT}/now/tpi_flood_d1",
    ]
    assert [value for op, value in calls if op == "gte"] == [0.6, 0.4] * 4
    assert [value for op, value in calls if op == "vectors"] == [
        {"scale": 1000, "geometry": list(alerts.NOW_BOUNDS), "maxPixels": 1e9},
    ] * 8


def test_refresh_alerts_reads_assets_not_stale_bundle(tmp_path, monkeypatch):
    from live import alerts

    regions = [{"class": 2, "label": "Alto", "kind": "flood",
                "geometry": {"type": "Polygon", "coordinates": []}}]
    monkeypatch.setattr(alerts, "critical_areas_from_assets", lambda: regions)
    monkeypatch.setattr(alerts, "load_now_regions", lambda *_: pytest.fail("stale bundle"))
    payload = json.loads(jobs._refresh_alerts(tmp_path).read_text())
    assert payload["candidates"] == regions
    assert payload["source"] == "gee-assets"


def test_critical_areas_fallback_initializes_with_temporary_key(monkeypatch):
    from pathlib import Path

    from live import alerts

    seen = {}
    key = {"client_email": "test@example.invalid", "private_key": "test-only"}

    def credentials(email, path):
        seen["email"] = email
        seen["path"] = Path(path)
        assert json.loads(Path(path).read_text()) == key
        return "credentials"

    def initialize(creds, project):
        seen["init"] = (creds, project)

    class EmptyImage:
        def gte(self, _):
            return self

        def selfMask(self):
            return self

        def reduceToVectors(self, **kwargs):
            return self

        def getInfo(self):
            return {"features": []}

    monkeypatch.setitem(sys.modules, "gee.common", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "ee", SimpleNamespace(
        ServiceAccountCredentials=credentials, Initialize=initialize,
        Image=lambda _: EmptyImage(), Geometry=SimpleNamespace(Rectangle=list)))
    monkeypatch.setenv("GCP_SA_KEY", json.dumps(key))
    monkeypatch.setenv("GEE_PROJECT", "test-project")
    assert alerts.critical_areas_from_assets() == []
    assert seen["email"] == key["client_email"]
    assert seen["init"] == ("credentials", "test-project")
    assert not seen["path"].exists()
