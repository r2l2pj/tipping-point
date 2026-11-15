"""NISAR GCOV radar flood candidates for the Now domain (unit R2, L27).

Searches CMR for NISAR_L2_GCOV_V1 granules over config.yaml domains.now.bbox,
pairs the newest pass with the previous pass on the same track + direction +
frame (<= 36 d), reads only the co-pol term of the chunks over the domain via
HTTP range reads, runs the wa_2025 flood method (tp.flood_mask) and writes

    data/now/response/flood_nisar.geojson   (status "candidate", never "confirmed")
    data/now/response/nisar_meta.json       (written last; nulls + _null_reasons)

Auth: EARTHDATA_TOKEN (bearer) only for the HDF5 bytes; the CMR search is
anonymous. Usage:
    uv run python pipeline/scripts/14_response_nisar.py [--out data/now/response]
        [--res 100] [--lookback-days 60] [--max-gap-days 36] [--gpw PATH|URL]
        [--no-hand]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1]
if str(PIPELINE) not in sys.path:
    sys.path.insert(0, str(PIPELINE))

from fetch import nisar as fetch_nisar
from fetch.edl import edl_token
from tp import config
from tp import response_nisar as rn


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=config.REPO_ROOT / "data" / "now" / "response")
    ap.add_argument("--res", type=float, default=rn.TARGET_RES_M, help="target grid (m)")
    ap.add_argument("--lookback-days", type=int, default=60)
    ap.add_argument("--max-gap-days", type=float, default=fetch_nisar.PAIR_MAX_GAP_DAYS)
    ap.add_argument("--min-overlap-km2", type=float, default=fetch_nisar.MIN_DOMAIN_OVERLAP_KM2)
    ap.add_argument("--gpw", help="GPW count raster (local path or /vsicurl/ URL)")
    ap.add_argument("--no-hand", action="store_true", help="skip the HAND gate")
    args = ap.parse_args(argv)

    bbox = config.now_bounds()
    hand = None if args.no_hand else config.REPO_ROOT / rn.HAND_REL
    if hand is not None and not hand.is_file():
        hand = None

    def _pair(granules, bb):
        return fetch_nisar.pair_latest_pass(
            granules, bb, max_gap_days=args.max_gap_days, min_overlap_km2=args.min_overlap_km2)

    meta = rn.run_response(
        bbox, args.out,
        search=lambda bb, start, end: fetch_nisar.search_gcov_domain(bb, start, end),
        pair=_pair,
        token=edl_token(),
        hand_path=hand,
        gpw_src=args.gpw,
        lookback_days=args.lookback_days,
        res=args.res,
        log=lambda m: print(m, file=sys.stderr, flush=True),
    )
    summary = {k: meta.get(k) for k in ("latest_acquired", "latency_h", "flooded_km2",
                                         "open_water_km2", "bright_km2", "receded_km2",
                                         "people_flooded", "n_polygons", "_null_reasons")}
    summary["pairs"] = [(p["pre"], p["post"]) for p in meta["pairs"]]
    import json

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
