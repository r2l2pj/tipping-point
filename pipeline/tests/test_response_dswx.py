"""Hermetic tests for tp.response_dswx + fetch.opera DSWx-S1 helpers.

Synthetic tiny WTR rasters only; search/fetch are injected (no network).
"""
import io
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from tp import response_dswx as rd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fetch import opera

# 0.1 x 0.1 deg domain on a 0.01 deg grid -> 10 x 10 cells
GRID = rd.Grid(-50.0, -30.1, -49.9, -30.0, 0.01)
T0 = datetime(2026, 10, 1, 9, tzinfo=UTC)


def wtr_bytes(arr: np.ndarray) -> bytes:
    """A 4326 WTR GeoTIFF covering GRID at 0.001 deg (10x finer)."""
    buf = io.BytesIO()
    with rasterio.open(buf, "w", driver="GTiff", height=arr.shape[0], width=arr.shape[1], count=1,
                       dtype="uint8", crs="EPSG:4326", nodata=255,
                       transform=from_origin(GRID.west, GRID.north, 0.001, 0.001)) as dst:
        dst.write(arr, 1)
    return buf.getvalue()


def tile(water_rows=(), fill=0, masked_rows=()):
    a = np.full((100, 100), fill, np.uint8)
    for r in water_rows:
        a[r * 10:(r + 1) * 10, :] = 1
    for r in masked_rows:
        a[r * 10:(r + 1) * 10, :] = 251
    return a


def granule(i, when, arr):
    return {"id": f"G{i:03d}", "acquired": rd.iso(when),
            "available": rd.iso(when + timedelta(hours=6)), "wtr_url": f"mem://{i}", "_arr": arr}


def write_lc(path, rows=None):
    """LC_Type1 raster on GRID: grassland (10) unless rows={row: class}."""
    a = np.full((10, 10), 10, np.uint8)
    for r, c in (rows or {}).items():
        a[r] = c
    with rasterio.open(path, "w", driver="GTiff", height=10, width=10, count=1, dtype="uint8",
                       crs="EPSG:4326", transform=GRID.transform, nodata=255) as dst:
        dst.write(a, 1)
    return path


def write_outline(path, west=GRID.west):
    """Brazil outline: everything east of `west`."""
    poly = [[west, GRID.south - 1], [GRID.east + 1, GRID.south - 1], [GRID.east + 1, GRID.north + 1],
            [west, GRID.north + 1], [west, GRID.south - 1]]
    path.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {}, "geometry": {"type": "Polygon", "coordinates": [poly]}}]}))
    return path


def make_run(tmp_path, catalogue, now, gpw=None, lc=None, outline=None, **kw):
    def search(start, end):
        s = rd.parse_iso(start)
        return [g for g in catalogue if rd.parse_iso(g["acquired"]) >= s]

    calls = []

    def fetch(g):
        calls.append(g["id"])
        if g["_arr"] is None:
            raise RuntimeError("HTTP 500")
        return wtr_bytes(g["_arr"])

    kw.setdefault("min_area_km2", 0.0)
    kw.setdefault("simplify_m", 0.0)
    kw.setdefault("workers", 2)
    kw.setdefault("min_obs", 3)
    kw.setdefault("landcover_path", lc or write_lc(tmp_path / "lc.tif"))
    kw.setdefault("outline_path", outline or write_outline(tmp_path / "br.geojson"))
    meta = rd.run(tmp_path, GRID, search, fetch, gpw or [], now=now, log=lambda *_: None, **kw)
    return meta, calls


def test_classify_wtr_codes():
    w = rd.classify_wtr(np.array([0, 1, 2, 3, 250, 251, 254, 255], np.uint8))
    assert w[:4].tolist() == [0, 1, 1, 1]
    assert np.isnan(w[4:]).all()


def test_tile_to_grid_average_and_unobserved():
    a = tile(water_rows=(2,), masked_rows=(5,))
    a[30:40, :50] = 1                      # half water -> frac 0.5
    r0, c0, frac = rd.tile_to_grid(a, from_origin(GRID.west, GRID.north, 0.001, 0.001), "EPSG:4326", GRID)
    assert (r0, c0, frac.shape) == (0, 0, (10, 10))
    assert np.allclose(frac[2], 1.0) and np.allclose(frac[0], 0.0)
    assert np.isnan(frac[5]).all()
    assert np.allclose(frac[3, :5], 1.0) and np.allclose(frac[3, 5:], 0.0)


