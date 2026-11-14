"""GEE feature extraction: static stack, dynamic(t), N1/N2 sampling, exports.

Every Earth-Engine-touching function goes through ``init_ee`` which raises a
clear error when credentials/GEE_PROJECT are absent — there are no GEE
credentials in this repo. Pure-python helpers (``calm_days``, candidate frame
times) work standalone.

    uv run python -m ml.gee_features --help
"""
from __future__ import annotations

import argparse
import logging

import pandas as pd

from . import config as cfg

log = logging.getLogger(__name__)

_AUTH_MSG = (
    "Earth Engine is not authenticated. Run `earthengine authenticate` and set "
    "GEE_PROJECT (see .env.example). No GEE credentials are committed to this repo."
)


def init_ee(project: str | None = None):
    """Initialize earthengine-api; clear error when creds/project are missing."""
    import ee

    proj = project or cfg.GEE_PROJECT
    try:
        ee.Initialize(project=proj or None)
    except Exception as e:
        raise RuntimeError(_AUTH_MSG) from e
    return ee


def aoi_geometry(ee):
    w, s, e, n = cfg.AOI_BBOX
    return ee.Geometry.Rectangle([w, s, e, n])


# --- Static stack (NASADEM derivatives + MODIS LC at 90 m) --------------------
def static_stack(ee=None):
    """One image with all STATIC_FEATURES bands, per spec §5.1."""
    ee = ee or init_ee()
    dem = ee.Image(cfg.IMG_NASADEM).select("elevation")
    slope = ee.Terrain.slope(dem)
    aspect = ee.Terrain.aspect(dem)

    scale = cfg.CELL_SIZE_M
    slope_mm = slope.reduceResolution(
        reducer=ee.Reducer.mean().combine(ee.Reducer.max(), sharedInputs=True),
        maxPixels=16,
    ).reproject(crs="EPSG:4326", scale=scale).rename(["slope_mean", "slope_max"])

    relief = dem.focalMax(radius=500, units="meters").subtract(
        dem.focalMin(radius=500, units="meters")
    ).rename("relief_500m")
    curv = dem.convolve(ee.Kernel.laplacian8()).rename("curv")
    hand = dem.subtract(dem.focalMin(radius=1500, units="meters")).rename("hand_proxy")
    lc = (
        ee.ImageCollection(cfg.COL_MODIS_LC)
        .filterDate(f"{cfg.LC_YEAR}-01-01", f"{cfg.LC_YEAR}-12-31")
        .select(cfg.BAND_LC).first().rename("lc")
    )
    return (
        slope_mm
        .addBands(relief).addBands(curv)
        .addBands(aspect.multiply(3.14159265 / 180).sin().rename("aspect_sin"))
        .addBands(aspect.multiply(3.14159265 / 180).cos().rename("aspect_cos"))
        .addBands(dem.rename("elev"))
        .addBands(hand).addBands(lc)
    )


# --- Dynamic stack at time t ---------------------------------------------------
def _imerg_accum(ee, start, end):
    """IMERG mm over [start, end): 30-min rates × 0.5."""
    return (
        ee.ImageCollection(cfg.COL_IMERG).select(cfg.BAND_IMERG)
        .filterDate(start, end).sum().multiply(0.5)
    )


def imerg_daily_p99(ee=None):
    """P99 of daily IMERG sums, 2001-2023 — exported once as an asset."""
    ee = ee or init_ee()
    days = ee.List.sequence(0, ee.Date("2024-01-01").difference(ee.Date("2001-01-01"), "day"))
    daily = ee.ImageCollection.fromImages(days.map(
        lambda d: _imerg_accum(
            ee, ee.Date("2001-01-01").advance(d, "day"), ee.Date("2001-01-01").advance(ee.Number(d).add(1), "day")
        ).rename("rain")
    ))
    return daily.reduce(ee.Reducer.percentile([99])).rename("p99")


def dynamic_stack(t: str, p99=None, ee=None):
    """Per-frame dynamic bands, per spec §5.1 (latency-honest SMAP at t−3 d)."""
    ee = ee or init_ee()
    t = ee.Date(t)
    rain_24h = _imerg_accum(ee, t.advance(-24, "hour"), t).rename("rain_24h")
    rain_ante = _imerg_accum(ee, t.advance(-72, "hour"), t.advance(-24, "hour")).rename("rain_ante_2d")
    rain_7d = _imerg_accum(ee, t.advance(-7, "day"), t).rename("rain_7d")
    p99 = p99 if p99 is not None else ee.Image(cfg.asset_path(cfg.ASSET_IMERG_P99))
    rain_rel = rain_24h.divide(p99).rename("rain_rel")
    sm_end = t.advance(-cfg.SMAP_LAG_DAYS, "day")
    sm = (
        ee.ImageCollection(cfg.COL_SMAP).select(cfg.BAND_SMAP)
        .filterDate(sm_end.advance(-24, "hour"), sm_end).mean()
        .rename("sm_profile_wetness")
    )
    return rain_24h.addBands(rain_ante).addBands(rain_7d).addBands(rain_rel).addBands(sm)


# --- Feature sampling ------------------------------------------------------------
def features_at(cells_fc, t: str, ee=None):
    """Static + dynamic(t) sampled at each point of a FeatureCollection."""
    ee = ee or init_ee()
    img = static_stack(ee).addBands(dynamic_stack(t, ee=ee))
    return img.sampleRegions(
        collection=cells_fc, scale=cfg.CELL_SIZE_M, geometries=True
    )


