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


# --- Now-domain production GCOV (unit R2, L27 response-first) --------------
# Production GCOV has 4-6 d latency over the Now domain; the BETA collection
# has nothing there. Search is plain anonymous CMR JSON (no asf_search), so
# the response job needs no creds until it reads HDF5 bytes.

PROD_GCOV_COLLECTION = "NISAR_L2_GCOV_V1"
CMR_GRANULES_URL = "https://cmr.earthdata.nasa.gov/search/granules.umm_json"
PAIR_MAX_GAP_DAYS = 36            # 3 x 12-day repeat cycles
MIN_DOMAIN_OVERLAP_KM2 = 200.0    # a frame clipping the domain edge is not a pass

# NISAR_L2_PR_GCOV_<cycle>_<track>_<A|D>_<frame>_<mode>_<pols>_<A|M>_<start>_
#   <end>_<crid>_<N>_<F|P>_<J>_<ver>   (F = full frame, P = partial frame)
PROD_GCOV_RE = re.compile(
    r"NISAR_L2_\w+?_GCOV_(?P<cycle>\d+)_(?P<track>\d+)_(?P<look>[AD])_"
    r"(?P<frame>\d+)_(?P<mode>\d+)_(?P<pols>[A-Z]+)_[A-Z]_"
    r"(?P<start>\d{8}T\d{6})_(?P<end>\d{8}T\d{6})_\w+?_[A-Z]_"
    r"(?P<coverage>[FP])_"
)


