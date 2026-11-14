"""Landslide-scar candidates from GUNW 20 m coherence change.

Usage: uv run python pipeline/scripts/03_landslide_coh.py --event wa-2025-12 \
    --coh-ref ... --coh-evt ... --slope ... \
    [--flood flood_nisar.tif] [--dem dem.tif] [--snow snow.tif] \
    [--hv-pre gcov_hvhv_pre.tif --hv-post gcov_hvhv_post.tif]
"""
import argparse
from pathlib import Path

from tp.events import event_data_dir
from tp.landslide_coh import MAX_ELEV_M, landslide_candidates


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", required=True)
    ap.add_argument("--coh-ref", required=True,
                    help="20 m coherence, no-event pair")
    ap.add_argument("--coh-evt", required=True,
                    help="20 m coherence, event-crossing pair")
    ap.add_argument("--slope", required=True, help="slope raster, degrees")
    ap.add_argument("--flood", default=None, help="flood mask to exclude")
    ap.add_argument("--snow", default=None, help="snow-cover mask to exclude")
    ap.add_argument("--dem", default=None,
                    help="elevation raster; pixels above --max-elev dropped")
    ap.add_argument("--hv-pre", default=None)
    ap.add_argument("--hv-post", default=None,
                    help="GCOV HVHV pre/post; HV drop > 2 dB -> high confidence")
    ap.add_argument("--max-elev", type=float, default=MAX_ELEV_M,
                    help="elevation cap in metres (crude snow line)")
    args = ap.parse_args()

    out = event_data_dir(args.event) / "cog" / "landslide_candidates.tif"
    print(
        landslide_candidates(
            Path(args.coh_ref), Path(args.coh_evt), Path(args.slope), out,
            flood_path=Path(args.flood) if args.flood else None,
            snow_path=Path(args.snow) if args.snow else None,
            dem_path=Path(args.dem) if args.dem else None,
            hv_pre_path=Path(args.hv_pre) if args.hv_pre else None,
            hv_post_path=Path(args.hv_post) if args.hv_post else None,
            max_elev_m=args.max_elev,
        )
    )


if __name__ == "__main__":
    main()
