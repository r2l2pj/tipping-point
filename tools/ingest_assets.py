"""Batch-ingest local pipeline products as Earth Engine assets.

ASSET_REGISTRY maps canonical products to glob patterns under data/; each
matched file is staged to gs://<bucket>/staging/<case>/<name><ext> and
ingested (pipeline/fetch/ingest.py) under
projects/<GEE_PROJECT>/assets/tipping-point/<case>/<name>, with the row
upserted into data/<case>/assets.json. Idempotent — existing assets are
skipped unless --force.

Usage:
  uv run python tools/ingest_assets.py --case rs_2024 --dry-run
  uv run python tools/ingest_assets.py --event wa-2025-12
  uv run python tools/ingest_assets.py --case wa_2025 --manifest-only
  uv run python tools/ingest_assets.py --case rs_2024 --force

Env: GEE_PROJECT (default hermes-fesolucoes — not registered with Earth
Engine yet, so real runs are blocked; --dry-run is the demo path),
NOW_GCS_BUCKET (GCS_BUCKET alias), GOOGLE_APPLICATION_CREDENTIALS.
"""
from __future__ import annotations

import argparse
import os
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


def _planned_entry(case, e, status, bucket) -> dict:
    return {
        "name": e["name"],
        "gee_id": ingest.asset_id(case, e["name"]),
        "gcs_uri": ingest.staging_uri(bucket, case, e["name"],
                                     e["path"].suffix),
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
                    help="print the plan only; no creds, upload or "
                         "manifest write")
    ap.add_argument("--manifest-only", action="store_true",
                    help="write assets.json rows (status pending) without "
                         "staging or ingesting")
    ap.add_argument("--force", action="store_true",
                    help="re-ingest even when ee.data.getAsset finds the "
                         "asset")
    args = ap.parse_args(argv)

    case = args.case or events.case_key(args.event)
    entries = resolve_registry(case, events.DATA_DIR)
    bucket = (os.environ.get("NOW_GCS_BUCKET") or os.environ.get("GCS_BUCKET")
              or "<NOW_GCS_BUCKET>")
    if not entries:
        print(f"no registry files matched under data/ for case {case}")
        return

    if args.dry_run:
        for e in entries:
            p = _planned_entry(case, e, "dry_run", bucket)
            print(f"{p['kind']:5s} {e['path']}\n      -> {p['gcs_uri']}"
                  f"\n      -> {p['gee_id']}")
        print(f"{len(entries)} asset(s) would be ingested for {case}")
        return

    if args.manifest_only:
        for e in entries:
            path = ingest.append_manifest(
                case, _planned_entry(case, e, "pending", bucket))
        print(f"{path}: {len(entries)} pending asset(s)")
        return

    bucket = ingest.staging_bucket()
    for e in entries:
        gcs_uri = ingest.stage_to_gcs(e["path"], bucket=bucket, case=case,
                                      name=e["name"])
        entry = ingest.ingest_asset(
            gcs_uri, asset_id=ingest.asset_id(case, e["name"]),
            kind=e["kind"], force=args.force)
        entry["source"] = str(e["path"])
        ingest.append_manifest(case, entry)
        print(f"{entry['kind']:5s} {entry['status']:<10} {entry['gee_id']}")


if __name__ == "__main__":
    main()
