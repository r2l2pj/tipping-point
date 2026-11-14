"""SWOT river-level series -> GEE table asset (validation.yml L8a).

Reads data/<case>/series/<case>_swot_*.json — records written by
tp.swot.river_series (reach_id, time_str, river_m; may carry reach
geometry) — and builds a GeoJSON FeatureCollection with one feature per
observation: geometry is the reach centroid/point when carried on the
record, else the reach endpoints from the event 'swot' block, else null
(the asset still ingests as a plain table). Feature properties:
{reach_id, t_utc (ISO), river_m, source: 'SWOT Hydrocron'}.

The staged GeoJSON is pushed to GCS and ingested as the table asset
projects/<GEE_PROJECT>/assets/tipping-point/<case>/swot_series via
ee.data.startTableIngestion, through the shared ingest contract
(stage_to_gcs / ingest_asset / append_manifest — lands on unit/ingest as
pipeline/fetch/ingest.py). The import is optional so --dry-run works
without it.

Idempotent: the run is skipped when data/<case>/assets.json already
records the asset with the same content hash, unless --force.

Usage:
    uv run python -m pipeline.fetch.swot --case rs_2024 --dry-run
    uv run python -m pipeline.fetch.swot --event rs-2024-05 --force

Env: GEE_PROJECT (asset project), NOW_GCS_BUCKET (staging bucket).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from tp.events import DATA_DIR, EVENTS_DIR, case_key, load_event

SOURCE = "SWOT Hydrocron"
ASSET_NAME = "swot_series"
ASSET_ROOT = "projects/{project}/assets/tipping-point/{case}"

for _modname in ("pipeline.fetch.ingest", "fetch.ingest", "tp.ingest"):
    try:  # ingest contract — lands on unit/ingest; absent -> --dry-run only
        _m = importlib.import_module(_modname)
        stage_to_gcs = _m.stage_to_gcs
        ingest_asset = _m.ingest_asset
        append_manifest = _m.append_manifest
        break
    except (ImportError, AttributeError):
        continue
else:
    stage_to_gcs = ingest_asset = append_manifest = None

_LON_LAT_KEYS = (
    ("lon", "lat"),
    ("longitude", "latitude"),
    ("reach_lon", "reach_lat"),
    ("x", "y"),
)
_GEOJSON_GEOM_TYPES = {
    "Point", "MultiPoint", "LineString", "MultiLineString",
    "Polygon", "MultiPolygon", "GeometryCollection",
}


def series_files(case_id: str, data_dir: Path | None = None) -> list[Path]:
    """data/<case>/series/<case>_swot_*.json, sorted."""
    d = Path(data_dir or DATA_DIR) / case_id / "series"
    return sorted(d.glob(f"{case_id}_swot_*.json"))


def load_records(path: Path) -> tuple[list[dict], dict]:
    """Series file -> (record dicts, per-reach geometry map).

    Accepts the tp.swot orient='records' list, or a dict carrying
    'records'/'features' plus an optional 'reaches' geometry map.
    GeoJSON Feature entries are flattened (properties + geometry).
    """
    data = json.loads(Path(path).read_text())
    if not isinstance(data, (list, dict)):
        raise TypeError(
            f"{path}: expected a JSON list or object, "
            f"got {type(data).__name__}"
        )
    items = data if isinstance(data, list) else (
        data.get("records") or data.get("features") or []
    )
    reaches = data.get("reaches") if isinstance(data, dict) else None
    records = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "Feature" and isinstance(item.get("properties"), dict):
            item = {**item["properties"], "geometry": item.get("geometry")}
        records.append(item)
    return records, reaches if isinstance(reaches, dict) else {}


def _iso_utc(value) -> str | None:
    """'2025-12-01T00:11:00Z' -> ISO with offset; unparseable kept verbatim."""
    if value in (None, ""):
        return None
    s = str(value)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return s
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


def _point_from_fields(d: dict) -> dict | None:
    for lon_k, lat_k in _LON_LAT_KEYS:
        if d.get(lon_k) is not None and d.get(lat_k) is not None:
            return {
                "type": "Point",
                "coordinates": [float(d[lon_k]), float(d[lat_k])],
            }
    return None


def _as_geometry(value) -> dict | None:
    """GeoJSON-ish value -> geometry dict, or None.

    Accepts a geometry/Feature dict, a {lon,lat}-style dict, a [lon,lat]
    pair, or a list of [lon,lat] endpoints (-> LineString).
    """
    if not value:
        return None
    if isinstance(value, dict):
        if value.get("type") == "Feature":
            return value.get("geometry")
        if value.get("type") in _GEOJSON_GEOM_TYPES:
            return value
        return _point_from_fields(value)
    if isinstance(value, (list, tuple)):
        if len(value) == 2 and all(isinstance(v, (int, float)) for v in value):
            return {"type": "Point", "coordinates": [float(value[0]), float(value[1])]}
        coords = [
            [float(pt[0]), float(pt[1])]
            for pt in value
            if isinstance(pt, (list, tuple)) and len(pt) >= 2
        ]
        if len(coords) == 1:
            return {"type": "Point", "coordinates": coords[0]}
        if len(coords) >= 2:
            return {"type": "LineString", "coordinates": coords}
    return None


def _centroid(geom: dict) -> dict:
    """Point passes through; anything else collapses to its centroid."""
    if geom.get("type") == "Point" or "coordinates" not in geom:
        return geom
    from shapely.geometry import mapping, shape

    c = shape(geom).centroid
    return mapping(c) if not c.is_empty else geom


def _record_geometry(rec: dict) -> dict | None:
    """Geometry carried on the observation -> reach centroid/point."""
    geom = _as_geometry(rec.get("geometry"))
    if geom is not None:
        return _centroid(geom)
    return _point_from_fields(rec)


def _event_geometry(event: dict | None, reach_id: str) -> dict | None:
    """Reach geometry/endpoints from the event's 'swot' block, if any."""
    swot = (event or {}).get("swot") or {}
    for key in ("reaches", "reach_geometry", "geometry", "geometries", "endpoints"):
        block = swot.get(key)
        if isinstance(block, dict) and reach_id in block:
            geom = _as_geometry(block[reach_id])
            if geom is not None:
                return geom
    return None


