"""Held-out TEST events for the 3b trigger: registry + validation (conductor 2026-11-15).

One RS storm (+ 8 WA reports) cannot rule out overfitting to that storm, so more
post-2015 rain-triggered landslide disasters with public dated inventories are held
out as TEST events. Each one is a ``TestEvent`` in ``test_events.yaml``:

  event_id         short id, also the 3b event/unit id (forced to TEST)
  date             event day D (fixed, from the inventory / official reports —
                   NEVER derived from rain: that is circular)
  trigger_window   [a, b] main triggering days; horizon labels score
                   "a landslide within [a, b]" when dating = fixed
  inventory_window [a, b] days excluded from same-point negative dates
                   (default: trigger_window padded by 7 d)
  dating           fixed     every point gets D (inventory has no per-point date)
                   per_point every point keeps its own date (``date_field``)
  source           local path (repo-relative or absolute) or http(s) URL
  format           geojson | vector (gpkg/shp/kml/zip via geopandas) | csv | arcgis
  enabled          false until the inventory is checked; ML_TEST_EVENTS=<ids>
                   overrides the file (comma list, or "none")

Only geometry (+ the date field) is ever read from an inventory: some layers
carry reporter names/contacts, which are never requested or stored.

This module must not import ``settings`` (settings imports it).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path

FORMATS = ("geojson", "vector", "csv", "arcgis")
DATINGS = ("fixed", "per_point")
INVENTORY_PAD_DAYS = 7


def _date(v) -> dt.date:
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    return dt.date.fromisoformat(str(v))


def _window(v) -> tuple[dt.date, dt.date]:
    a, b = (_date(x) for x in v)
    if b < a:
        raise ValueError(f"window {v} ends before it starts")
    return a, b


@dataclass(frozen=True)
class TestEvent:
    __test__ = False  # not a pytest class

    event_id: str
    name: str
    date: dt.date
    trigger_window: tuple[dt.date, dt.date]
    inventory_window: tuple[dt.date, dt.date]
    dating: str = "fixed"
    source: str | None = None
    format: str = "geojson"
    layer: str | None = None
    date_field: str | None = None
    date_format: str | None = None      # strptime format; None = ISO / epoch-ms auto
    lon_field: str = "lon"
    lat_field: str = "lat"
    where: str = "1=1"                  # arcgis only
    max_points: int = 3000
    country: str = ""
    citation: str = ""
    license: str = ""
    notes: str = ""
    enabled: bool = False
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "TestEvent":
        d = dict(d)
        known = set(cls.__dataclass_fields__) - {"extra"}
        extra = {k: d.pop(k) for k in list(d) if k not in known}
        tw = _window(d.pop("trigger_window"))
        iw = d.pop("inventory_window", None)
        iw = _window(iw) if iw else (tw[0] - dt.timedelta(days=INVENTORY_PAD_DAYS),
                                     tw[1] + dt.timedelta(days=INVENTORY_PAD_DAYS))
        ev = cls(trigger_window=tw, inventory_window=iw, date=_date(d.pop("date")),
                 extra=extra, **d)
        ev.validate()
        return ev

    def validate(self) -> None:
        if not self.event_id or not self.event_id.replace("_", "").isalnum():
            raise ValueError(f"bad event_id {self.event_id!r}")
        if self.dating not in DATINGS:
            raise ValueError(f"{self.event_id}: dating must be one of {DATINGS}")
        if self.format not in FORMATS:
            raise ValueError(f"{self.event_id}: format must be one of {FORMATS}")
        a, b = self.trigger_window
        if not a <= self.date <= b:
            raise ValueError(f"{self.event_id}: date {self.date} outside trigger_window")
        ia, ib = self.inventory_window
        if ia > a or ib < b:
            raise ValueError(f"{self.event_id}: inventory_window must contain trigger_window")
        if self.dating == "per_point" and not self.date_field:
            raise ValueError(f"{self.event_id}: dating=per_point needs date_field")
        if self.format == "csv" and not (self.lon_field and self.lat_field):
            raise ValueError(f"{self.event_id}: csv needs lon_field/lat_field")

    @property
    def leak_window(self) -> tuple[dt.date, dt.date]:
        """Days whose GLC points may be this event (inventory window)."""
        return self.inventory_window


def load_registry(path: Path) -> dict[str, TestEvent]:
    import yaml

    path = Path(path)
    """``path`` (test_events.yaml) + every ``<stem>.d/*.yaml`` next to it (one file per
    event, so parallel workers never edit the same file)."""
    items: list[dict] = []
    files = ([path] if path.exists() else []) + sorted(path.with_suffix(".d").glob("*.yaml"))
    for f in files:
        raw = yaml.safe_load(f.read_text()) or {}
        items += raw.get("events") or []
    out: dict[str, TestEvent] = {}
    for d in items:
        ev = TestEvent.from_dict(d)
        if ev.event_id in out:
            raise ValueError(f"duplicate event_id {ev.event_id}")
        out[ev.event_id] = ev
    return out


def select(registry: dict[str, TestEvent], override: str | None) -> list[TestEvent]:
    """Enabled events; ``override`` (env ML_TEST_EVENTS) = comma ids or 'none'."""
    if override is None or override.strip() == "":
        return [e for e in registry.values() if e.enabled]
    if override.strip().lower() == "none":
        return []
    ids = [s.strip() for s in override.split(",") if s.strip()]
    missing = [i for i in ids if i not in registry]
    if missing:
        raise KeyError(f"ML_TEST_EVENTS: unknown event ids {missing} (see test_events.yaml)")
    return [registry[i] for i in ids]
