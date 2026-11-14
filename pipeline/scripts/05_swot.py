"""Fetch the SWOT river-level series for an event into data/<event>/series/.

Usage: uv run python pipeline/scripts/05_swot.py --event wa-2025-12
       uv run python pipeline/scripts/05_swot.py --event wa-2025-12 \
           --reach-id 71224100223 --reach-id 71224100231
"""
import argparse

from tp.events import load_event
from tp.swot import resolve_reach_ids, river_series, series_path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", required=True)
    ap.add_argument("--reach-id", action="append",
                    help="SWORD reach id — repeatable; defaults to "
                         "event['swot']['reach_ids'] in events/<event>.json")
    args = ap.parse_args()

    event = load_event(args.event)
    try:
        reach_ids = resolve_reach_ids(args.reach_id, event)
    except ValueError as e:
        ap.error(str(e))

    w = event["window"]
    out = series_path(args.event)
    print(river_series(reach_ids, w["start"], w["end"], out))
    print("wrote", out)


if __name__ == "__main__":
    main()