def test_state_fold_and_same_pass():
    st = rd.State(GRID)
    one = np.ones((2, 2), np.float32)
    st.ingest(0, 0, one * 0, T0, "a")
    st.ingest(0, 0, one, T0 + timedelta(minutes=20), "b")        # same pass: merge, no fold
    assert st.obs_n[0, 0] == 0 and st.cur[0, 0] == 1
    st.ingest(0, 0, one * 0, T0 + timedelta(days=4), "c")         # newer: fold prior water
    assert st.obs_n[0, 0] == 1 and st.water_n[0, 0] == 1 and st.cur[0, 0] == 0
    st.ingest(0, 0, one, T0 + timedelta(days=2), "late")           # older than cur: baseline only
    assert st.obs_n[0, 0] == 2 and st.water_n[0, 0] == 2 and st.cur[0, 0] == 0


def test_end_to_end_bootstrap_incremental_change(tmp_path):
    # baseline: rows 0 permanent water, 3 passes; then flood on rows 5-6
    cat = [granule(i, T0 + timedelta(days=4 * i), tile(water_rows=(0,))) for i in range(4)]
    cat.append(granule(4, T0 + timedelta(days=16), tile(water_rows=(0, 5, 6))))
    gpw = tmp_path / "gpw.tif"
    with rasterio.open(gpw, "w", driver="GTiff", height=10, width=10, count=1, dtype="float32",
                       crs="EPSG:4326", transform=GRID.transform) as dst:
        dst.write(np.full((10, 10), 2.0, np.float32), 1)

    meta, calls = make_run(tmp_path, cat, T0 + timedelta(days=17), gpw=[str(gpw)], window_h=48)
    assert len(calls) == 5 and meta["n_granules"] == 5
    assert meta["bootstrap"]["ok"] and meta["_null_reasons"].get("bootstrap") is None
    assert meta["latest_acquired"] == cat[-1]["acquired"]
    assert meta["latency_h"] == 6.0
    assert meta["people_flooded"] == 40.0                      # 20 cells x 2
    assert 0 < meta["coverage_pct"] <= 100
    assert meta["people_source"].startswith("GPW v4.11")
    assert meta["flooded_km2"] == pytest.approx(meta["new_km2"]) and meta["flooded_km2"] > 0
    assert meta["receded_km2"] is None and "receded_km2" in meta["_null_reasons"]
    fc = json.loads((tmp_path / "flood_dswx.geojson").read_text())
    props = [f["properties"] for f in fc["features"]]
    assert props and all(p["status"] == "candidate" and p["change"] == "new" for p in props)
    assert all(p["source"] == "opera-dswx-s1" and p["granules"] == ["G004"] for p in props)
    assert all(p["confidence"] == "standard" and p["landcover"] == "grassland" for p in props)
    assert sum(p["people"] for p in props) == pytest.approx(40.0)
    assert "confirmed" not in (tmp_path / "flood_dswx.geojson").read_text()
    assert (tmp_path / "dswx_state.npz").is_file()

    # incremental: only the new granule is fetched; row 5 persists, row 6 recedes
    cat.append(granule(5, T0 + timedelta(days=20), tile(water_rows=(0, 5))))
    meta2, calls2 = make_run(tmp_path, cat, T0 + timedelta(days=21), gpw=[str(gpw)], window_h=48)
    assert calls2 == ["G005"] and meta2["n_granules"] == 1
    assert meta2["new_km2"] == 0.0 and meta2["receded_km2"] > 0
    changes = {f["properties"]["change"] for f in
               json.loads((tmp_path / "flood_dswx.geojson").read_text())["features"]}
    assert changes == {"persisting"}


def test_bootstrap_failure_reports_null(tmp_path):
    cat = [granule(i, T0 + timedelta(days=i), None) for i in range(3)]
    meta, _ = make_run(tmp_path, cat, T0 + timedelta(days=4))
    assert not meta["bootstrap"]["ok"]
    assert "bootstrap" in meta["_null_reasons"]
    assert meta["flooded_km2"] is None and meta["people_flooded"] is None
    assert json.loads((tmp_path / "flood_dswx.geojson").read_text())["features"] == []


