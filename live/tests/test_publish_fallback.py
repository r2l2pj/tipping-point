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


def test_ml_export_ships_to_fallback(tmp_path):
    """lead-ml's export (data/ml/*, data/cog/t_ml.tif) is app-facing: every
    now.yml snapshot must carry it (conductor 2026-11-15)."""
    from live.publish_fallback import _app_facing
    src = tmp_path / "data"
    for rel in ("ml/ml_card.json", "ml/feature_importance_3b.json", "ml/t_ml.json",
                "ml/t_ml.png", "ml/model_metrics.json", "cog/t_ml.tif", "metrics.json"):
        assert _app_facing(src / rel, src), rel


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   text=True)


def _real_repo(tmp_path):
    """origin (bare) with main and an orphan fallback, both holding
    data/manifest.json, data/now/response/flood_nisar.geojson and
    data/wa_2025/scars.geojson; fallback also has data/now/p_old.png."""
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    repo = tmp_path / "repo"
    _git(tmp_path, "clone", "-q", str(origin), str(repo))
    for k, v in (("user.name", "t"), ("user.email", "t@t")):
        _git(repo, "config", k, v)
    files = ["manifest.json", "now/response/flood_nisar.geojson",
             "wa_2025/scars.geojson"]
    for rel in files:
        (repo / "data" / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / "data" / rel).write_text("{}")
    _git(repo, "add", "data")
    _git(repo, "commit", "-q", "-m", "main")
    _git(repo, "push", "-q", "origin", "main")
    _git(repo, "checkout", "-q", "--orphan", "fallback")
    (repo / "data" / "now" / "p_old.png").write_bytes(b"png")
    _git(repo, "add", "data")
    _git(repo, "commit", "-q", "-m", "fb")
    _git(repo, "push", "-q", "origin", "fallback")
    _git(repo, "checkout", "-q", "main")
    return repo


def _fallback_files(repo):
    _git(repo, "fetch", "-q", "origin", "fallback")
    out = subprocess.run(["git", "ls-tree", "-r", "--name-only", "origin/fallback"],
                         cwd=repo, capture_output=True, text=True, check=True)
    return set(out.stdout.split())


def test_guard_refuses_to_drop_paths_on_main(tmp_path, monkeypatch, capsys):
    """Conductor 2026-11-15 (fallback race): a snapshot that would drop an
    app-facing path present on BOTH the current fallback and main HEAD
    fails and publishes nothing; the dropped paths are logged."""
    repo = _real_repo(tmp_path)
    monkeypatch.setattr(publish, "ROOT", repo)
    monkeypatch.setattr(publish, "DATA", repo / "data")
    monkeypatch.delenv("FALLBACK_ALLOW_DROP", raising=False)
    (repo / "data" / "wa_2025" / "scars.geojson").unlink()     # the race
    before = _fallback_files(repo)
    with pytest.raises(SystemExit, match="drop 1 app-facing"):
        publish.main([])
    assert "DROPPED (on fallback and main HEAD): data/wa_2025/scars.geojson" \
        in capsys.readouterr().out
    assert _fallback_files(repo) == before


def test_guard_allows_fallback_only_drops_and_allowlisted(tmp_path, monkeypatch):
    repo = _real_repo(tmp_path)
    monkeypatch.setattr(publish, "ROOT", repo)
    monkeypatch.setattr(publish, "DATA", repo / "data")
    # p_old.png exists only on fallback: dropping it is fine
    publish.main([])
    assert "data/now/p_old.png" not in _fallback_files(repo)
    # an intended removal of a main path needs FALLBACK_ALLOW_DROP
    (repo / "data" / "now" / "response" / "flood_nisar.geojson").unlink()
    monkeypatch.setenv("FALLBACK_ALLOW_DROP", "data/now/response/flood_nisar.geojson")
    publish.main([])
    files = _fallback_files(repo)
    assert "data/now/response/flood_nisar.geojson" not in files
    assert "data/wa_2025/scars.geojson" in files


def test_now_history_carried_over_when_job_has_none(tmp_path):
    """nisar/validation/reference snapshot a checkout without data/now/history:
    the fallback copy survives; the Now job's own history replaces it."""
    source, target = tmp_path / "src", tmp_path / "dst"
    (source / "now").mkdir(parents=True)
    (source / "manifest.json").write_text("{}")
    (target / "now" / "history" / "20261115T191917Z").mkdir(parents=True)
    (target / "now" / "history" / "index.json").write_text('{"runs": [1]}')
    (target / "now" / "history" / "20261115T191917Z" / "manifest.json").write_text("{}")
    publish.sync_data(source, target, 50)
    assert (target / "now" / "history" / "index.json").read_text() == '{"runs": [1]}'
    assert (target / "manifest.json").exists()
    # the Now job writes its own history: that one wins, stale runs go
    (source / "now" / "history").mkdir()
    (source / "now" / "history" / "index.json").write_text('{"runs": [2]}')
    publish.sync_data(source, target, 50)
    assert (target / "now" / "history" / "index.json").read_text() == '{"runs": [2]}'
    assert not (target / "now" / "history" / "20261115T191917Z").exists()


def test_fallback_keep_now_for_non_now_jobs(tmp_path, monkeypatch):
    """nisar/validation/reference set FALLBACK_KEEP=now: their checkout's
    data/now (main's stale copy) never replaces the live Now outputs."""
    source, target = tmp_path / "src", tmp_path / "dst"
    (source / "now").mkdir(parents=True)
    (source / "now" / "manifest.json").write_text('{"stale": true}')
    (source / "wa_2025").mkdir()
    (source / "wa_2025" / "x.json").write_text("new")
    (target / "now" / "nisar_stack" / "png").mkdir(parents=True)
    (target / "now" / "manifest.json").write_text('{"live": true}')
    (target / "now" / "nisar_stack" / "png" / "a.png").write_bytes(b"png")
    monkeypatch.setenv("FALLBACK_KEEP", "now")
    publish.sync_data(source, target, 50)
    assert (target / "now" / "manifest.json").read_text() == '{"live": true}'
    assert (target / "now" / "nisar_stack" / "png" / "a.png").exists()
    assert not (target / "now" / "now").exists()
    assert (target / "wa_2025" / "x.json").read_text() == "new"
