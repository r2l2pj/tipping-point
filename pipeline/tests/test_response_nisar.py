"""Hermetic tests for the NISAR GCOV Now-domain response (unit R2).

Synthetic GCOV-shaped HDF5 files (chunked + shuffle + gzip, like the real
production granules) and a synthetic GPW grid; no network.
"""
import json
from datetime import UTC, datetime, timedelta

import h5py
import numpy as np
import pytest
import rasterio
from fetch import nisar as fn
from rasterio.transform import from_origin
from rasterio.warp import transform_bounds
from tp import response_nisar as rn

EPSG = 32722
X0, Y0, NATIVE = 500000.0, 7300000.0, 10.0
N = 400                       # 4 km x 4 km at 10 m -> 40 x 40 at 100 m
LAND, WATER = 10 ** (-8 / 10), 10 ** (-20 / 10)
G_PRE = "NISAR_L2_PR_GCOV_034_024_D_101_4005_DHDH_A_20261025T211603_20261025T211610_P05036_N_F_J_001"
G_POST = "NISAR_L2_PR_GCOV_035_024_D_101_4005_DHDH_A_20261106T211603_20261106T211610_P05036_N_F_J_001"


def _granule_bbox():
    return transform_bounds(f"EPSG:{EPSG}", "EPSG:4326", X0, Y0 - N * NATIVE, X0 + N * NATIVE, Y0)


def write_gcov(path, hh, term="HHHH", chunks=(64, 64), epsg=EPSG):
    g = h5py.File(path, "w").create_group(rn.GCOV_GRIDS)
    g.create_dataset(term, data=hh.astype("float32"), chunks=chunks, shuffle=True,
                     compression="gzip", compression_opts=1, fillvalue=0.0)
    g.create_dataset("xCoordinates", data=X0 + NATIVE / 2 + NATIVE * np.arange(hh.shape[1]))
    g.create_dataset("yCoordinates", data=Y0 - NATIVE / 2 - NATIVE * np.arange(hh.shape[0]))
    g.create_dataset("listOfCovarianceTerms", data=np.array([term.encode(), b"HVHV"]))
    p = g.create_dataset("projection", data=np.uint32(epsg))
    p.attrs["epsg_code"] = np.int64(epsg)
    g.file.close()
    return path


def write_gpw(path, persons_per_cell=100.0):
    w, s, e, n = _granule_bbox()
    w, s, e, n = w - 0.05, s - 0.05, e + 0.05, n + 0.05
    nx, ny = int(np.ceil((e - w) / 0.01)), int(np.ceil((n - s) / 0.01))
    with rasterio.open(path, "w", driver="GTiff", width=nx, height=ny, count=1, dtype="float32",
                       crs="EPSG:4326", transform=from_origin(w, n, 0.01, 0.01), nodata=np.nan) as d:
        d.write(np.full((ny, nx), persons_per_cell, np.float32), 1)
    return path


def scenes():
    rng = np.random.default_rng(0)
    pre = np.full((N, N), LAND) * rng.uniform(0.8, 1.2, (N, N))
    pre[0:100, 0:100] = WATER          # permanent lake: water in pre AND post
    post = pre.copy()
    post[200:300, 200:300] = WATER     # new flood: 1 km2
    pre[:, -6:] = 0.0                  # fill/nodata stripe: last block 40% valid
    post[:, -6:] = 0.0
    return pre, post


def _g(gid, h5_url="x.h5", available="2026-11-11T00:58:14Z", fp=None):
    item = {"umm": {
        "GranuleUR": gid,
        "RelatedUrls": [{"Type": "GET DATA", "URL": f"https://example.test/{gid}.h5"}],
        "ProviderDates": [{"Type": "Insert", "Date": available}],
    }}
    if fp is not None:
        w, s, e, n = fp
        item["umm"]["SpatialExtent"] = {"HorizontalSpatialDomain": {"Geometry": {"GPolygons": [
            {"Boundary": {"Points": [
                {"Longitude": w, "Latitude": s}, {"Longitude": e, "Latitude": s},
                {"Longitude": e, "Latitude": n}, {"Longitude": w, "Latitude": n},
                {"Longitude": w, "Latitude": s}]}}]}}}
    g = fn.cmr_gcov_granule(item)
    g["h5_url"] = h5_url
    return g