def test_missing_gpw_people_null(tmp_path):
    cat = [granule(i, T0 + timedelta(days=4 * i), tile(water_rows=(5,) if i == 3 else ())) for i in range(4)]
    meta, _ = make_run(tmp_path, cat, T0 + timedelta(days=13), gpw=[str(tmp_path / "nope.tif")])
    assert meta["people_flooded"] is None and "people" in meta["_null_reasons"]
    assert meta["flooded_km2"] > 0


def test_unbaselined_water_not_candidate(tmp_path):
    cat = [granule(0, T0, tile(water_rows=(5,)))]
    meta, _ = make_run(tmp_path, cat, T0 + timedelta(days=1))
    assert meta["flooded_km2"] == 0.0 and meta["unbaselined_water_km2"] > 0


def test_cmr_granule_record_and_pagination():
    item = {"umm": {
        "GranuleUR": "OPERA_L3_DSWx-S1_T21HVC_X",
        "TemporalExtent": {"RangeDateTime": {"BeginningDateTime": "2026-11-14T09:06:12.000Z"}},
        "ProviderDates": [{"Type": "Insert", "Date": "2026-11-14T15:28:08Z"}],
        "RelatedUrls": [
            {"Type": "GET DATA VIA DIRECT ACCESS", "URL": "s3://b/x_B01_WTR.tif"},
            {"Type": "GET DATA", "URL": "https://h/x_B02_BWTR.tif"},
            {"Type": "GET DATA", "URL": "https://h/x_B01_WTR.tif"},
        ]}}
    rec = opera._granule_record(item)
    assert rec["wtr_url"] == "https://h/x_B01_WTR.tif"
    assert rec["available"] == "2026-11-14T15:28:08Z"

    class R:
        def __init__(self, items, after):
            self._items, self.headers = items, ({"CMR-Search-After": after} if after else {})

        def raise_for_status(self):
            pass

        def json(self):
            return {"items": self._items}

    class S:
        def __init__(self):
            self.calls = []

        def get(self, url, params=None, headers=None, timeout=None):
            self.calls.append(headers)
            return R([item, item], "tok") if len(self.calls) == 1 else R([item], None)

    s = S()
    out = opera.search_dswx_s1((-58, -34, -48, -22.5), "2026-11-01T00:00:00Z", session=s, page_size=2)
    assert len(out) == 3 and s.calls[1] == {"CMR-Search-After": "tok"}


def test_download_wtr_never_leaks_token(monkeypatch):
    monkeypatch.setenv("EARTHDATA_TOKEN", "SECRET-TOKEN")
    monkeypatch.setattr("time.sleep", lambda *_: None)

    class S:
        def get(self, url, headers=None, timeout=None, allow_redirects=None):
            assert headers == {"Authorization": "Bearer SECRET-TOKEN"}
            return type("R", (), {"status_code": 401, "content": b""})()

    with pytest.raises(RuntimeError) as exc:
        opera.download_wtr("https://h/x_B01_WTR.tif", session=S())
    assert "SECRET" not in str(exc.value) and "401" in str(exc.value)


def test_landcover_exclusion_confidence_and_brazil_clip(tmp_path):
    # new water on rows 2 (next to a wetland row 1 -> excluded by dilation),
    # 5 (cropland -> lower confidence), 8 (grassland -> standard); columns
    # west of -49.95 are outside "Brazil"
    cat = [granule(i, T0 + timedelta(days=4 * i), tile()) for i in range(4)]
    cat.append(granule(4, T0 + timedelta(days=16), tile(water_rows=(2, 5, 8))))
    lc = write_lc(tmp_path / "lc.tif", {0: 10, 1: 11, 5: 12})
    meta, _ = make_run(tmp_path, cat, T0 + timedelta(days=17), lc=lc,
                       outline=write_outline(tmp_path / "br.geojson", west=-49.95))
    cell = meta["raw_candidate_km2"] / 30            # 3 rows x 10 cols, ~equal-area cells
    assert meta["excluded_water_wetland_km2"] == pytest.approx(10 * cell, rel=0.01)
    assert meta["outside_brazil_km2"] == pytest.approx(10 * cell, rel=0.01)   # 5 W cols x rows 5, 8
    assert meta["flooded_km2"] == pytest.approx(5 * cell, rel=0.01)
    assert meta["lower_confidence_km2"] == pytest.approx(5 * cell, rel=0.01)
    fc = json.loads((tmp_path / "flood_dswx.geojson").read_text())
    by_conf = {f["properties"]["confidence"]: f["properties"]["landcover"] for f in fc["features"]}
    assert by_conf == {"standard": "grassland", "lower": "cropland"}
    for f in fc["features"]:
        assert min(x for x, _ in f["geometry"]["coordinates"][0]) >= -49.95 - 1e-9
    # headline == what the map draws (sum over mapped polygons per tier)
    for conf, key in (("standard", "flooded_km2"), ("lower", "lower_confidence_km2")):
        drawn = sum(f["properties"]["area_km2"] for f in fc["features"]
                    if f["properties"]["confidence"] == conf)
        assert meta[key] == pytest.approx(drawn, abs=1e-3)
    std_people = [f["properties"]["people"] for f in fc["features"]
                  if f["properties"]["confidence"] == "standard"]
    if meta["people_flooded"] is not None:
        assert meta["people_flooded"] == pytest.approx(sum(std_people), abs=0.2)
    assert meta["sub_mmu_km2"] >= 0


