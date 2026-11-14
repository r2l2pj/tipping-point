"""Validate the NISAR water mask against OPERA DSWx-HLS; write metrics.json."""
import argparse
from pathlib import Path

from tp.events import event_data_dir
from tp.validation import validate_flood, write_metrics


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--event", required=True)
    ap.add_argument("--opera-wtr", type=Path, required=True,
                    help="path to the OPERA B01_WTR layer")
    args = ap.parse_args()

    d = event_data_dir(args.event)
    m = validate_flood(d / "cog" / "water_nisar.tif", args.opera_wtr)
    write_metrics({"event": args.event, "flood": m}, d / "metrics.json")
    print(m)


if __name__ == "__main__":
    main()
