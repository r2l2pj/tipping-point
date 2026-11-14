"""Local latency-aware TPI replay from GES DISC Early; optional archived GEE asset path."""
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta

import ee

from . import common, tpi

ASSUMPTIONS = {
    "imerg": "Archived IMERG Early half-hourly precipitationCal (mm/h); interval end <= t-4h; no Late/Final substitution",
    "smap": "Latest SMAP L4 observation with system:time_start <= t-75h (3-hour interval end <= t-72h); held until next eligible observation; conservative release-latency assumption",
    "geos_fp": "Excluded: no forecast or perfect-forecast replay layer",
}


def replay_plan(case_id: str, imerg_early_asset: str | None = None) -> dict:
    dates = common.event_dates(case_id)
    start = datetime.fromisoformat(dates[0]).replace(tzinfo=UTC)
    end = datetime.fromisoformat(dates[-1]).replace(tzinfo=UTC) + timedelta(days=1)
    frames = []
    while start < end:
        frames.append({
            "at": start.isoformat(), "band": start.strftime("%Y%m%dT%H%M"),
            "imerg_interval_end_lte": (start - timedelta(hours=4)).isoformat(),
            "smap_interval_end_lte": (start - timedelta(hours=72)).isoformat(),
        })
        start += timedelta(hours=3)
    return {
        "case": case_id, "step_h": 3, "frames": frames,
        "export_crs": "EPSG:6933" if imerg_early_asset else "EPSG:4326",
        "export_scale_m": tpi.SCALE_M if imerg_early_asset else None,
        "local_grid": "native IMERG lat/lon; GEE static and SMAP sampled on that grid",
        "imerg_early_asset": imerg_early_asset,
        "execution_requires": ("--imerg-early-asset with precipitationCal band, children YYYYMMDDTHHmm"
                               if imerg_early_asset else "GES DISC GPM_3IMERGHHE V07 archive; GEE direct static/SMAP downloads"),
        "lead_time_assumptions": ASSUMPTIONS,
        "outputs": {band: f"{case_id}_lead_time_{band}_3h" for band in ("slide", "flood")},
    }


def early_collection(asset: str):
    def timestamp(image):
        tag = ee.String(image.get("system:index")).split("/").get(-1)
        start = ee.Date.parse("yyyyMMdd'T'HHmm", tag)
        return image.set("system:time_start", start.millis())
    return ee.ImageCollection(asset).map(timestamp).select("precipitationCal")


def replay_at(at, imerg, clim, terrain):
    at = ee.Date(at)
    rain_end = at.advance(-4, "hour")
    rain_frames = imerg.filterDate(rain_end.advance(-3, "day"), rain_end)
    rain = rain_frames.sum().multiply(0.5).updateMask(rain_frames.count().eq(144))
    eligible = (ee.ImageCollection(common.COL_SMAP).select(common.BAND_SMAP)
                .filter(ee.Filter.lte("system:time_start", at.advance(-75, "hour").millis()))
                .sort("system:time_start", False).limit(1))
    soil = eligible.mosaic().unitScale(*tpi.thresholds()["soil_wetness_range"]).clamp(0, 1)
    rain = tpi.rain_score(rain, clim.select("precipitation_p90"),
                          clim.select("precipitation_p95"), clim.select("precipitation_p99"))
    return (tpi.geomean(rain, soil, terrain[0]).rename("TPI_slide")
            .addBands(tpi.geomean(rain, soil, terrain[1]).rename("TPI_flood"))
            .set("system:time_start", at.millis()))


def run(case_id: str, asset: str, project: str | None = None) -> dict:
    plan = replay_plan(case_id, asset)
    project = common.init(project)
    imerg = early_collection(asset)
    clim = tpi.climatology(case_id, project)
    terrain = tpi.terrain_scores()
    images = [replay_at(frame["at"], imerg, clim, terrain) for frame in plan["frames"]]
    collection = ee.ImageCollection.fromImages(images)
    aoi = common.aoi_geom(case_id)
    for kind, name in plan["outputs"].items():
        image = (collection.select(f"TPI_{kind}").toBands()
                 .rename([frame["band"] for frame in plan["frames"]]).toFloat().clip(aoi)
                 .set("lead_time_assumptions", json.dumps(ASSUMPTIONS)))
        ee.batch.Export.image.toDrive(
            image=image, description=name, region=aoi, scale=plan["export_scale_m"],
            crs=plan["export_crs"], fileFormat="GeoTIFF", formatOptions={"cloudOptimized": True}).start()
    return plan


