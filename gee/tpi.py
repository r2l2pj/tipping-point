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
from tp.config import REPO_ROOT, threshold, thresholds
from tp.config import climatology as _clim_cfg

from . import common

SCALE_M = 1000            # series export scale, same as the JS scripts
CLIM_SCALE_M = 10_000     # climatology asset scale (~IMERG native 0.1 deg)
CASE_SCALE_M = 90         # case TPI combination: NASADEM-native terrain
                          # (doc rule 3 — coarse R/S resampled up, not T down)
SLOPE_FLOOD_MAX_DEG = float(threshold("slope_flood_max_deg", 5.0))  # T_flood = 0 above (gee/01)
RAIN_PERCENTILES = [int(p) for p in threshold("rain_percentiles", [90, 95, 99])]
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
    """rain_percentiles of N-day rain at the ref DOY, CLIM_YEARS, stepped windows."""
    clim_cfg = _clim_cfg()
    window = int(clim_cfg.get("window_days", 3))
    off = int(clim_cfg.get("doy_offset_days", 30))
    step = int(clim_cfg.get("doy_offset_step", 3))
    ref = ee.Date(ref.isoformat())
    years = ee.List.sequence(*common.CLIM_YEARS)
    offsets = ee.List.sequence(-off, off, step)
    imerg = _imerg()

    def per_year(y):
        return offsets.map(lambda o: _accum(
            imerg,
            ee.Date.fromYMD(y, ref.get("month"), ref.get("day")).advance(o, "day"),
            window))

    clim = ee.ImageCollection.fromImages(years.map(per_year).flatten())
    return clim.reduce(ee.Reducer.percentile(RAIN_PERCENTILES))


def clim_asset_name(case_id: str) -> str:
    """One climatology asset per case (the DOY window depends on event_date)."""
    return f"{case_id}_clim_rain3_{common.CLIM_YEARS[0]}_{common.CLIM_YEARS[1]}"


