"""tools/rs_ems_landslide.py — per-polygon touched-cell scoring, GFS frame
selection, scar hit/precision + steep baseline (hermetic, synthetic)."""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box, mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import rs_ems_landslide as rl

T = from_origin(-50.0, -29.0, 0.01, 0.01)  # 4x4 grid; row0 y -29.00..-29.01
SHAPE = (4, 4)


def _write(path, bands: dict[str, np.ndarray], shape=SHAPE, transform=T):
    names = list(bands)
    h, w = shape
    with rasterio.open(path, "w", driver="GTiff", height=h, width=w, count=len(names),
                       dtype="float32", crs="EPSG:4326", transform=transform,
                       nodata=np.nan) as dst:
        for i, n in enumerate(names, 1):
            dst.write(bands[n].astype("float32"), i)
            dst.set_band_description(i, n)
    return path


def _fc(features):
    return {"type": "FeatureCollection", "features": features}


def _feat(geom, **props):
    return {"type": "Feature", "geometry": mapping(geom), "properties": props}


def test_poly_shares_any_cell_vs_centroid():
    """P1 touches a hot cell its centroid misses: any_cell warns, centroid not."""
    tpi = np.full(16, 0.1, dtype=np.float32)
    tpi[1] = 0.9
    domain = np.ones(16, bool)
    # asymmetric box touches cells (0,0),(0,1),(1,0),(1,1); centroid in (1,1)=idx5
    p1 = box(-49.999, -29.019, -49.9805, -29.002)
    polys = [{"geom": p1, "area_km2": 1.0, "aoi": "A", "product": "P",
              "image_date_utc": "2024-05-06"}]
    cells = [rl.touched_cells(p1, T, SHAPE, domain)]
    cents = [rl.centroid_cell(p1, T, SHAPE)]
    assert sorted(cells[0].tolist()) == [0, 1, 4, 5]
    assert cents[0] == 5
    out = rl.poly_shares(polys, cells, cents, tpi)
    assert out["any_cell"]["alto_plus"]["pct"] == 100.0
    assert out["any_cell"]["critico"]["pct"] == 100.0
    assert out["centroid_cell"]["alto_plus"]["pct"] == 0.0
    assert out["centroid_cell"]["alto_plus"]["hit_n"] == 0


def test_poly_shares_area_weighted_and_nodata():
    tpi = np.array([0.7, 0.1, np.nan, 0.1] + [0.1] * 12, dtype=np.float32)
    domain = np.ones(16, bool)
    hot = box(-49.999, -29.009, -49.991, -29.001)   # cell (0,0)=idx0
    cold = box(-49.979, -29.019, -49.971, -29.011)  # cell (1,2)=idx6
    polys = [{"geom": hot, "area_km2": 3.0, "aoi": "A", "product": "P",
              "image_date_utc": "2024-05-06"},
             {"geom": cold, "area_km2": 1.0, "aoi": "A", "product": "P",
              "image_date_utc": "2024-05-06"}]
    cells = [rl.touched_cells(p["geom"], T, SHAPE, domain) for p in polys]
    cents = [rl.centroid_cell(p["geom"], T, SHAPE) for p in polys]
    out = rl.poly_shares(polys, cells, cents, tpi)
    a = out["any_cell"]["alto_plus"]
    assert a["hit_n"] == 1 and a["pct"] == 50.0
    assert a["area_pct"] == 75.0  # 3 of 4 km2


def test_gfs_slide_frames_filters_dates_and_hazard(tmp_path):
    cog = tmp_path
    cold = np.zeros(SHAPE, dtype=np.float32)
    hot = np.full(SHAPE, 0.9, dtype=np.float32)
    issue = dt.date(2024, 4, 28)
    # d1_slide -> 04-29 (<= onset day 04-29), d2/d3 -> 04-30/05-01 excluded;
    # _flood bands ignored even when hot
    _write(cog / "rs_2024_tpi_gfs_20240428.tif",
           {"d1_slide": cold, "d1_flood": hot, "d2_slide": hot, "d3_slide": hot,
            "d2_flood": hot})
    names, arr = rl.gfs_slide_frames(cog, "rs_2024", issue, dt.date(2024, 4, 29),
                                     SHAPE, T)
    assert names == [f"issued {issue} d1_slide -> 2024-04-29"]
    assert float(np.nanmax(arr)) == 0.0
    # a missing issue -> (names, None)
    names, arr = rl.gfs_slide_frames(cog, "rs_2024", dt.date(2024, 4, 20),
                                     dt.date(2024, 4, 29), SHAPE, T)
    assert names == [] and arr is None


