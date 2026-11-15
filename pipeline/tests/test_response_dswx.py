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


def make_run(tmp_path, catalogue, now, gpw=None, **kw):
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
    assert meta["flooded_km2"] == pytest.approx(meta["new_km2"]) and meta["flooded_km2"] > 0
    assert meta["receded_km2"] is None and "receded_km2" in meta["_null_reasons"]
    fc = json.loads((tmp_path / "flood_dswx.geojson").read_text())
    props = [f["properties"] for f in fc["features"]]
    assert props and all(p["status"] == "candidate" and p["change"] == "new" for p in props)
    assert all(p["source"] == "opera-dswx-s1" and p["granules"] == ["G004"] for p in props)
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
