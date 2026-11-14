"""domains.now + climatology.scale config accessors, and a repo-wide guard
that config.yaml stays the single source of the 'now' bbox literal.

Files still carrying their own copy (fetch defaults, live config, docs and
data artifacts) are owned by sibling units; the guard pins that set so no
NEW hardcoded copy can appear while they migrate to tp.config.now_bounds().
"""
from __future__ import annotations

import itertools
import re
from pathlib import Path

import pytest
from tp import config

REPO_ROOT = Path(__file__).resolve().parents[2]

VALUES = (-58.0, -34.0, -48.0, -22.5)   # west, south, east, north

# Guard scans code/config/doc sources only; data/**, fixtures and generated
# outputs legitimately carry the bounds value in the published contract.
SCAN_SUFFIXES = {".py", ".js", ".ts", ".yml", ".yaml", ".md"}
SKIP_DIRS = {".git", ".venv", "node_modules", ".herdr", "tests", "__pycache__",
             "data", "fixtures", "__fixtures__"}

# Pre-existing copies outside test/report files (see docstring). Each entry
# must disappear as its owning unit migrates; nothing may be ADDED here.
KNOWN_MIRRORS = {
    "pipeline/fetch/geosfp.py",
    "pipeline/fetch/imerg.py",
    "pipeline/fetch/smap.py",
    "docs/PLAN.md",
}


def _num_re(v: float) -> str:
    """'-58.0' matching both '-58.0' and '-58' spellings."""
    return re.escape(str(v)).replace(r"\.0", r"(?:\.0)?")


BBOX_RES = [re.compile(r",\s*".join(_num_re(v) for v in perm))
            for perm in itertools.permutations(VALUES)]


def _files_with_bbox_literal() -> set[str]:
    """Repo-relative files containing the bbox tuple in ANY ordering."""
    found = set()
    for p in REPO_ROOT.rglob("*"):
        rel = p.relative_to(REPO_ROOT)
        if not p.is_file() or p.suffix not in SCAN_SUFFIXES \
                or SKIP_DIRS & set(rel.parts):
            continue
        if rel.name.startswith("test_") or rel.name == "config.yaml":
            continue
        try:
            text = p.read_text(errors="strict")
        except (UnicodeDecodeError, OSError):
            continue
        if any(r.search(text) for r in BBOX_RES):
            found.add(str(rel))
    return found


def test_now_bounds_reads_config_yaml():
    assert config.now_bounds() == VALUES


def test_now_bounds_missing_key_raises(monkeypatch):
    monkeypatch.setattr(config, "load_config", lambda *a, **k: {})
    with pytest.raises(KeyError, match="domains.now"):
        config.now_bounds()
    monkeypatch.setattr(config, "load_config",
                        lambda *a, **k: {"domains": {"now": {"name": "x"}}})
    with pytest.raises(KeyError, match="domains.now"):
        config.now_bounds()


def test_clim_scale_reads_config_yaml():
    assert config.clim_scale() == 10


def test_clim_scale_defaults_to_1(monkeypatch):
    monkeypatch.setattr(config, "load_config", lambda *a, **k: {})
    assert config.clim_scale() == 1


def test_config_yaml_is_the_only_now_bbox_source():
    cfg = (REPO_ROOT / "config.yaml").read_text()
    assert "-58.0, -34.0, -48.0, -22.5" in cfg  # canonical W,S,E,N present
    found = _files_with_bbox_literal()
    new_copies = found - KNOWN_MIRRORS
    assert not new_copies, (
        "hardcoded 'now' bbox outside config.yaml — read "
        f"tp.config.now_bounds() instead: {sorted(new_copies)}")
    assert "gee/now_tpi.py" not in found
