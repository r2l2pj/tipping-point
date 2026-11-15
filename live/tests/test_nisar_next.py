"""Hermetic tests for live/nisar_next.py — synthetic CMR items, L0 passes,
municipalities, zones and alerts; the `get` callable fakes the CMR HTTP
layer so nothing touches the network."""
import datetime as dt
import json

from shapely.geometry import box, mapping

from live import nisar_next

NOW = dt.datetime(2026, 11, 15, 12, 0, 0, tzinfo=dt.UTC)
BOUNDS = (-58.0, -34.0, -48.0, -22.5)
FP1 = [(-53.0, -28.5), (-51.0, -28.5), (-51.0, -27.0), (-53.0, -27.0)]
FP2 = [(-49.0, -28.5), (-47.0, -28.5), (-47.0, -27.0), (-49.0, -27.0)]


def _granule(cycle, track, look, frame, start, end, coverage="F",
             poly=None):
    """A CMR umm_json item that pipeline.fetch.nisar.cmr_gcov_granule parses."""
    pts = poly if poly is not None else FP1
    gid = (f"NISAR_L2_PR_GCOV_{cycle:03d}_{track:03d}_{look}_{frame:03d}_"
           f"04_HHHH_A_{start}_{end}_P05036_N_{coverage}_J_001")
    return {
        "umm": {
            "GranuleUR": gid,
            "SpatialExtent": {"HorizontalSpatialDomain": {"Geometry": {
                "GPolygons": [{"Boundary": {"Points":
                    [{"Longitude": x, "Latitude": y}
                     for x, y in pts]}}]}}},
            "ProviderDates": [{"Type": "Insert",
                               "Date": "2026-11-15T00:00:00Z"}],
            "RelatedUrls": [],
        },
        "meta": {},
    }


class _Resp:
    def __init__(self, items):
        self._items = items
        self.headers = {}

    def raise_for_status(self):
        pass

    def json(self):
        return {"items": self._items}


def fake_get(items):
    """requests.get stand-in: one non-paged CMR response per call."""
    def get(url, params=None, headers=None, timeout=None):
        return _Resp(items)
    return get


def _feat(geom, props):
    return {"type": "Feature", "geometry": mapping(geom),
            "properties": props}


def _fc(features):
    return json.dumps({"type": "FeatureCollection", "features": features})


def _write(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj) if not isinstance(obj, str) else obj)


def _seed_munis(d):
    _write(d / "geojson/municipal_boundaries.geojson", _fc([
        _feat(box(-52.0, -28.3, -51.5, -27.8),
              {"code": "4205407", "name": "Florianópolis", "uf": "SC"}),
        _feat(box(-48.0, -28.3, -47.5, -27.8),
              {"code": "4210000", "name": "Coastal", "uf": "SC"}),
    ]))


def _seed_both(tmp_path):
    """The two CMR frames + L0 pass + munis used by the stamp tests."""
    items = [
        _granule(36, 132, "A", 148, "20261103T093737", "20261103T094613",
                 poly=FP1),
        _granule(35, 125, "D", 156, "20261113T212415", "20261113T212537",
                 poly=FP2),
    ]
    _seed_munis(tmp_path)
    _write(tmp_path / "nisar_passes.json", [
        {"granule": "NISAR_L0_PR_RRSD_036_132_A_148S_20261114T093737_"
                    "20261114T094613_P05036_F_J_001",
         "acquired": "2026-11-14T09:37:37Z", "path": 132,
         "direction": "ASCENDING"}])
    return items


# --- pure functions -------------------------------------------------------


def test_next_pass_adds_one_repeat():
    last = dt.datetime(2026, 11, 6, 9, 37, tzinfo=dt.UTC)
    assert nisar_next.next_pass(last, NOW) == last + dt.timedelta(days=12)


def test_next_pass_skips_to_first_future():
    last = dt.datetime(2026, 10, 20, 9, 37, tzinfo=dt.UTC)
    assert nisar_next.next_pass(last, NOW) == last + dt.timedelta(days=36)


def test_next_pass_boundary_takes_next_cycle():
    last = NOW - dt.timedelta(days=12)      # exactly `now` after one repeat
    assert nisar_next.next_pass(last, NOW) == last + 2 * dt.timedelta(days=12)


def test_next_pass_future_last_still_one_repeat():
    last = NOW + dt.timedelta(days=2)
    assert nisar_next.next_pass(last, NOW) == last + dt.timedelta(days=12)


def test_covers_sorted_codes_and_null_footprint():
    munis = [
        {"code": "4205407", "name": "Florianópolis", "uf": "SC",
         "geom": box(-49, -28, -48, -27.5)},
        {"code": "4314902", "name": "Porto Alegre", "uf": "RS",
         "geom": box(-51.5, -30.5, -51, -30)},
        {"code": "", "name": "codigo-vazio", "uf": "RS",
         "geom": box(-48.8, -28.4, -48.6, -28.3)},
    ]
    fp = box(-50, -28.5, -48.5, -27.5)
    assert nisar_next.covers(fp, munis) == ["4205407"]
    assert nisar_next.covers(None, munis) == []


# --- inputs ----------------------------------------------------------------


def test_load_recent_passes_file_then_manifest(tmp_path):
    assert nisar_next.load_recent_passes(tmp_path) == []
    _write(tmp_path / "manifest.json", {"nisar_passes": [
        {"granule": "G1", "acquired": "2026-11-14T09:37:37Z", "path": 132,
         "direction": "ASCENDING"},
        {"granule": "G2", "acquired": "not-a-date", "path": 125,
         "direction": "DESCENDING"},
        {"granule": "G3", "acquired": "2026-11-13T21:25:37Z", "path": 125,
         "direction": "DESCENDING"}]})
    got = nisar_next.load_recent_passes(tmp_path)
    assert [(p["track"], p["look"]) for p in got] == [(132, "A"), (125, "D")]
    _write(tmp_path / "nisar_passes.json", [
        {"acquired": "2026-11-14T00:00:00Z", "path": 7,
         "direction": "ascending"}])
    assert [p["track"] for p in nisar_next.load_recent_passes(tmp_path)] == [7]