def assign_peak24h_gee(points_fc, window: tuple[str, str] = cfg.EVENT_WINDOW, ee=None):
    """Per-cell argmax of rain_24h over daily window ends (GEE path).

    Returns a pandas DataFrame (lon, lat, t_utc) — the label-time assignment
    declared in spec §2.2.
    """
    ee = ee or init_ee()
    ends = pd.date_range(window[0], window[1], freq="D", tz="UTC")
    accum = pd.DataFrame(index=None)
    for end in ends:
        img = _imerg_accum(ee, ee.Date(end.strftime("%Y-%m-%dT%H:%M:%S")).advance(-24, "hour"),
                           ee.Date(end.strftime("%Y-%m-%dT%H:%M:%S"))).rename("rain")
        fc = img.sampleRegions(collection=points_fc, scale=10_000, geometries=True)
        df = pd.DataFrame([f["properties"] for f in fc.getInfo()["features"]])
        accum[end] = df["rain"]
    best_end = accum.idxmax(axis=1)
    return pd.DataFrame({"t_utc": pd.to_datetime(best_end, utc=True)})


# --- Negative sampling -----------------------------------------------------------
def calm_days(n: int = cfg.N2_CALM_DAYS) -> pd.DatetimeIndex:
    """~n calm days in N2_RANGE, skipping known heavy-rain windows (spec §2.3).

    Deterministic (evenly spaced). Pure python — no GEE needed.
    """
    import numpy as np

    days = pd.date_range(cfg.N2_RANGE[0], cfg.N2_RANGE[1], freq="D", tz="UTC")
    for lo, hi in cfg.EXCLUDED_RAIN_WINDOWS:
        days = days[(days < pd.Timestamp(lo, tz="UTC")) | (days > pd.Timestamp(hi, tz="UTC"))]
    idx = np.unique(np.linspace(0, len(days) - 1, min(n, len(days))).round().astype(int))
    return days[idx]


def sample_n1(footprint_geom, slides_fc, static_img=None, ee=None):
    """N1: random cells inside the mapped footprint, ≥250 m from any slide.

    50% anywhere, 50% from slope > 10° (spec §2.3).
    """
    ee = ee or init_ee()
    static_img = static_img if static_img is not None else static_stack(ee)
    dist = (
        slides_fc.map(lambda f: f.set("v", 1)).reduceToImage(["v"], ee.Reducer.first())
        .distance(ee.Kernel.euclidean(radius=cfg.N1_MIN_DIST_M, units="meters"))
    )
    n_each = cfg.N1_COUNT // 2
    pts = ee.FeatureCollection.randomPoints(footprint_geom, cfg.N1_COUNT * 2, seed=1)
    tagged = (
        dist.addBands(static_img.select("slope_mean"))
        .sampleRegions(pts, scale=cfg.CELL_SIZE_M, geometries=True)
    )
    far = ee.Filter.gte("distance", cfg.N1_MIN_DIST_M)
    flat = tagged.filter(far).limit(n_each)
    steep = tagged.filter(far).filter(
        ee.Filter.gte("slope_mean", cfg.N1_STEEP_SLOPE_DEG)
    ).limit(n_each)
    return flat.merge(steep)


def sample_n2(cells_fc, days: pd.DatetimeIndex | None = None, ee=None):
    """N2: the same cells sampled on calm days — teaches "when" (spec §2.3)."""
    ee = ee or init_ee()
    days = calm_days() if days is None else days
    fcs = [features_at(cells_fc, d.strftime("%Y-%m-%dT%H:%M:%S"), ee=ee) for d in days]
    return ee.FeatureCollection(fcs).flatten()


# --- Exports ------------------------------------------------------------------
def export_table(fc, description: str, asset_name: str | None = None, ee=None):
    """Export.table → GEE asset (needs a project) or Drive."""
    ee = ee or init_ee()
    if asset_name:
        return ee.batch.Export.table.toAsset(
            collection=fc, description=description, assetId=cfg.asset_path(asset_name)
        )
    return ee.batch.Export.table.toDrive(collection=fc, description=description)


def export_image(img, description: str, region=None, scale: float | None = None, ee=None):
    """Export.image → Drive as a cloud-optimized GeoTIFF."""
    ee = ee or init_ee()
    return ee.batch.Export.image.toDrive(
        image=img, description=description, region=region or aoi_geometry(ee),
        scale=scale or cfg.CELL_SIZE_M, fileFormat="GeoTIFF",
        formatOptions={"cloudOptimized": True},
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("static", help="export the 90 m static stack COG")
    p.add_argument("--description", default="tp_static_stack")
    p = sub.add_parser("p99", help="export the one-time IMERG daily-P99 asset")
    p.add_argument("--asset", default=cfg.ASSET_IMERG_P99)
    p = sub.add_parser("dynamic", help="export one dynamic stack per frame time")
    p.add_argument("--times", nargs="+", default=cfg.FRAME_TIMES_UTC)
    p.add_argument("--description", default="tp_dynamic")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ee = init_ee()
    if args.cmd == "static":
        task = export_image(static_stack(ee), args.description, ee=ee)
        task.start()
        log.info("started export %s", args.description)
    elif args.cmd == "p99":
        task = ee.batch.Export.image.toAsset(
            image=imerg_daily_p99(ee), description="tp_imerg_p99",
            assetId=cfg.asset_path(args.asset), region=aoi_geometry(ee), scale=10_000,
        )
        task.start()
        log.info("started p99 asset export → %s", cfg.asset_path(args.asset))
    elif args.cmd == "dynamic":
        for t in args.times:
            task = export_image(dynamic_stack(t, ee=ee), f"{args.description}_{t[:10]}",
                                scale=10_000, ee=ee)
            task.start()
            log.info("started export %s_%s", args.description, t[:10])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
