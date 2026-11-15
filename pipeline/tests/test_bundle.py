"""tp.bundle.publish_case: synthetic COGs -> stable app bundle, no creds.

Layout per decision L4: all case outputs live under data/<case-id>/
(rs_2024, wa_2025); publish_case still takes the event id.
"""
import json

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from tp.bundle import publish_case

RES = 0.01
ORIGIN = (-52.0, -29.0)          # top-left corner, EPSG:4326 (RS AOI)


def _data_dir(tmp_path, case="rs_2024"):
    d = tmp_path / "data" / case
    for sub in ("cog", "geojson", "series", "png"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    return d


def _write_cog(path, bands, names=None, res=RES, origin=ORIGIN, nodata=np.nan):
    path.parent.mkdir(parents=True, exist_ok=True)
    h, w = bands[0].shape
    with rasterio.open(
        path, "w", driver="GTiff", height=h, width=w, count=len(bands),
        dtype=bands[0].dtype, crs="EPSG:4326",
        transform=from_origin(*origin, res, res), nodata=nodata,
    ) as dst:
        for i, a in enumerate(bands, 1):
            dst.write(a, i)
            if names:
                dst.set_band_description(i, names[i - 1])
    return path


def _write_places(gj_dir, places):
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [x, y]},
         "properties": {"name": n, "kind": "town"}}
        for x, y, n in places]}
    (gj_dir / "places.geojson").write_text(json.dumps(fc))


def _tpi(fill=0.0, shape=(20, 20)):
    return np.full(shape, fill, np.float32)


def test_series_cog_to_png_and_index(tmp_path):
    d = _data_dir(tmp_path)
    tpi = _tpi()
    tpi[:5, :5] = 0.7
    tpi[5:, 5:] = np.nan                       # nodata -> transparent
    _write_cog(d / "cog" / "rs_2024_tpi_slide_20240502.tif", [tpi], ["20240502"])
    (tmp_path / "data" / "events.json").write_text(json.dumps(
        {"schema": "tipping-point/events-index@1",
         "events": [{"id": "rs-2024-05", "layers": []}]}))

    s = publish_case("rs-2024-05", data_dir=tmp_path / "data")

    png = d / "png" / "tpi_slide_20240502.png"
    assert png.exists()
    with rasterio.open(png) as src:
        assert src.count == 4
        assert src.dtypes[0] == "uint8"
        alpha = src.read(4)
        assert alpha.max() == 255 and alpha.min() == 0   # opaque + transparent px

    idx = json.loads((d / "png" / "_index.json").read_text())
    assert set(idx) == {"layers"}                        # pinned shape
    entry = idx["layers"][0]
    assert entry == {"id": "tpi_slide", "date": "2024-05-02",
                     "png": "rs_2024/png/tpi_slide_20240502.png",
                     "cog": "rs_2024/cog/rs_2024_tpi_slide_20240502.tif",
                     "bounds": entry["bounds"], "forecast": False}
    w, s_, e, n = entry["bounds"]
    assert w < e and s_ < n and w == pytest.approx(-52.0, abs=0.05)

    ev = json.loads((tmp_path / "data" / "events.json").read_text())
    assert ev["events"][0]["layers"] == [
        {"id": "tpi_slide", "date": "2024-05-02",
         "png": "rs_2024/png/tpi_slide_20240502.png"}]
    assert s["written"] and not s["dry_run"]


def test_forecast_cog_layers(tmp_path):
    d = _data_dir(tmp_path, "wa_2025")
    tpi = _tpi(0.5)
    _write_cog(d / "cog" / "wa_2025_tpi_forecast_d1_20251210.tif",
               [tpi, tpi + 0.1], ["tpi_slide", "tpi_flood"])
    s = publish_case("wa-2025-12", data_dir=tmp_path / "data")
    # d1 issued 2025-12-10 depicts 2025-12-11
    for layer in ("tpi_slide_d1", "tpi_flood_d1"):
        assert (d / "png" / f"{layer}_20251211.png").exists()
        ent = next(e for e in s["layers"] if e["id"] == layer)
        assert ent["date"] == "2025-12-11" and ent["forecast"] is True


def test_forecast_png_stem_pinned(tmp_path):
    """SCHEMA pins <layer-id>_d<k>_<YYYYMMDD>.png where the date is the
    depicted day (issue + k) — the literal doc example."""
    d = _data_dir(tmp_path)
    _write_cog(d / "cog" / "rs_2024_tpi_forecast_d1_20240502.tif",
               [_tpi(0.5)], ["tpi_slide"])
    s = publish_case("rs-2024-05", data_dir=tmp_path / "data")
    assert (d / "png" / "tpi_slide_d1_20240503.png").exists()
    ent = next(e for e in s["layers"] if e["id"] == "tpi_slide_d1")
    assert ent["png"] == "rs_2024/png/tpi_slide_d1_20240503.png"
    idx = json.loads((d / "png" / "_index.json").read_text())
    assert idx["layers"][0]["png"] == "rs_2024/png/tpi_slide_d1_20240503.png"