# -- chunk decode / block average -------------------------------------------
def test_block_average_matches_naive_mean(tmp_path):
    pre, _ = scenes()
    path = write_gcov(tmp_path / "a.h5", pre)
    bbox = _granule_bbox()
    with rn.open_gcov(str(path)) as (h5, rr):
        info = rn.granule_grid_info(h5)
        grid = rn.target_grid(info["x"], info["y"], info["epsg"], bbox, 100.0)
        mean, io = rn.block_average(h5[f"{rn.GCOV_GRIDS}/HHHH"], info["x"], info["y"], grid, rr,
                                    batch_bytes=4096)
    assert (grid.width, grid.height) == (40, 40)
    assert grid.left == X0 and grid.top == Y0
    a = np.where(pre > 0, pre, np.nan).astype(np.float32).reshape(40, 10, 40, 10)
    naive = np.nanmean(a, axis=(1, 3))
    np.testing.assert_allclose(mean[:, :39], naive[:, :39], rtol=1e-5)
    assert np.isnan(mean[:, 39]).all()           # 40 of 100 native px valid < 50%
    assert io["chunks"] == 49 and io["bytes"] > 0


def test_block_average_drops_sub_noise_floor_samples(tmp_path):
    hh = np.full((N, N), LAND, np.float32)
    hh[:, :200] = 1e-6                       # -60 dB edge population -> nodata
    hh[0:10, 200:210] = 1e-6                 # one block 100% sub-floor -> NaN
    path = write_gcov(tmp_path / "a.h5", hh)
    with rn.open_gcov(str(path)) as (h5, rr):
        info = rn.granule_grid_info(h5)
        grid = rn.target_grid(info["x"], info["y"], info["epsg"], _granule_bbox(), 100.0)
        mean, _ = rn.block_average(h5[f"{rn.GCOV_GRIDS}/HHHH"], info["x"], info["y"], grid, rr)
    assert np.isnan(mean[:, :20]).all() and np.isnan(mean[0, 20])
    np.testing.assert_allclose(mean[1:, 20:], LAND, rtol=1e-6)


def test_block_average_rejects_non_multiple_res(tmp_path):
    path = write_gcov(tmp_path / "a.h5", scenes()[0])
    with rn.open_gcov(str(path)) as (h5, rr):
        info = rn.granule_grid_info(h5)
        grid = rn.target_grid(info["x"], info["y"], info["epsg"], _granule_bbox(), 105.0)
        with pytest.raises(ValueError, match="multiple"):
            rn.block_average(h5[f"{rn.GCOV_GRIDS}/HHHH"], info["x"], info["y"], grid, rr)


def test_target_grid_clips_to_bbox_and_none_outside():
    x = X0 + 5 + 10 * np.arange(N)
    y = Y0 - 5 - 10 * np.arange(N)
    w, s, e, n = _granule_bbox()
    half = rn.target_grid(x, y, EPSG, (w, s, (w + e) / 2, n), 100.0)
    assert 18 <= half.width <= 22 and half.height == 40
    assert rn.target_grid(x, y, EPSG, (10.0, 10.0, 11.0, 11.0), 100.0) is None


# -- CMR parsing, search paging, pairing ------------------------------------
def test_cmr_granule_parse():
    g = _g(G_POST)
    assert (g["cycle"], g["track"], g["look"], g["frame"], g["coverage"]) == (35, 24, "D", 101, "F")
    assert g["start"] == datetime(2026, 11, 6, 21, 16, 3, tzinfo=UTC)
    assert g["available"] == datetime(2026, 11, 11, 0, 58, 14, tzinfo=UTC)
    assert fn.cmr_gcov_granule({"umm": {"GranuleUR": "NISAR_L0B_RRSD_whatever"}}) is None


