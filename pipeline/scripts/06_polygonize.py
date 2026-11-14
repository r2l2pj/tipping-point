"""Polygonise a mask raster (flood/water/landslide) to GeoJSON EPSG:4326.

Usage: uv run python pipeline/scripts/06_polygonize.py --event wa-2025-12 \
    --raster data/wa-2025-12/cog/flood_nisar.tif
"""
import argparse
from pathlib import Path

from tp.events import event_data_dir
from tp.polygonize import mask_to_geojson


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", required=True)
    ap.add_argument("--raster", type=Path, required=True)
    ap.add_argument("--values", type=int, nargs="+", default=[1],
                    help="class values to polygonise (e.g. 1 2 for landslide)")
    ap.add_argument("--min-area-km2", type=float, default=0.0)
    ap.add_argument("--out", type=Path, default=None,
                    help="default data/<event>/geojson/<raster>.geojson")
    args = ap.parse_args()

    out = args.out or (
        event_data_dir(args.event) / "geojson" / f"{args.raster.stem}.geojson"
    )
    print(
        mask_to_geojson(
            args.raster, out, values=tuple(args.values),
            min_area_km2=args.min_area_km2,
        )
    )


if __name__ == "__main__":
    main()
