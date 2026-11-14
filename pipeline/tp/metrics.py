"""Atomic merge-writer for data/<event>/metrics.json.

Contract (data/SCHEMA.md): flat v4 keys at top level, merged by every
producer — the file is never overwritten and existing keys are never
dropped. Unknown values are written as JSON null with a reason under
"_null_reasons"; dataset id/version records merge under "collections".
Mirrors ml/evaluate.py:update_metrics_json, plus atomicity and null reasons.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def merge_metrics(
    event_id: str,
    flat: dict | None = None,
    reasons: dict | None = None,
    collections: dict | None = None,
    path: Path | None = None,
) -> dict:
    """Merge ``flat`` keys into data/<event_id>/metrics.json; return the merged dict.

    A ``flat`` value of None is written as null and its reason — from
    ``reasons`` or a generic fallback — recorded under the top-level
    "_null_reasons" map; writing a real value later clears the stale reason.
    ``collections`` merges dataset id/version records under "collections".
    "event" is always set to ``event_id``. Atomic: writes <path>.tmp then
    os.replace, so a crash never leaves a partially written file.
    """
    path = Path(path) if path else DATA_DIR / event_id / "metrics.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    metrics = json.loads(path.read_text()) if path.exists() else {}
    metrics["event"] = event_id
    reasons = reasons or {}
    null_reasons = metrics.setdefault("_null_reasons", {})
    for key, value in (flat or {}).items():
        metrics[key] = value
        if value is None:
            null_reasons[key] = reasons.get(key, "not yet computed")
        else:
            null_reasons.pop(key, None)
    if not null_reasons:
        metrics.pop("_null_reasons", None)
    if collections:
        metrics.setdefault("collections", {}).update(collections)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(metrics, indent=2) + "\n")
    os.replace(tmp, path)
    return metrics