def test_latest_frames_merges_collections_prefers_full(tmp_path):
    items = [
        _granule(35, 132, "A", 148, "20261106T093737", "20261106T094613"),
        _granule(36, 132, "A", 148, "20261114T093737", "20261114T094613",
                 coverage="P"),
        _granule(36, 132, "A", 148, "20261114T093737", "20261114T094613"),
    ]
    frames = nisar_next.latest_frames(BOUNDS, 45, get=fake_get(items),
                                      now=NOW)
    g = frames[(132, "A", 148)]
    assert g["coverage"] == "F" and g["start"].day == 14


# --- build + stamps ---------------------------------------------------------


def test_build_predicts_sorted_and_reanchors_on_l0(tmp_path):
    items = _seed_both(tmp_path)
    product, fps = nisar_next.build(tmp_path, now=NOW, get=fake_get(items),
                                    bounds=BOUNDS)
    assert product["repeat_days"] == 12 and product["method"] == nisar_next.METHOD
    passes = product["passes"]
    assert [p["track"] for p in passes] == [125, 132]          # soonest first
    d, a = passes
    assert d["direction"] == "descending" and d["frame"] == 156
    assert d["utc"] == "2026-11-25T21:24:15Z"
    assert d["last_observed_utc"] == "2026-11-13T21:24:15Z"
    assert a["direction"] == "ascending"
    # L0 overpass (2026-11-14) is newer than the newest CMR product (11-03)
    assert a["last_observed_utc"] == "2026-11-14T09:37:37Z"
    assert a["utc"] == "2026-11-26T09:37:37Z"
    assert a["covers"] == ["4205407"]      # FP1 overlaps Florianópolis only
    assert d["covers"] == ["4210000"]      # FP2 overlaps Coastal only
    assert len(fps) == 2


def test_build_empty_cmr_empty_passes(tmp_path):
    product, fps = nisar_next.build(tmp_path, now=NOW, get=fake_get([]),
                                    bounds=BOUNDS)
    assert product["passes"] == [] and fps == []


def test_run_writes_product_and_stamps(tmp_path):
    items = _seed_both(tmp_path)
    zones = _fc([
        _feat(box(-51.8, -28.2, -51.6, -28.0),   # inside FP1
              {"name": "Florianópolis", "code": "4205407", "rank": 1}),
        _feat(box(-46.0, -26.0, -45.0, -25.0),   # no pass covers it
              {"name": "Nowhere", "code": "9900000", "rank": 2}),
    ])
    _write(tmp_path / "geojson/priority_zones.geojson", zones)
    _write(tmp_path / "response/priority_zones.geojson", zones)
    _write(tmp_path / "alerts.json", {"active": [
        {"class": 3, "centroid": [-48.0, -28.0],   # inside FP2
         "municipality": "Coastal", "uf": "SC"},
        {"class": 2, "centroid": [-46.0, -26.0],   # centroid outside; muni in FP2
         "municipality": "Coastal", "uf": "SC"},
        {"class": 2, "centroid": [-46.0, -26.0],
         "municipality": "Nowhere", "uf": "XX"}]})
    product = nisar_next.run(tmp_path, now=NOW, get=fake_get(items),
                             bounds=BOUNDS)
    written = json.loads(
        (tmp_path / "nisar_next_passes.json").read_text())
    assert written == product
    for rel in ("geojson/priority_zones.geojson",
                "response/priority_zones.geojson"):
        feats = json.loads((tmp_path / rel).read_text())["features"]
        assert feats[0]["properties"]["next_nisar_pass_utc"] == \
            "2026-11-26T09:37:37Z"
        assert feats[0]["properties"]["next_nisar_track"] == 132
        assert feats[1]["properties"]["next_nisar_pass_utc"] is None
        assert feats[1]["properties"]["next_nisar_track"] is None
    active = json.loads((tmp_path / "alerts.json").read_text())["active"]
    assert active[0]["next_nisar_pass_utc"] == "2026-11-25T21:24:15Z"
    assert active[0]["next_nisar_track"] == 125
    assert active[1]["next_nisar_track"] == 125   # municipality mesh fallback
    assert active[2]["next_nisar_pass_utc"] is None
    assert not list(tmp_path.glob("*.tmp"))


def test_run_no_inputs_still_writes_product(tmp_path):
    product = nisar_next.run(tmp_path, now=NOW, get=fake_get([]),
                             bounds=BOUNDS)
    assert product["passes"] == []
    assert json.loads(
        (tmp_path / "nisar_next_passes.json").read_text())["passes"] == []


def test_stamp_survives_broken_geojson(tmp_path):
    items = _seed_both(tmp_path)
    p = tmp_path / "geojson/priority_zones.geojson"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{ not json")
    _write(tmp_path / "alerts.json", {"active": [
        {"class": 3, "centroid": [-48.0, -28.0],
         "municipality": "Coastal", "uf": "SC"}]})
    product = nisar_next.run(tmp_path, now=NOW, get=fake_get(items),
                             bounds=BOUNDS)
    active = json.loads((tmp_path / "alerts.json").read_text())["active"]
    assert active[0]["next_nisar_track"] == 125
    assert len(product["passes"]) == 2
