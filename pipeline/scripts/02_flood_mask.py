"""Build the NISAR flood mask for an event.

Usage: uv run python pipeline/scripts/02_flood_mask.py --event wa-2025-12 \
    --pre data/raw/nisar/gcov_hh_pre.tif --post data/raw/nisar/gcov_hh_post.tif \
    [--watermask nisar_watermask.tif] [--hand hand.tif] [--no-bright]
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
    ap.add_argument("--watermask", type=Path, default=None,
                    help="NISAR_WATERMASK permanent-water raster")
    ap.add_argument("--hand", type=Path, default=None,
                    help="HAND proxy raster (metres); flood gated to < 15 m")
    ap.add_argument("--mmu-ha", type=float, default=3.0,
                    help="minimum mapping unit in hectares (~3 ha, DSWx-NI)")
    ap.add_argument("--no-bright", action="store_true",
                    help="skip the bright-flood candidate layer")
    args = ap.parse_args()

    out = event_data_dir(args.event) / "cog"
    print(
        flood_mask(
            args.pre, args.post, out,
            watermask_path=args.watermask, hand_path=args.hand,
            mmu_ha=args.mmu_ha, bright=not args.no_bright,
        )
    )


if __name__ == "__main__":
    main()
