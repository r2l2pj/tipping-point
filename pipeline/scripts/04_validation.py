"""Validate the NISAR water mask against OPERA DSWx; merge metrics.json.

Usage: uv run python pipeline/scripts/04_validation.py --event wa-2025-12
    [--opera-wtr tile.tif | --auto] [--hls-cloud fmask.tif] [--cloud-only]

--opera-wtr: one local OPERA WTR raster.
--auto: earthaccess search+download of OPERA_L3_DSWX-HLS_V1
    (OPERA_L3_DSWX-S1_V1 for wa_2025) over the case AOI, event_date..+5d;
    downloaded tiles are mosaicked onto the NISAR grid (nearest) before the
    iou/f1/cloud_pct compare.
--hls-cloud: an HLS Fmask raster (assumes bit1=cloud) whose cloud fraction
    feeds cloud_pct when OPERA is unavailable.
--cloud-only: merge cloud_pct + iou/f1 = null with reasons (the RS case:
    optical fully clouded — the "why radar" number).
"""
import argparse
from datetime import date, timedelta
from pathlib import Path

import rioxarray as rxr
from rioxarray.exceptions import RioXarrayError
from tp import config
from tp.events import DATA_DIR, artifact_stem
from tp.metrics import merge_metrics
from tp.validation import (
    case_id,
    cloud_only_merge,
    cloud_pct,
    compare_wtr,
    hls_fmask_cloud_pct,
    mosaic_tiles,
    opera_product,
    search_download_opera,
)


def _download_opera(event: str, short_name: str, collections: dict) -> list:
    """earthaccess fetch of OPERA DSWx over the case AOI, event_date..+5d."""
    c = config.case(case_id(event))
    start = c["event_date"]
    if isinstance(start, str):
        start = date.fromisoformat(start)
    temporal = (start.isoformat(), (start + timedelta(days=5)).isoformat())
    dest = DATA_DIR / "raw" / "opera" / case_id(event)
    dest.mkdir(parents=True, exist_ok=True)
    paths, ids = search_download_opera(
        tuple(c["aoi"]), temporal, dest, short_name=short_name
    )
    collections["opera_dswx"] = short_name
    if ids:
        collections["opera_dswx_granules"] = ", ".join(sorted(ids))
    return [Path(p) for p in paths]


def _aoi_ref(tile_path: Path, case_id: str):
    """First tile as the mosaic grid, clipped to the case AOI when possible."""
    ref = rxr.open_rasterio(tile_path).squeeze()
    try:
        return ref.rio.clip_box(*config.case(case_id)["aoi"], crs="EPSG:4326")
    except (RioXarrayError, ValueError):
        return ref


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", required=True)
    ap.add_argument("--opera-wtr", type=Path,
                    help="path to a local OPERA B01_WTR raster")
    ap.add_argument("--auto", action="store_true",
                    help="earthaccess search+download OPERA DSWx for the case "
                         "AOI, event_date..+5d (DSWx-S1 for wa_2025)")
    ap.add_argument("--hls-cloud", type=Path,
                    help="HLS Fmask raster (bit1=cloud); feeds cloud_pct when "
                         "OPERA is unavailable")
    ap.add_argument("--cloud-only", action="store_true",
                    help="merge cloud_pct + null iou/f1 with reasons (optical "
                         "fully clouded)")
    args = ap.parse_args()

    if not (args.opera_wtr or args.auto or args.hls_cloud):
        ap.error("one of --opera-wtr, --auto or --hls-cloud is required")
    if args.opera_wtr and args.auto:
        ap.error("--opera-wtr and --auto are mutually exclusive")
    if args.cloud_only and not (args.hls_cloud or args.auto or args.opera_wtr):
        ap.error("--cloud-only needs a cloud source: --hls-cloud or OPERA")

    event = args.event
    d = DATA_DIR / case_id(event)          # L4: data/<case>/, not data/<event>/
    nisar_path = d / "cog" / f"{artifact_stem(event, 'water_nisar')}.tif"
    short_name = opera_product(case_id(event))
    collections: dict = {}

    paths = []
    if args.auto:
        paths = _download_opera(event, short_name, collections)
        if not paths:
            print("earthaccess returned no OPERA granules for the window")
    elif args.opera_wtr:
        paths = [args.opera_wtr]
        collections["opera_dswx"] = short_name

    # WTR mosaic: on the NISAR grid when the mask exists, else on the tile
    # grid clipped to the AOI (cloud_pct only).
    wtr = None
    nisar = None
    if paths:
        if nisar_path.exists():
            nisar = rxr.open_rasterio(nisar_path).squeeze()
            wtr = mosaic_tiles(paths, nisar)
        else:
            wtr = mosaic_tiles(paths, _aoi_ref(paths[0], case_id(event)))
            print(f"no NISAR water mask at {nisar_path} — cloud stats only")

    if args.cloud_only:
        if wtr is not None:
            pct, source = cloud_pct(wtr), short_name
        elif args.hls_cloud:
            pct, source = hls_fmask_cloud_pct(args.hls_cloud), "hls_fmask"
        else:
            ap.error("--cloud-only: no OPERA tiles and no --hls-cloud raster")
        merged = cloud_only_merge(event, pct, source)
        print(merged)
        return

    if wtr is not None and nisar is not None:
        merged = merge_metrics(
            event,
            flat=compare_wtr(nisar, wtr),
            collections={**collections, "cloud_pct_source": short_name},
        )
    elif wtr is not None or args.hls_cloud:
        # cloud stat only: OPERA mosaic preferred, Fmask as fallback
        if wtr is not None:
            pct, source = cloud_pct(wtr), short_name
        else:
            pct, source = hls_fmask_cloud_pct(args.hls_cloud), "hls_fmask"
        flat = {"cloud_pct": pct}
        reasons = {}
        if wtr is not None:
            flat["iou"] = flat["f1"] = None
            reasons["iou"] = reasons["f1"] = f"no NISAR water mask at {nisar_path.name}"
        merged = merge_metrics(
            event, flat=flat, reasons=reasons,
            collections={**collections, "cloud_pct_source": source},
        )
    print(merged)


if __name__ == "__main__":
    main()