def build_features(
    records: list[dict], event: dict | None = None, reaches: dict | None = None
) -> list[dict]:
    """One GeoJSON feature per observation (geometry may be null)."""
    feats = []
    for rec in records:
        reach_id = rec.get("reach_id")
        rid = str(reach_id) if reach_id is not None else None
        geom = (
            _record_geometry(rec)
            or (rid and _event_geometry(event, rid))
            or (rid and _as_geometry((reaches or {}).get(rid)))
        )
        feats.append({
            "type": "Feature",
            "geometry": geom,
            "properties": {
                "reach_id": rid,
                "t_utc": _iso_utc(
                    rec.get("time_str") or rec.get("t_utc") or rec.get("time")
                ),
                "river_m": float(rec["river_m"])
                if rec.get("river_m") is not None
                else None,
                "source": SOURCE,
            },
        })
    return feats


def build_feature_collection(
    records: list[dict], event: dict | None = None, reaches: dict | None = None
) -> dict:
    return {
        "type": "FeatureCollection",
        "features": build_features(records, event, reaches),
    }


def collect_records(
    case_id: str, data_dir: Path | None = None
) -> tuple[list[dict], dict]:
    """All records + merged per-reach geometry map across the series files."""
    records, reaches = [], {}
    for path in series_files(case_id, data_dir):
        recs, rmap = load_records(path)
        records.extend(recs)
        for k, v in rmap.items():
            reaches.setdefault(str(k), v)
    return records, reaches


def content_hash(fc: dict) -> str:
    """sha256 over the canonical FeatureCollection (stable across runs)."""
    blob = json.dumps(fc, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


def staging_path(case_id: str, data_dir: Path | None = None) -> Path:
    return Path(data_dir or DATA_DIR) / case_id / "staging" / f"{ASSET_NAME}.geojson"


def manifest_path(case_id: str, data_dir: Path | None = None) -> Path:
    return Path(data_dir or DATA_DIR) / case_id / "assets.json"


def recorded_hash(case_id: str, data_dir: Path | None = None) -> str | None:
    """Last hash recorded for the asset in data/<case>/assets.json.

    Tolerates {'swot_series': {...}}, {'assets': {...}} and [...] layouts —
    the canonical writer is append_manifest on unit/ingest.
    """
    p = manifest_path(case_id, data_dir)
    try:
        data = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, (list, dict)):
        return None
    entries = data if isinstance(data, list) else data.get("assets", data)
    if not isinstance(entries, (list, dict)):
        return None
    if isinstance(entries, dict):
        e = entries.get(ASSET_NAME)
        return e.get("hash") if isinstance(e, dict) else None
    for e in reversed(entries):
        if isinstance(e, dict) and (
            e.get("name") == ASSET_NAME
            or str(e.get("asset_id", "")).endswith("/" + ASSET_NAME)
        ):
            return e.get("hash")
    return None


def asset_id(case_id: str, project: str | None) -> str:
    return ASSET_ROOT.format(
        project=project or "<GEE_PROJECT>", case=case_id
    ) + f"/{ASSET_NAME}"


