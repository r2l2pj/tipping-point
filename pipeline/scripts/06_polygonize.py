"""Polygonise a mask raster (flood/water/landslide) to GeoJSON EPSG:4326.

Usage: uv run python pipeline/scripts/06_polygonize.py --event wa-2025-12 \
    --raster data/wa-2025-12/cog/wa_2025_flood_nisar_20251210.tif

The default output name keeps the raster stem, so v4-named inputs
(<caso>_<camada>_<AAAAMMDD>) yield same-named .geojson outputs.
"""
import argparse
from pathlib import Path

from tp import config, events
from tp.polygonize import PRODUCT_PATTERNS, mask_to_geojson, product_kind


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", "--case", required=True)
    ap.add_argument("--raster", type=Path)
    ap.add_argument("--product", choices=tuple(PRODUCT_PATTERNS))
    ap.add_argument("--config", type=Path)
    ap.add_argument("--date")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--simplify-m", type=float, default=None)
    ap.add_argument("--values", type=int, nargs="+", default=None,
                    help="class values to polygonise (e.g. 1 2 for landslide)")
    ap.add_argument("--min-area-km2", type=float, default=None)
    ap.add_argument("--out", type=Path, default=None,
                    help="default data/<event>/geojson/<raster>.geojson")
    args = ap.parse_args()

    case = config.case_id(args.event)
    settings = config.load_config(args.config).get("polygonize", {})
    minimum = args.min_area_km2 if args.min_area_km2 is not None else settings.get("min_area_km2", 0.001)
    tolerance = args.simplify_m if args.simplify_m is not None else settings.get("simplify_m", 1.0)
    root = events.DATA_DIR / case
    if args.out and not args.raster:
        ap.error("--out requires --raster")
    plans = []
    if args.raster:
        plans.append((args.raster, args.out or root / "geojson" / f"{args.raster.stem}.geojson", args.product))
    else:
        for product in ([args.product] if args.product else PRODUCT_PATTERNS):
            matches = sorted({
                path for directory in (root / "cog", events.DATA_DIR / "raw" / "nisar")
                for pattern in PRODUCT_PATTERNS[product] for path in directory.rglob(pattern)
                if directory == root / "cog" or path.name.startswith(case + "_") or case in path.parts
            })
            if not matches:
                if not args.dry_run:
                    ap.error(f"input mask raster not found for {case}/{product}")
                print(f"{product}: missing input; search {root / 'cog'} and {events.DATA_DIR / 'raw' / 'nisar'}")
                print(f"planned output: {root / 'geojson' / (product + '.geojson')}")
            for raster in matches:
                plans.append((raster, root / "geojson" / f"{raster.stem}.geojson", product))
    for raster, out, product in plans:
        print(f"{raster} -> {out}")
        if not args.dry_run:
            try:
                print(mask_to_geojson(
                    raster, out,
                    values=tuple(args.values or ((1, 2) if (product or product_kind(raster)) ==
                                                "landslide_candidates" else (1,))),
                    min_area_km2=minimum, simplify_m=tolerance, kind=product, date=args.date,
                ))
            except (FileNotFoundError, ValueError) as exc:
                ap.error(str(exc))


if __name__ == "__main__":
    main()
