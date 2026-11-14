"""Search NISAR products for an event and print candidate pairs.

Usage: uv run python pipeline/scripts/01_get_nisar.py --event wa-2025-12 --product GCOV
"""
import argparse

from tp.events import load_event
from tp.nisar import search


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", required=True, help="event id, e.g. wa-2025-12")
    ap.add_argument("--product", default="GCOV", choices=["GCOV", "GUNW", "RSLC"])
    args = ap.parse_args()

    event = load_event(args.event)
    results = search(event, product=args.product)
    for r in results:
        p = r.properties
        print(p["fileID"], p["startTime"], p.get("pathNumber"), p.get("frameNumber"))
    print(f"{len(results)} granule(s)")


if __name__ == "__main__":
    main()
