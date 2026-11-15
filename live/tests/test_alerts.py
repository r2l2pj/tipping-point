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


def test_hazard_alerts_tag_and_cluster():
    from live.alerts import emit_hazard_alerts
    flood = _classes()
    flood[0, 0] = flood[0, 2] = flood[2, 1] = 2      # scatter within 3 px
    flood[9, 9] = 3                                   # far away
    slide_d1 = _classes()
    slide_d1[5, 5] = 2
    out = emit_hazard_alerts(
        None, {"landslide": (_classes(), slide_d1), "flood": (flood, _classes())},
        NOON, TR, cluster_px=3)
    cands = out["candidates"]
    assert sorted(c["hazard"] for c in cands) == ["flood", "flood", "landslide"]
    assert cands[0]["class"] == 3 and cands[0]["hazard"] == "flood"  # Crítico first
    scatter = [c for c in cands if c["hazard"] == "flood" and c["class"] == 2]
    assert len(scatter) == 1 and scatter[0]["pixels"] == 3
    slide = [c for c in cands if c["hazard"] == "landslide"][0]
    assert slide["when"] == "d1" and slide["area_km2"] > 0
    assert slide["class_today"] == 0
    w, s, e, n = slide["bbox"]
    assert w < slide["centroid"][0] < e and s < slide["centroid"][1] < n


def test_hazard_alerts_prev_per_hazard_suppresses_repeats():
    from live.alerts import emit_hazard_alerts
    flood = _classes()
    flood[3:6, 3:6] = 2
    out = emit_hazard_alerts({"flood": flood}, {"flood": (flood, _classes())},
                             NOON, TR)
    assert out["candidates"] == []


def test_no_high_or_critical_means_no_tpi_alerts():
    """0 km2 Alto/Crítico (classes <= Moderado everywhere) => 0 TPI alerts."""
    from live.alerts import emit_hazard_alerts
    moderate = _classes(1)
    out = emit_hazard_alerts(None, {"landslide": (moderate, moderate),
                                    "flood": (_classes(), moderate)}, NOON, TR)
    assert out["candidates"] == []


def test_run_checks_pause_alerts():
    from live.run_now import run_checks
    ok = run_checks({"slope_p99_ok": True, "t_slide_ok": True}, 0)
    assert ok["passed"] is True
    assert run_checks({"slope_p99_ok": False, "t_slide_ok": True}, 0)["passed"] is False
    assert run_checks({"slope_p99_ok": True, "t_slide_ok": True}, 3)["passed"] is False
    assert run_checks(None, 0)["passed"] is False      # unchecked terrain


def test_municipality_lookup(tmp_path):
    import json as _json
    from live.now_geojson import municipality_lookup
    sq = [[[-52.0, -30.0], [-51.0, -30.0], [-51.0, -29.0], [-52.0, -29.0], [-52.0, -30.0]]]
    (tmp_path / "municipal_boundaries.geojson").write_text(_json.dumps({
        "type": "FeatureCollection", "features": [{
            "type": "Feature", "geometry": {"type": "Polygon", "coordinates": sq},
            "properties": {"name": "Porto Alegre",
                           "url": "https://servicodados.ibge.gov.br/api/v3/malhas/estados/RS"}}]}))
    where = municipality_lookup(tmp_path)
    assert where(-51.5, -29.5) == {"municipality": "Porto Alegre", "uf": "RS"}
    assert where(-50.95, -29.5)["municipality"] == "Porto Alegre"   # 5 km off: snaps
    assert where(-45.0, -29.5) is None                               # far: none
    assert municipality_lookup(tmp_path / "nope")(-51.5, -29.5) is None


def test_when_follows_the_day_of_the_worst_class():
    """Alto today but Crítico only on D+1 -> class 3, when 'd1'."""
    from live.alerts import emit_hazard_alerts
    today, d1 = _classes(), _classes()
    today[3:6, 3:6] = 2
    d1[3:6, 3:6] = 3
    (c,) = emit_hazard_alerts(None, {"landslide": (today, d1)}, NOON, TR)["candidates"]
    assert c["class"] == 3 and c["class_today"] == 2 and c["when"] == "d1"


