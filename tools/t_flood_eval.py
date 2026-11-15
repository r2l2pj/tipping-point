"""EVALUATION ONLY (no serving change): candidate T_flood vs the live one.

live T_flood  = 1 where HAND <= 5 m, ramp to 0 at 15 m; 0 where slope > 5 deg (as served)
candidate     = clip(1 - HAND / 30 m, 0, 1); 0 where the **90 m** slope > 5 deg
TPI_flood     = R^wR * S^wS * T^wT (live.tpi weights). Only T changes; R and S are the
case's own published series (R/S bilinear to the 90 m grid), checked to reproduce the
published TPI first.

RS 2024: vs Copernicus EMS EMSR720 flood extent (5 Taquari AOIs), event-period max TPI.
rs_uruguai_2026: obs replay COGs (bands tpi, r, s, t at 0.01 deg); candidate T aggregated
from 90 m; Sep 30 critical, daily High+ share (false-alarm days), lift on the 7 NISAR
flood polygons. 90 m slope + HAND from the S+SE Brazil static90 stack (ML cache).

    PYTHONPATH=.:pipeline python tools/t_flood_eval.py [--out ml/models/t_flood_eval.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
for p in (str(REPO), str(REPO / "pipeline")):
    if p not in sys.path:
        sys.path.insert(0, p)

STATIC90 = Path.home() / ".cache/tipping-point-ml/static90/static90.vrt"
RELEASE = "https://github.com/r2l2pj/tipping-point-data/releases/download/static-v1"
HIGH, CRIT = 0.4, 0.6


def t_candidate(hand, slope):
    t = np.clip(1.0 - hand / 30.0, 0.0, 1.0)
    return np.where(slope > 5.0, 0.0, t)


def static90_on(transform, shape, crs="EPSG:4326"):
    """90 m slope (static90 NASADEM stack) + 90 m HAND (static-v1 hand_90m.tif) on the grid."""
    import rasterio
    from rasterio.warp import Resampling, reproject

    names = json.loads((STATIC90.parent / "index.json").read_text())["bands"]
    slope = np.full(shape, np.nan, np.float32)
    with rasterio.open(STATIC90) as src:
        reproject(rasterio.band(src, names.index("slope") + 1), slope, dst_transform=transform,
                  dst_crs=crs, resampling=Resampling.nearest)
    hand = np.full(shape, np.nan, np.float32)
    with rasterio.open(f"/vsicurl/{RELEASE}/hand_90m.tif") as src:
        reproject(rasterio.band(src, 1), hand, dst_transform=transform, dst_crs=crs,
                  resampling=Resampling.nearest)
    return hand.astype(float), slope.astype(float)


def to_grid(path, band, transform, shape):
    import rasterio
    from rasterio.warp import Resampling, reproject

    a = np.full(shape, np.nan, np.float32)
    with rasterio.open(path) as src:
        reproject(rasterio.band(src, band), a, dst_transform=transform, dst_crs="EPSG:4326",
                  resampling=Resampling.bilinear)
    return a.astype(float)


def auc(x, y):
    from sklearn.metrics import roc_auc_score
    ok = np.isfinite(x)
    return round(float(roc_auc_score(y[ok], x[ok])), 4) if len(np.unique(y[ok])) == 2 else None


def block(tpi, truth, inside, px_km2):
    sel = inside & np.isfinite(tpi)
    y, x = truth[sel], tpi[sel]
    hi = x > HIGH
    hit = float(hi[y].mean()) if y.any() else None
    share = float(hi.mean())
    return {"auc": auc(x, y), "truth_high_pct": None if hit is None else round(100 * hit, 2),
            "truth_crit_pct": round(100 * float((x[y] > CRIT).mean()), 2) if y.any() else None,
            "flagged_high_pct_of_area": round(100 * share, 2),
            "lift_high": round(hit / share, 2) if hit and share else None,
            "flagged_high_km2": round(float(hi.sum() * px_km2), 1)}


def rs_2024():
    import geopandas as gpd
    import rasterio
    from rasterio.features import geometry_mask

    from live.tpi import TPI_WEIGHTS
    wr, ws, wt = TPI_WEIGHTS
    d = REPO / "data/rs_2024/cog"
    with rasterio.open(d / "rs_2024_tpi_flood_20240502.tif") as src:
        tr, shape, dates = src.transform, (src.height, src.width), list(src.descriptions)
        tpi_pub = {dd: src.read(i + 1).astype(float) for i, dd in enumerate(dates)}
    with rasterio.open(d / "rs_2024_t_flood_20240502.tif") as src:
        t_old = src.read(1).astype(float)
    hand, slope = static90_on(tr, shape)
    t_new = t_candidate(hand, slope)
    from live.tpi import terrain_flood
    t_ctl = terrain_flood(hand, slope)   # control: live formula on the same 90 m inputs
    aoi = gpd.read_file(REPO / "data/rs_2024/geojson/flood_ems_aoi.geojson").to_crs(4326)
    fl = gpd.read_file(REPO / "data/rs_2024/geojson/flood_ems.geojson").to_crs(4326)
    inside = ~geometry_mask(aoi.geometry, out_shape=shape, transform=tr)
    truth = ~geometry_mask(fl.geometry, out_shape=shape, transform=tr) & inside
    lat_c = tr.f + tr.e * shape[0] / 2
    px_km2 = abs(tr.a) * 111.32 * np.cos(np.radians(lat_c)) * abs(tr.e) * 110.57
    event = [x for x in dates if "20240429" <= x <= "20240505"]
    old_max, new_max, ctl_max, repro = None, None, None, []
    for dd in event:
        b = dates.index(dd) + 1
        r = to_grid(d / "rs_2024_r_series_20240502.tif", b, tr, shape)
        s = to_grid(d / "rs_2024_s_series_20240502.tif", b, tr, shape)
        old = r ** wr * s ** ws * t_old ** wt
        new = r ** wr * s ** ws * t_new ** wt
        ctl = r ** wr * s ** ws * t_ctl ** wt
        ctl_max = ctl if ctl_max is None else np.fmax(ctl_max, ctl)
        ok = np.isfinite(old) & np.isfinite(tpi_pub[dd])
        repro.append(round(float(np.abs(old[ok] - tpi_pub[dd][ok]).mean()), 4))
        old_max = old if old_max is None else np.fmax(old_max, old)
        new_max = new if new_max is None else np.fmax(new_max, new)
    case = np.ones(shape, bool)
    return {"reference": "Copernicus EMS EMSR720 flood extent, 5 Taquari AOIs",
            "frames": f"max TPI_flood over {event[0]}..{event[-1]} (observed replay)",
            "reproduction_mean_abs_err_vs_published": repro,
            "t_zero_share_in_aois": {"old": round(float((t_old[inside] == 0).mean()), 3),
                                     "new": round(float((t_new[inside] == 0).mean()), 3)},
            "aois": {"old": block(old_max, truth, inside, px_km2), "new": block(new_max, truth, inside, px_km2),
                     "control_live_formula_90m": block(ctl_max, truth, inside, px_km2)},
            "case_wide_high_km2": {"old": round(float((old_max > HIGH).sum() * px_km2), 1),
                                   "new": round(float((new_max > HIGH).sum() * px_km2), 1),
                                   "control": round(float((ctl_max > HIGH).sum() * px_km2), 1)},
            "case_wide_crit_km2": {"old": round(float((old_max[case] > CRIT).sum() * px_km2), 1),
                                   "new": round(float((new_max[case] > CRIT).sum() * px_km2), 1)}}


def uruguai():
    import geopandas as gpd
    import rasterio
    from rasterio.features import geometry_mask
    from rasterio.transform import from_origin

    from live.tpi import TPI_WEIGHTS
    wr, ws, wt = TPI_WEIGHTS
    d = REPO / "data/rs_uruguai_2026/cog"
    files = sorted(d.glob("rs_uruguai_2026_tpi_flood_obs_*.tif"))
    with rasterio.open(files[0]) as src:
        tr, shape = src.transform, (src.height, src.width)
    # candidate T at 90 m, averaged into each 0.01 deg cell (k x k block mean)
    k = 12
    fine_tr = from_origin(tr.c, tr.f, tr.a / k, abs(tr.e) / k)
    hand, slope = static90_on(fine_tr, (shape[0] * k, shape[1] * k))
    tn = t_candidate(hand, slope).reshape(shape[0], k, shape[1], k)
    t_new = np.nanmean(tn, axis=(1, 3))
    from live.tpi import terrain_flood
    t_ctl = np.nanmean(terrain_flood(hand, slope).reshape(shape[0], k, shape[1], k), axis=(1, 3))
    polys = gpd.read_file(REPO / "data/rs_uruguai_2026/geojson/flood_confirmed.geojson").to_crs(4326)
    truth = ~geometry_mask(polys.geometry, out_shape=shape, transform=tr, all_touched=True)
    uru = (-57.25, -29.85, -56.45, -29.05)   # Uruguaiana-Itaqui corridor
    rows, cols = np.indices(shape)
    lon, lat = tr * (cols + 0.5, rows + 0.5)
    region = (lon >= uru[0]) & (lon <= uru[2]) & (lat >= uru[1]) & (lat <= uru[3])
    daily, at = {}, {}
    for f in files:
        dd = f.stem.split("_")[-1]
        with rasterio.open(f) as src:
            b = {n: src.read(i + 1).astype(float) for i, n in enumerate(src.descriptions)}
        valid = np.isfinite(b["tpi"])
        new = np.where(valid, b["r"] ** wr * b["s"] ** ws * t_new ** wt, np.nan)
        old = b["tpi"]
        ctl = np.where(valid, b["r"] ** wr * b["s"] ** ws * t_ctl ** wt, np.nan)
        daily[dd] = {v: {"high_pct_region": round(100 * float((x[region & valid] > HIGH).mean()), 2),
                         "crit_pct_region": round(100 * float((x[region & valid] > CRIT).mean()), 2)}
                     for v, x in (("old", old), ("new", new), ("control", ctl))}
        if dd in ("20260930", "20261012"):
            at[dd] = {v: block(x, truth, region & valid, 1.0) for v, x in (("old", old), ("new", new), ("control", ctl))}
    fa = {v: sum(1 for x in daily.values() if x[v]["high_pct_region"] >= 5.0) for v in ("old", "new", "control")}
    return {"region": "Uruguaiana-Itaqui corridor (-57.25,-29.85,-56.45,-29.05)",
            "replay": "observed IMERG replay (local rain only, no upstream term)",
            "t_mean_region": {"old": round(float(np.nanmean(rasterio.open(files[0]).read(4)[region])), 3),
                              "new": round(float(np.nanmean(t_new[region])), 3)},
            "nisar_polygons_lift": at, "days_high_ge5pct_of_region": fa, "daily": daily}


def wa_terrain(transform, shape):
    """90 m HAND (MERIT Hydro hnd) + slope (NASADEM) for WA via a one-off GEE download
    (evaluation only, cached in ~/.cache/lead-ml/wa_terrain.npz)."""
    import io
    import zipfile

    import rasterio
    import requests
    from rasterio.warp import Resampling, reproject

    cache = Path.home() / ".cache/lead-ml/wa_terrain.npz"
    if cache.exists():
        z = np.load(cache)
        return z["hand"], z["slope"]
    import ee

    from gee.common import ee_init
    ee_init()
    h, w = shape
    west, north = transform.c, transform.f
    east, south = west + transform.a * w, north + transform.e * h
    region = ee.Geometry.Rectangle([west, south, east, north], "EPSG:4326", False)
    hand = ee.Image("MERIT/Hydro/v1_0_1").select("hnd")
    slope = ee.Terrain.slope(ee.Image("NASA/NASADEM_HGT/001").select("elevation"))
    out = {}
    for name, img in (("hand", hand), ("slope", slope)):
        url = img.toFloat().getDownloadURL({"region": region, "scale": 90, "crs": "EPSG:4326",
                                            "format": "GEO_TIFF"})
        data = requests.get(url, timeout=600).content
        if data[:4] == b"PK\x03\x04":
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                data = zf.read(next(n for n in zf.namelist() if n.endswith(".tif")))
        a = np.full(shape, np.nan, np.float32)
        with rasterio.open(io.BytesIO(data)) as src:
            reproject(rasterio.band(src, 1), a, dst_transform=transform, dst_crs="EPSG:4326",
                      resampling=Resampling.nearest)
        out[name] = a.astype(float)
    np.savez_compressed(cache, **out)
    return out["hand"], out["slope"]


def wa_2025():
    import rasterio
    from rasterio.warp import Resampling, reproject

    from live.tpi import TPI_WEIGHTS
    wr, ws, wt = TPI_WEIGHTS
    d = REPO / "data/wa_2025/cog"
    with rasterio.open(d / "wa_2025_tpi_flood_20251210.tif") as src:
        tr, shape, dates = src.transform, (src.height, src.width), list(src.descriptions)
    with rasterio.open(d / "wa_2025_t_flood_20251210.tif") as src:
        t_old = src.read(1).astype(float)
    hand, slope = wa_terrain(tr, shape)
    t_new = t_candidate(hand, slope)
    from live.tpi import terrain_flood
    t_ctl = terrain_flood(hand, slope)
    truth = np.zeros(shape, np.float32)
    with rasterio.open(d / "wa_2025_flood_nisar_20251210.tif") as src:   # 12 m -> any flood in the 90 m cell
        reproject(rasterio.band(src, 1), truth, dst_transform=tr, dst_crs="EPSG:4326",
                  resampling=Resampling.max)
    truth = truth > 0
    with rasterio.open(d / "wa_2025_flood_nisar_20251210.tif") as src:
        b = src.bounds
    rows, cols = np.indices(shape)
    lon, lat = tr * (cols + 0.5, rows + 0.5)
    swath = (lon >= b.left) & (lon <= b.right) & (lat >= b.bottom) & (lat <= b.top) & np.isfinite(hand)
    event = [x for x in dates if "20251208" <= x <= "20251212"]
    old_max = new_max = ctl_max = None
    for dd in event:
        bi = dates.index(dd) + 1
        r = to_grid(d / "wa_2025_r_series_20251210.tif", bi, tr, shape)
        s = to_grid(d / "wa_2025_s_series_20251210.tif", bi, tr, shape)
        old, new = r ** wr * s ** ws * t_old ** wt, r ** wr * s ** ws * t_new ** wt
        old_max = old if old_max is None else np.fmax(old_max, old)
        new_max = new if new_max is None else np.fmax(new_max, new)
        ctl = r ** wr * s ** ws * t_ctl ** wt
        ctl_max = ctl if ctl_max is None else np.fmax(ctl_max, ctl)
    lat_c = tr.f + tr.e * shape[0] / 2
    px_km2 = abs(tr.a) * 111.32 * np.cos(np.radians(lat_c)) * abs(tr.e) * 110.57
    return {"reference": "NISAR GCOV flood candidates (post 2025-12-16), inside the NISAR raster extent",
            "frames": f"max TPI_flood over {event[0]}..{event[-1]} (observed replay)",
            "terrain_source": "MERIT Hydro hnd + NASADEM slope at 90 m via GEE (evaluation only)",
            "t_zero_share": {"old": round(float((t_old[swath] == 0).mean()), 3),
                             "new": round(float((t_new[swath] == 0).mean()), 3)},
            "swath": {"old": block(old_max, truth, swath, px_km2), "new": block(new_max, truth, swath, px_km2),
                      "control_live_formula_90m": block(ctl_max, truth, swath, px_km2)},
            "note": "flood_warned (GFS forecast frames) not re-scored here: forecast R not stored per frame"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=REPO / "ml/models/t_flood_eval.json")
    a = ap.parse_args(argv)
    res = {"candidate": "T = clip(1 - HAND/30 m) and 0 where 90 m slope > 5 deg",
           "live": "T = 1 at HAND <= 5 m ramp to 0 at 15 m, 0 where slope > 5 deg",
           "rs_2024": rs_2024(), "rs_uruguai_2026": uruguai(), "wa_2025": wa_2025()}
    a.out.write_text(json.dumps(res, indent=1))
    print(json.dumps({k: v for k, v in res["rs_2024"].items()}, indent=1))
    u = res["rs_uruguai_2026"]
    print(json.dumps({k: u[k] for k in ("t_mean_region", "nisar_polygons_lift", "days_high_ge5pct_of_region")}, indent=1))
    print(json.dumps(res["wa_2025"], indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
