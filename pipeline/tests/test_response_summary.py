"""Unit R3 — response_summary: union of flood + Critical TPI, GPW people,
municipality priority zones, summary merge, manifest node. Hermetic: tiny
synthetic GeoJSON + a synthetic GPW raster; boundaries are injected.

Synthetic world (bbox 0,0,4,4; res 0.25° -> 16x16 px; GPW 0.5° cells = 100
persons -> 25 people per analysis px):

* flood_dswx square [1.0..2.0]^2          -> 16 px  (cols 4-7, rows 8-11)
* flood_nisar [1.5..2.5]x[1.0..2.0]       -> 16 px  (cols 6-9, rows 8-11)
    union = 24 px (cols 4-9) -> 600 people; overlap 8 px -> 200 people
* critical class 3 (Crítico, kind flood) [3.0..4.0]^2 -> 16 px, 400 p
* critical class 2 (Alto, kind slide)    [0.0..0.25]x[3.0..4.0]
    -> 4 px (col 0, rows 0-3), 100 people
* municipalities: A = x<2.5, B = x>=2.5 (split on the px boundary x=2.5)

people_flooded = 600, people_critical = 400 (Crítico only, A8), people_alert = 500,
people_exposed = 1100.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from tp import response_summary as rs

BBOX = (0.0, 0.0, 4.0, 4.0)
RES = 0.25


def _square(w, s, e, n):
    return {"type": "Polygon",
            "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]}


def _fc(features):
    return {"type": "FeatureCollection", "features": features}


def _feat(geom, **props):
    return {"type": "Feature", "geometry": geom, "properties": props}


def _write(path: Path, obj: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj))
    return path


def _write_gpw(path: Path, cell_people: float = 100.0) -> Path:
    arr = np.full((8, 8), cell_people, np.float32)
    with rasterio.open(path, "w", driver="GTiff", height=8, width=8, count=1,
                       dtype="float32", crs="EPSG:4326",
                       transform=from_origin(0.0, 4.0, 0.5, 0.5),
                       nodata=float("nan")) as dst:
        dst.write(arr, 1)
    return path


DSWX_FC = _fc([_feat(_square(1.0, 1.0, 2.0, 2.0),
                     source="opera-dswx-s1", status="candidate", change="new",
                     area_km2=1.0, people=400)])
NISAR_FC = _fc([_feat(_square(1.5, 1.0, 2.5, 2.0),
                      source="nisar-gcov", status="candidate", change="new",
                      area_km2=1.0, people=400)])
CRIT_FC = _fc([
    _feat(_square(3.0, 3.0, 4.0, 4.0),
          kind="flood", day="2026-11-15", **{"class": 3}, label="Crítico", pixels=10),
    _feat(_square(0.0, 3.0, 0.25, 4.0),
          kind="slide", day="2026-11-15", **{"class": 2}, label="Alto", pixels=4),
])
MUNIS_FC = _fc([
    _feat(_square(0.0, 0.0, 2.5, 4.0), name="Alpha", code="4300001", uf="RS"),
    _feat(_square(2.5, 0.0, 4.0, 4.0), name="Beta", code="4300002", uf="RS"),
])

DSWX_META = {"source": "opera-dswx-s1", "latest_acquired": "2026-11-14T09:06:12Z",
             "latency_h": 6.8, "flooded_km2": 1544.1, "new_km2": 12.0,
             "receded_km2": 0.0, "people_flooded": 9029.1}
NISAR_META = {"source": "nisar-gcov", "latest_acquired": "2026-11-06T21:16:03Z",
              "latency_h": 99.7, "flooded_km2": 12.06, "people_flooded": 313,
              "pairs": [{"pre": "G_PRE_001", "post": "G_POST_001",
                         "pre_acquired": "2026-10-25T21:16:03Z",
                         "post_acquired": "2026-11-06T21:16:03Z",
                         "track": 24, "look": "D", "frame": 101}]}


def _seed(now_dir: Path, *, dswx=DSWX_FC, nisar=NISAR_FC, crit=CRIT_FC,
          dswx_meta=DSWX_META, nisar_meta=NISAR_META, summary=None) -> Path:
    resp = now_dir / "response"
    if dswx is not None:
        _write(resp / "flood_dswx.geojson", dswx)
    if dswx_meta is not None:
        _write(resp / "dswx_meta.json", dswx_meta)
    if nisar is not None:
        _write(resp / "flood_nisar.geojson", nisar)
    if nisar_meta is not None:
        _write(resp / "nisar_meta.json", nisar_meta)
    if crit is not None:
        _write(now_dir / "geojson" / "critical_areas.geojson", crit)
    _write(now_dir / "summary.json", summary or {
        "generated_at": "2026-11-15T03:49:40Z",
        "critical_km2": {"slide": 0.0, "flood": 1225.0, "any": 1915.1},
        "people_exposed": None, "people_critical": None,
        "forecast_source": "noaa-gfs", "imerg_source": "gesdisc-early",
        "classes_km2": {"Baixo": 1.0, "Moderado": 0.0, "Alto": 0.0, "Crítico": 0.0},
        "data_dates": {"imerg": "2026-11-14T00Z", "gfs_issued": "2026-11-14T18:00:00Z"},
    })
    return now_dir


def _run(now_dir: Path, gpw: Path | None = None, boundaries=MUNIS_FC):
    return rs.run_summary(
        BBOX, now_dir,
        load_boundaries=(lambda bb: boundaries) if boundaries is not None else None,
        gpw_src=str(gpw) if gpw else None, res=RES, log=lambda m: None)


# ---------------------------------------------------------------------------
def test_happy_path(tmp_path: Path):
    gpw = _write_gpw(tmp_path / "gpw.tif")
    now_dir = _seed(tmp_path / "now")
    s = _run(now_dir, gpw)

    from shapely.geometry import box
    # same vertex set as the module's union (geodesic edges: extra vertices on
    # a parallel change the area), so build it the same way
    flood = rs.geodesic_km2(rs.union_of([box(1.0, 1.0, 2.0, 2.0), box(1.5, 1.0, 2.5, 2.0)]))
    crit = rs.geodesic_km2(box(3.0, 3.0, 4.0, 4.0)) + rs.geodesic_km2(box(0.0, 3.0, 0.25, 4.0))
    overlap = rs.geodesic_km2(box(1.5, 1.0, 2.0, 2.0))
    assert s["people_flooded"] == 600          # deduped union (400+400-200)
    assert s["people_critical"] == 400         # A8: Crítico only
    assert s["people_alert"] == 500            # 400 Crítico + 100 Alto
    assert s["people_exposed"] == 1100
    assert s["flooded_km2"] == pytest.approx(flood, abs=0.01)
    assert s["critical_km2"] == pytest.approx(rs.geodesic_km2(box(3.0, 3.0, 4.0, 4.0)), abs=0.01)
    assert s["alert_km2"] == pytest.approx(crit, abs=0.01)
    assert s["exposed_km2"] == pytest.approx(flood + crit, abs=0.01)   # disjoint
    assert s["overlap_km2"] == pytest.approx(overlap, abs=0.01)        # dswx vs nisar
    assert s["nisar"]["pre_granule"] == "G_PRE_001"
    assert not str(s["people_source"]).startswith("/")    # no local absolute path
    assert s["sources"]["opera-dswx-s1"]["latest_acquired"] == "2026-11-14T09:06:12Z"
    assert s["sources"]["nisar-gcov"]["latency_h"] == 99.7
    assert s["sources"]["tpi-critical"]["classes"] == {"Crítico": 1, "Alto": 1}
    assert s["_null_reasons"] == {}

    zones = json.loads((now_dir / "geojson" / "priority_zones.geojson").read_text())
    assert [z["properties"]["name"] for z in zones["features"]] == ["Alpha", "Beta"]
    a = zones["features"][0]["properties"]
    assert a["rank"] == 1 and a["people"] == 700 and a["status"] == "candidate"
    assert a["class"] == "High" and a["tpi"] == 0.4
    assert a["hazard"] == "flood+landslide"
    assert a["sources"] == ["opera-dswx-s1", "nisar-gcov", "tpi-critical"]
    assert a["people_flooded"] == 600 and a["people_critical"] == 0
    assert a["people_alert"] == 100 and a["critical_km2"] == 0.0 and a["alert_km2"] > 0
    assert a["uf"] == "RS" and a["code"] == "4300001"
    assert isinstance(a["label_lon"], float) and isinstance(a["label_lat"], float)
    b = zones["features"][1]["properties"]
    assert b["rank"] == 2 and b["class"] == "Critical" and b["tpi"] == 0.6
    assert b["hazard"] == "flood" and b["sources"] == ["tpi-critical"]
    assert b["people"] == 400 and b["people_flooded"] == 0
    # response copy is identical
    resp_zones = json.loads((now_dir / "response" / "priority_zones.geojson").read_text())
    assert resp_zones == zones

    merged = json.loads((now_dir / "summary.json").read_text())
    assert merged["forecast_source"] == "noaa-gfs"          # preserved
    assert merged["critical_km2"]["any"] == 1915.1          # preserved
    assert merged["data_dates"]["imerg"] == "2026-11-14T00Z"  # preserved
    assert merged["people_critical"] == 400
    assert merged["people_alert"] == 500
    assert merged["people_exposed"] == 1100
    assert merged["data_dates"]["nisar_pre"] == "2026-10-25"
    assert merged["data_dates"]["nisar_post"] == "2026-11-06"
    assert merged["data_dates"]["opera-dswx-s1"] == "2026-11-14T09:06:12Z"
    assert merged["response"]["people_exposed"] == 1100

    node = json.loads((now_dir / "response" / "manifest_node.json").read_text())
    assert node["data_dates"]["nisar_pre"] == "2026-10-25"
    assert node["data_dates"]["nisar_post"] == "2026-11-06"
    assert node["products"]["priority_zones"] == "geojson/priority_zones.geojson"
    assert node["products"]["flood_dswx"] == "response/flood_dswx.geojson"
    assert node["products"]["flood_nisar"] == "response/flood_nisar.geojson"
    assert node["response"]["nisar"]["post_granule"] == "G_POST_001"


def test_missing_all_inputs(tmp_path: Path):
    gpw = _write_gpw(tmp_path / "gpw.tif")
    now_dir = _seed(tmp_path / "now", dswx=None, nisar=None, crit=None,
                    dswx_meta=None, nisar_meta=None)
    s = _run(now_dir, gpw)
    assert s["people_flooded"] is None
    assert s["people_critical"] is None
    assert s["people_exposed"] is None
    assert s["flooded_km2"] is None and s["critical_km2"] is None
    assert s["overlap_km2"] is None
    assert s["nisar"] is None
    assert set(s["_null_reasons"]) >= {"flood_dswx", "flood_nisar",
                                       "critical_areas", "people_flooded"}
    zones = json.loads((now_dir / "geojson" / "priority_zones.geojson").read_text())
    assert zones["features"] == []
    merged = json.loads((now_dir / "summary.json").read_text())
    assert merged["people_exposed"] is None
    assert merged["forecast_source"] == "noaa-gfs"
    node = json.loads((now_dir / "response" / "manifest_node.json").read_text())
    assert node["products"]["flood_dswx"] is None
    assert "nisar_pre" not in node["data_dates"]
    assert "flood_dswx" in node["_null_reasons"]


def test_single_source_and_empty_fc(tmp_path: Path):
    gpw = _write_gpw(tmp_path / "gpw.tif")
    now_dir = _seed(tmp_path / "now", nisar=None, nisar_meta=None,
                    crit=None, dswx_meta=DSWX_META)
    s = _run(now_dir, gpw)
    assert s["people_flooded"] == 400          # dswx only, no dedup partner
    assert s["people_critical"] is None
    assert s["people_exposed"] == 400
    assert s["overlap_km2"] is None

    # present-but-empty flood file: honest 0, not null
    now_dir2 = _seed(tmp_path / "now2", nisar=_fc([]), nisar_meta=None, crit=None)
    s2 = _run(now_dir2, gpw)
    assert s2["people_flooded"] == 400
    assert s2["sources"]["nisar-gcov"]["status"] == "empty"
    assert s2["sources"]["nisar-gcov"]["features"] == 0


def test_gpw_missing(tmp_path: Path):
    now_dir = _seed(tmp_path / "now")
    s = rs.run_summary(BBOX, now_dir,
                       load_boundaries=lambda bb: MUNIS_FC,
                       gpw_src=str(tmp_path / "absent.tif"), res=RES,
                       log=lambda m: None)
    assert s["people_flooded"] is None
    assert s["people_critical"] is None
    assert s["people_exposed"] is None
    assert "people" in s["_null_reasons"]
    assert s["flooded_km2"] is not None        # areas still computed
    zones = json.loads((now_dir / "geojson" / "priority_zones.geojson").read_text())
    assert len(zones["features"]) == 2
    assert zones["features"][0]["properties"]["people"] is None
    # rank falls back to exposed area (Alpha still first: 28 px > 16 px)
    assert zones["features"][0]["properties"]["name"] == "Alpha"


def test_no_boundaries_and_fetch_failure(tmp_path: Path):
    gpw = _write_gpw(tmp_path / "gpw.tif")
    now_dir = _seed(tmp_path / "now")
    s = rs.run_summary(BBOX, now_dir, load_boundaries=None,
                       gpw_src=str(gpw), res=RES, log=lambda m: None)
    assert "priority_zones" in s["_null_reasons"]
    zones = json.loads((now_dir / "geojson" / "priority_zones.geojson").read_text())
    assert zones["features"] == []

    now_dir2 = _seed(tmp_path / "now2")

    def _boom(bb):
        raise OSError("ibge down")

    s2 = rs.run_summary(BBOX, now_dir2, load_boundaries=_boom,
                        gpw_src=str(gpw), res=RES, log=lambda m: None)
    assert "ibge down" in s2["_null_reasons"]["priority_zones"]
    assert json.loads((now_dir2 / "geojson" / "priority_zones.geojson")
                      .read_text())["features"] == []


def test_summary_merge_keeps_existing_people_when_null(tmp_path: Path):
    """A real people_critical already in summary.json is not clobbered by null."""
    gpw = _write_gpw(tmp_path / "gpw.tif")
    now_dir = _seed(tmp_path / "now", crit=None)
    base = json.loads((now_dir / "summary.json").read_text())
    base["people_critical"] = 4242
    (now_dir / "summary.json").write_text(json.dumps(base))
    s = _run(now_dir, gpw)
    merged = json.loads((now_dir / "summary.json").read_text())
    assert merged["people_critical"] == 4242   # mine was null -> theirs kept
    assert merged["people_exposed"] == 600     # mine real -> written
    assert s["people_critical"] is None


def test_summary_created_when_absent(tmp_path: Path):
    gpw = _write_gpw(tmp_path / "gpw.tif")
    now_dir = _seed(tmp_path / "now")
    (now_dir / "summary.json").unlink()
    _run(now_dir, gpw)
    merged = json.loads((now_dir / "summary.json").read_text())
    assert merged["people_exposed"] == 1100
    assert merged["response"]["schema"] == "tipping-point/response@1"


def test_ibge_municipalities_hermetic():
    """IBGE fetch with injected get_json: codarea -> code/uf/name join."""
    calls = []

    def fake_get_json(url, **params):
        calls.append(url)
        if "malhas" in url:
            return {"features": [
                _feat(_square(-52.0, -29.0, -51.0, -28.0),
                      codarea="4314902"),
                _feat(_square(-40.0, -20.0, -39.0, -19.0),  # outside bbox
                      codarea="3200000"),
            ]}
        return [{"id": 4314902, "nome": "Porto Alegre"},
                {"id": 3200000, "nome": "Far"}]

    fc = rs.ibge_municipalities(BBOX if False else (-58.0, -34.0, -48.0, -22.5),
                                ufs=("RS",), get_json=fake_get_json)
    assert len(calls) == 2
    feats = fc["features"]
    assert len(feats) == 1
    p = feats[0]["properties"]
    assert p["name"] == "Porto Alegre" and p["code"] == "4314902"
    assert p["uf"] == "RS" and p["source"] == "IBGE"


def test_grid_and_people_helpers(tmp_path: Path):
    grid = rs.Grid(*BBOX, RES)
    assert grid.shape == (16, 16)
    from shapely.geometry import box
    # geodesic: a 1-deg cell nearer the equator is bigger
    assert rs.geodesic_km2(box(0, 0, 1, 1)) > rs.geodesic_km2(box(0, 3, 1, 4))
    pop = rs.load_gpw_pop(_write_gpw(tmp_path / "g.tif"), grid)
    assert pop.shape == (16, 16)
    assert pop[0, 0] == pytest.approx(25.0)    # 100/cell x (0.25/0.5)^2
    mask = np.zeros((16, 16), bool)
    mask[8:12, 4:8] = True
    assert rs.people_in(mask, pop) == 400
    assert rs.people_in(None, pop) is None
    assert rs.people_in(mask, None) is None


def test_empty_boundaries_reported_not_silent(tmp_path: Path):
    gpw = _write_gpw(tmp_path / "gpw.tif")
    now_dir = _seed(tmp_path / "now")
    s = _run(now_dir, gpw, boundaries=_fc([]))
    assert s["priority_zones"]["municipalities"] == 0
    assert "0 features" in s["_null_reasons"]["priority_zones"]
