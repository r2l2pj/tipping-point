"""Replay TPI — Python port of gee/01_tpi_index.js and gee/07_tpi_wa.js.

    uv run python -m gee.tpi --case rs_2024|wa_2025 [--dry-run|--tasks]

Builds the daily TPI_slide / TPI_flood multiband series over the case window
(one band per day, band name = YYYYMMDD), exports cloud-optimized GeoTIFFs
destined for data/<event>/cog/<caso>_tpi_{slide,flood}_<AAAAMMDD>.tif, writes
the per-day area-mean rain3/rain7/soil table to
data/<event>/series/<caso>_rain_soil_<AAAAMMDD>.json, and exports the 3-day
rain climatology (CLIM_YEARS, 3-day windows every 3 days, ±30 d DOY offsets)
as an asset once, reusing it afterwards.

--dry-run prints the window dates and planned exports without
ee.Initialize; --tasks prints the export parameter dicts (also offline).
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import ee
from tp.config import REPO_ROOT, thresholds

from . import common

SCALE_M = 1000            # series export scale, same as the JS scripts
CLIM_SCALE_M = 10_000     # climatology asset scale (~IMERG native 0.1 deg)
SLOPE_FLOOD_MAX_DEG = 5.0  # T_flood = 0 above this slope (gee/01: slope.gt(5))
MAX_PIXELS = 1e9

# Delta L8: derived products live as GEE assets (single source of truth)
# under projects/<GEE_PROJECT>/assets/tipping-point/<case>/.
DERIVED_ASSETS = (
    "tpi_slide_series", "tpi_flood_series", "r_series", "s_series",
    "t_slide", "t_flood",
)


def derived_asset_ids(case_id: str, project: str | None = None) -> dict:
    """{name: assetId} for the L8 derived assets under <case>/.

    Records the literal `<GEE_PROJECT>` placeholder when no project is
    known (metrics collection entries stay honest without creds).
    """
    proj = project or common.GEE_PROJECT or "<GEE_PROJECT>"
    root = f"{common.ASSET_ROOT.format(GEE_PROJECT=proj)}/{case_id}"
    return {name: f"{root}/{name}" for name in DERIVED_ASSETS}


def _imerg():
    return ee.ImageCollection(common.COL_IMERG).select(common.BAND_IMERG)


def _accum(imerg, end, days):
    """IMERG mm over [end - days, end): 30-min mm/h sums x 0.5."""
    return imerg.filterDate(end.advance(-days, "day"), end).sum().multiply(0.5)


def _smap_mean(end):
    """24 h sm_rootzone_wetness mean before `end` (ee.Date)."""
    return (ee.ImageCollection(common.COL_SMAP).select(common.BAND_SMAP)
            .filterDate(end.advance(-1, "day"), end).mean())


def climatology_image(ref: date):
    """p90/p95/p99 of 3-day rain at the ref DOY, CLIM_YEARS, 3-day windows."""
    ref = ee.Date(ref.isoformat())
    years = ee.List.sequence(*common.CLIM_YEARS)
    offsets = ee.List.sequence(-30, 30, 3)
    imerg = _imerg()

    def per_year(y):
        return offsets.map(lambda o: _accum(
            imerg,
            ee.Date.fromYMD(y, ref.get("month"), ref.get("day")).advance(o, "day"),
            3))

    clim = ee.ImageCollection.fromImages(years.map(per_year).flatten())
    return clim.reduce(ee.Reducer.percentile([90, 95, 99]))


def clim_asset_name(case_id: str) -> str:
    """One climatology asset per case (the DOY window depends on event_date)."""
    return f"{case_id}_clim_rain3_{common.CLIM_YEARS[0]}_{common.CLIM_YEARS[1]}"


def climatology(case_id: str, project: str):
    """Climatology image — the asset when it exists, else build + export once."""
    name = clim_asset_name(case_id)
    asset = common.asset_path(name, project)
    try:
        ee.data.getAsset(asset)
        return ee.Image(asset)
    except ee.EEException:
        aoi = common.aoi_geom(case_id)
        img = climatology_image(common.case_cfg(case_id)["event_date"])
        ee.batch.Export.image.toAsset(
            image=img.toFloat().clip(aoi), description=name, assetId=asset,
            region=aoi, scale=CLIM_SCALE_M).start()
        return img


def terrain_scores():
    """(T_slide, T_flood) in [0,1] from NASADEM, per config.yaml thresholds."""
    thr = thresholds()
    dem = ee.Image(common.IMG_NASADEM).select(common.BAND_DEM)
    slope = ee.Terrain.slope(dem)
    t_slide = slope.unitScale(*thr["slope_range_deg"]).clamp(0, 1)
    hand = dem.subtract(
        dem.focalMin(radius=thr["hand_proxy_radius_m"], units="meters"))
    t_flood = (ee.Image(1).subtract(hand.unitScale(*thr["hand_range_m"]))
               .clamp(0, 1).where(slope.gt(SLOPE_FLOOD_MAX_DEG), 0))
    return t_slide, t_flood


def rain_score(rain3, p90, p95, p99):
    """R in [0,1]: 0 <p90, 0.5 at p95, 1 >=p99 — same .where() chain as gee/01.

    Later .where() conditions win, so degenerate percentiles (p95 == p90)
    cannot leak a 0/0: any rain3 >= p90 is also >= p95 and takes the high
    branch before clamping.
    """
    return (ee.Image(0)
            .where(rain3.gte(p90),
                   rain3.subtract(p90).divide(p95.subtract(p90)).multiply(0.5))
            .where(rain3.gte(p95),
                   rain3.subtract(p95).divide(p99.subtract(p95))
                   .multiply(0.5).add(0.5))
            .where(rain3.gte(p99), 1)
            .clamp(0, 1))


def geomean(r, s, t, weights=None):
    """TPI = R^wR · S^wS · T^wT — config.yaml tpi_weights (uniform 1/3)."""
    w = weights or thresholds().get("tpi_weights", [1 / 3, 1 / 3, 1 / 3])
    return r.pow(w[0]).multiply(s.pow(w[1])).multiply(t.pow(w[2]))


def tpi_at(t, clim, t_slide, t_flood):
    """4-band (R, S, TPI_slide, TPI_flood) image at day t.

    R and S ride along unclassified in [0,1] — the r_series/s_series COGs
    (delta L5) select them straight out of the same per-day images.
    """
    t = ee.Date(t)
    thr = thresholds()
    rain3 = _accum(_imerg(), t, 3)
    r = rain_score(rain3, clim.select("precipitation_p90"),
                   clim.select("precipitation_p95"),
                   clim.select("precipitation_p99"))
    s = _smap_mean(t).unitScale(*thr["soil_wetness_range"]).clamp(0, 1)
    w = thr.get("tpi_weights")
    return (r.rename("R").addBands(s.rename("S"))
            .addBands(geomean(r, s, t_slide, w).rename("TPI_slide"))
            .addBands(geomean(r, s, t_flood, w).rename("TPI_flood"))
            .set("system:time_start", t.millis()))


def series(clim, case_id: str, terrain=None):
    """Daily ImageCollection over the case window."""
    t_slide, t_flood = terrain or terrain_scores()
    return ee.ImageCollection(
        [tpi_at(d, clim, t_slide, t_flood) for d in common.event_dates(case_id)])


def band_names(case_id: str) -> list[str]:
    """One YYYYMMDD band name per window day."""
    return [d.replace("-", "") for d in common.event_dates(case_id)]


def rain_soil_fc(case_id: str):
    """Per-day area means of rain3, rain7 and SMAP — one feature per day."""
    aoi = common.aoi_geom(case_id)
    imerg = _imerg()
    feats = []
    for d in common.event_dates(case_id):
        t = ee.Date(d)
        img = (_accum(imerg, t, 3).rename("rain3_mm")
               .addBands(_accum(imerg, t, 7).rename("rain7_mm"))
               .addBands(_smap_mean(t).rename("smap")))
        stats = img.reduceRegion(
            reducer=ee.Reducer.mean(), geometry=aoi, scale=SCALE_M,
            maxPixels=MAX_PIXELS)
        feats.append(ee.Feature(None, stats.set("date", d)))
    return ee.FeatureCollection(feats)


def export_plan(case_id: str) -> dict:
    """Pure-python plan: window dates + every export this module would run.

    Paths are under data/<case-id>/ (delta L4). The "image" key names the
    band/static score each drive_cog/asset export carries: TPI_* are the
    classified-source geomeans, R/S are the unclassified daily scores and
    T_slide/T_flood the static terrain scores (all float32, delta L5).
    """
    cfg = common.case_cfg(case_id)
    out = common.case_dir(case_id)
    ymd = f"{cfg['event_date']:%Y%m%d}"
    dates = common.event_dates(case_id)

    def cog(image, stem, bands):
        return {"kind": "drive_cog", "image": image,
                "description": stem, "bands": bands,
                "path": str(out / "cog" / f"{stem}.tif"),
                "scale": SCALE_M}

    def asset(image, rel, note=None):
        spec = {"kind": "asset", "image": image,
                "description": rel.replace("/", "_"), "asset": rel}
        if note:
            spec["note"] = note
        return spec

    image_of = {"tpi_slide_series": "TPI_slide",
                "tpi_flood_series": "TPI_flood",
                "r_series": "R", "s_series": "S",
                "t_slide": "T_slide", "t_flood": "T_flood"}

    exports = [
        cog("TPI_slide", f"{case_id}_tpi_slide_{ymd}", len(dates)),
        cog("TPI_flood", f"{case_id}_tpi_flood_{ymd}", len(dates)),
        cog("R", f"{case_id}_r_series_{ymd}", len(dates)),
        cog("S", f"{case_id}_s_series_{ymd}", len(dates)),
        cog("T_slide", f"{case_id}_t_slide_{ymd}", 1),
        cog("T_flood", f"{case_id}_t_flood_{ymd}", 1),
        {"kind": "series_json",
         "description": f"{case_id}_rain_soil_{ymd}",
         "path": str(out / "series" / f"{case_id}_rain_soil_{ymd}.json"),
         "scale": SCALE_M},
    ]
    # L8: the derived assets are the source of truth; the COGs above are
    # the data/<case>/ fallback path. Asset ids live under <case>/.
    for name in DERIVED_ASSETS:
        exports.append(asset(image_of[name], f"{case_id}/{name}"))
    # Flat-named slide/flood series stay for gee/08 + gee/09 (pinned ids).
    for band, name in (("TPI_slide", f"{case_id}_tpi_slide_series"),
                       ("TPI_flood", f"{case_id}_tpi_flood_series")):
        spec = asset(band, name, "compat: gee/08 + gee/09 read this id")
        spec["description"] += "_compat"
        exports.append(spec)
    exports.append({"kind": "asset",
                    "description": clim_asset_name(case_id),
                    "asset": clim_asset_name(case_id),
                    "note": "skipped when the asset already exists"})
    return {"case": case_id, "event_id": common.event_id(case_id),
            "event_date": cfg["event_date"].isoformat(),
            "dates": dates, "band_names": band_names(case_id),
            "exports": exports}


def run(case_id: str, project: str | None = None) -> dict:
    """Build everything, start the exports, write the rain_soil JSON."""
    proj = common.init(project)
    plan = export_plan(case_id)
    aoi = common.aoi_geom(case_id)
    t_slide, t_flood = terrain_scores()
    col = series(climatology(case_id, proj), case_id,
                 terrain=(t_slide, t_flood))
    names = plan["band_names"]
    images = {
        band: col.select(band).toBands().rename(names).toFloat().clip(aoi)
        for band in ("TPI_slide", "TPI_flood", "R", "S")}
    # Static terrain scores — float32 single-band, unclassified (delta L5).
    images["T_slide"] = t_slide.toFloat().clip(aoi)
    images["T_flood"] = t_flood.toFloat().clip(aoi)
    for spec in plan["exports"]:
        if spec["kind"] == "drive_cog":
            ee.batch.Export.image.toDrive(
                image=images[spec["image"]], description=spec["description"],
                region=aoi, scale=spec["scale"], fileFormat="GeoTIFF",
                formatOptions={"cloudOptimized": True}).start()
        elif spec["kind"] == "asset" and "image" in spec:
            ee.batch.Export.image.toAsset(
                image=images[spec["image"]], description=spec["description"],
                assetId=common.asset_path(spec["asset"], proj),
                region=aoi, scale=SCALE_M).start()
    rows = [f["properties"]
            for f in rain_soil_fc(case_id).getInfo()["features"]]
    spec = next(s for s in plan["exports"] if s["kind"] == "series_json")
    path = Path(REPO_ROOT) / spec["path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"case": case_id, "event": plan["event_id"],
         "reference_date": plan["event_date"], "scale_m": SCALE_M,
         "days": [{"date": r["date"], "rain3_mm": r.get("rain3_mm"),
                   "rain7_mm": r.get("rain7_mm"), "smap": r.get("smap")}
                  for r in rows]},
        indent=2) + "\n")
    return plan


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--case", required=True, choices=("rs_2024", "wa_2025"))
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="list window dates + planned exports, no ee.Initialize")
    mode.add_argument("--tasks", action="store_true",
                      help="print the export parameter dicts, no ee.Initialize")
    ap.add_argument("--project", default=None,
                    help="GEE project (default: env GEE_PROJECT)")
    args = ap.parse_args(argv)

    plan = export_plan(args.case)
    if args.dry_run:
        print(f"case {plan['case']} (event {plan['event_id']}), "
              f"{len(plan['dates'])} days:")
        for d in plan["dates"]:
            print(f"  {d}")
        for spec in plan["exports"]:
            target = spec.get("path") or spec.get("asset")
            print(f"export: {spec['kind']} {spec['description']} -> {target}")
        return 0
    if args.tasks:
        print(json.dumps(plan, indent=2))
        return 0
    run(args.case, project=args.project)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
