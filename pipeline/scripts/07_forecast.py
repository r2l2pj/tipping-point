"""Emit the perfect-forecast TPI layers for a replay event.

Usage: uv run python pipeline/scripts/07_forecast.py --event rs-2024-05 \
    --series data/rs-2024-05/cog/rs_2024_tpi_slide_20240502.tif \
    [--issue 2024-05-02] [--days 1,2,3]

The d+k raster is the observed TPI band of issue+k (perfect forecast —
DECIDED). ``--issue`` defaults to the event's reference_date from
events/<event>.json.
"""
import argparse
from pathlib import Path

from tp.events import event_data_dir, load_event
from tp.forecast import perfect_forecast


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", required=True, help="event id (e.g. rs-2024-05)")
    ap.add_argument("--series", type=Path, required=True,
                    help="float TPI series COG (bands named YYYYMMDD)")
    ap.add_argument("--issue", default=None,
                    help="issue date YYYY-MM-DD (default: event reference_date)")
    ap.add_argument("--days", default="1,2,3", help="comma-separated forecast days")
    args = ap.parse_args()

    issue = args.issue or load_event(args.event)["reference_date"]
    days = tuple(int(d) for d in args.days.split(","))
    report = perfect_forecast(args.series, args.event, issue, days=days,
                              out_dir=event_data_dir(args.event) / "cog")
    print(report)


if __name__ == "__main__":
    main()
