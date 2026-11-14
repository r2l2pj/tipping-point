"""Fetch the SWOT river-level series for an event into data/<event>/series/."""
import argparse

from tp.events import event_data_dir, load_event
from tp.swot import river_series


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", required=True)
    ap.add_argument("--reach-id", required=True,
                    help="SWORD reach id — find it in the SWOT/PO.DAAC viewer")
    args = ap.parse_args()

    event = load_event(args.event)
    w = event["window"]
    out = event_data_dir(args.event) / "series" / "swot_river.json"
    print(river_series(args.reach_id, w["start"], w["end"], out))


if __name__ == "__main__":
    main()
