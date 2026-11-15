"""tp.response_dist — DIST-ALERT landslide scar candidates (hermetic).

Synthetic 40x40 UTM 22S tile (30 m) with injected status/date layers; the
steep and land-cover rasters are written in EPSG:4326 over the tile so the
real reprojection path runs.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pytest
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin
from tp import response_dist as rd

X0, Y0, RES, N = 500000.0, 6750000.0, 30.0, 40          # UTM 22S, ~ -51 E, -29.4 S
T = from_origin(X0, Y0, RES, RES)
CRS = "EPSG:32722"
START, END = dt.date(2024, 4, 27), dt.date(2024, 6, 15)
IN_WIN = (dt.date(2024, 5, 6) - rd.DATE_BASE).days
OLD = (dt.date(2023, 6, 1) - rd.DATE_BASE).days


def _ll_bounds():
    tr = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True)
    xs, ys = tr.transform([X0, X0 + N * RES, X0, X0 + N * RES], [Y0, Y0, Y0 - N * RES, Y0 - N * RES])
    return min(xs) - 0.01, min(ys) - 0.01, max(xs) + 0.01, max(ys) + 0.01


def _write_ll(path, fill, west_frac_value=None):
    """4326 raster over the tile; optional value on the WEST half."""
    w, s, e, n = _ll_bounds()
    res = 0.0001
    h, wd = int(round((n - s) / res)), int(round((e - w) / res))
    a = np.full((h, wd), fill, np.uint8)
    if west_frac_value is not None:
        a[:, : wd // 2] = west_frac_value
    with rasterio.open(path, "w", driver="GTiff", height=h, width=wd, count=1, dtype="uint8",
                       crs="EPSG:4326", transform=from_origin(w, n, res, res), nodata=255) as dst:
        dst.write(a, 1)
    return path


def _tile():
    status = np.zeros((N, N), np.uint8)
    date = np.zeros((N, N), np.int16)
    # A: 4x4 confirmed >=50 % loss, in window, east half      -> candidate
    status[5:9, 30:34] = 6
    date[5:9, 30:34] = IN_WIN
    # B: same but OLD date                                    -> dropped (date)
    status[15:19, 30:34] = 6
    date[15:19, 30:34] = OLD
    # C: <50 % loss (status 3), in window                     -> dropped (status)
    status[25:29, 30:34] = 3
    date[25:29, 30:34] = IN_WIN
    # D: 1 px in window                                       -> dropped (< MIN_PX)
    status[35, 35] = 6
    date[35, 35] = IN_WIN
    # E: 4x4 in window on the WEST half (flat or cropland)   -> dropped (gate)
    status[5:9, 2:6] = 6
    date[5:9, 2:6] = IN_WIN
    return {"VEG-DIST-STATUS": (status, T, CRS), "VEG-DIST-DATE": (date, T, CRS)}


G = {"id": "OPERA_L3_DIST-ALERT-HLS_T22JXX_x", "tile": "T22JXX", "acquired": "2024-06-12T13:00:00Z", "base": ""}


def test_gates_keep_only_steep_new_strong_loss(tmp_path):
    layers = _tile()
    steep = _write_ll(tmp_path / "steep.tif", 1, west_frac_value=0)      # west half flat
    lc = _write_ll(tmp_path / "lc.tif", 10)                               # grassland
    res = rd.tile_candidates(G, START, END, steep, lc, reader=lambda layer: layers[layer])
    assert len(res["features"]) == 1
    f = res["features"][0]
    assert f["n_px"] == 16 and f["area_km2"] == pytest.approx(16 * 0.0009)
    assert f["date"] == "2024-05-06"
    st = res["stats"]
    assert st["flat_px"] == 16 and st["too_small"] == 1 and st["kept_px"] == 17


def test_cropland_gate(tmp_path):
    layers = _tile()
    steep = _write_ll(tmp_path / "steep.tif", 1)                          # everything steep
    lc = _write_ll(tmp_path / "lc.tif", 10, west_frac_value=12)           # west half cropland
    res = rd.tile_candidates(G, START, END, steep, lc, reader=lambda layer: layers[layer])
    assert len(res["features"]) == 1 and res["stats"]["crop_px"] == 16


def test_run_dedupes_overlapping_tiles_and_attaches_municipality(tmp_path):
    layers = _tile()
    steep = _write_ll(tmp_path / "steep.tif", 1, west_frac_value=0)
    lc = _write_ll(tmp_path / "lc.tif", 10)
    g2 = {**G, "id": G["id"] + "b", "tile": "T22JYY"}                     # same pixels, 2nd tile
    w, s, e, n = _ll_bounds()
    munis = {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {
        "name": "Alpha", "code": "4300001", "uf": "RS"}, "geometry": {"type": "Polygon", "coordinates": [
        [[w, s], [e, s], [e, n], [w, n], [w, s]]]}}]}
    fc, meta = rd.run((w, s, e, n), START, END, steep, lc, granules=[G, g2], munis_fc=munis,
                      token="", reader_for=lambda g: (lambda layer: layers[layer]), log=lambda *_: None)
    assert meta["features"] == 1 and meta["n_tiles"] == 2 and meta["n_tiles_failed"] == 0
    p = fc["features"][0]["properties"]
    assert p["status"] == "candidate" and p["kind"] == "landslide_candidate"
    assert p["municipality"] == "Alpha" and p["uf"] == "RS"
    assert p["people"] is None and "people" in meta["_null_reasons"]


def test_failed_tile_is_reported_not_fatal(tmp_path):
    steep = _write_ll(tmp_path / "steep.tif", 1)
    lc = _write_ll(tmp_path / "lc.tif", 10)

    def boom(layer):
        raise OSError("HTTP 500")

    fc, meta = rd.run(_ll_bounds(), START, END, steep, lc, granules=[G], token="",
                      reader_for=lambda g: boom, log=lambda *_: None)
    assert meta["features"] == 0 and meta["n_tiles_failed"] == 1
    assert "tiles_failed" in meta["_null_reasons"]


def test_latest_per_tile_and_hit_rates():
    gs = [{"id": "a", "tile": "T1", "acquired": "2024-06-01T00:00:00Z"},
          {"id": "b", "tile": "T1", "acquired": "2024-06-10T00:00:00Z"},
          {"id": "c", "tile": "T2", "acquired": "2024-05-01T00:00:00Z"}]
    assert {t: g["id"] for t, g in rd.latest_per_tile(gs).items()} == {"T1": "b", "T2": "c"}
    # 0.001 deg square at (-51, -29.5); points ~0, ~300 m and ~2 km away
    sq = {"type": "Polygon", "coordinates": [[[-51.0, -29.5], [-50.999, -29.5], [-50.999, -29.501],
                                              [-51.0, -29.501], [-51.0, -29.5]]]}
    fc = {"features": [{"geometry": sq}]}
    v = rd.hit_rates([(-50.9995, -29.5005), (-50.996, -29.5005), (-50.98, -29.5005)], fc)
    assert v["hit_100m_pct"] == pytest.approx(100 / 3, abs=0.01)
    assert v["hit_500m_pct"] == pytest.approx(200 / 3, abs=0.01)
    assert v["precision_500m_pct"] == 100.0
