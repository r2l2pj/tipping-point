"""Auxiliary products per event: municipal boundaries + landslide catalog.

wa-* events get the NASA COOLR Global Landslide Catalog points layer
(REFERENCE-ONLY footer credit, never a TPI input); other events (rs) get the
landslide-inventory catalog instead.
"""
import argparse
from pathlib import Path

from tp.events import list_events, load_event


def aux_products(event_id: str) -> list[Path]:
    """Write the aux products for one event; return the written paths."""
    from fetch.boundaries import municipal_boundaries

    event = load_event(event_id)
    paths = [municipal_boundaries(event)]
    if event_id.startswith("wa-"):
        from fetch.glc import glc_catalog

        paths.append(glc_catalog(event))
    else:
        from fetch.inventory import inventory_catalog

        paths.append(inventory_catalog(event))
    return paths


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sel = ap.add_mutually_exclusive_group(required=True)
    sel.add_argument("--event", choices=sorted(list_events()))
    sel.add_argument("--all", action="store_true",
                     help="run every event in events/")
    args = ap.parse_args()

    for event_id in [args.event] if args.event else sorted(list_events()):
        for path in aux_products(event_id):
            print(path)


if __name__ == "__main__":
    main()