def test_priority_zones(tmp_path):
    d = _data_dir(tmp_path)
    tpi = _tpi()
    tpi[2:6, 2:6] = 0.9                        # 16 px >= min_blob, class Critical
    tpi[10:20, 10:20] = 0.3                    # Moderate — below the Alto cut
    _write_cog(d / "cog" / "rs_2024_tpi_slide_20240502.tif", [tpi], ["20240502"])
    # blob centroid ~(-51.965, -29.035); near/far places to check naming
    _write_places(d / "geojson", [(-51.96, -29.03, "Near Town"),
                                  (-50.60, -29.50, "Far Town")])

    publish_case("rs-2024-05", data_dir=tmp_path / "data")

    fc = json.loads((d / "geojson" / "priority_zones.geojson").read_text())
    assert len(fc["features"]) == 1
    p = fc["features"][0]["properties"]
    assert p["rank"] == 1
    assert p["name"] == "Near Town"            # nearest place, not the far one
    assert p["class"] == "Critical"            # EN label by class index
    assert p["hazard"] == "landslide"
    assert p["tpi"] == pytest.approx(0.9, abs=1e-4)
    assert p["area_km2"] > 0
    assert p["people"] is None                 # no pop COG -> null


def test_imerg_smap_daily(tmp_path):
    d = _data_dir(tmp_path)
    bands = [_tpi(0.5), _tpi(0.65), _tpi(0.3)]
    _write_cog(d / "cog" / "rs_2024_tpi_slide_20240502.tif",
               bands, ["20240501", "20240502", "20240503"])
    for day, imerg, smap in (("20240501", 12.4, 0.71),
                             ("20240502", 45.0, 0.80),
                             ("20240503", 8.0, 0.73)):
        (d / "series" / f"rs_2024_rain_soil_{day}.json").write_text(json.dumps(
            {"imerg_mm": imerg, "smap": smap, "rain_p95_mm": 45.0}))
    (d / "series" / "rs_2024_swot_20240502.json").write_text(json.dumps(
        [{"reach_id": "1", "time_str": "2024-05-02T12:00:00Z", "wse": 4.1}]))

    publish_case("rs-2024-05", data_dir=tmp_path / "data")

    doc = json.loads((d / "series" / "imerg_smap_daily.json").read_text())
    assert doc["event"] == "rs-2024-05"
    assert doc["reference_date"] == "2024-05-02"
    assert doc["rain_p95_mm"] == 45.0
    assert doc["tpi_threshold"] == 0.6
    assert [r["offset"] for r in doc["days"]] == [-1, 0, 1]
    assert [r["kind"] for r in doc["days"]] == ["observed", "observed", "forecast"]
    d0 = doc["days"][1]
    assert d0["imerg_mm"] == 45.0 and d0["smap"] == 0.80
    assert d0["tpi"] == pytest.approx(0.65, abs=1e-4)      # area-max of band
    assert d0["river_m"] == 4.1
    assert doc["days"][0]["river_m"] is None               # no SWOT that day


def test_mask_products_and_candidate_status(tmp_path):
    d = _data_dir(tmp_path, "wa_2025")
    mask = np.zeros((10, 10), np.uint8)
    mask[1:4, 1:4] = 1                                  # candidate
    mask[5:8, 5:8] = 2                                  # high-confidence
    _write_cog(d / "cog" / "wa_2025_landslide_candidates_20251210.tif",
               [mask.astype(np.float32)], nodata=None)
    _write_cog(d / "cog" / "wa_2025_water_nisar_20251210.tif",
               [mask.astype(np.float32)], nodata=None)

    s = publish_case("wa-2025-12", data_dir=tmp_path / "data")

    cand = json.loads(
        (d / "geojson" / "landslide_candidates.geojson").read_text())
    assert cand["features"]
    assert {f["properties"]["status"] for f in cand["features"]} == {"candidate"}
    assert (d / "geojson" / "permanent_water.geojson").exists()
    # flood_confirmed only when the NISAR flood mask exists
    assert not (d / "geojson" / "flood_confirmed.geojson").exists()
    assert any(x["target"] == "flood_confirmed" for x in s["skipped"])


def test_missing_sources_skip_not_crash(tmp_path):
    _data_dir(tmp_path, "wa_2025")               # all dirs empty
    s = publish_case("wa-2025-12", data_dir=tmp_path / "data")
    targets = {x["target"] for x in s["skipped"]}
    assert {"tpi_slide", "tpi_flood", "hillshade", "permanent_water",
            "flood_confirmed", "landslide_candidates"} <= targets
    assert s["written"] == ["wa_2025/png/_index.json"]
    assert json.loads(
        (tmp_path / "data" / "wa_2025" / "png" / "_index.json").read_text()
    ) == {"layers": []}