def test_search_pages_with_search_after():
    calls = []

    class R:
        def __init__(self, items, after):
            self._items, self.headers = items, ({"CMR-Search-After": after} if after else {})

        def raise_for_status(self):
            pass

        def json(self):
            return {"items": self._items}

    def item(gid):
        return {"umm": {"GranuleUR": gid, "RelatedUrls": []}}

    pages = [R([item(G_POST), item("junk")], "tok"), R([item(G_PRE)], None)]

    def get(url, params, headers, timeout):
        calls.append((params["short_name"], dict(headers)))
        return pages[len(calls) - 1]

    out = fn.search_gcov_domain((-58, -34, -48, -22.5), "2026-10-01T00:00:00Z", get=get, page_size=2)
    assert [g["granule_id"] for g in out] == [G_POST, G_PRE]
    assert calls[0] == ("NISAR_L2_GCOV_V1", {}) and calls[1][1] == {"CMR-Search-After": "tok"}


def test_pair_same_track_frame_direction_within_gap():
    fp = (-50.0, -26.0, -49.0, -25.0)
    bbox = (-58, -34, -48, -22.5)
    post = _g(G_POST, fp=fp)
    pre = _g(G_PRE, fp=fp)
    older = _g(G_PRE.replace("034_024", "033_024").replace("20261025", "20261013"), fp=fp)
    other_frame = _g(G_PRE.replace("_D_101_", "_D_102_"), fp=fp)
    asc = _g(G_PRE.replace("_D_101_", "_A_101_"), fp=fp)
    res = fn.pair_latest_pass([older, other_frame, asc, post, pre], bbox)
    assert len(res["pairs"]) == 1
    assert res["pairs"][0]["pre"]["granule_id"] == G_PRE
    assert res["pairs"][0]["post"]["granule_id"] == G_POST


def test_pair_rejects_gap_and_reports_unpaired_newer_pass():
    fp = (-50.0, -26.0, -49.0, -25.0)
    bbox = (-58, -34, -48, -22.5)
    newest = _g(G_POST.replace("035_024", "036_030").replace("20261106", "20261110"), fp=fp)
    post = _g(G_POST, fp=fp)
    too_old = _g(G_PRE.replace("034_024", "031_024").replace("20261025", "20260920"), fp=fp)
    res = fn.pair_latest_pass([newest, post, too_old], bbox)
    assert res["pairs"] == []
    assert res["pass"] is None
    assert len(res["skipped"]) == 3
    assert "within 36 d" in res["skipped"][1]["reasons"][0]


def test_pair_prefers_full_frame_and_skips_sliver_overlap():
    bbox = (-58, -34, -48, -22.5)
    fp = (-50.0, -26.0, -49.0, -25.0)
    partial = _g(G_POST.replace("_N_F_", "_N_P_"), fp=fp)
    full = _g(G_POST, fp=fp)
    pre = _g(G_PRE, fp=fp)
    res = fn.pair_latest_pass([partial, full, pre], bbox)
    assert res["pairs"][0]["post"]["coverage"] == "F"
    sliver = (-48.01, -26.0, -47.0, -25.0)       # ~1 km wide inside the domain
    res = fn.pair_latest_pass([_g(G_POST, fp=sliver), _g(G_PRE, fp=sliver)], bbox)
    assert res["pairs"] == [] and "overlap" in res["skipped"][0]["reasons"][0]


# -- end to end (synthetic granules) ----------------------------------------
@pytest.fixture
def synthetic(tmp_path):
    pre, post = scenes()
    pre_p = write_gcov(tmp_path / "pre.h5", pre)
    post_p = write_gcov(tmp_path / "post.h5", post)
    gpw_p = write_gpw(tmp_path / "gpw.tif")
    fp = _granule_bbox()
    granules = [_g(G_POST, h5_url=str(post_p), fp=fp), _g(G_PRE, h5_url=str(pre_p), fp=fp)]
    return {"granules": granules, "gpw": str(gpw_p), "bbox": (-58, -34, -48, -22.5)}


def _run(synthetic, out, **kw):
    return rn.run_response(
        synthetic["bbox"], out,
        search=lambda bb, s, e: synthetic["granules"],
        pair=lambda gs, bb: fn.pair_latest_pass(gs, bb, min_overlap_km2=1.0),
        gpw_src=kw.pop("gpw_src", synthetic["gpw"]),
        now=datetime(2026, 11, 15, 3, 0, tzinfo=UTC), log=lambda m: None, **kw)


