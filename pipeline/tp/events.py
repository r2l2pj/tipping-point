"""Load event configs from events/*.json (single source of truth for AOIs/dates)."""
from __future__ import annotations

import json
from pathlib import Path

EVENTS_DIR = Path(__file__).resolve().parents[2] / "events"
DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def load_event(event_id: str) -> dict:
    path = EVENTS_DIR / f"{event_id}.json"
    return json.loads(path.read_text())


def list_events() -> list[str]:
    return sorted(p.stem for p in EVENTS_DIR.glob("*.json"))


def event_data_dir(event_id: str) -> Path:
    d = DATA_DIR / event_id
    for sub in ("cog", "geojson", "series"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    return d
