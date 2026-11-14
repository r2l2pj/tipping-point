"""Build the NISAR flood mask for an event.

Usage: uv run python pipeline/scripts/02_flood_mask.py --event wa-2025-12 \
    --pre data/raw/nisar/gcov_hh_pre.tif --post data/raw/nisar/gcov_hh_post.tif
"""
import argparse
from pathlib import Path

from tp.events import event_data_dir
from tp.flood_mask import flood_mask


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", required=True)
    ap.add_argument("--pre", type=Path, required=True)
    ap.add_argument("--post", type=Path, required=True)
    args = ap.parse_args()

    out = event_data_dir(args.event) / "cog"
    print(flood_mask(args.pre, args.post, out))


if __name__ == "__main__":
    main()
