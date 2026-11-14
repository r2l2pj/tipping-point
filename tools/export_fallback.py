"""Render missing static fallback files into data/ from GEE assets (no upload)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))

from tp import fallback


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--now", action="store_true")
    group.add_argument("--case", choices=fallback.CASES)
    parser.add_argument("--root", type=Path, default=Path("data"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.now and not args.dry_run:
            try:
                entries = fallback.render_now(args.root)
            except Exception as exc:
                raise RuntimeError(
                    "GEE fallback rendering failed; check required assets, "
                    "GOOGLE_APPLICATION_CREDENTIALS, GCP_SA_KEY and connectivity") from exc
        else:
            entries = (fallback.now_plan(args.root) if args.now else
                       fallback.plan(args.root, case=args.case))
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        parser.exit(1, f"fallback export failed: {exc}\n")
    for rel in entries:
        print(rel)


if __name__ == "__main__":
    main()
