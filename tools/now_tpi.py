"""Modo-Agora TPI (GEE) — entry point called by .github/workflows/now.yml.

Thin wrapper over gee/now_tpi.py: computes the 4-day TPI stack on the
ingested `now/` assets and exports now/{tpi_slide,tpi_flood,r,s}_d{0..3}
(one stamped Image per layer/day), then merges metrics under
cases['now'] in data/metrics.json.

Usage: uv run python tools/now_tpi.py [--dry-run] [--project P]
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

for path in (REPO_ROOT, REPO_ROOT / "pipeline"):  # bare `python` outside uv
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from gee.now_tpi import main

if __name__ == "__main__":
    raise SystemExit(main())
