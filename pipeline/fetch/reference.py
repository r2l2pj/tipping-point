"""Ingest reference-only GeoJSON products as GEE table assets.

REFERENCE-ONLY layers (map footer credits, never TPI inputs):

    data/<case>/geojson/municipal_boundaries.geojson -> .../municipal_boundaries
    data/<case>/geojson/landslide_catalog.geojson    -> .../landslide_catalog

under projects/<GEE_PROJECT>/assets/tipping-point/<case>/.

The GeoJSON on disk is ingested via fetch.ingest.ingest_table (client-side
ee.FeatureCollection + Export.table.toAsset — no GCS bucket, ruling L10).
This module is import-safe without it so --dry-run works in checkouts
where that unit hasn't landed. Landslide catalog points gain a `t_utc`
property (ISO UTC) only when the source record already carries a date —
rs inventory points mostly have none, and we don't invent one. All other
properties (source / license / url / doi attribution) pass through
untouched. Idempotent: a product already listed in
data/<case>/assets.json is skipped unless --force.

    uv run python -m pipeline.fetch.reference --kind glc --case wa_2025
    uv run python -m pipeline.fetch.reference --kind all --all --dry-run

Env: GEE_PROJECT (default 'hermes-fesolucoes'), plus whatever credentials
fetch.ingest itself needs (GOOGLE_APPLICATION_CREDENTIALS / GCP_SA_KEY).
No bucket env is needed.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
from datetime import UTC, date, datetime
from pathlib import Path

try:
    from tp.events import DATA_DIR, case_key, list_events, load_event
except ImportError:  # `python -m pipeline.fetch.reference` without .pth
    from pipeline.tp.events import DATA_DIR, case_key, list_events, load_event

try:  # package context (`fetch.reference` / `pipeline.fetch.reference`)
    from .ingest import append_manifest, ingest_table
except ImportError:
    try:  # plain-script / PYTHONPATH context
        from fetch.ingest import append_manifest, ingest_table
    except ImportError:  # unit/ingest not landed yet — --dry-run still works
        append_manifest = ingest_table = None

DEFAULT_GEE_PROJECT = "hermes-fesolucoes"
MANIFEST_NAME = "assets.json"
STAGING_DIRNAME = "staging"

KINDS = {
    "boundaries": {"filename": "municipal_boundaries.geojson",
                   "asset": "municipal_boundaries"},
    "glc": {"filename": "landslide_catalog.geojson",
            "asset": "landslide_catalog"},
    "inventory": {"filename": "landslide_catalog.geojson",
                  "asset": "landslide_catalog"},
}
CATALOG_KINDS = ("glc", "inventory")

# Property names that may carry a record's date, in priority order.
_DATE_KEYS = ("event_date", "date", "datetime", "timestamp")
_ISO = "%Y-%m-%dT%H:%M:%SZ"


def _t_utc(props: dict) -> str | None:
    """ISO-UTC t_utc from an existing date prop, or None — never invented."""
    raw = props.get("t_utc")
    if raw in (None, ""):
        raw = next(
            (props[k] for k in _DATE_KEYS if props.get(k) not in (None, "")),
            None)
    if raw in (None, ""):
        return None
    if isinstance(raw, (int, float)):  # epoch ms (raw ArcGIS exports)
        ts = raw / 1000 if raw > 1e12 else raw
        return datetime.fromtimestamp(ts, tz=UTC).strftime(_ISO)
    try:
        d = datetime.fromisoformat(str(raw).strip())
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=UTC)
    return d.astimezone(UTC).strftime(_ISO)


def prepare_geojson(src: Path, kind: str, staging_dir: Path) -> Path:
    """Return the file to stage, adding t_utc to catalog points when derivable.

    Returns src itself when nothing is added — municipal boundaries and
    date-less catalogs are staged byte-identical.
    """
    if kind not in CATALOG_KINDS:
        return src
    fc = json.loads(src.read_text())
    changed = False
    for feature in fc.get("features") or []:
        props = feature.setdefault("properties", {})
        t = _t_utc(props)
        if t is not None and props.get("t_utc") != t:
            props["t_utc"] = t
            changed = True
    if not changed:
        return src
    staging_dir.mkdir(parents=True, exist_ok=True)
    out = staging_dir / src.name
    out.write_text(json.dumps(fc) + "\n")
    return out


def _manifest_entries(case: str) -> list[dict]:
    """Entries of data/<case>/assets.json — tolerant of list or dict shapes."""
    path = DATA_DIR / case / MANIFEST_NAME
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    if isinstance(data, list):
        return [e for e in data if isinstance(e, dict)]
    if isinstance(data, dict):
        if isinstance(data.get("assets"), list):
            return [e for e in data["assets"] if isinstance(e, dict)]
        return [{"name": k, **v} if isinstance(v, dict) else {"name": k}
                for k, v in data.items()]
    return []


def _manifest_has(case: str, name: str) -> bool:
    return any(e.get("name") == name for e in _manifest_entries(case))


def _fetcher(kind: str):
    """The fetch step for a kind — lazy import, only needed for --refresh."""
    mod_name, func_name = {
        "boundaries": ("fetch.boundaries", "municipal_boundaries"),
        "glc": ("fetch.glc", "glc_catalog"),
        "inventory": ("fetch.inventory", "inventory_catalog"),
    }[kind]
    try:
        mod = importlib.import_module(mod_name)
    except ImportError:
        mod = importlib.import_module(f"pipeline.{mod_name}")
    return getattr(mod, func_name)


def _events_for_case(case: str) -> list[dict]:
    return [load_event(e) for e in list_events() if case_key(e) == case]


def _window_arg(arg: str) -> dict:
    """--window type: 'YYYY-MM-DD,YYYY-MM-DD' -> {start, end} dict."""
    try:
        start, end = (s.strip() for s in arg.split(","))
        out = {"start": date.fromisoformat(start).isoformat(),
               "end": date.fromisoformat(end).isoformat()}
    except ValueError as e:
        raise argparse.ArgumentTypeError(
            f"expected START,END ISO dates, got {arg!r}") from e
    if out["start"] > out["end"]:
        raise argparse.ArgumentTypeError(
            f"start {out['start']} is after end {out['end']}")
    return out


def _kinds_for(case: str, kind: str) -> list[str]:
    """Explicit kind, or 'all' -> boundaries + the case's catalog source.

    wa-* events use the GLC catalog; every other case uses the inventory
    (same split as pipeline/scripts/12_aux_products.py).
    """
    if kind != "all":
        return [kind]
    catalog = "inventory"
    for event_id in list_events():
        if case_key(event_id) == case:
            catalog = "glc" if event_id.startswith("wa-") else "inventory"
            break
    return ["boundaries", catalog]


def ingest_product(case: str, kind: str, *, project: str,
                   dry_run: bool, force: bool, refresh: bool,
                   window: dict | None) -> dict:
    """Ingest one reference product for a case; return a status dict."""
    spec = KINDS[kind]
    name = spec["asset"]
    src = DATA_DIR / case / "geojson" / spec["filename"]
    asset_id = f"projects/{project}/assets/tipping-point/{case}/{name}"
    result = {"case": case, "kind": kind, "asset": name, "asset_id": asset_id}

    if refresh:
        events = _events_for_case(case)
        if not events:
            raise RuntimeError(f"--refresh: no events map to case {case!r}")
        if dry_run:
            result["detail"] = "would re-fetch " + ", ".join(
                e["id"] for e in events)
        else:
            fetch = _fetcher(kind)
            for event in events:
                # without --window, glc_catalog falls back to the
                # event['glc_window'] filter configured on the event itself
                if kind == "glc" and window:
                    fetch(event, window=window)
                else:
                    fetch(event)

    if not src.exists():
        return {**result, "status": "missing", "detail": str(src)}
    result["src"] = str(src)
    if not force and _manifest_has(case, name):
        return {**result, "status": "skipped",
                "detail": f"{name} already in data/{case}/{MANIFEST_NAME}"}
    if dry_run:
        return {**result, "status": "dry-run"}

    if ingest_table is None or append_manifest is None:
        raise RuntimeError(
            "fetch.ingest is not importable (it lands on unit/ingest) — "
            "only --dry-run works in this checkout")

    staged = prepare_geojson(src, kind, DATA_DIR / case / STAGING_DIRNAME)
    ingest = ingest_table(staged, asset_id=asset_id, force=force) or {}
    # fetch.ingest returns the manifest entry; these defaults only fill gaps
    entry = {"name": name, "gee_id": asset_id, "kind": "table",
             "status": "ingested",
             "ingested_at": datetime.now(UTC).strftime(_ISO), **ingest}
    append_manifest(case, entry)
    return {**result, "status": entry.get("status", "ingested"),
            "entry": entry}


def _all_cases() -> list[str]:
    return sorted({case_key(e) for e in list_events()})


def run(argv: list[str] | None = None) -> list[dict]:
    """CLI entry — returns one status dict per (case, product)."""
    args = _parser().parse_args(argv)
    project = os.environ.get("GEE_PROJECT", DEFAULT_GEE_PROJECT)
    cases = (sorted({case_key(c) for c in args.case})
             if args.case else _all_cases())
    results = []
    for case in cases:
        for kind in _kinds_for(case, args.kind):
            results.append(ingest_product(
                case, kind, project=project,
                dry_run=args.dry_run, force=args.force, refresh=args.refresh,
                window=args.window))
    return results


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pipeline.fetch.reference", description=__doc__.splitlines()[0])
    p.add_argument("--kind", required=True, choices=[*KINDS, "all"],
                   help="reference product to ingest ('all' = boundaries + "
                        "the case's landslide catalog)")
    sel = p.add_mutually_exclusive_group(required=True)
    sel.add_argument("--case", action="append", metavar="CASE",
                     help="config.yaml case key or event id (repeatable)")
    sel.add_argument("--all", action="store_true",
                     help="every case in events/")
    p.add_argument("--refresh", action="store_true",
                   help="re-run the matching fetch step (boundaries/glc/"
                        "inventory) before ingesting")
    p.add_argument("--dry-run", action="store_true",
                   help="print the plan only — no fetch, staging, ingest "
                        "or manifest writes")
    p.add_argument("--force", action="store_true",
                   help="re-ingest even when assets.json lists the asset")
    p.add_argument("--window", type=_window_arg, metavar="START,END",
                   help="with --refresh --kind glc: filter the GLC query to "
                        "these ISO dates (default: all-time catalog, or "
                        "event['glc_window'] when the event sets one)")
    return p


def main() -> None:
    for r in run():
        line = f"{r['case']}/{r['asset']}: {r['status']}"
        if r["status"] in ("ingested", "ingesting", "dry-run"):
            line += f" -> {r['asset_id']}"
        if r.get("detail"):
            line += f" ({r['detail']})"
        print(line)


if __name__ == "__main__":
    main()
