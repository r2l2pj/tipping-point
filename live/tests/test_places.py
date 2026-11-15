"""live/places.py — per-municipality summary (hermetic, tiny synthetic grid)."""
import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

from live import places as lp


def test_group_max_and_quantile():
    lab = np.array([1, 1, 1, 2, 0, 2])
    val = np.array([0.1, 0.5, 0.3, np.nan, 9.0, 0.2])
    mx = lp.group_max(lab, val, 2)
    assert mx[1] == 0.5 and mx[2] == 0.2 and np.isnan(lp.group_max(lab, val, 3)[3])
    q = lp.group_quantile(lab, val, 2, 0.5)
    assert q[1] == 0.3 and q[2] == 0.2


def _box(w, s, e, n):
    return {"type": "Polygon", "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}


def test_build_end_to_end(tmp_path: Path):
    now = tmp_path / "now"
    (now / "geojson").mkdir(parents=True)
    (now / "static" / "gpw").mkdir(parents=True)
    bounds = (-50.0, -30.0, -49.9, -29.9)            # 10 x 10 cells at 0.01 deg
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": _box(-50.0, -30.0, -49.95, -29.9),
         "properties": {"code": "1", "name": "Oeste", "uf": "RS"}},
        {"type": "Feature", "geometry": _box(-49.95, -30.0, -49.9, -29.9),
         "properties": {"code": "2", "name": "Leste", "uf": "RS"}}]}
    (now / "geojson" / "municipal_boundaries.geojson").write_text(json.dumps(fc))
    slide = np.zeros((4, 10, 10), np.float16)
    flood = np.zeros((4, 10, 10), np.float16)
    flood[0, :, 5:] = 0.7                               # east half Crítico today
    np.savez(now / "places_tpi.npz", slide=slide, flood=flood,
             dates=np.array(["2026-11-15", "2026-11-16", "2026-11-17", "2026-11-18"]),
             bounds=np.array(bounds))
    gpw = np.full((12, 12), 10.0, np.float32)          # 30" cells, 10 people each
    with rasterio.open(now / "static" / "gpw" / "gpw_popcount_2020_30sec.tif", "w", driver="GTiff",
                       height=12, width=12, count=1, dtype="float32", crs="EPSG:4326",
                       transform=from_origin(-50.0, -29.9, 1 / 120, 1 / 120)) as dst:
        dst.write(gpw, 1)
    (now / "alerts.json").write_text(json.dumps({"active": [
        {"municipality": "Leste", "uf": "RS", "label": "Crítico", "hazard": "flood", "class": 3}]}))
    out = lp.build(now)
    P = {p["name"]: p for p in out["places"]}
    assert P["Leste"]["flood"]["cls"][0] == 3 and P["Oeste"]["flood"]["cls"][0] == 0
    assert P["Leste"]["alert"] and P["Leste"]["alert_level"] == "Crítico" and not P["Oeste"]["alert"]
    assert P["Oeste"]["pop"] + P["Leste"]["pop"] == 1440
    assert P["Leste"]["people_high"] > 0 and P["Oeste"]["people_high"] == 0
    assert out["dates"]["d3"] == "2026-11-18" and P["Leste"]["p_trigger"]["24h"] is None