def test_missing_landcover_nulls_headline(tmp_path):
    cat = [granule(i, T0 + timedelta(days=4 * i), tile(water_rows=(5,) if i == 3 else ())) for i in range(4)]
    meta, _ = make_run(tmp_path, cat, T0 + timedelta(days=13), landcover_path=tmp_path / "nope.tif")
    assert meta["flooded_km2"] is None and "landcover" in meta["_null_reasons"]
    assert json.loads((tmp_path / "flood_dswx.geojson").read_text())["features"] == []


def write_gsw(path, rows=None):
    """GSW-like uint8 raster on GRID: 0 unless rows={row: value}; 255 = no data."""
    a = np.zeros((10, 10), np.uint8)
    for r, v in (rows or {}).items():
        a[r] = v
    with rasterio.open(path, "w", driver="GTiff", height=10, width=10, count=1, dtype="uint8",
                       crs="EPSG:4326", transform=GRID.transform, nodata=255) as dst:
        dst.write(a, 1)
    return path


def test_seasonal_water_downgraded_to_lower(tmp_path):
    # new water on rows 2 (occurrence 40% -> seasonal), 5 (seasonality 4 mo),
    # 8 (occurrence 10%, 1 mo -> stays standard); all grassland, all Brazil
    cat = [granule(i, T0 + timedelta(days=4 * i), tile()) for i in range(4)]
    cat.append(granule(4, T0 + timedelta(days=16), tile(water_rows=(2, 5, 8))))
    occ = write_gsw(tmp_path / "occ.tif", {2: 40, 5: 5, 8: 10})
    seas = write_gsw(tmp_path / "seas.tif", {2: 2, 5: 4, 8: 1})
    meta, _ = make_run(tmp_path, cat, T0 + timedelta(days=17), gsw_paths=(occ, seas))
    cell = meta["raw_candidate_km2"] / 30
    assert meta["flooded_km2"] == pytest.approx(10 * cell, rel=0.01)            # row 8 only
    assert meta["lower_confidence_km2"] == pytest.approx(20 * cell, rel=0.01)   # rows 2 + 5
    assert meta["seasonal_downgraded_km2"] == pytest.approx(20 * cell, rel=0.01)
    assert meta["seasonal_flagged_pixel_km2"] == pytest.approx(20 * cell, rel=0.01)
    assert meta["seasonal_water_source"] and meta["params"]["gsw_occurrence_min_pct"] == 25
    fc = json.loads((tmp_path / "flood_dswx.geojson").read_text())
    reasons = [f["properties"]["lower_reason"] for f in fc["features"]
               if f["properties"]["confidence"] == "lower"]
    assert reasons and all(r == [rd.REASON_SEAS] for r in reasons)
    assert all(f["properties"]["lower_reason"] is None for f in fc["features"]
               if f["properties"]["confidence"] == "standard")


def test_missing_gsw_skips_rule_with_reason(tmp_path):
    cat = [granule(i, T0 + timedelta(days=4 * i), tile()) for i in range(4)]
    cat.append(granule(4, T0 + timedelta(days=16), tile(water_rows=(5,))))
    meta, _ = make_run(tmp_path, cat, T0 + timedelta(days=17),
                       gsw_paths=(tmp_path / "nope_occ.tif", tmp_path / "nope_seas.tif"))
    assert meta["flooded_km2"] > 0                                   # headline still published
    assert meta["seasonal_downgraded_km2"] is None
    assert "seasonal_water" in meta["_null_reasons"] and meta["seasonal_water_source"] is None


