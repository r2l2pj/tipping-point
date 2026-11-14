"""Search NISAR products for an event, print candidate pairs, and the
Harmony/GDAL extraction plan (no download without credentials).

Usage: uv run python pipeline/scripts/01_get_nisar.py --event wa-2025-12 --product GCOV
       uv run python pipeline/scripts/01_get_nisar.py --event wa-2025-12 --plan
"""
import argparse

from tp.events import load_event
from tp.nisar import COLLECTIONS, download_plan, gdal_subset_cmd, pick_pairs, search


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", required=True, help="event id, e.g. wa-2025-12")
    ap.add_argument("--product", default="GCOV",
                    choices=list(COLLECTIONS) + ["RSLC"])
    ap.add_argument("--pairs", action="store_true",
                    help="group granules into pre/post pairs on the same frame")
    ap.add_argument("--plan", action="store_true",
                    help="print the variable-extraction plan (Harmony/GDAL cmds)")
    args = ap.parse_args()

    event = load_event(args.event)

    if args.plan:
        bbox = tuple(event["aoi"]["bbox"])
        for step in download_plan(event):
            print(f"{step['product']:9s} {step['which']:4s} {step['variable']:18s} "
                  f"-> {step['out']}")
            if step["h5_path"]:
                print("   ", gdal_subset_cmd(
                    "<granule_h5_url>", step["h5_path"], bbox, step["out"]))
        return

    results = search(event, product=args.product)
    if args.pairs:
        pairs = pick_pairs(results, event["reference_date"])
        for key, pair in pairs.items():
            print(key, "->", pair)
        print(f"{len(pairs)} pair(s) from {len(results)} granule(s)")
    else:
        for r in results:
            p = r.properties
            print(p["fileID"], p["startTime"], p.get("pathNumber"),
                  p.get("frameNumber"))
        print(f"{len(results)} granule(s)")


if __name__ == "__main__":
    main()
