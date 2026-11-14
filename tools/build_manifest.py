"""Build data/manifest.json — merges per-case nodes + data/now/manifest.json.

The root manifest is the contract file the app reads (data/SCHEMA.md). It is
always written LAST, after every other data/ file, atomically via tmp +
os.replace. --dry-run prints the JSON and writes nothing.

Usage: uv run python tools/build_manifest.py [--data-root data] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

if str(REPO_ROOT / "pipeline") not in sys.path:  # bare `python` outside the uv env
    sys.path.insert(0, str(REPO_ROOT / "pipeline"))

from tp.manifest import build_root, write_root_manifest


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-root", type=Path, default=REPO_ROOT / "data",
                    help="data/ directory to read and write (default: repo data/)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the manifest JSON; write nothing")
    args = ap.parse_args()

    if args.dry_run:
        print(json.dumps(build_root(args.data_root), ensure_ascii=False, indent=2))
        return
    root = write_root_manifest(args.data_root)
    print(f"wrote {args.data_root / 'manifest.json'} ({len(root['cases'])} cases)")


if __name__ == "__main__":
    main()
