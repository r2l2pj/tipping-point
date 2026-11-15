"""Repo-wide pytest guard: tests must never write the published data/ tree.

data/ holds the numbers the app and the submission quote (metrics.json,
manifests, COGs). Tests write to tmp_path only. This snapshots (size,
mtime_ns) of every file under data/ at session start and fails the run if
any file was created, changed or deleted by the time the session ends.
"""
from __future__ import annotations

from pathlib import Path

import pytest

DATA = Path(__file__).resolve().parent / "data"


def _snapshot() -> dict[str, tuple[int, int]]:
    if not DATA.is_dir():
        return {}
    out = {}
    for p in DATA.rglob("*"):
        if p.is_file():
            st = p.stat()
            out[p.relative_to(DATA).as_posix()] = (st.st_size, st.st_mtime_ns)
    return out


def pytest_sessionstart(session: pytest.Session) -> None:
    session.config._data_snapshot = _snapshot()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    before = getattr(session.config, "_data_snapshot", None)
    if before is None:
        return
    after = _snapshot()
    changed = sorted(k for k in before.keys() & after.keys() if before[k] != after[k])
    added = sorted(after.keys() - before.keys())
    removed = sorted(before.keys() - after.keys())
    if changed or added or removed:
        tr = session.config.pluginmanager.get_plugin("terminalreporter")
        msg = ("tests modified the published data/ tree (write to tmp_path only): "
               f"changed={changed[:10]} added={added[:10]} removed={removed[:10]}")
        if tr:
            tr.write_line("ERROR: " + msg, red=True)
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
