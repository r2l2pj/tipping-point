"""Alert candidates for Modo Agora — stub, no Telegram sending.

Doc "Regras de envio": only when the class upgrades to Alto or Crítico
(considering today and D+1), never between 23h and 6h unless Crítico, at most
one alert per 6h per person (rate limiting lives in the bot, not here).

This emits data/now/alerts.json: the regions that WOULD trigger an alert, each with
a `hold` flag for quiet hours. The related alerts-spec agent owns the real
subscriber loop and message text.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
from scipy.ndimage import label

from .config import ALERT_MIN_CLASS, LABELS, QUIET_HOURS


def _regions(arr: np.ndarray, transform) -> list[dict]:
    lab, n = label(arr)
    out = []
    for i in range(1, n + 1):
        rows, cols = np.where(lab == i)
        r, c = rows.mean(), cols.mean()
        lon, lat = transform * (c + 0.5, r + 0.5)
        out.append({"pixels": int(rows.size), "centroid": [round(float(lon), 4), round(float(lat), 4)]})
    return out


def emit_alerts(prev_cls: np.ndarray | None, cls_today: np.ndarray, cls_d1: np.ndarray,
                now_utc: dt.datetime, transform) -> dict:
    """Compare today's and D+1 worst class per pixel against the previous run.

    prev_cls: worst class array from the previous run (None -> everything that
    is Alto/Crítico now counts as an upgrade).
    """
    worst = np.maximum(cls_today, cls_d1).astype(np.int16)
    prev = np.zeros_like(worst) if prev_cls is None else np.asarray(prev_cls, dtype=np.int16)
    upgraded = (worst >= ALERT_MIN_CLASS) & (worst > prev)
    quiet = now_utc.hour in QUIET_HOURS

    candidates = []
    for cls in (3, 2):
        m = upgraded & (worst == cls)
        if not m.any():
            continue
        for region in _regions(m, transform):
            candidates.append({
                "class": int(cls),
                "label": LABELS[cls],
                "centroid": region["centroid"],
                "pixels": region["pixels"],
                "hold": bool(quiet and cls < 3),
            })
    return {
        "generated_at": now_utc.isoformat().replace("+00:00", "Z"),
        "quiet_hours": quiet,
        "rule": "upgrade to Alto/Crítico today or D+1; quiet 23h-6h unless Crítico",
        "candidates": candidates,
    }