def climatology(case_id: str, project: str, *, assets: bool = True):
    """Climatology image — the asset when it exists, else build (+ export once)."""
    name = clim_asset_name(case_id)
    asset = common.asset_path(name, project)
    try:
        ee.data.getAsset(asset)
        return ee.Image(asset)
    except ee.EEException:
        aoi = common.aoi_geom(case_id)
        img = climatology_image(common.case_cfg(case_id)["event_date"])
        if assets:
            ee.batch.Export.image.toAsset(
                image=common.stamp(img.toFloat().clip(aoi), "static"),
                description=name, assetId=asset,
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
    rain3 = _accum(_imerg(), t, int(_clim_cfg().get("window_days", 3)))
    p90, p95, p99 = (clim.select(f"precipitation_p{p}") for p in RAIN_PERCENTILES)
    r = rain_score(rain3, p90, p95, p99)
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
        img = (_accum(imerg, t, int(_clim_cfg().get("window_days", 3))).rename("rain3_mm")
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


def _combine_case_tpi(paths: dict[str, Path], names: list[str],
                      weights: list[float] | None) -> None:
    """Build TPI_slide/TPI_flood multiband COGs on the 90 m terrain grid.

    Doc rule 3: the coarse R/S series are resampled UP to the NASADEM grid
    and combined there — terrain is never flattened. Interactive EE compute
    at 90 m exceeds the memory limit, so the combination is local numpy:
    TPI_i = R^wR · S^wS · T^wT per day band, with nodata propagation.
    """
    import numpy as np
    import rasterio
    from rasterio.warp import Resampling, reproject

    w = weights or [1 / 3, 1 / 3, 1 / 3]
    with rasterio.open(paths["T_slide"]) as t_src:
        t_profile = dict(t_src.profile)
        h, wpx = t_profile["height"], t_profile["width"]
        t_slide = t_src.read(1).astype("float64")
        t_nodata = t_src.nodata
    with rasterio.open(paths["T_flood"]) as t_src:
        t_flood = t_src.read(1).astype("float64")

    def upsample(src_path: Path) -> np.ndarray:
        with rasterio.open(src_path) as src:
            out = np.full((src.count, h, wpx), np.nan, dtype="float64")
            nodata = src.nodata
            for b in range(1, src.count + 1):
                raw = src.read(b).astype("float64")
                arr = (np.where(raw == nodata, np.nan, raw)
                       if nodata is not None else raw)
                reproject(arr, out[b - 1],
                          src_transform=src.transform, src_crs=src.crs,
                          src_nodata=np.nan,
                          dst_transform=t_profile["transform"],
                          dst_crs=t_profile["crs"],
                          dst_nodata=np.nan,
                          resampling=Resampling.bilinear)
            return out

    r = upsample(paths["R"])
    s = upsample(paths["S"])
    t_mask = np.isfinite(t_slide) & np.isfinite(t_flood)
    for image, t in (("TPI_slide", t_slide), ("TPI_flood", t_flood)):
        tpi = np.where(
            np.isfinite(r) & np.isfinite(s) & t_mask[None, :, :],
            np.power(np.clip(r, 0, None), w[0])
            * np.power(np.clip(s, 0, None), w[1])
            * np.power(np.clip(t[None, :, :], 0, None), w[2]),
            np.nan).astype("float32")
        prof = dict(t_profile, count=r.shape[0], dtype="float32",
                    nodata=np.nan, compress="deflate",
                    predictor=3, tiled=True)
        with rasterio.open(paths[image], "w", **prof) as dst:
            for i in range(r.shape[0]):
                dst.write_band(i + 1, tpi[i])
                dst.set_band_description(i + 1, names[i])


def _merge_band_files(parts: list[Path], names: list[str], path: Path) -> None:
    """Stack single-band GeoTIFFs into one multiband COG + band names."""
    import rasterio
    with rasterio.open(parts[0]) as first:
        profile = dict(first.profile)
    profile.update(count=len(parts), compress="deflate",
                   predictor=3, tiled=True)
    with rasterio.open(path, "w", **profile) as dst:
        for i, part in enumerate(parts, 1):
            with rasterio.open(part) as src:
                dst.write_band(i, src.read(1))
            dst.set_band_description(i, names[i - 1])
    for part in parts:
        part.unlink()


def run(case_id: str, project: str | None = None, *,
        assets: bool = True) -> dict:
    """Build everything, start the exports, write the rain_soil JSON."""
    proj = common.init(project)
    plan = export_plan(case_id)
    aoi = common.aoi_geom(case_id)
    t_slide, t_flood = terrain_scores()
    clim = climatology(case_id, proj, assets=assets)
    col = series(clim, case_id, terrain=(t_slide, t_flood))
    names = plan["band_names"]
    images = {
        band: col.select(band).toBands().rename(names).toFloat().clip(aoi)
        for band in ("TPI_slide", "TPI_flood", "R", "S")}
    # Static terrain scores — float32 single-band, unclassified (delta L5).
    images["T_slide"] = t_slide.toFloat().clip(aoi)
    images["T_flood"] = t_flood.toFloat().clip(aoi)
    dates = common.event_dates(case_id)
    for spec in plan["exports"]:
        if spec["kind"] == "drive_cog":
            from tp.fallback import _download, _write_cog

            path = Path(REPO_ROOT) / spec["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            if spec["image"] in ("TPI_slide", "TPI_flood"):
                # Rule-3 combination happens locally on the 90 m terrain
                # grid in _combine_case_tpi() below — a 90 m interactive
                # download exceeds EE's compute memory limit.
                continue
            scale = (CASE_SCALE_M if spec["image"] in ("T_slide", "T_flood")
                     else SCALE_M)
            if not _write_cog(images[spec["image"]], aoi, path, fetch=_download,
                              max_bytes=32 * 1024 * 1024, scale=scale):
                raise RuntimeError(f"direct GEE download exceeds size limit: {path}")
            band_desc = names if spec["bands"] > 1 else [spec["image"].lower()]
            import rasterio
            with rasterio.open(path, "r+") as src:
                for i, n in enumerate(band_desc, 1):
                    src.set_band_description(i, n)
        elif spec["kind"] == "asset" and "image" in spec:
            if not assets:
                continue
            target = common.asset_path(spec["asset"], proj)
            try:
                ee.data.getAsset(target)
                continue            # exists — never overwrite
            except ee.EEException:
                pass
            img = images[spec["image"]]
            if spec["image"] in ("TPI_slide", "TPI_flood", "R", "S"):
                # GEE band ids may not start with a digit — prefix the
                # YYYYMMDD series names (local COGs keep the raw form)
                img = img.rename([f"d{n}" for n in names])
            data_date = ("static" if spec["image"] in ("T_slide", "T_flood")
                         else plan["dates"][-1])
            ee.batch.Export.image.toAsset(
                image=common.stamp(img, data_date),
                description=spec["description"],
                assetId=target,
                region=aoi, scale=SCALE_M).start()
    cog_paths = {spec["image"]: Path(REPO_ROOT) / spec["path"]
                 for spec in plan["exports"] if spec["kind"] == "drive_cog"}
    _combine_case_tpi(cog_paths, names, thresholds().get("tpi_weights"))
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
    ap.add_argument("--no-assets", action="store_true",
                    help="local outputs only — no toAsset/batch exports")
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
    run(args.case, project=args.project, assets=not args.no_assets)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
