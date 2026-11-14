"""wa_2025 landslide scar candidates from post-event HLS optical dNBR.

Usage: uv run python pipeline/scripts/09_hls_scar.py --event wa-2025-12 \
    --pre data/raw/hls/pre_nir_swir2.tif \
    --post data/raw/hls/post_nir_swir2_fmask.tif \
    [--slope data/raw/aux/slope.tif] [--dnbr-min 0.1]
"""
import argparse
from pathlib import Path

from tp.events import artifact_stem, event_data_dir
from tp.hls_scar import DNBR_MIN, detect
from tp.metrics import merge_metrics


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", required=True, help="event id, e.g. wa-2025-12")
    ap.add_argument("--pre", type=Path, required=True,
                    help="pre-event HLS raster: [NIR, SWIR2] or 1-band NBR")
    ap.add_argument("--post", type=Path, required=True,
                    help="post-event HLS raster; optional band 3 = Fmask")
    ap.add_argument("--slope", type=Path, default=None,
                    help="slope raster in degrees (gate > config slope_range_deg[0])")
    ap.add_argument("--dnbr-min", type=float, default=DNBR_MIN,
                    help="dNBR threshold for a candidate pixel")
    args = ap.parse_args()

    out = event_data_dir(args.event) / "cog" / (
        artifact_stem(args.event, "landslide_candidates") + ".tif")
    stats = detect(
        args.pre, args.post, out,
        slope_tif=args.slope, dnbr_min=args.dnbr_min,
    )
    # v4 metrics.json keys are flat (data/SCHEMA.md); merge, never overwrite.
    # Optical dNBR patches are candidates only — the missing GUNW pair is a
    # declared limitation.
    merge_metrics(
        args.event,
        flat={
            "landslide_hls_candidates": stats["candidate_patches"],
            "landslide_hls_candidate_px": stats["candidate_px"],
        },
        collections={"hls": f"{args.pre.stem},{args.post.stem}"},
    )
    print(stats)


if __name__ == "__main__":
    main()