def test_persisting_critico_stays_active_without_notification():
    """Conductor 2026-11-15: a Crítico area that persists must stay in the
    public 'active' list (first_issued_at kept) even though no new
    notification is sent for it."""
    from live.alerts import active_alerts, emit_hazard_alerts, keyed_active
    flood = _classes()
    flood[3:6, 3:6] = 3
    layers = {"flood": (flood, _classes())}
    t1 = NOON
    t2 = NOON + dt.timedelta(hours=3)
    # run 1: upgrade -> notification + active
    n1 = emit_hazard_alerts(None, layers, t1, TR)["candidates"]
    a1, _ = active_alerts(layers, t1, TR, None)
    for a in a1:
        a.update({"municipality": "Florianópolis", "uf": "SC"})
    s1 = keyed_active(a1, {}, "2026-11-14T12:00:00Z")
    assert len(n1) == 1 and len(a1) == 1
    # run 2: same classes -> no notification, still active, first_issued kept
    n2 = emit_hazard_alerts({"flood": flood}, layers, t2, TR)["candidates"]
    a2, _ = active_alerts(layers, t2, TR, None)
    for a in a2:
        a.update({"municipality": "Florianópolis", "uf": "SC"})
    s2 = keyed_active(a2, s1, "2026-11-14T15:00:00Z")
    assert n2 == []
    assert len(a2) == 1 and a2[0]["class"] == 3 and a2[0]["hazard"] == "flood"
    assert a2[0]["first_issued_at"] == "2026-11-14T12:00:00Z"
    assert a2[0]["last_confirmed_at"] == "2026-11-14T15:00:00Z"
    assert "hold" not in a2[0]
    # run 3: area gone -> not active, dropped from state
    a3, _ = active_alerts({"flood": (_classes(), _classes())}, t2, TR, None)
    assert a3 == [] and keyed_active(a3, s2, "x") == {}


# --------------------------------------------------------------------------
# alerts.json refresh from the GEE TPI assets (L55: no subscriber store or
# messaging channel any more — Telegram removed).

import json

import pytest

from live import alerts

_REGION = {
    "class": 3,
    "label": "Crítico",
    "kind": "flood",
    "geometry": {
        "type": "Polygon",
        "coordinates": [[[-52.0, -30.5], [-51.0, -30.5],
                         [-51.0, -29.5], [-52.0, -29.5], [-52.0, -30.5]]],
    },
}


@pytest.mark.parametrize("hour,quiet", [(4, True), (23, False), (9, False)])
def test_quiet_hours_use_brasilia(hour, quiet, monkeypatch, tmp_path):
    from live import jobs

    when = NOON.replace(hour=hour)
    out = emit_alerts(None, _classes(2), _classes(), when, TR)
    assert out["quiet_hours"] is quiet
    assert out["candidates"][0]["hold"] is quiet
    _freeze_now(monkeypatch, when)
    monkeypatch.setattr(alerts, "critical_areas_from_assets", list)
    assert json.loads(jobs._refresh_alerts(tmp_path).read_text())["quiet_hours"] is quiet


def _freeze_now(monkeypatch, when):
    class Clock(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return when.astimezone(tz)

    monkeypatch.setattr(alerts.dt, "datetime", Clock)


def test_assets_d0_d1_worst_class(monkeypatch, tmp_path):
    from unittest.mock import Mock

    _freeze_now(monkeypatch, NOON)

    ee = Mock()
    images = []

    def image(asset):
        images.append(asset)
        result = Mock()

        def gte(threshold):
            active = "slide_d0" in asset and threshold == 0.4 or "flood_d1" in asset
            result.selfMask.return_value.reduceToVectors.return_value.getInfo.return_value = {
                "features": [{"geometry": _REGION["geometry"]}] if active else []}
            return result

        result.gte.side_effect = gte
        return result

    ee.Image.side_effect = image
    regions = alerts.critical_areas_from_assets(ee)
    assert len(images) == 4
    assert {asset.rsplit("/", 1)[1] for asset in images} == {
        "tpi_slide_d0", "tpi_slide_d1", "tpi_flood_d0", "tpi_flood_d1"}
    assert regions and max(int(r["class"]) for r in regions) == 3
    assert any(r.get("label") == "Crítico" for r in regions)
