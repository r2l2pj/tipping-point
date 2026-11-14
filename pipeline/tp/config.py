"""config.yaml access — single source of truth for thresholds and case AOIs.

GEE scripts keep a pasted copy (JS cannot read files); pipeline Python reads
the repo-root config.yaml through this module (cached).
"""
from __future__ import annotations

from functools import cache
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = REPO_ROOT / "config.yaml"


@cache
def load_config(path: str | Path | None = None) -> dict:
    """yaml.safe_load of config.yaml (cached per path)."""
    return yaml.safe_load(Path(path or CONFIG_PATH).read_text())


def thresholds() -> dict:
    """The 'thresholds' block (flood/coherence/slope/HAND/TPI settings)."""
    return load_config()["thresholds"]


def threshold(name: str, default=None):
    """One value from 'thresholds', with a fallback when the key is absent."""
    return thresholds().get(name, default)


def case(case_id: str) -> dict:
    """Per-case dict under 'cases' (aoi, event_date, window days)."""
    cases = load_config()["cases"]
    if case_id not in cases:
        raise KeyError(f"unknown case {case_id!r}; config.yaml cases: {sorted(cases)}")
    return cases[case_id]