def _default_event(case_id: str) -> dict | None:
    """events/<case-dash>-*.json, first match — only for reach geometry."""
    hits = sorted(EVENTS_DIR.glob(f"{case_id.replace('_', '-')}-*.json"))
    return load_event(hits[0].stem) if hits else None


def run(
    case_id: str,
    *,
    event: dict | None = None,
    project: str | None = None,
    bucket: str | None = None,
    dry_run: bool = False,
    force: bool = False,
    data_dir: Path | None = None,
    out_path: Path | None = None,
) -> dict:
    """Build -> (maybe skip) -> stage -> ingest -> manifest. Returns the plan."""
    records, reaches = collect_records(case_id, data_dir)
    if not records:
        raise SystemExit(
            f"no SWOT records under "
            f"{data_dir or DATA_DIR}/{case_id}/series/{case_id}_swot_*.json"
        )
    fc = build_feature_collection(records, event=event, reaches=reaches)
    digest = content_hash(fc)
    stage = Path(out_path or staging_path(case_id, data_dir))
    aid = asset_id(case_id, project or os.environ.get("GEE_PROJECT"))
    skip = recorded_hash(case_id, data_dir) == digest and not force

    plan = {
        "case": case_id,
        "asset_id": aid,
        "kind": "table",
        "features": len(fc["features"]),
        "with_geometry": sum(1 for f in fc["features"] if f["geometry"]),
        "hash": digest,
        "staging": str(stage),
        "series_files": [
            p.name for p in series_files(case_id, data_dir)
        ],
        "skipped": skip,
    }

    if dry_run:
        stage.parent.mkdir(parents=True, exist_ok=True)
        stage.write_text(json.dumps(fc))
        plan["dry_run"] = True
        print(json.dumps(plan, indent=2))
        return plan

    if skip:
        print(
            f"{ASSET_NAME}: assets.json hash unchanged — skipping "
            f"(use --force to re-ingest)"
        )
        return plan

    if stage_to_gcs is None or ingest_asset is None or append_manifest is None:
        raise SystemExit(
            "ingest contract unavailable (pipeline/fetch/ingest.py lands on "
            "unit/ingest) — merge that unit first, or re-run --dry-run"
        )
    if not (project or os.environ.get("GEE_PROJECT")):
        raise SystemExit("GEE_PROJECT unset (env or --project)")
    if not (bucket or os.environ.get("NOW_GCS_BUCKET")):
        raise SystemExit("NOW_GCS_BUCKET unset (env or --bucket)")

    stage.parent.mkdir(parents=True, exist_ok=True)
    stage.write_text(json.dumps(fc))
    gcs_uri = stage_to_gcs(
        stage, bucket=bucket or os.environ["NOW_GCS_BUCKET"],
        case=case_id, name=ASSET_NAME,
    )
    ingest_asset(gcs_uri, asset_id=aid, kind="table", force=force)
    entry = {
        "name": ASSET_NAME,
        "asset_id": aid,
        "kind": "table",
        "gcs_uri": gcs_uri,
        "hash": digest,
        "features": len(fc["features"]),
        "source": str(stage),
        "ingested_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    manifest_file = append_manifest(case_id, entry)
    plan.update({"gcs_uri": gcs_uri, "manifest": str(manifest_file)})
    print(json.dumps(plan, indent=2))
    return plan


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m pipeline.fetch.swot",
        description="SWOT series -> GEE table asset "
        "projects/<GEE_PROJECT>/assets/tipping-point/<case>/swot_series",
    )
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--case", help="case key (rs_2024) or event id (rs-2024-05)")
    src.add_argument("--event", help="event id — events/<event>.json supplies "
                                     "reach geometry")
    ap.add_argument("--dry-run", action="store_true",
                    help="write the staging GeoJSON locally and print the plan; "
                         "no GCS/EE access needed")
    ap.add_argument("--force", action="store_true",
                    help="ingest even when the recorded content hash is unchanged")
    ap.add_argument("--project", default=os.environ.get("GEE_PROJECT"),
                    help="GEE cloud project (env GEE_PROJECT)")
    ap.add_argument("--bucket", default=os.environ.get("NOW_GCS_BUCKET"),
                    help="staging bucket (env NOW_GCS_BUCKET)")
    args = ap.parse_args(argv)

    case_id = case_key(args.event or args.case)
    event = load_event(args.event) if args.event else _default_event(case_id)
    run(
        case_id, event=event, project=args.project, bucket=args.bucket,
        dry_run=args.dry_run, force=args.force,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
