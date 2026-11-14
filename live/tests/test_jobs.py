"""Offline tests for live/jobs.py — subprocess.run is monkeypatched."""
import datetime as dt
import sys

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
        "tools/now_tpi.py",
        "tools/export_fallback.py",
        "tools/build_manifest.py",
    ]
    w, s, e, n = (str(v) for v in jobs.NOW_BOUNDS)
    imerg = calls[0]
    assert imerg[0] == sys.executable
    assert imerg[imerg.index("--bounds") + 1:imerg.index("--bounds") + 5] == [w, s, e, n]
    assert imerg[imerg.index("--days") + 1] == "7"
    assert calls[2][calls[2].index("--days") + 1] == "3"


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


def test_validation_active_case_runs_opera_and_swot(monkeypatch, calls):
    monkeypatch.setattr(
        jobs, "_event_dates",
        lambda: {"zz-2030-01": dt.date(2030, 1, 10),
                 "ev-old": dt.date(2000, 1, 1)})
    monkeypatch.setattr(jobs, "_event_to_case", dict)
    jobs.validation(today=dt.date(2030, 1, 11))
    assert [_tokens(c) for c in calls] == [
        "pipeline.fetch.opera", "pipeline.fetch.swot"]
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
