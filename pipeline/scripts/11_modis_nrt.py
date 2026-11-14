"""Fetch MODIS NRT flood (MCDWD) scenes for an event and compare the water
mask against a reference raster; print flat modis_nrt_* metrics as JSON.

Usage: uv run python pipeline/scripts/11_modis_nrt.py --event wa-2025-12 --dry-run
       uv run python pipeline/scripts/11_modis_nrt.py --event wa-2025-12 \
           --ref data/wa-2025-12/cog/wa_2025_water_nisar_20251210.tif
"""
import argparse
import json
from pathlib import Path

from tp.events import artifact_stem, event_data_dir, load_event
from tp.modis_nrt import SHORT_NAME, compare_water, fetch_nrt, pick_scene, search_params


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", required=True, help="event id, e.g. wa-2025-12")
    ap.add_argument("--ref", type=Path, default=None,
                    help="reference water raster; defaults to the event's "
                         "water_nisar COG when present")
    ap.add_argument("--start", default=None, help="override window start (YYYY-MM-DD)")
    ap.add_argument("--end", default=None, help="override window end (YYYY-MM-DD)")
    ap.add_argument("--short-name", default=SHORT_NAME,
                    help="CMR short_name; resolved against known MCDWD names at run time")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the CMR search parameters only (no credentials needed)")
    args = ap.parse_args()

    event = load_event(args.event)

    if args.dry_run:
        print(json.dumps(
            search_params(event, args.short_name, args.start, args.end), indent=2))
        return

    d = event_data_dir(args.event) / "modis_nrt"
    paths = fetch_nrt(event, d, short_name=args.short_name,
                      start=args.start, end=args.end)
    for p in paths:
        print(p)

    ref = args.ref
    if ref is None:
        cand = (event_data_dir(args.event) / "cog"
                / f"{artifact_stem(args.event, 'water_nisar')}.tif")
        ref = cand if cand.exists() else None

    metrics = {
        "event": args.event,
        **compare_water(pick_scene(paths, event.get("reference_date")), ref),
    }
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