def local_cube(rates, starts, frames, percentiles, soil, terrain):
    import numpy as np
    import pandas as pd

    starts = pd.DatetimeIndex(pd.to_datetime(starts, utc=True))
    if starts.has_duplicates or not starts.is_monotonic_increasing:
        raise ValueError("IMERG intervals must be unique and ordered")
    output = []
    p90, p95, p99 = percentiles
    low, high = tpi.thresholds()["soil_wetness_range"]
    for i, frame in enumerate(frames):
        end = pd.Timestamp(frame) - pd.Timedelta(hours=4)
        expected = pd.date_range(end - pd.Timedelta(hours=72), end,
                                 freq="30min", inclusive="left")
        indexes = starts.get_indexer(expected)
        rain = (np.sum(rates[indexes], axis=0) * 0.5 if (indexes >= 0).all()
                else np.full(rates.shape[1:], np.nan))
        with np.errstate(divide="ignore", invalid="ignore"):
            score = np.where(rain >= p90, (rain - p90) / (p95 - p90) * 0.5, 0)
            score = np.where(rain >= p95, (rain - p95) / (p99 - p95) * 0.5 + 0.5, score)
        score = np.where(rain >= p99, 1, score)
        score = np.where(np.isfinite(rain) & np.isfinite(percentiles).all(axis=0),
                         np.clip(score, 0, 1), np.nan)
        wetness = np.clip((soil[i] - low) / (high - low), 0, 1)
        output.append(np.stack([np.cbrt(score * wetness * t) for t in terrain]))
    return np.asarray(output, dtype=np.float32)


def run_local(case_id: str, project: str | None = None) -> dict:
    import io
    from pathlib import Path

    import numpy as np
    import pandas as pd
    import rasterio
    import xarray as xr
    from rasterio.transform import from_origin
    from tp.config import REPO_ROOT
    from tp.fallback import _download

    from pipeline.fetch import imerg as archive

    plan = replay_plan(case_id)
    frames = [f["at"] for f in plan["frames"]]
    start, end = pd.Timestamp(frames[0]), pd.Timestamp(frames[-1])
    bounds = common.case_cfg(case_id)["aoi"]
    if isinstance(bounds, dict):
        bounds = bounds["bbox"]
    root = Path(REPO_ROOT) / "data" / case_id
    granules = archive.search_granules_range(bounds, (start - pd.Timedelta(hours=76)).date(), end.date())
    if not granules:
        raise RuntimeError("No GES DISC IMERG Early granules for replay window")
    paths = archive.download_granules(granules, root / "raw" / "imerg_early")
    subsets = []
    for path in paths:
        subset = archive.open_halfhourly([path], bounds)
        subsets.append(subset.load())
        subset.close()
    rain = (xr.concat(subsets, dim="time").sortby("time")
            .transpose("time", "lat", "lon").sortby("lat", ascending=False))
    lats, lons = rain.lat.values, rain.lon.values
    dx, dy = float(np.median(np.diff(lons))), float(abs(np.median(np.diff(lats))))
    transform = from_origin(float(lons[0] - dx / 2), float(lats[0] + dy / 2), dx, dy)
    project = common.init(project)
    aoi = common.aoi_geom(case_id)

    def download(image):
        url = image.toFloat().getDownloadURL({
            "region": aoi, "crs": "EPSG:4326", "crs_transform": list(transform)[:6],
            "dimensions": [len(lons), len(lats)], "format": "GEO_TIFF", "filePerBand": False})
        payload = _download(url, limit=32 * 1024 * 1024)
        if payload is None:
            raise RuntimeError("Replay input exceeds GEE direct download limit")
        with rasterio.MemoryFile(io.BytesIO(payload)) as mem, mem.open() as src:
            return src.read(masked=True).filled(np.nan)

    static = download(tpi.climatology(case_id, project).select([
        "precipitation_p90", "precipitation_p95", "precipitation_p99"]))
    terrain = download(ee.Image.cat(list(tpi.terrain_scores())))
    soil = []
    for frame in frames:
        eligible = (ee.ImageCollection(common.COL_SMAP).select(common.BAND_SMAP)
                    .filter(ee.Filter.lte("system:time_start", ee.Date(frame).advance(-75, "hour").millis()))
                    .sort("system:time_start", False).limit(1))
        soil.append(download(eligible.mosaic())[0])
    cube = local_cube(rain.values, rain.time.values, frames, static, np.asarray(soil), terrain)
    for k, kind in enumerate(("slide", "flood")):
        path = root / "cog" / f"{case_id}_lead_time_{kind}_3h.tif"
        path.parent.mkdir(parents=True, exist_ok=True)
        with rasterio.open(path, "w", driver="GTiff", height=len(lats), width=len(lons),
                           count=len(frames), dtype="float32", crs="EPSG:4326",
                           transform=transform, nodata=np.nan, compress="deflate") as dst:
            dst.write(cube[:, k])
            dst.descriptions = tuple(f["band"] for f in plan["frames"])
            dst.update_tags(lead_time_assumptions=json.dumps(ASSUMPTIONS),
                            precipitation_source="GES DISC GPM_3IMERGHHE V07")
    return plan


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True, choices=("rs_2024", "wa_2025"))
    parser.add_argument("--step", choices=("3h",), default="3h")
    parser.add_argument("--imerg-early-asset")
    parser.add_argument("--project")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.dry_run:
        print(json.dumps(replay_plan(args.case, args.imerg_early_asset), indent=2))
        return 0
    if args.imerg_early_asset:
        run(args.case, args.imerg_early_asset, args.project)
    else:
        run_local(args.case, args.project)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
