"""tools/rs_ems_validation.py — rasterization, confusion metrics, flagged-before-onset
(hermetic: synthetic 4x4 grid, synthetic EMS geojson + meta)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import rs_ems_validation as rev

T = from_origin(-50.0, -29.0, 0.01, 0.01)  # 4x4 grid, ~1 km cells


def _write(path, bands: dict[str, np.ndarray]):
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=4,
        width=4,
        count=len(bands),
        dtype="float32",
        crs="EPSG:4326",
        transform=T,
        nodata=np.nan,
    ) as dst:
        for i, n in enumerate(bands, 1):
            dst.write(bands[n].astype("float32"), i)
            dst.set_band_description(i, n)
    return path


def _square(x0, y0, x1, y1):
    return {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}


def _mk_case(tmp_path):
    """Synthetic case: flood square over cells rows 0-1, cols 0-1 (top-left 2x2);
    AOI covers the whole grid."""
    rs = tmp_path / "rs_2024"
    (rs / "cog").mkdir(parents=True)
    (rs / "geojson").mkdir(parents=True)
    hot, crit = np.zeros((4, 4)), np.zeros((4, 4))
    hot[0, 0] = 0.5  # one Alto+ cell inside the flood square
    hot[2, 2] = 0.5  # one Alto+ cell outside (fp)
    crit[0, 0] = 0.7
    crit[0, 1] = 0.7  # two Critico cells inside
    obs = np.zeros((4, 4))
    obs[0, 0] = 0.5  # onset-day observed: one cell Alto+
    _write(
        rs / "cog" / "rs_2024_tpi_flood_20240502.tif",
        {
            "20240427": np.zeros((4, 4)),
            "20240428": np.zeros((4, 4)),
            "20240429": obs,
            "20240505": hot,
            "20240506": hot + crit,
        },
    )
    # GFS issued 2024-05-05 (24 h before E=2024-05-06): d1 -> 05-06, d2 -> 05-07 (>E, ignored)
    _write(
        rs / "cog" / "rs_2024_tpi_gfs_20240505.tif",
        {
            "d1_slide": np.zeros((4, 4)),
            "d1_flood": np.zeros((4, 4)),
            "d2_slide": np.ones((4, 4)),
            "d2_flood": np.ones((4, 4)),
        },
    )
    # GFS issued 2024-04-28: d1 -> 04-29 hot in cell (0,0)
    g = np.zeros((4, 4))
    g[0, 0] = 0.5
    _write(
        rs / "cog" / "rs_2024_tpi_gfs_20240428.tif",
        {
            "d1_slide": np.zeros((4, 4)),
            "d1_flood": g,
            "d2_slide": np.zeros((4, 4)),
            "d2_flood": np.zeros((4, 4)),
            "d3_slide": np.zeros((4, 4)),
            "d3_flood": np.zeros((4, 4)),
        },
    )
    _write(
        rs / "cog" / "rs_2024_tpi_gfs_20240427.tif",
        {
            "d1_slide": np.zeros((4, 4)),
            "d1_flood": np.zeros((4, 4)),
            "d2_slide": np.zeros((4, 4)),
            "d2_flood": np.zeros((4, 4)),
            "d3_slide": np.zeros((4, 4)),
            "d3_flood": np.zeros((4, 4)),
        },
    )
    _write(
        rs / "cog" / "rs_2024_tpi_gfs_20240426.tif",
        {
            "d1_slide": np.zeros((4, 4)),
            "d1_flood": np.zeros((4, 4)),
            "d2_slide": np.zeros((4, 4)),
            "d2_flood": np.zeros((4, 4)),
            "d3_slide": np.zeros((4, 4)),
            "d3_flood": np.zeros((4, 4)),
        },
    )
    flood = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": _square(-50.0, -29.02, -49.98, -29.0),
                "properties": {
                    "aoi": "AOI01",
                    "product": "GRA_PRODUCT",
                    "image_date_utc": "2024-05-06",
                    "sensor": "Pleiades Neo",
                    "source": rev.SOURCE,
                    "status": rev.STATUS,
                },
            }
        ],
    }
    (rs / "geojson" / "flood_ems.geojson").write_text(json.dumps(flood))
    aoi_fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": _square(-50.0, -29.04, -49.96, -29.0),
                "properties": {"aoi": "AOI01"},
            }
        ],
    }
    (rs / "geojson" / "flood_ems_aoi.geojson").write_text(json.dumps(aoi_fc))
    meta = {
        "activation": {"id": "EMSR720"},
        "licence": rev.LICENCE,
        "aois": [
            {
                "aoi": "AOI01",
                "name": "Synthetic",
                "extent_wkt": "POLYGON ((-50 -29.04, -49.96 -29.04, -49.96 -29, -50 -29, -50 -29.04))",
            }
        ],
    }
    (rs / "flood_ems_meta.json").write_text(json.dumps(meta))
    return tmp_path


def test_rasterize_center_vs_area_weighted():
    # flood covers exactly the top-left 2x2 cells
    geoms = [_square(-50.0, -29.02, -49.98, -29.0)]
    m = rev.rasterize_mask(geoms, T, (4, 4), all_touched=False)
    assert m.sum() == 4 and m[:2, :2].all() and not m[2:, :].any()
    # thin sliver along the left edge: <50% of each cell -> dropped by area weighting
    sliver = [_square(-50.0, -29.04, -49.999, -29.0)]
    aw = rev.rasterize_area_weighted(_square_geoms(sliver), T, (4, 4))
    assert not aw.any()
    ct = rev.rasterize_mask(_square_geoms(sliver), T, (4, 4), all_touched=False)
    assert ct.sum() == 0  # sliver never reaches a cell centre


def _square_geoms(sqs):
    import shapely.geometry

    return [shapely.geometry.shape(s) for s in sqs]


def test_confusion_known_values():
    pred = np.array([[1, 1], [0, 0]], bool)
    truth = np.array([[1, 0], [1, 0]], bool)
    dom = np.ones((2, 2), bool)
    c = rev.confusion(pred, truth, dom)
    assert c["tp"] == 1 and c["fp"] == 1 and c["fn"] == 1
    assert c["iou"] == round(1 / 3, 4) and c["precision"] == 0.5 and c["recall"] == 0.5
    assert c["f1"] == 0.5


def test_score_end_to_end(tmp_path):
    root = _mk_case(tmp_path)
    res = rev.score(root)
    rows = res["rows"]
    d = "2024-05-06"
    r_obs = next(
        r
        for r in rows
        if r["image_date_utc"] == d
        and r["rasterization"] == "center"
        and r["threshold"] == "alto_plus"
        and r["tpi_source"] == "observed"
    )
    # truth = 4 cells; pred Alto+ on 05-06 = hot(0,0)+hot(2,2)+crit(0,0),(0,1) -> 3 cells,
    # tp=(0,0),(0,1)=2, fp=(2,2)=1, fn=(1,0),(1,1)=2
    assert r_obs["tp"] == 2 and r_obs["fp"] == 1 and r_obs["fn"] == 2
    assert r_obs["iou"] == round(2 / 5, 4)
    r_gfs = next(
        r
        for r in rows
        if r["image_date_utc"] == d
        and r["rasterization"] == "center"
        and r["threshold"] == "alto_plus"
        and r["tpi_source"] == "gfs_issued_24h"
    )
    # gfs issue 05-05: d1_flood all zeros (d2 -> 05-07 > E excluded) -> no flagged cells
    assert r_gfs["gfs_frames"] == ["issued 2024-05-05 d1_flood -> 2024-05-06"]
    assert r_gfs["tp"] == 0
    assert res["headline"]["image_date_utc"] == d
    fb = res["flagged_before_onset"]
    # observed band 2024-04-29 has one Alto+ cell inside flood -> 25 % of 4 cells' area
    assert fb["observed_band_20240429"]["alto_or_critico_pct"] == 25.0
    # gfs issued 2024-04-28 d1_flood hot at (0,0) -> 25 %; 04-27/04-26 cold
    assert fb["gfs_issued"]["2024-04-28"]["alto_or_critico_pct"] == 25.0
    assert fb["gfs_issued"]["2024-04-27"]["alto_or_critico_pct"] == 0.0
    assert fb["gfs_any_issue_alto_or_critico_pct"] == 25.0


def test_merge_writes_only_rs_2024_keys(tmp_path):
    root = _mk_case(tmp_path)
    res = rev.score(root)
    mpath = root / "metrics.json"
    mpath.write_text(
        json.dumps(
            {
                "cases": {
                    "rs_2024": {"flooded_km2": 1.5, "flood_warned_pct": None},
                    "wa_2025": {"flooded_km2": 9.9},
                }
            }
        )
    )
    rev.merge(root, res)
    m = json.loads(mpath.read_text())["cases"]
    assert m["rs_2024"]["flooded_km2"] == 1.5  # untouched
    assert m["rs_2024"]["flood_warned_pct"] is None  # untouched
    assert m["wa_2025"]["flooded_km2"] == 9.9  # other case untouched
    assert m["rs_2024"]["flood_ems_iou"] == round(2 / 5, 4)
    assert m["rs_2024"]["flood_ems_flagged_before_onset_pct"] == 25.0
    assert m["rs_2024"]["flood_ems"]["flagged_before_onset"]["gfs_any_issue_critico_pct"] == 0.0
    assert "Taquari" in m["rs_2024"]["flood_ems_method"]