def _synth_root(tmp_path: Path) -> Path:
    """Minimal rs_2024 case dir: TPI grid 40x40 @0.001 deg, one AOI, two EMS
    polygons (one on hot cells), three scar candidates (one near), steep mask.

    Cell (r,c) centre: x=-52.04+0.001(c+0.5), y=-28.96-0.001(r+0.5).
    AOI extent covers cell centres rows 10..35, cols 8..30; steep = cols 8..20.
    """
    data = tmp_path / "data"
    rs = data / "rs_2024"
    (rs / "cog").mkdir(parents=True)
    (rs / "geojson").mkdir(parents=True)
    tr = from_origin(-52.04, -28.96, 0.001, 0.001)
    shape = (40, 40)
    z = np.full(shape, 0.05, dtype=np.float32)
    obs29 = z.copy(); obs30 = z.copy()
    obs29[12:17, 10:15] = 0.8      # hot block inside the AOI
    obs30[10:20, 8:20] = 0.9
    _write(rs / "cog" / "rs_2024_tpi_slide_20240502.tif",
           {d: a for d, a in zip(("20240429", "20240430", "20240501", "20240502"),
                                 (obs29, obs30, obs30, obs30))}, shape, tr)
    gfs_cold = z.copy()
    gfs_hot = z.copy(); gfs_hot[10:20, 8:20] = 0.75
    _write(rs / "cog" / "rs_2024_tpi_gfs_20240428.tif",
           {"d1_slide": gfs_hot, "d1_flood": gfs_hot,
            "d2_slide": gfs_hot, "d3_slide": gfs_hot}, shape, tr)
    _write(rs / "cog" / "rs_2024_tpi_gfs_20240427.tif",
           {"d1_slide": gfs_cold, "d1_flood": gfs_cold,
            "d2_slide": gfs_cold, "d3_slide": gfs_cold}, shape, tr)
    _write(rs / "cog" / "rs_2024_tpi_gfs_20240426.tif",
           {"d1_slide": gfs_cold, "d1_flood": gfs_cold,
            "d2_slide": gfs_cold, "d3_slide": gfs_cold}, shape, tr)
    steep = np.zeros(shape, dtype=np.float32); steep[10:36, 8:21] = 1.0
    _write(rs / "cog" / "rs_2024_t_slide_20240502.tif", {"t_slide": steep},
           shape, tr)

    aoi = box(-52.0315, -28.9955, -52.0095, -28.9705)
    (rs / "geojson" / "flood_ems_aoi.geojson").write_text(
        json.dumps(_fc([_feat(aoi, aoi="AOI01")])))
    ems1 = box(-52.0285, -28.9745, -52.0275, -28.9735)  # cells rows 13,14 cols 11,12 (hot)
    ems2 = box(-52.0175, -28.9745, -52.0165, -28.9735)  # cells rows 13,14 cols 22,23 (cold)
    (rs / "geojson" / "landslide_ems.geojson").write_text(json.dumps(_fc([
        _feat(__import__("shapely").geometry.MultiPolygon([ems1]),
              aoi="AOI01", product="GRA_PRODUCT", image_date_utc="2024-05-06"),
        _feat(ems2, aoi="AOI01", product="GRA_PRODUCT",
              image_date_utc="2024-05-06")])))
    near = ems1.buffer(0.0003)   # ~30 m away -> hit at 100/500 m
    far = box(-52.0125, -28.9805, -52.0115, -28.9795)  # ~950 m from ems2, in AOI
    outside = box(-52.0050, -28.9650, -52.0040, -28.9640)  # outside AOI
    (rs / "geojson" / "landslide_candidates.geojson").write_text(
        json.dumps(_fc([
            _feat(near, area_km2=0.02, kind="landslide_candidate"),
            _feat(far, area_km2=0.02, kind="landslide_candidate"),
            _feat(outside, area_km2=0.02, kind="landslide_candidate")])))
    (rs / "flood_ems_meta.json").write_text(json.dumps({"activation": {"id": "EMSR720"}}))
    data.joinpath("metrics.json").write_text(json.dumps({"cases": {"rs_2024": {
        "slide_warned_n": 16027, "slide_warned_gfs_24h_pct": 36.45,
        "slide_warned_gfs_48h_pct": 0.0, "slide_warned_gfs_72h_pct": 0.0,
        "slide_warned": {"by_date_observed": [
            {"date": "2024-04-29", "alto_or_critico_pct": 3.66}]},
        "sentinel_key": "keep-me"}}}))
    return data


def test_score_end_to_end_and_merge(tmp_path):
    data = _synth_root(tmp_path)
    res = rl.score(data, seeds=8)
    t = res["tpi_warned"]
    assert t["n_polygons"] == 2
    o29 = next(r for r in t["observed_by_date"] if r["band_date"] == "2024-04-29")
    # ems1 sits on hot cells -> any_cell warned; ems2 cold -> 50 %
    assert o29["any_cell"]["alto_plus"]["pct"] == 50.0
    g24 = next(r for r in t["gfs_by_lead"] if r["lead_h"] == 24)
    assert g24["any_cell"]["alto_plus"]["pct"] == 50.0
    g48 = next(r for r in t["gfs_by_lead"] if r["lead_h"] == 48)
    assert g48["gfs_frames"]  # d1..d3 all <= 04-29
    scar = res["scar"]
    assert scar["n_candidates_total"] == 3
    assert scar["n_candidates_in_aoi"] == 2          # `outside` dropped
    assert scar["observed"]["hit_100m_pct"] == 50.0  # only ems1 near a candidate
    assert scar["observed"]["hit_500m_pct"] == 50.0
    assert scar["baseline"]["seeds"] == 8
    assert res["inventory_16027"]["n_points"] == 16027

    rl.merge(data, res)
    m = json.loads((data / "metrics.json").read_text())
    rsb = m["cases"]["rs_2024"]
    assert rsb["landslide_ems_n"] == 2
    assert rsb["landslide_ems_warned_gfs_24h_pct"] == 50.0
    assert rsb["landslide_ems_warned_gfs_48h_pct"] == 0.0
    assert rsb["landslide_ems_warned_observed_pct"] == 50.0
    assert rsb["scar_ems_hit_100m_pct"] == 50.0
    assert rsb["scar_ems_baseline"]["seeds"] == 8
    assert rsb["sentinel_key"] == "keep-me"      # merge never drops keys
    assert set(m["cases"]) == {"rs_2024"}        # rs_2024 blob only


def test_baseline_deterministic(tmp_path):
    data = _synth_root(tmp_path)
    a = rl.score(data, seeds=8)["scar"]["baseline"]
    b = rl.score(data, seeds=8)["scar"]["baseline"]
    assert a == b
