"""Response summary for the Now domain (unit R3, L27).

Unions the flood candidates in data/now/response/ (OPERA DSWx-S1 from R1 +
NISAR GCOV from R2) with the Now TPI job's Critical areas, counts GPW
people (deduped where sources overlap), ranks municipalities into
priority_zones, merges the headline into data/now/summary.json and emits
the manifest node for lead-live. Any subset of the inputs may be absent —
missing input -> null + _null_reasons, never 0, never synthetic.

    PYTHONPATH=.:pipeline .venv/bin/python pipeline/scripts/15_response_summary.py
        [--now-dir data/now] [--res 0.0025] [--ufs RS,SC,PR,SP,MS]
        [--boundaries PATH.geojson] [--no-zones]

People: ONLY <now-dir>/static/gpw/gpw_popcount_2020_30sec.tif (missing ->
people_* null + reason). Boundaries default: live IBGE fetch (malhas, qualidade=minima). Offline
runs pass --boundaries <fc.geojson> or --no-zones.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1]
if str(PIPELINE) not in sys.path:
    sys.path.insert(0, str(PIPELINE))

from tp import config
from tp import response_summary as rs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--now-dir", type=Path,
                    default=config.REPO_ROOT / "data" / "now",
                    help="data/now directory (default: repo data/now)")
    ap.add_argument("--res", type=float, default=rs.ANALYSIS_RES_DEG,
                    help="analysis grid posting, degrees (default 0.0025)")
    ap.add_argument("--ufs", default=",".join(rs.UFS_DEFAULT),
                    help="comma-separated IBGE UFs for municipal boundaries")
    ap.add_argument("--boundaries", type=Path,
                    help="local FeatureCollection of municipalities (skips the "
                         "IBGE fetch; offline mode)")
    ap.add_argument("--no-zones", action="store_true",
                    help="skip municipal boundaries entirely (empty zones)")
    args = ap.parse_args(argv)

    bbox = config.now_bounds()
    ufs = tuple(u.strip() for u in args.ufs.split(",") if u.strip())
    if args.no_zones:
        load_boundaries = None
    elif args.boundaries is not None:
        bpath = args.boundaries
        load_boundaries = lambda bb: json.loads(bpath.read_text())
    else:
        load_boundaries = lambda bb: rs.ibge_municipalities(bb, ufs=ufs)

    summary = rs.run_summary(
        bbox, args.now_dir,
        load_boundaries=load_boundaries,
        res=args.res,
        log=lambda m: print(m, file=sys.stderr, flush=True),
    )
    print(json.dumps({k: summary.get(k) for k in (
        "people_flooded", "people_flooded_lower", "people_critical",
        "people_exposed", "flooded_km2", "flooded_km2_lower", "critical_km2",
        "exposed_km2", "overlap_km2",
        "priority_zones", "nisar", "_null_reasons")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
