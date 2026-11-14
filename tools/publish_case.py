"""Publish the stable app-read bundle for one event into data/<event>/.

Emits EPSG:3857 PNGs + png/_index.json, fixed-name geojson products,
series/imerg_smap_daily.json, and refreshes data/events.json layers[]
(runtime only — do not commit that file). Missing sources are reported as
skips, never fatal. Runs fully offline; credentials are never needed.

Usage: uv run python tools/publish_case.py --event wa-2025-12 [--dry-run]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))

from tp.bundle import publish_case


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--event", required=True,
                    help="event id, e.g. rs-2024-05 or wa-2025-12")
    ap.add_argument("--dry-run", action="store_true",
                    help="print planned writes + missing sources, write nothing")
    args = ap.parse_args()
    print(json.dumps(publish_case(args.event, dry_run=args.dry_run),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
