"""Landslide-scar candidates from GUNW coherence change.

Usage: uv run python pipeline/scripts/03_landslide_coh.py --event wa-2025-12 \
    --coh-ref ... --coh-evt ... --slope ...
"""
import argparse

from tp.events import event_data_dir
from tp.landslide_coh import landslide_candidates


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", required=True)
    ap.add_argument("--coh-ref", required=True)
    ap.add_argument("--coh-evt", required=True)
    ap.add_argument("--slope", required=True)
    args = ap.parse_args()

    out = event_data_dir(args.event) / "cog" / "landslide_candidates.tif"
    from pathlib import Path

    print(
        landslide_candidates(
            Path(args.coh_ref), Path(args.coh_evt), Path(args.slope), out
        )
    )


if __name__ == "__main__":
    main()
