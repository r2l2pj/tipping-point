"""Batch-ingest local pipeline products as Earth Engine assets.

ASSET_REGISTRY maps canonical products to glob patterns under data/.
Table products are ingested via ingest_table (client-side
ee.FeatureCollection + Export.table.toAsset — no bucket, ruling L10)
under projects/<GEE_PROJECT>/assets/tipping-point/<case>/<name>, with the
row upserted into data/<case>/assets.json. Image products have no ingest
path (no bucket-free raster upload exists) and are recorded with status
'local-only'. Idempotent — existing assets are skipped unless --force.

Usage:
  uv run python tools/ingest_assets.py --case rs_2024 --dry-run
  uv run python tools/ingest_assets.py --event wa-2025-12
  uv run python tools/ingest_assets.py --case wa_2025 --manifest-only
  uv run python tools/ingest_assets.py --case rs_2024 --force

Env: GEE_PROJECT (default hermes-fesolucoes — not registered with Earth
Engine yet, so real runs are blocked; --dry-run is the demo path),
GOOGLE_APPLICATION_CREDENTIALS / GCP_SA_KEY. No bucket env is needed.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
from fetch import ingest
from tp import config, events

# Canonical product -> source files. 'patterns' are globs relative to data/
# with {case} = the case key; 'kind' selects the GEE ingestion endpoint.
# New COG products (flood_nisar, water_nisar, ...) are appended as one dict —
# nothing else in this file needs to change.
ASSET_REGISTRY = [
    # NISAR GCOV COGs: pipeline/tp/nisar.py execute_plan writes
    # data/raw/nisar/gcov_<var>_<pre|post>.tif (recorded in
    # data/<case>/nisar_products.json); published per-case clips live at
    # data/<case>/cog/<caso>_gcov_<var>_<AAAAMMDD>.tif.
    {"product": "nisar_gcov", "kind": "image",
     "patterns": ["{case}/cog/*gcov*.tif", "raw/nisar/gcov_*.tif"]},
    # OPERA DSWx WTR mosaics.
    {"product": "opera_dswx_wtr", "kind": "image",
     "patterns": ["{case}/cog/*dswx*.tif"]},
    # Landslide inventories -> table assets (rs_2024 local inventory,
    # wa_2025 GLC).
    {"product": "landslide_catalog", "kind": "table",
     "patterns": ["{case}/geojson/landslide_catalog.geojson"]},
    {"product": "municipal_boundaries", "kind": "table",
     "patterns": ["{case}/geojson/municipal_boundaries.geojson"]},
]


def asset_name(case: str, product: str, path: Path) -> str:
    """Canonical asset name: file stem minus the '<case>_' prefix."""
    stem = re.sub(r"[^a-z0-9]+", "_", path.stem.lower()).strip("_")
    if stem.startswith(f"{case}_"):
        stem = stem[len(case) + 1:]
    return stem or product


def resolve_registry(case, data_dir, registry=ASSET_REGISTRY) -> list[dict]:
    """Match registry patterns under data_dir -> [{name, path, kind, product}]."""
    data_dir = Path(data_dir)
    seen, entries = set(), []
    for spec in registry:
        for pattern in spec["patterns"]:
            for path in sorted(data_dir.glob(pattern.format(case=case))):
                name = asset_name(case, spec["product"], path)
                if name in seen:
                    continue
                seen.add(name)
                entries.append({"name": name, "path": path,
                                "kind": spec["kind"],
                                "product": spec["product"]})
    return entries


def _planned_entry(case, e, status) -> dict:
    return {
        "name": e["name"],
        "gee_id": ingest.asset_id(case, e["name"]),
        "kind": e["kind"],
        "status": status,
        "ingested_at": None,
        "source": str(e["path"]),
    }


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--case", choices=sorted(config.load_config()["cases"]),
                     help="config.yaml case key")
    grp.add_argument("--event", help="event id, e.g. rs-2024-05 "
                                   "(resolved via tp.events.case_key)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan only; no creds or manifest write")
    ap.add_argument("--manifest-only", action="store_true",
                    help="write assets.json rows (status pending) without "
                         "ingesting")
    ap.add_argument("--force", action="store_true",
                    help="re-ingest even when ee.data.getAsset finds the "
                         "asset")
    args = ap.parse_args(argv)

    case = args.case or events.case_key(args.event)
    entries = resolve_registry(case, events.DATA_DIR)
    if not entries:
        print(f"no registry files matched under data/ for case {case}")
        return

    if args.dry_run:
        for e in entries:
            p = _planned_entry(case, e, "dry_run")
            print(f"{p['kind']:5s} {e['path']}\n      -> {p['gee_id']}")
        print(f"{len(entries)} asset(s) would be ingested for {case}")
        return

    if args.manifest_only:
        for e in entries:
            path = ingest.append_manifest(
                case, _planned_entry(case, e, "pending"))
        print(f"{path}: {len(entries)} pending asset(s)")
        return

    for e in entries:
        gee_id = ingest.asset_id(case, e["name"])
        if e["kind"] == "table":
            entry = ingest.ingest_table(
                e["path"], asset_id=gee_id, force=args.force)
        else:  # L10: no raster-ingest path — record the planned asset
            entry = _planned_entry(case, e, "local-only")
            entry["gee_id"] = gee_id
        entry["source"] = str(e["path"])
        ingest.append_manifest(case, entry)
        print(f"{entry['kind']:5s} {entry['status']:<10} {entry['gee_id']}")


if __name__ == "__main__":
    main()