def test_dry_run_writes_nothing(tmp_path):
    d = _data_dir(tmp_path)
    _write_cog(d / "cog" / "rs_2024_tpi_slide_20240502.tif",
               [_tpi(0.7)], ["20240502"])
    (d / "series" / "rs_2024_rain_soil_20240502.json").write_text(
        json.dumps({"imerg_mm": 1.0, "smap": 0.5}))
    events = tmp_path / "data" / "events.json"
    events.write_text('{"events": [{"id": "rs-2024-05", "layers": []}]}')

    s = publish_case("rs-2024-05", dry_run=True, data_dir=tmp_path / "data")

    assert s["dry_run"] and s["planned"]
    assert s["written"] == []
    assert not (d / "png" / "tpi_slide_20240502.png").exists()
    assert not (d / "png" / "_index.json").exists()
    assert not (d / "series" / "imerg_smap_daily.json").exists()
    assert json.loads(events.read_text())["events"][0]["layers"] == []


def test_zone_people_native_area_fraction(tmp_path):
    """B1 regression: zone + critical-area `people` weight each GPW cell by
    area(cell ∩ polygon)/area(cell) on the native count grid — never the
    whole cell; nodata (-3.4e38) and negatives count as 0. Fails on ANY
    negative people in the published products."""
    d = _data_dir(tmp_path)
    tpi = _tpi()
    tpi[2:4, 2:4] = 0.9        # exactly GPW cell (1,1) at res 0.02
    tpi[10, 10] = 0.9          # quarter of cell (5,5): 40 -> 10
    tpi[15, 15] = 0.9          # quarter of a -3.4e38 nodata cell -> 0
    tpi[6, 6] = 0.9            # quarter of a -7 cell (no nodata tag) -> 0
    _write_cog(d / "cog" / "rs_2024_tpi_slide_20240502.tif", [tpi], ["20240502"])
    _write_places(d / "geojson", [(-51.96, -29.03, "Town")])
    pop = np.zeros((10, 10), np.float32)
    pop[1, 1] = 100.0
    pop[5, 5] = 40.0
    pop[7, 7] = -3.4e38
    pop[3, 3] = -7.0
    _write_cog(d / "cog" / "rs_2024_pop_gpw_native.tif", [pop],
               res=0.02, nodata=-3.4e38)
    (d / "series" / "rs_2024_rain_soil_20240502.json").write_text(
        json.dumps({"imerg_mm": 1.0, "smap": 0.5}))

    publish_case("rs-2024-05", data_dir=tmp_path / "data")

    for name in ("priority_zones", "critical_areas"):
        fc = json.loads((d / "geojson" / f"{name}.geojson").read_text())
        people = [f["properties"]["people"] for f in fc["features"]]
        assert all(p is not None and p >= 0 for p in people)   # never < 0, never -3.4e38
        assert sorted(people, reverse=True) == [100, 10, 0, 0]
    ser = json.loads((d / "series" / "imerg_smap_daily.json").read_text())
    day = ser["days"][0]
    assert day["people_critical"] == 110                       # 100 + 10 + 0 + 0
    assert day["people_critical"] >= 0
    met = json.loads((tmp_path / "data" / "metrics.json").read_text())
    assert met["cases"]["rs_2024"]["people_critical"] == 110


def test_critical_stats_matches_independent_count(tmp_path):
    """series days[].critical_* and the metrics scalar share ONE definition:
    pixel count with TPI > 0.6, geodesic px area, per hazard + union."""
    from tp.bundle import _critical_stats, TPI_THRESHOLD
    d = _data_dir(tmp_path)
    import datetime as dt
    day1, day2 = dt.date(2024, 5, 1), dt.date(2024, 5, 2)
    s1, s2 = _tpi(), _tpi()
    s1[:10, :10] = 0.7; s2[:15, :15] = 0.7          # slide crit grows
    f1, f2 = _tpi(), _tpi()
    f1[15:, 15:] = 0.9                               # flood crit const
    cs = _write_cog(d / "cog" / "rs_2024_tpi_slide_20240502.tif",
                    [s1, s2], ["20240501", "20240502"])
    cf = _write_cog(d / "cog" / "rs_2024_tpi_flood_20240502.tif",
                    [f1, f2], ["20240501", "20240502"])
    bbd = {"slide": {day1: (cs, 1), day2: (cs, 2)},
           "flood": {day1: (cf, 1), day2: (cf, 2)}}
    stats = _critical_stats(bbd, None)

    # independent recount for day2, same band, same geodesic formula
    with rasterio.open(cs) as src:
        a = src.read(2).astype(np.float64); tr = src.transform
    m = np.isfinite(a) & (a > TPI_THRESHOLD)
    lats = tr.f + (np.arange(a.shape[0]) + 0.5) * tr.e
    px = (abs(tr.a) * 111320.0 * np.cos(np.deg2rad(lats))
          * abs(tr.e) * 110540.0) / 1e6
    expected = (m * px[:, None]).sum()
    s = stats["2024-05-02"]
    assert s["critical_slide_km2"] == pytest.approx(expected, abs=0.01)
    # union = slide ∪ flood (flood is a disjoint block)
    assert s["critical_km2"] == pytest.approx(
        s["critical_slide_km2"] + s["critical_flood_km2"], abs=0.01)
    # metrics scalar = peak-day union of THE SAME dict -> agreement by construction
    peak = max(stats.items(), key=lambda kv: kv[1]["critical_km2"])
    assert peak[0] == "2024-05-02"
    assert peak[1]["critical_km2"] == s["critical_km2"]
