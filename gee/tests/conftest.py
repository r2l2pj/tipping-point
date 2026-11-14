"""Fake `ee` — a recording stand-in installed via monkeypatch.

Every attribute access returns another Fake; every call is appended to the
shared `.log` as (qualified_name, args, kwargs) and returns a child Fake, so
whole Earth-Engine expression chains build without credentials. Tests assert
on the log instead of a live EE graph. Scoped per test (monkeypatch), so the
real `ee` stays in place for the other test suites sharing the process.
"""
from __future__ import annotations

import pytest


class Fake:
    """Recording ee stand-in: attribute -> Fake, call -> log + Fake."""

    def __init__(self, log: list, name: str = "ee"):
        self.log = log
        self.name = name

    def __getattr__(self, attr):
        if attr.startswith("_"):
            raise AttributeError(attr)
        return Fake(self.log, f"{self.name}.{attr}")

    def __call__(self, *args, **kwargs):
        self.log.append((self.name, args, kwargs))
        return Fake(self.log, f"{self.name}()")

    def node(self, name: str) -> Fake:
        """Child Fake sharing this log, named like an operand (e.g. 'rain3')."""
        return Fake(self.log, name)


@pytest.fixture
def fake_ee(monkeypatch):
    """Install the recording fake as `ee` in gee.common and gee.tpi."""
    import gee.common
    import gee.tpi

    fake = Fake([])
    monkeypatch.setattr(gee.common, "ee", fake)
    monkeypatch.setattr(gee.tpi, "ee", fake)
    return fake
