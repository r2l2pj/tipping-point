"""tp.sqlite_store — builds data/tipping_point.sqlite from a synthetic data tree."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from tp import sqlite_store as ss

SQ = {"type": "Polygon", "coordinates": [[[-51, -30], [-50.99, -30], [-50.99, -29.99], [-51, -29.99], [-51, -30]]]}


def _w(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj))


def _fc(*props):
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": SQ, "properties": p} for p in props]}


def _tree(root: Path) -> Path:
    d = root / "data"
    _w(d / "manifest.json", {"updated_at": "2026-11-15T05:00:00Z", "cases": {}})
    _w(d / "events.json", {"events": [{"id": "rs-2024-05", "title": "RS", "reference_date": "2024-05-02",
                                       "bbox": [-52.5, -30.5, -50.5, -29.0]}]})
    _w(d / "metrics.json", {"cases": {"rs_2024": {
        "critical_km2": 430.1, "people_critical": 12, "landslide_hit_pct": 85.9,
        "flood_source": None, "landslide_hit": {"n_points": 3}, "ok": True,
        "_null_reasons": {"flood_source": "no NISAR"}}}})
    _w(d / "rs_2024/series/imerg_smap_daily.json", {"days": [
        {"date": "2024-05-01", "offset": -1, "kind": "observed", "imerg_mm": 190.1, "smap": 0.9,
         "river_m": None, "gfs_issued": "2024-04-30"}]})
    _w(d / "now/manifest.json", {"updated_at": "2026-11-15T05:13:01Z", "verified": False,
                                 "data_dates": {"imerg": "2026-11-14T00Z"},
                                 "nisar_passes": [{"granule": "G1", "acquired": "2026-11-06T21:16:03Z",
                                                   "path": 24, "direction": "D"}]})
    _w(d / "now/summary.json", {"critical_km2": {"slide": 1.5, "flood": 2.0}, "people_exposed": 552,
                                "response": {"flooded_km2": 9.9, "schema": "x"}})
    _w(d / "now/geojson/priority_zones.geojson", _fc(
        {"name": "Avaré", "code": "3504503", "uf": "SP", "hazard": "flood", "area_km2": 0.5,
         "people": 269, "class": "High", "rank": 1}))
    _w(d / "now/response/flood_dswx.geojson", _fc(
        {"source": "opera-dswx-s1", "area_km2": 0.64, "people": 1.3, "confidence": "standard"},
        {"source": "opera-dswx-s1", "area_km2": 0.2, "people": 0.5, "confidence": "lower"}))
    _w(d / "now/response/landslide_dist.geojson", _fc(
        {"kind": "landslide_candidate", "status": "candidate", "area_km2": 0.01, "people": 0.2,
         "municipality": "Bento Gonçalves", "date": "2026-10-20"}))
    return d


def test_build_tables_and_values(tmp_path):
    d = _tree(tmp_path)
    counts = ss.build(d)
    db = d / "tipping_point.sqlite"
    assert db.is_file() and counts["bytes"] == db.stat().st_size < 50 * 1024 * 1024
    con = sqlite3.connect(db)
    q = lambda sql, *a: con.execute(sql, a).fetchall()  # noqa: E731
    assert q("SELECT event_id, case_id, west FROM events") == [("rs-2024-05", "rs_2024", -52.5)]
    assert q("SELECT value, unit FROM metrics WHERE case_id='rs_2024' AND key='critical_km2'") == [(430.1, "km2")]
    assert q("SELECT value, unit FROM metrics WHERE key='people_critical'") == [(12, "persons")]
    assert q("SELECT value FROM metrics WHERE key='ok'") == [(1,)]
    assert q("SELECT value FROM metrics WHERE key='flood_source'") == [(None,)]
    assert q("SELECT value FROM metrics WHERE key='_null_reasons.flood_source'") == [("no NISAR",)]
    assert json.loads(q("SELECT value FROM metrics WHERE key='landslide_hit'")[0][0]) == {"n_points": 3}
    assert q("SELECT value FROM metrics WHERE case_id='now' AND key='critical_km2.flood'") == [(2.0,)]
    assert q("SELECT value FROM metrics WHERE case_id='now' AND key='response.flooded_km2'") == [(9.9,)]
    assert sorted(q("SELECT var, value FROM series")) == [("imerg_mm", 190.1), ("smap", 0.9)]
    assert q("SELECT run_id, kind, verified FROM runs WHERE kind='now'") == [("2026-11-15T05:13:01Z", "now", 0)]
    assert q("SELECT granule, path FROM nisar_passes") == [("G1", 24)]
    assert q("SELECT municipality, uf, people FROM zones") == [("Avaré", "SP", 269.0)]
    rows = q("SELECT source, hazard, confidence, municipality FROM response_candidates ORDER BY area_km2")
    assert rows == [("opera-dist-alert-hls", "landslide", "standard", "Bento Gonçalves"),
                    ("opera-dswx-s1", "flood", "lower", None), ("opera-dswx-s1", "flood", "standard", None)]
    lon, lat = q("SELECT lon, lat FROM response_candidates LIMIT 1")[0]
    assert -51 <= lon <= -50.99 and -30 <= lat <= -29.99


def test_missing_inputs_are_noted_and_rebuild_is_idempotent(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    c1 = ss.build(d)
    assert c1["metrics"] == 0 and c1["events"] == 0
    con = sqlite3.connect(d / "tipping_point.sqlite")
    items = {r[0] for r in con.execute("SELECT item FROM build_notes")}
    con.close()
    assert {"events", "metrics", "runs.now"} <= items
    c2 = ss.build(d)                                   # rebuild replaces, never appends
    assert c2["metrics"] == 0 and not (d / "tipping_point.sqlite.tmp").exists()


def test_units():
    assert ss.unit_for("flooded_km2") == "km2" and ss.unit_for("hit_500m_pct") == "%"
    assert ss.unit_for("lead_time_median_h") == "h" and ss.unit_for("people_flooded") == "persons"
    assert ss.unit_for("scar_people") == "persons" and ss.unit_for("flood_source") is None


def test_runs_keep_one_row_per_now_run(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    (d / "now").mkdir()

    def run(ts):
        (d / "now" / "manifest.json").write_text(json.dumps(
            {"updated_at": ts, "verified": True, "data_dates": {"imerg": ts[:10]}}))
        return ss.build(d)

    run("2026-11-15T08:00:00Z")
    run("2026-11-15T11:00:00Z")
    c = run("2026-11-15T11:00:00Z")                      # same run rebuilt: no duplicate
    con = sqlite3.connect(d / "tipping_point.sqlite")
    ids = [r[0] for r in con.execute("SELECT run_id FROM runs WHERE kind='now' ORDER BY run_id")]
    con.close()
    assert ids == ["2026-11-15T08:00:00Z", "2026-11-15T11:00:00Z"]
    assert c["runs_carried"] == 2
