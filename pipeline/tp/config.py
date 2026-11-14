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


def case_id(name: str) -> str:
    """Canonical case id ('rs_2024') from a case id or event id ('rs-2024-05')."""
    cases = load_config()["cases"]
    if name in cases:
        return name
    cand = "_".join(name.split("-")[:2])
    if cand in cases:
        return cand
    raise KeyError(f"unknown case/event {name!r}; config.yaml cases: {sorted(cases)}")


def event_id(name: str) -> str:
    """Event id ('rs-2024-05') for a case id or event id.

    Event ids pass through unchanged; a canonical case id resolves through
    events/*.json ('rs_2024' -> 'rs-2024-05'); unresolvable names come back
    as given (canonical).
    """
    cid = case_id(name)
    if name != cid:
        return name
    stem = cid.replace("_", "-")
    matches = sorted(p.stem for p in (REPO_ROOT / "events").glob(f"{stem}-*.json"))
    return matches[0] if matches else name
