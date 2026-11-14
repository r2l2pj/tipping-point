"""Alert-candidate rules: upgrade to Alto/Crítico on today or D+1; quiet hours
23h-6h hold everything except Crítico."""
import datetime as dt

import numpy as np
from rasterio.transform import from_origin

from live.alerts import emit_alerts

TR = from_origin(-52.0, -29.0, 0.01, 0.01)
NOON = dt.datetime(2026, 11, 14, 12, 0, tzinfo=dt.UTC)
NIGHT = dt.datetime(2026, 11, 14, 2, 0, tzinfo=dt.UTC)


def _classes(fill=0):
    return np.full((10, 10), fill, dtype=np.uint8)


def test_upgrade_to_alto_emits_candidate():
    today = _classes()
    today[3:6, 3:6] = 2
    out = emit_alerts(_classes(), today, _classes(), NOON, TR)
    assert len(out["candidates"]) == 1
    c = out["candidates"][0]
    assert c["class"] == 2 and c["label"] == "Alto" and c["hold"] is False
    assert c["pixels"] == 9


def test_upgrade_only_on_d1_still_emits():
    d1 = _classes()
    d1[0:2, 0:2] = 3
    out = emit_alerts(_classes(), _classes(), d1, NOON, TR)
    assert out["candidates"][0]["class"] == 3


def test_no_upgrade_no_candidates():
    today = _classes()
    today[3:6, 3:6] = 2
    out = emit_alerts(today, today, _classes(), NOON, TR)
    assert out["candidates"] == []


def test_quiet_hours_hold_alto_but_not_critico():
    today = _classes()
    today[1:3, 1:3] = 2
    today[6:9, 6:9] = 3
    out = emit_alerts(None, today, _classes(), NIGHT, TR)
    assert out["quiet_hours"] is True
    by_class = {c["class"]: c for c in out["candidates"]}
    assert by_class[2]["hold"] is True
    assert by_class[3]["hold"] is False
