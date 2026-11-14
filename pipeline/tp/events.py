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


def case_key(event_id: str) -> str:
    """'wa-2025-12' -> 'wa_2025' — the config.yaml case key / data dir name."""
    return "_".join(event_id.split("-")[:2])


def event_data_dir(event_id: str) -> Path:
    d = DATA_DIR / case_key(event_id)
    for sub in ("cog", "geojson", "series"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    return d


def artifact_stem(event_id: str, layer: str, date: str | None = None) -> str:
    """'<caso>_<camada>_<AAAAMMDD>' filename stem (doc v4 file-naming rule).

    <caso> is the config.yaml case key ('wa-2025-12' -> 'wa_2025'); the date
    defaults to the event's reference_date (ISO 'YYYY-MM-DD').
    """
    ymd = (date or load_event(event_id)["reference_date"]).replace("-", "")
    return f"{case_key(event_id)}_{layer}_{ymd}"