def test_run_response_end_to_end(synthetic, tmp_path):
    out = tmp_path / "response"
    meta = _run(synthetic, out)
    fc = json.loads((out / "flood_nisar.geojson").read_text())
    on_disk = json.loads((out / "nisar_meta.json").read_text())
    assert on_disk == meta
    open_w = [f for f in fc["features"] if f["properties"]["flood_type"] == "open-water"]
    assert len(open_w) == 1
    p = open_w[0]["properties"]
    assert p["source"] == "nisar-gcov" and p["status"] == "candidate"
    assert p["pre"] == G_PRE and p["post"] == G_POST
    assert (p["track"], p["frame"], p["acquired"]) == (24, 101, "2026-11-06T21:16:03Z")
    assert p["area_km2"] == pytest.approx(1.0, abs=0.05)
    # 1 km2 at ~100 persons per 0.01 deg cell (~1.13 km2 at 24.4 S)
    assert 80 <= p["people"] <= 95
    # the permanent lake (water before AND after) is not a flood
    lon_min = min(c[0] for c in open_w[0]["geometry"]["coordinates"][0])
    _, _, lake_e, _ = transform_bounds(f"EPSG:{EPSG}", "EPSG:4326", X0, Y0 - 1000, X0 + 1000, Y0)
    assert lon_min > lake_e
    assert all(f["properties"]["status"] != "confirmed" for f in fc["features"])
    assert meta["latest_acquired"] == "2026-11-06T21:16:03Z"
    assert meta["latency_h"] == pytest.approx(99.7, abs=0.1)
    assert meta["open_water_km2"] == pytest.approx(1.0, abs=0.05)
    assert meta["people_flooded"] == sum(f["properties"]["people"] for f in fc["features"])
    assert meta["pairs"][0]["term"] == "HHHH" and meta["pairs"][0]["grid"]["res"] == 100.0
    assert meta["receded_km2"] == 0.0
    assert "people_flooded" not in meta["_null_reasons"]


def test_run_response_receded_is_reverse_change(synthetic, tmp_path):
    synthetic["granules"][0]["h5_url"], synthetic["granules"][1]["h5_url"] = (
        synthetic["granules"][1]["h5_url"], synthetic["granules"][0]["h5_url"])
    meta = _run(synthetic, tmp_path / "r")
    assert meta["open_water_km2"] == 0.0
    assert meta["receded_km2"] == pytest.approx(1.0, abs=0.05)


def test_run_response_no_pair_writes_null_meta_only(synthetic, tmp_path):
    out = tmp_path / "response"
    out.mkdir()
    (out / "flood_nisar.geojson").write_text("stale")
    synthetic["granules"] = synthetic["granules"][:1]          # post only
    meta = _run(synthetic, out)
    assert not (out / "flood_nisar.geojson").exists()
    assert meta["flooded_km2"] is None and meta["people_flooded"] is None
    assert meta["latest_acquired"] is None and meta["geojson"] is None
    assert "pairs" in meta["_null_reasons"] and "flooded_km2" in meta["_null_reasons"]
    assert json.loads((out / "nisar_meta.json").read_text())["_null_reasons"]


def test_run_response_people_null_when_gpw_missing(synthetic, tmp_path):
    meta = _run(synthetic, tmp_path / "r", gpw_src=str(tmp_path / "missing.tif"))
    fc = json.loads((tmp_path / "r" / "flood_nisar.geojson").read_text())
    assert meta["people_flooded"] is None and "people_flooded" in meta["_null_reasons"]
    assert all(f["properties"]["people"] is None for f in fc["features"])
    assert meta["flooded_km2"] > 0


def test_run_response_search_failure_is_null_reason(synthetic, tmp_path):
    def boom(*a):
        raise ConnectionError("down")

    meta = rn.run_response(synthetic["bbox"], tmp_path / "r", search=boom,
                           pair=fn.pair_latest_pass, now=datetime.now(UTC) - timedelta(0),
                           log=lambda m: None)
    assert meta["_null_reasons"]["search"].startswith("CMR search failed")
    assert meta["flooded_km2"] is None
