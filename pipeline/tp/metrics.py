"""Atomic merge-writer for the root data/metrics.json.

Contract (L4 ruling): ONE root data/metrics.json keyed by canonical case
id: {"cases": {"<case_id>": {"event": "<event-id>", ...flat v4 keys}}}.
Every producer merges — the file is never overwritten, existing keys are
never dropped, and a written non-null value is never clobbered by null.
Unknown values are written as JSON null with a reason under the case
blob's "_null_reasons"; dataset id/version records merge under its
"collections". Mirrors ml/evaluate.py:update_metrics_json.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from tp.config import case_id, event_id

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def merge_metrics(
    event_or_case: str,
    flat: dict | None = None,
    reasons: dict | None = None,
    collections: dict | None = None,
    path: Path | None = None,
) -> dict:
    """Merge ``flat`` keys into the case blob of data/metrics.json.

    ``event_or_case`` accepts an event id ('rs-2024-05') or a case id
    ('rs_2024'); the case blob's "event" holds the event id. A ``flat``
    value of None is written null (unless a non-null value already exists —
    that is never clobbered) and its reason — from ``reasons`` or a generic
    fallback — recorded under "_null_reasons"; writing a real value clears
    the stale reason. ``collections`` merges dataset id/version records
    under "collections". Returns the merged root dict. Atomic: writes
    <path>.tmp then os.replace, so a crash never leaves a partial file.
    """
    path = Path(path) if path else DATA_DIR / "metrics.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    metrics = json.loads(path.read_text()) if path.exists() else {}
    blob = metrics.setdefault("cases", {}).setdefault(case_id(event_or_case), {})
    blob["event"] = event_id(event_or_case)
    reasons = reasons or {}
    null_reasons = blob.setdefault("_null_reasons", {})
    for key, value in (flat or {}).items():
        if value is None:
            if blob.get(key) is None:
                blob[key] = None
                null_reasons[key] = reasons.get(key, "not yet computed")
        else:
            blob[key] = value
            null_reasons.pop(key, None)
    if not null_reasons:
        blob.pop("_null_reasons", None)
    if collections:
        blob.setdefault("collections", {}).update(collections)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(metrics, indent=2) + "\n")
    os.replace(tmp, path)
    return metrics
