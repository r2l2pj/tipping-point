"""Copy producer-written data files to GCS as a static fallback."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))

from tp import fallback


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--now", action="store_true")
    group.add_argument("--case", choices=fallback.CASES)
    parser.add_argument("--root", type=Path, default=Path("data"))
    parser.add_argument("--prefix", default="fallback/")
    parser.add_argument("--bucket")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        entries = fallback.plan(args.root, now=args.now, case=args.case, prefix=args.prefix)
        if not args.dry_run:
            fallback.upload(entries, bucket=args.bucket)
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        parser.exit(1, f"fallback export failed: {exc}\n")
    for _, key in entries:
        print(key)


if __name__ == "__main__":
    main()
