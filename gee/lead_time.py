"""Latency-aware retrospective TPI replay; requires an archived IMERG Early asset."""
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
        "export_crs": "EPSG:6933", "export_scale_m": tpi.SCALE_M,
        "imerg_early_asset": imerg_early_asset,
        "execution_requires": "--imerg-early-asset with precipitationCal band, children YYYYMMDDTHHmm",
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
    if not args.imerg_early_asset:
        parser.error("--imerg-early-asset is required: public IMERG_V07 is not archived Early")
    run(args.case, args.imerg_early_asset, args.project)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
