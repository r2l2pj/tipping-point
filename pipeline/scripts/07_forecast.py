"""Emit the perfect-forecast TPI layers for a replay event.

Usage: uv run python pipeline/scripts/07_forecast.py --event rs-2024-05 \
    --series data/rs_2024/cog/rs_2024_tpi_slide_20240502.tif \
    [--issue 2024-05-02] [--days 1,2,3]

The d+k raster is the observed TPI band of issue+k (perfect forecast —
DECIDED). ``--issue`` defaults to the event's reference_date from
events/<event>.json. ``--event`` accepts either the event id or the case id
(L4 — outputs land under data/<case-id>/cog/).
"""
import argparse
from pathlib import Path

from tp.events import load_event
from tp.forecast import perfect_forecast, resolve_event_id


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", required=True,
                    help="event id (rs-2024-05) or case id (rs_2024)")
    ap.add_argument("--series", type=Path, required=True,
                    help="float TPI series COG (bands named YYYYMMDD)")
    ap.add_argument("--issue", default=None,
                    help="issue date YYYY-MM-DD (default: event reference_date)")
    ap.add_argument("--days", default="1,2,3", help="comma-separated forecast days")
    args = ap.parse_args()

    event_id = resolve_event_id(args.event)
    issue = args.issue or load_event(event_id)["reference_date"]
    days = tuple(int(d) for d in args.days.split(","))
    report = perfect_forecast(args.series, event_id, issue, days=days)
    print(report)


if __name__ == "__main__":
    main()
