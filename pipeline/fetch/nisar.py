"""Daily NISAR new-pass search + GEE ingest (ruling L8a).

For each case (events/*.json with a "nisar" block, or --case), search
asf_search for NISAR_L2_GCOV BETA granules intersecting the event AOI — plus
any current alert areas given via --alerts-geojson — acquired since the
newest known scene. Known scenes are the granule_ids/dates of
event["nisar"]["frames"][*]["scenes"] and the nisar_gcov_* assets already
listed in data/<case>/assets.json.

New scenes are clipped to the AOI as COGs via tp.nisar's /vsicurl/ machinery
(HHHH backscatter) and recorded in data/<case>/assets.json with status
'local-only' — the planned image assets nisar_gcov_<yyyymmdd> live under
projects/<GEE_PROJECT>/assets/tipping-point/<case>/nisar/. Re-running is
idempotent: an asset_id already in assets.json is never re-downloaded.

Ruling L10: no GCS bucket and no bucket-free raster upload exists, so
there is NO raster-ingest path — --execute downloads+clips locally only.
--ingest is accepted but only warns that raster ingest is not supported
without a GCS bucket (L10).

Usage:
    uv run python -m pipeline.fetch.nisar --case wa_2025 --dry-run
    uv run python -m pipeline.fetch.nisar --since 2026-01-01 --dry-run
    uv run python -m pipeline.fetch.nisar --alerts-geojson data/now/alerts.json --dry-run
    uv run python -m pipeline.fetch.nisar --execute

--dry-run only needs network (asf metadata search is anonymous). --execute
needs Earthdata creds (EARTHDATA_TOKEN, else EDL_USER/EDL_PASS or ~/.netrc).
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from tp import events as events_mod
from tp import nisar as tp_nisar

from .edl import ensure_earthdata, gdal_auth_env

GCOV_COLLECTION = "NISAR_L2_GCOV_BETA_V1"
ASSET_STEM = "nisar_gcov"           # asset ids are nisar_gcov_<yyyymmdd>
GCOV_VARIABLE = "HHHH"              # primary-pol gamma0 for the image asset
ALERT_BUFFER_DEG = 0.25             # half-side of the box around an alert centroid

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_ASSET_RE = re.compile(r"nisar_gcov_\d{8}")


def _iter_events(events_dir: Path | None = None) -> list[dict]:
    """events/*.json that carry a "nisar" block, sorted by filename."""
    d = Path(events_dir) if events_dir else events_mod.EVENTS_DIR
    out = []
    for p in sorted(d.glob("*.json")):
        e = json.loads(p.read_text())
        if isinstance(e.get("nisar"), dict):
            out.append(e)
    return out


def _known_scenes(event: dict) -> tuple[set[str], set[str]]:
    """(granule_ids, iso_dates) the event already catalogues.

    Tolerates the same scene shapes as tp.nisar.download_plan: a "scenes"
    list of {date, role, granule_id} dicts, a "scenes" mapping, or flat
    pre/post dates on the frame.
    """
    ids, dates = set(), set()
    for frame in (event.get("nisar") or {}).get("frames", []):
        scenes = frame.get("scenes")
        items = scenes.values() if isinstance(scenes, dict) else (scenes or [])
        for s in items:
            if isinstance(s, dict):
                if s.get("granule_id"):
                    ids.add(str(s["granule_id"]))
                if s.get("date"):
                    dates.add(str(s["date"])[:10])
            elif isinstance(s, str) and _DATE_RE.match(s):
                dates.add(s[:10])
        for key in ("pre", "post"):
            v = frame.get(key)
            if isinstance(v, str) and _DATE_RE.match(v):
                dates.add(v[:10])
    return ids, dates


def _asset_entries(case: str, data_dir: Path | None = None) -> list:
    """Entries of data/<case>/assets.json ([] when absent/unreadable).

    ingest.py lands on unit/ingest; until then the file may not exist and
    its shape may be a list, {"assets": [...]} or {"<asset_id>": {...}} —
    all are tolerated.
    """
    root = Path(data_dir) if data_dir else events_mod.DATA_DIR
    path = root / case / "assets.json"
    if not path.is_file():
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
        return [
            dict(v, asset_id=v.get("asset_id", k)) if isinstance(v, dict)
            else {"asset_id": k}
            for k, v in data.items()
        ]
    return []


def _strings(value) -> list[str]:
    """Every string inside a manifest entry (values + one nested level)."""
    out = []

    def _walk(v):
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, dict):
            for x in v.values():
                _walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                _walk(x)

    _walk(value)
    return out


def _known_asset_ids(entries: list) -> set[str]:
    """asset_ids already ingested — match 'nisar_gcov_<yyyymmdd>' inside any
    string field (covers both short ids and full projects/... paths)."""
    ids = set()
    for e in entries:
        for s in _strings(e):
            ids.update(_ASSET_RE.findall(s))
        if isinstance(e, dict):
            for key in ("asset_id", "id", "name"):
                if isinstance(e.get(key), str):
                    ids.add(e[key])
    return ids


def _bbox_wkt(bbox) -> str:
    w, s, e, n = bbox
    return f"POLYGON(({w} {s}, {e} {s}, {e} {n}, {w} {n}, {w} {s}))"


def _alert_wkts(path: str | Path, buffer_deg: float = ALERT_BUFFER_DEG) -> list[str]:
    """Extra search AOIs from data/now/alerts.json (or a GeoJSON file).

    alerts.json candidates carry [lon, lat] centroids; each becomes a small
    bbox so a new pass over the alert area is caught even off-centre. A
    GeoJSON FeatureCollection contributes each feature's own geometry.
    """
    data = json.loads(Path(path).read_text())
    wkts = []
    for c in data.get("candidates") or []:
        lon, lat = c["centroid"]
        wkts.append(_bbox_wkt((
            lon - buffer_deg, lat - buffer_deg,
            lon + buffer_deg, lat + buffer_deg,
        )))
    if not wkts and data.get("type") == "FeatureCollection":
        from shapely.geometry import shape

        for f in data.get("features") or []:
            if f.get("geometry"):
                wkts.append(shape(f["geometry"]).wkt)
    return wkts


def _product_props(product) -> dict:
    if isinstance(product, dict):
        return product.get("properties") or product
    return getattr(product, "properties", None) or {}


def _scene_meta(product) -> dict | None:
    """Normalize an ASF result to {granule_id, date, track, look, frame}."""
    props = _product_props(product)
    gid = str(props.get("fileID") or props.get("sceneName") or "")
    meta = tp_nisar._granule_meta(gid)
    iso = meta["date"] if meta else str(props.get("startTime") or "")[:10]
    if not _DATE_RE.match(iso):
        return None
    return {
        "granule_id": gid,
        "date": iso,
        "track": meta["track"] if meta else props.get("pathNumber"),
        "look": meta["look"] if meta else None,
        "frame": meta["frame"] if meta else props.get("frameNumber"),
    }


def _h5_url(product) -> str | None:
    """HTTPS URL of the granule's HDF5, for GDAL /vsicurl/ access."""
    props = _product_props(product)
    cands = [props.get("url")]
    for key in ("additionalUrls", "urls"):
        v = props.get(key) or []
        cands.extend(v if isinstance(v, list) else [v])
    return next(
        (
            u for u in cands
            if isinstance(u, str) and u.startswith("http")
            and u.lower().endswith((".h5", ".hdf5", ".he5"))
        ),
        None,
    )


def _search_granules(wkts: list[str], since: str, collection: str) -> list:
    """asf_search (LAZY — tests inject a fake) per AOI; results merged and
    deduped by granule_id. Metadata search is anonymous: no creds needed."""
    import asf_search

    out, seen = [], set()
    for wkt in wkts:
        # shortName alone pins the CMR collection (verified 2026-11:
        # NISAR_L2_GCOV_BETA_V1 over the wa AOI -> 14 granules; adding
        # dataset=NISAR defeats the filter and returns every NISAR product).
        for g in asf_search.search(
            shortName=collection,
            intersectsWith=wkt,
            start=since,
        ):
            props = _product_props(g)
            gid = str(props.get("fileID") or props.get("sceneName") or id(g))
            if gid not in seen:
                seen.add(gid)
                out.append(g)
    return out


def _collection_for(event: dict) -> str:
    for frame in (event.get("nisar") or {}).get("frames", []):
        coll = (frame.get("collections") or {}).get("BETA")
        if coll:
            return coll
    return GCOV_COLLECTION


def _declared_frames(event: dict) -> set[tuple]:
    """(track, look, frame) tuples the event's nisar block declares —
    preferred when two frames cover the AOI on the same date."""
    out = set()
    for f in (event.get("nisar") or {}).get("frames", []):
        tl = tp_nisar._track_look(f.get("track"))
        frame_txt = str(f.get("frame") or "")
        if tl and frame_txt.isdigit():
            out.add((tl[0], tl[1], int(frame_txt)))
    return out


def build_plan(
    *,
    case: str | None = None,
    since: str | None = None,
    alerts_geojson: str | Path | None = None,
    events_dir: Path | None = None,
    data_dir: Path | None = None,
    gee_project: str | None = None,
) -> dict:
    """Search asf for new GCOV BETA scenes per case and build the JSON plan.

    `since` overrides the 'newest known scene' start date. `gee_project`
    defaults to $GEE_PROJECT; when neither is set the gee_asset strings keep a
    literal <GEE_PROJECT> placeholder (dry-run only — --execute refuses it).
    """
    root = Path(data_dir) if data_dir else events_mod.DATA_DIR
    gee = gee_project or os.environ.get("GEE_PROJECT") or "<GEE_PROJECT>"
    extra_wkts = _alert_wkts(alerts_geojson) if alerts_geojson else []
    plan = {
        "schema": "tipping-point/nisar-update@1",
        "generated_at": datetime.now(UTC).isoformat().replace(
            "+00:00", "Z"),
        "alert_aois": len(extra_wkts),
        "cases": {},
    }
    for event in _iter_events(events_dir):
        ck = events_mod.case_key(event["id"])
        if case and case not in (ck, event["id"]):
            continue
        collection = _collection_for(event)
        known_ids, known_dates = _known_scenes(event)
        known_assets = _known_asset_ids(_asset_entries(ck, data_dir))

        # No known scenes -> 'new passes' means everything since the event
        # window began; window.end would skip passes between the event and
        # its (possibly future-dated) end. Never start in the future.
        window_start = (
            (event.get("window") or {}).get("start")
            or event.get("reference_date")
            or date.today().isoformat()
        )
        eff_since = (
            since
            or (max(known_dates) if known_dates else None)
            or min(window_start, date.today().isoformat())
        )
        aoi_wkt = (event.get("aoi") or {}).get("wkt") or _bbox_wkt(
            event["aoi"]["bbox"])
        wkts = [aoi_wkt] + extra_wkts
        results = _search_granules(wkts, eff_since, collection)
        metas = [(_scene_meta(g), g) for g in results]
        # Declared frames win the one-asset-per-date dedup (stable sort).
        declared = _declared_frames(event)
        metas.sort(key=lambda t: t[0] is None or (
            t[0]["track"], t[0]["look"], t[0]["frame"]) not in declared)

        new_scenes, skipped = [], []
        for meta, g in metas:
            if meta is None:
                skipped.append({"granule_id": None, "reason": "unparsed"})
                continue
            gid = meta["granule_id"]
            asset_id = f"{ASSET_STEM}_{meta['date'].replace('-', '')}"
            if gid in known_ids or meta["date"] in known_dates:
                skipped.append({"granule_id": gid, "reason": "known-scene"})
            elif asset_id in known_assets:
                skipped.append({"granule_id": gid, "reason": "asset-exists"})
            elif any(s["asset_id"] == asset_id for s in new_scenes):
                skipped.append({"granule_id": gid, "reason": "duplicate-date"})
            else:
                ymd = meta["date"].replace("-", "")
                cog = root / "raw" / "nisar" / f"{ck}_{ASSET_STEM}_{ymd}.tif"
                new_scenes.append({
                    **meta,
                    "asset_id": asset_id,
                    "gee_asset": (
                        f"projects/{gee}/assets/tipping-point/{ck}/nisar/"
                        f"{asset_id}"
                    ),
                    "variable": GCOV_VARIABLE,
                    "h5_path": tp_nisar.HDF5_PATHS["GCOV"][GCOV_VARIABLE],
                    "h5_url": _h5_url(g),
                    "cog": str(cog),
                })
        new_scenes.sort(key=lambda s: s["date"])
        plan["cases"][ck] = {
            "event_id": event["id"],
            "collection": collection,
            "since": eff_since,
            "bbox": list(event["aoi"]["bbox"]),
            "search_aois": (
                [{"kind": "event", "wkt": aoi_wkt}]
                + [{"kind": "alert", "wkt": w} for w in extra_wkts]
            ),
            "known_dates": sorted(known_dates),
            "found": len(results),
            "new_scenes": new_scenes,
            "skipped": skipped,
        }
    return plan


def _load_ingest():
    """The unit/ingest contract module, imported lazily + guarded.

    importlib at call time — `from fetch import ingest` would reuse the
    cached package attribute once the real submodule was imported anywhere,
    silently bypassing a sys.modules fake in tests.
    """
    try:
        ingest = importlib.import_module(f"{__package__}.ingest")
    except ImportError:
        try:
            import ingest  # script-context fallback (sys.path=pipeline/fetch)
        except ImportError as e:
            raise RuntimeError(
                "pipeline/fetch/ingest.py (unit/ingest) is not available — "
                "append_manifest is required for "
                "--execute; --dry-run needs only the asf metadata search."
            ) from e
    missing = [
        n for n in ("append_manifest",)
        if not hasattr(ingest, n)
    ]
    if missing:
        raise RuntimeError(f"fetch.ingest lacks {missing} — contract mismatch")
    return ingest


def execute_plan(
    plan: dict,
    *,
    data_dir: Path | None = None,
) -> list[dict]:
    """Download/clip each planned scene; record it in assets.json.

    Ruling L10 — no GCS bucket and no bucket-free raster upload exists, so
    the COG stays local and the manifest entry records the planned asset
    id with status 'local-only'. Re-reads data/<case>/assets.json per case
    before writing so a second run over the same plan is a no-op. Needs
    Earthdata creds.
    """
    mod = _load_ingest()
    proj = os.environ.get("GEE_PROJECT")
    creds = ensure_earthdata()
    gdal_env = gdal_auth_env(creds)

    results = []
    for ck, info in plan["cases"].items():
        known_assets = _known_asset_ids(_asset_entries(ck, data_dir))
        bbox = tuple(info["bbox"])
        for scene in info["new_scenes"]:
            if scene["asset_id"] in known_assets:
                continue
            if not scene.get("h5_url"):
                raise RuntimeError(
                    f"granule {scene['granule_id']} exposes no .h5 data link")
            cog = Path(scene["cog"])
            tp_nisar._subset_h5_variable(
                scene["h5_url"], scene["h5_path"], bbox, cog, gdal_env)
            gee_asset = scene["gee_asset"].replace(
                "<GEE_PROJECT>", proj or "<GEE_PROJECT>")
            entry = {
                "asset_id": scene["asset_id"],
                "gee_asset": gee_asset,
                "kind": "image",
                "granule_id": scene["granule_id"],
                "date": scene["date"],
                "source": "pipeline.fetch.nisar",
                "local_path": str(cog),
                "status": "local-only",
            }
            manifest = mod.append_manifest(ck, entry)
            known_assets.add(scene["asset_id"])
            results.append({
                "case": ck,
                "asset_id": scene["asset_id"],
                "gee_asset": gee_asset,
                "local_path": str(cog),
                "status": "local-only",
                "manifest": str(manifest),
            })
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m pipeline.fetch.nisar", description=__doc__)
    ap.add_argument(
        "--case",
        help="case key (wa_2025) or event id (wa-2025-12); "
             "default: every events/*.json with a nisar block")
    ap.add_argument(
        "--since",
        help="ISO date override for the 'newest known scene' search start")
    ap.add_argument(
        "--alerts-geojson", metavar="PATH",
        help="data/now/alerts.json (or GeoJSON) — add alert-area AOIs")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run", action="store_true",
        help="search only, print the JSON plan (default; no creds needed)")
    mode.add_argument(
        "--execute", action="store_true",
        help="download + clip COGs locally and record 'local-only' "
             "assets (EARTHDATA_TOKEN or EDL_USER/EDL_PASS)")
    ap.add_argument(
        "--ingest", action="store_true",
        help="deprecated — raster ingest is not supported without a GCS "
             "bucket (L10); accepted for compatibility and only warns")
    args = ap.parse_args(argv)

    try:
        if args.ingest:
            print("warning: --ingest is not supported without a GCS "
                  "bucket (L10) — scenes stay local-only", file=sys.stderr)
        plan = build_plan(case=args.case, since=args.since,
                          alerts_geojson=args.alerts_geojson)
        if args.execute:
            plan["executed"] = execute_plan(plan)
    except RuntimeError as e:
        ap.error(str(e))
    print(json.dumps(plan, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
