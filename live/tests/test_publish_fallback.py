"""Offline snapshot publishing checks."""
import subprocess
from pathlib import Path

import pytest

from live import publish_fallback as publish


@pytest.fixture(autouse=True)
def no_rsync(monkeypatch):
    monkeypatch.setattr(publish.shutil, "which", lambda name: None)


@pytest.fixture
def snapshot(tmp_path, monkeypatch):
    data = tmp_path / "repo" / "data"
    data.mkdir(parents=True)
    (data / "manifest.json").write_text('{}')
    monkeypatch.setattr(publish, "DATA", data)
    calls = []

    def run(cmd, **kwargs):
        calls.append(cmd)
        code = 1 if "diff" in cmd else 0
        return subprocess.CompletedProcess(cmd, code, stdout="", stderr="")

    monkeypatch.setattr(publish.subprocess, "run", run)
    return data, calls, run


def test_existing_snapshot(snapshot, monkeypatch):
    _, calls, _ = snapshot
    original_sync = publish.sync_data

    def sync(source, target, max_mb):
        skipped = original_sync(source, target, max_mb)
        assert (target / "manifest.json").read_text() == '{}'
        return skipped

    monkeypatch.setattr(publish, "sync_data", sync)
    publish.main([])
    assert calls[0][-3:] == ["fetch", "origin", "fallback"]
    assert any(cmd[-3:] == ["rev-parse", "--verify", "origin/fallback"] for cmd in calls)
    assert any("worktree" in cmd and "add" in cmd and cmd[-1] == "fallback" for cmd in calls)
    assert any("commit" in cmd and "--amend" in cmd and "--reset-author" in cmd for cmd in calls)
    assert any(cmd[-4:] == ["push", "-f", "origin", "fallback"] for cmd in calls)
    assert calls[-1][-4:-1] == ["worktree", "remove", "--force"]


def test_new_orphan(snapshot, monkeypatch):
    _, calls, run = snapshot

    def missing(cmd, **kwargs):
        result = run(cmd, **kwargs)
        if "fetch" in cmd or "rev-parse" in cmd:
            result.returncode = 1
        return result

    monkeypatch.setattr(publish.subprocess, "run", missing)
    publish.main([])
    assert any(cmd[-2:] == ["--detach", "HEAD"] for cmd in calls)
    assert any(cmd[-3:] == ["checkout", "--orphan", "fallback"] for cmd in calls)
    assert any("commit" in cmd and "--amend" not in cmd for cmd in calls)


def test_unchanged(snapshot, monkeypatch, capsys):
    _, calls, run = snapshot

    def unchanged(cmd, **kwargs):
        result = run(cmd, **kwargs)
        result.returncode = 0
        return result

    monkeypatch.setattr(publish.subprocess, "run", unchanged)
    publish.main([])
    assert "no changes" in capsys.readouterr().out
    assert not any("commit" in cmd or "push" in cmd for cmd in calls)
    assert "remove" in calls[-1]


def test_oversized_and_stale_files(tmp_path, capsys):
    """Snapshot semantics (L26): the branch mirrors the working data/ —
    target-only files are pruned, oversized sources are excluded (and
    their stale target copies removed) for release-asset upload."""
    source, target = tmp_path / "source", tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "large.tif").write_bytes(b"x" * 2048)
    (source / "small.tif").write_bytes(b"x")
    (target / "stale.json").write_text('{}')
    (target / "large.tif").write_text('old')
    assert publish.sync_data(source, target, 0.001) == 1
    assert {path.name for path in target.iterdir()} == {"small.tif"}
    assert "WARNING" in capsys.readouterr().out


def test_snapshot_prunes_files_absent_from_source(tmp_path):
    """L26: a file that only exists on the fallback branch but not in the
    publishing data/ is removed — stale artifacts must not survive."""
    source, target = tmp_path / "source", tmp_path / "target"
    (source / "case_a").mkdir(parents=True)
    (source / "case_a" / "assets.json").write_text('[]')
    (target / "now").mkdir(parents=True)
    (target / "now" / "summary.png").write_bytes(b"png")
    publish.sync_data(source, target, 10)
    assert not (target / "now" / "summary.png").exists()
    assert (target / "case_a" / "assets.json").read_text() == '[]'


def test_cleanup_on_commit_failure(snapshot, monkeypatch):
    _, calls, run = snapshot

    def fail(cmd, **kwargs):
        result = run(cmd, **kwargs)
        if "commit" in cmd:
            result.returncode = 1
        return result

    monkeypatch.setattr(publish.subprocess, "run", fail)
    with pytest.raises(SystemExit, match="git commit failed"):
        publish.main([])
    assert "remove" in calls[-1]
    assert not Path(calls[-1][-1]).exists()


def test_rsync_size_exclusions(snapshot, monkeypatch):
    data, calls, _ = snapshot
    (data / "large.tif").write_bytes(b"x" * 2048)
    monkeypatch.setattr(publish.shutil, "which",
                        lambda name: "/usr/bin/rsync" if name == "rsync" else None)
    assert publish.sync_data(data, data.parent / "target", 0.001) == 1
    assert calls[0][0] == "/usr/bin/rsync"
    assert "--delete" in calls[0]
    assert "--delete-excluded" in calls[0]
    assert "--exclude=/large.tif" in calls[0]


def test_dry_run(snapshot):
    _, calls, _ = snapshot
    publish.main(["--dry-run"])
    assert calls == []


def test_static_inputs_never_published(tmp_path):
    from live.publish_fallback import _app_facing
    src = tmp_path / "data"
    assert not _app_facing(src / "static/south_brazil/clim/doy_319.tif", src)
    assert not _app_facing(src / "static/south_brazil/clim/clim_imerg_0p1.tif", src)
    assert not _app_facing(src / "now/static/gpw/gpw_popcount_2020_30sec.tif", src)
    assert _app_facing(src / "now/cog/tpi_slide_today.tif", src)
    assert _app_facing(src / "now/response/flood_nisar.geojson", src)
    assert not _app_facing(src / "now/response/dswx_state.npz", src)