def _utc(text: str) -> datetime:
    """'20261106T211603' or ISO-8601 (Z or offset) -> aware UTC datetime."""
    text = str(text)
    if re.fullmatch(r"\d{8}T\d{6}", text):
        return datetime.strptime(text, "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
    return datetime.fromisoformat(text).astimezone(UTC)


def _footprint(umm: dict):
    """Shapely footprint from UMM GPolygons (None when absent)."""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union

    geom = ((umm.get("SpatialExtent") or {}).get("HorizontalSpatialDomain") or {}).get(
        "Geometry") or {}
    polys = []
    for gp in geom.get("GPolygons") or []:
        pts = ((gp.get("Boundary") or {}).get("Points")) or []
        if len(pts) >= 3:
            polys.append(Polygon([(p["Longitude"], p["Latitude"]) for p in pts]))
    return unary_union(polys) if polys else None


def _approx_km2(geom) -> float:
    """Area of a lon/lat geometry in km^2 (local cos(lat) scaling)."""
    if geom is None or geom.is_empty:
        return 0.0
    import math

    lat = geom.centroid.y
    return geom.area * 111.32 * 111.32 * math.cos(math.radians(lat))


def cmr_gcov_granule(item: dict) -> dict | None:
    """Normalize one CMR umm_json item to the dict the response job uses.

    None when the GranuleUR does not parse as a production GCOV name.
    """
    umm = item.get("umm") or {}
    meta = item.get("meta") or {}
    gid = str(umm.get("GranuleUR") or meta.get("native-id") or "")
    m = PROD_GCOV_RE.search(gid)
    if not m:
        return None
    d = m.groupdict()
    h5 = next(
        (
            u.get("URL") for u in umm.get("RelatedUrls") or []
            if u.get("Type") == "GET DATA" and str(u.get("URL", "")).startswith("https")
            and str(u.get("URL")).lower().endswith(".h5")
        ),
        None,
    )
    pdates = {p.get("Type"): p.get("Date") for p in umm.get("ProviderDates") or []}
    available = pdates.get("Insert") or pdates.get("Create") or meta.get("revision-date")
    return {
        "granule_id": gid,
        "cycle": int(d["cycle"]),
        "track": int(d["track"]),
        "look": d["look"],
        "frame": int(d["frame"]),
        "mode": d["mode"],
        "pols": d["pols"],
        "coverage": d["coverage"],
        "start": _utc(d["start"]),
        "end": _utc(d["end"]),
        "available": _utc(available) if available else None,
        "h5_url": h5,
        "footprint": _footprint(umm),
    }


def search_gcov_domain(
    bbox,
    start: datetime | str,
    end: datetime | str | None = None,
    *,
    collection: str = PROD_GCOV_COLLECTION,
    get=None,
    page_size: int = 500,
) -> list[dict]:
    """Every production GCOV granule intersecting `bbox` acquired in [start, end].

    Anonymous CMR umm_json search, paged with CMR-Search-After. `get` is a
    requests.get-compatible callable (tests inject a fake).
    """
    if get is None:
        import requests

        get = requests.get
    w, s, e, n = bbox
    t0 = start if isinstance(start, str) else start.strftime("%Y-%m-%dT%H:%M:%SZ")
    t1 = "" if end is None else (end if isinstance(end, str) else end.strftime("%Y-%m-%dT%H:%M:%SZ"))
    params = {
        "short_name": collection,
        "bounding_box": f"{w},{s},{e},{n}",
        "temporal": f"{t0},{t1}",
        "page_size": page_size,
        "sort_key": "-start_date",
    }
    out, headers = [], {}
    while True:
        r = get(CMR_GRANULES_URL, params=params, headers=headers, timeout=60)
        r.raise_for_status()
        items = (r.json() or {}).get("items") or []
        out.extend(g for g in map(cmr_gcov_granule, items) if g is not None)
        after = r.headers.get("CMR-Search-After")
        if not items or not after or len(items) < page_size:
            break
        headers = {"CMR-Search-After": after}
    return out


def _domain_geom(bbox):
    from shapely.geometry import box

    return box(*bbox)


def _overlap_km2(g: dict, domain) -> float:
    fp = g.get("footprint")
    if fp is None or domain is None:
        return 0.0
    return _approx_km2(fp.intersection(domain))


def _best_per_frame(granules: list[dict], domain) -> dict[tuple, dict]:
    """One granule per (track, look, frame, acquisition date).

    Several products can exist for one frame/date (partial vs full frame,
    20 vs 40 MHz mode): keep the full frame, then the larger domain overlap.
    """
    best: dict[tuple, dict] = {}
    for g in granules:
        key = (g["track"], g["look"], g["frame"], g["start"].date())
        score = (g["coverage"] == "F", _overlap_km2(g, domain), g["granule_id"])
        if key not in best or score > best[key][0]:
            best[key] = (score, g)
    return {k: v[1] for k, v in best.items()}


def pair_latest_pass(
    granules: list[dict],
    bbox=None,
    *,
    max_gap_days: float = PAIR_MAX_GAP_DAYS,
    min_overlap_km2: float = MIN_DOMAIN_OVERLAP_KM2,
) -> dict:
    """Pair the newest pass over the domain with its previous pass.

    A pass is (cycle, track, look). Passes are tried newest first; for each
    frame of the pass overlapping the domain by >= min_overlap_km2 the pre is
    the most recent earlier granule on the SAME track + look + frame no more
    than `max_gap_days` before it — granules matching the post's acquisition
    mode (tp.response_nisar.mode_token) are preferred when a frame/date has
    several products. The first pass with >= 1 pair wins; newer
    passes that could not be paired are reported in "skipped".

    Returns {"pass": {...} | None, "pairs": [{"pre", "post"}], "skipped": [...]}.
    """
    from tp.response_nisar import mode_token

    domain = _domain_geom(bbox) if bbox is not None else None
    chosen = list(_best_per_frame(granules, domain).values())
    passes: dict[tuple, list[dict]] = {}
    for g in chosen:
        passes.setdefault((g["cycle"], g["track"], g["look"]), []).append(g)
    order = sorted(passes, key=lambda k: max(g["start"] for g in passes[k]), reverse=True)

    skipped = []
    for key in order:
        frames = sorted(passes[key], key=lambda g: g["frame"])
        pairs, reasons = [], []
        for post in frames:
            label = f"{post['track']:03d}{post['look']}_{post['frame']:03d}"
            if domain is not None and _overlap_km2(post, domain) < min_overlap_km2:
                reasons.append(f"{label}: domain overlap < {min_overlap_km2:g} km2")
                continue
            if not post.get("h5_url"):
                reasons.append(f"{label}: no .h5 data link")
                continue
            earlier = [
                g for g in granules
                if (g["track"], g["look"], g["frame"]) == (post["track"], post["look"], post["frame"])
                and g["start"] < post["start"]
                and (post["start"] - g["start"]).total_seconds() <= max_gap_days * 86400
                and g.get("h5_url")
            ]
            if not earlier:
                reasons.append(f"{label}: no earlier pass on the same track/frame within {max_gap_days:g} d")
                continue
            post_mode = mode_token(post["granule_id"])
            same_mode = [g for g in earlier
                         if post_mode and mode_token(g["granule_id"]) == post_mode]
            pool = same_mode or earlier          # prefer the same acquisition mode
            pairs.append({"pre": max(pool, key=lambda g: (
                g["start"], g["coverage"] == "F", _overlap_km2(g, domain),
                g["granule_id"])), "post": post})
        info = {
            "cycle": key[0], "track": key[1], "look": key[2],
            "acquired": max(g["start"] for g in frames),
            "frames": [g["frame"] for g in frames],
        }
        if pairs:
            return {"pass": info, "pairs": pairs, "skipped": skipped}
        skipped.append({**info, "reasons": reasons})
    return {"pass": None, "pairs": [], "skipped": skipped}


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