IMERG_W, IMERG_N = -51.5, -28.5            # 0.1 deg, 30x30 cells around GRID


def write_imerg(dir_, end, n=8, wet=None, mm=0.0, west=IMERG_W, north=IMERG_N, shape=(30, 30)):
    """Daily imerg_early COGs ending `end`; `wet`=(lon, lat) cell gets `mm`/day."""
    from rasterio.transform import from_origin

    dir_.mkdir(parents=True, exist_ok=True)
    t = from_origin(west, north, 0.1, 0.1)
    for k in range(n):
        a = np.zeros(shape, np.float32)
        if wet is not None:
            r, c = rasterio.transform.rowcol(t, *wet)
            a[r, c] = mm
        day = end - timedelta(days=k)
        with rasterio.open(dir_ / f"imerg_early_{day:%Y%m%d}.tif", "w", driver="GTiff",
                           height=shape[0], width=shape[1], count=1, dtype="float32",
                           crs="EPSG:4326", transform=t, nodata=np.nan) as dst:
            dst.write(a, 1)
    return dir_


def _rain_run(tmp_path, imerg_dir):
    cat = [granule(i, T0 + timedelta(days=4 * i), tile()) for i in range(4)]
    cat.append(granule(4, T0 + timedelta(days=16), tile(water_rows=(5,))))
    occ = write_gsw(tmp_path / "occ.tif")                 # never water
    seas = write_gsw(tmp_path / "seas.tif")
    return make_run(tmp_path, cat, T0 + timedelta(days=17), gsw_paths=(occ, seas),
                    imerg_dir=imerg_dir)[0]


LAST = (T0 + timedelta(days=16)).date()


def test_no_rain_downgrades_to_lower(tmp_path):
    meta = _rain_run(tmp_path, write_imerg(tmp_path / "imerg", LAST))
    assert meta["flooded_km2"] == 0.0 and meta["lower_confidence_km2"] > 0
    # pixel-area sum inside polygons vs polygon area: same region, <1 % apart
    assert meta["norain_downgraded_km2"] == pytest.approx(meta["lower_confidence_km2"], rel=0.01)
    assert meta["all_candidates_km2"] == pytest.approx(meta["lower_confidence_km2"])
    assert meta["rain_rule"]["n_files"] == 8 and meta["rain_source"]
    fc = json.loads((tmp_path / "flood_dswx.geojson").read_text())
    assert all(f["properties"]["lower_reason"] == [rd.REASON_NORAIN] for f in fc["features"])


def test_rain_within_50km_keeps_standard(tmp_path):
    # ~30 km east of the grid: 2 mm/day x 8 = 16 mm >= 10 -> supported
    meta = _rain_run(tmp_path, write_imerg(tmp_path / "imerg", LAST, wet=(-49.65, -30.05), mm=2.0))
    assert meta["flooded_km2"] > 0 and meta["norain_downgraded_km2"] == 0.0


def test_rain_beyond_50km_does_not_support(tmp_path):
    # ~100 km east: outside the radius -> still no supporting rain
    meta = _rain_run(tmp_path, write_imerg(tmp_path / "imerg", LAST, wet=(-48.95, -30.05), mm=5.0))
    assert meta["flooded_km2"] == 0.0 and meta["norain_downgraded_km2"] > 0


def test_rain_rule_skipped_on_bad_or_missing_files(tmp_path):
    # transposed-like file that does not cover the domain -> skipped, never downgrades
    bad = write_imerg(tmp_path / "bad", LAST, west=-49.5, shape=(30, 30))   # misses the domain
    (tmp_path / "a").mkdir()
    meta = _rain_run(tmp_path / "a", bad)
    assert meta["flooded_km2"] > 0 and meta["norain_downgraded_km2"] is None
    assert "do not cover" in meta["_null_reasons"]["rain_support"]
    few = write_imerg(tmp_path / "few", LAST, n=5)
    (tmp_path / "b").mkdir()
    meta = _rain_run(tmp_path / "b", few)
    assert meta["flooded_km2"] > 0 and "5/8" in meta["_null_reasons"]["rain_support"]
