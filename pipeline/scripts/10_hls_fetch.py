"""Search/download post-event HLS v2.0 scenes; prints the record list as JSON.

Usage: uv run python pipeline/scripts/10_hls_fetch.py --event wa-2025-12 --dry-run
       uv run python pipeline/scripts/10_hls_fetch.py --event wa-2025-12 --limit 4
"""
import argparse
import json

from tp.events import load_event
from tp.hls_fetch import RAW_HLS_DIR, SHORT_NAMES, fetch_scenes, resolve_window


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", required=True, help="event id, e.g. wa-2025-12")
    ap.add_argument("--after", help="first date (default: event reference_date)")
    ap.add_argument("--before", help="last date (default: event window end)")
    ap.add_argument("--limit", type=int,
                    help="keep the N least-cloudy scenes")
    ap.add_argument("--no-download", action="store_true",
                    help="search only — local_path stays null")
    ap.add_argument("--dry-run", action="store_true",
                    help="print resolved search params (no credentials needed)")
    args = ap.parse_args()

    event = load_event(args.event)

    if args.dry_run:
        print(json.dumps({
            "event": args.event,
            "short_names": list(SHORT_NAMES),
            "bounding_box": event["aoi"]["bbox"],
            "temporal": list(resolve_window(event, args.after, args.before)),
            "limit": args.limit,
            "download": not args.no_download,
            "out_dir": str(RAW_HLS_DIR),
        }, indent=2))
        return

    records = fetch_scenes(
        event,
        after=args.after,
        before=args.before,
        limit=args.limit,
        download=not args.no_download,
    )
    print(json.dumps(records, indent=2))


if __name__ == "__main__":
    main()
