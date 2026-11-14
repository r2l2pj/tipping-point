"""Hit-rate of catalog landslide points on the event-day TPI class COG.

Usage: uv run python pipeline/scripts/08_landslide_hit.py --event wa-2025-12 \
    --points data/raw/hls/landslide_catalog.csv \
    --tpi-classes data/wa_2025/cog/wa_2025_tpi_slide_20251210.tif
"""
import argparse
from pathlib import Path

from tp.landslide_hit import landslide_hit
from tp.metrics import merge_metrics


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", required=True, help="event id, e.g. wa-2025-12")
    ap.add_argument("--points", type=Path, required=True,
                    help="landslide catalog/inventory CSV (lat/lon, optional 'source')")
    ap.add_argument("--tpi-classes", type=Path, required=True,
                    help="event-day classified TPI COG (classes 0..3)")
    args = ap.parse_args()

    stats = landslide_hit(args.points, args.tpi_classes)
    # v4 metrics.json keys are flat (data/SCHEMA.md); merge, never overwrite.
    merge_metrics(
        args.event,
        flat={"landslide_hit_pct": stats["hit_pct"]},
        collections=stats["collections"],
    )
    print(stats)


if __name__ == "__main__":
    main()
