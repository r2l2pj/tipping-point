"""Build data/tipping_point.sqlite from the published JSON/GeoJSON under data/.

    PYTHONPATH=pipeline python tools/build_sqlite.py [--data-root data] [--out PATH]

Run after the producers (and after the manifests are written). The app keeps
reading JSON; this file is for analysis/queries. See data/SCHEMA.md.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "pipeline"))
from tp.sqlite_store import build  # noqa: E402

MAX_BYTES = 50 * 1024 * 1024


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-root", type=Path, default=REPO / "data")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    counts = build(args.data_root, args.out)
    print(json.dumps(counts))
    if counts["bytes"] > MAX_BYTES:
        print(f"ERROR: sqlite {counts['bytes']} B > {MAX_BYTES} B budget", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
