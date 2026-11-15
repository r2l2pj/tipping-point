"""Root manifest builder — data/manifest.json (contract: data/SCHEMA.md).

Merges the per-case nodes (rs_2024, wa_2025) with data/now/manifest.json
copied verbatim. Written LAST of every data/ file, atomically (tmp +
os.replace), so a reader keying on a non-null updated_at never sees a
half-written tree.

Data directories are data/<case-id>/ (rs_2024, wa_2025); the node's
`event_id` keeps the hyphen id only as a field. `metrics` points into the
single root data/metrics.json as "metrics.json#<case>".

Layer discovery: the case publisher (tools/publish_case.py / tp/bundle.py)
drops data/<case>/png/_index.json = {"layers": [{id, date, png, cog,
bounds, forecast}]} — consumed verbatim (png/bounds may be null), legend
injected. Without the index the expected tpi_* layers are still listed with
bounds null. COG-only score layers (r_series, s_series, t_slide, t_flood —
geotiff.js range reads) are appended when their cog/<caso>_<id>_*.tif file
exists; a missing one is recorded under the node's _null_reasons.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tp.config import case as config_case
from tp.events import DATA_DIR, load_event

EVENT_TO_CASE = {"rs-2024-05": "rs_2024", "wa-2025-12": "wa_2025"}

# pt-BR class names pinned by contract (live/config.py LABELS); order fixed.
LEGEND = ["Baixo", "Moderado", "Alto", "Crítico"]

PRODUCTS = [
    "municipal_boundaries",
    "landslide_catalog",
    "places",
    "priority_zones",
    "permanent_water",
    "flood_confirmed",
    "landslide_candidates",
]

# Layers a replay case is expected to publish even before _index.json exists.
EXPECTED_LAYERS = [
    "tpi_slide",
    "tpi_slide_d1",
    "tpi_slide_d2",
    "tpi_slide_d3",
    "tpi_flood",
    "tpi_flood_d1",
    "tpi_flood_d2",
    "tpi_flood_d3",
]

# COG-only score layers for geotiff.js range reads (png: null, EPSG:4326).
COG_ONLY_LAYERS = ["r_series", "s_series", "t_slide", "t_flood"]

_FORECAST_RE = re.compile(r"_d([1-3])$")
_YMD_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")


def _now_z() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ymd(iso_date: str) -> str:
    return iso_date.replace("-", "")


def _iso_from_ymd(stamp: str) -> str | None:
    m = _YMD_RE.match(stamp)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def _reference_date(event_id: str):
    try:
        raw = load_event(event_id).get("reference_date")
        return datetime.fromisoformat(raw).date() if raw else None
    except (OSError, ValueError, TypeError):
        return None


def _png_index(case_dir: Path) -> list:
    """data/<case>/png/_index.json -> [{id,date,png,cog,bounds,forecast}] —
    one entry per rendered frame; repeated ids are the per-day series."""
    path = case_dir / "png" / "_index.json"
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return []
    return [e for e in raw.get("layers", [])
            if isinstance(e, dict) and e.get("id")]


def _derived_date(layer_id: str, ref_date) -> str | None:
    m = _FORECAST_RE.search(layer_id)
    if m and ref_date:
        return (ref_date + timedelta(days=int(m.group(1)))).isoformat()
    return ref_date.isoformat() if ref_date else None


def _cog_path(case_id: str, layer_id: str, date: str | None, ref_date) -> str:
    m = _FORECAST_RE.search(layer_id)
    if m and layer_id.startswith("tpi_") and ref_date:
        stem = f"{case_id}_tpi_forecast_d{m.group(1)}_{_ymd(ref_date.isoformat())}"
    elif date:
        stem = f"{case_id}_{layer_id}_{_ymd(date)}"
    else:
        stem = f"{case_id}_{layer_id}"
    return f"{case_id}/cog/{stem}.tif"


def _layer_entry(case_id: str, layer_id: str, ref_date, index_entry: dict | None) -> dict:
    """{id,date,png,cog,legend,bounds,forecast?} — index keys verbatim when given."""
    index_entry = index_entry or {}
    m = _FORECAST_RE.search(layer_id)
    date = index_entry["date"] if "date" in index_entry else _derived_date(layer_id, ref_date)
    if "png" in index_entry:
        png = index_entry["png"]
    else:
        png = f"{case_id}/png/{layer_id}_{_ymd(date)}.png" if date else f"{case_id}/png/{layer_id}.png"
    cog = index_entry["cog"] if "cog" in index_entry else _cog_path(case_id, layer_id, date, ref_date)
    entry = {
        "id": layer_id,
        "date": date,
        "png": png,
        "cog": cog,
        "legend": list(LEGEND),
        "bounds": index_entry.get("bounds"),
    }
    forecast = index_entry.get("forecast", True if m else None)
    if forecast is not None:
        entry["forecast"] = forecast
    for extra in ("issued", "forecast_source", "kind",
                  "critical_km2", "people_critical", "_null_reasons"):
        if extra in index_entry:
            entry[extra] = index_entry[extra]
    if entry.get("forecast") and "forecast_source" not in entry:
        # L22: the copied-observation frames are the labelled upper bound,
        # not a real issued forecast (NOAA GFS frames carry their own tag).
        entry["forecast_source"] = "perfect-forecast"
        entry["upper_bound"] = True
    return entry


def _cog_only_entry(case_id: str, case_dir: Path, layer_id: str, ref_date) -> dict | None:
    """Minimal {id,date,png:null,cog} entry (bounds/legend unused per SCHEMA)."""
    cogs = sorted((case_dir / "cog").glob(f"{case_id}_{layer_id}_*.tif"))
    if not cogs:
        return None
    cog = cogs[-1]
    stamp = cog.stem.removeprefix(f"{case_id}_{layer_id}_")
    date = _iso_from_ymd(stamp) or (ref_date.isoformat() if ref_date else None)
    return {
        "id": layer_id,
        "date": date,
        "png": None,
        "cog": f"{case_id}/cog/{cog.name}",
    }


def _layers(case_id: str, case_dir: Path, ref_date) -> tuple[list, dict]:
    """(layers, null_reasons) — every rendered frame (per-day series and
    forecast d1..d3) from _index.json, placeholders for expected layers the
    publisher has not produced yet, plus cog-only score layers."""
    index = _png_index(case_dir)
    index_ids = {e["id"] for e in index}
    layers, missing, seen = [], {}, set()
    # every rendered frame: repeated ids = per-day series (SCHEMA)
    for e in sorted(index, key=lambda x: (x["id"], x.get("date") or "")):
        seen.add(e["id"])
        layers.append(_layer_entry(case_id, e["id"], ref_date, e))
    # expected-but-unproduced layers keep a placeholder (pruned later if
    # their derived png/cog paths do not exist either)
    for lid in EXPECTED_LAYERS:
        if lid not in index_ids:
            layers.append(_layer_entry(case_id, lid, ref_date, None))
    for lid in COG_ONLY_LAYERS:
        seen.add(lid)
        entry = _cog_only_entry(case_id, case_dir, lid, ref_date)
        if entry:
            layers.append(entry)
        elif lid not in index_ids:
            missing[lid] = f"{case_id}_{lid} COG not yet produced"
    return layers, missing


def _missing_reason(event_id: str, product: str) -> str:
    if product == "flood_confirmed":
        try:
            nisar = load_event(event_id).get("nisar") or {}
        except (OSError, ValueError):
            nisar = {}
        if not nisar.get("frames"):
            note = (nisar.get("note") or "").split(".")[0].strip()
            return note or "no NISAR coverage — event predates launch (Jul 2025)"
        return "no validated NISAR flood mask yet"
    if product == "landslide_candidates":
        return "no landslide candidate product yet"
    return "not yet produced"


def _products(event_id: str, case_id: str, case_dir: Path) -> tuple[dict, dict]:
    products, reasons = {}, {}
    for name in PRODUCTS:
        if (case_dir / "geojson" / f"{name}.geojson").exists():
            products[name] = f"{case_id}/geojson/{name}.geojson"
        else:
            products[name] = None
            reasons[name] = _missing_reason(event_id, name)
    swot = case_dir / "series" / "swot_reference.json"
    products["swot_reference"] = (
        f"{case_id}/series/swot_reference.json" if swot.exists() else None)
    if not swot.exists():
        reasons["swot_reference"] = "no SWOT pass in the case window"
    series = case_dir / "series" / "swot.json"
    products["swot_series"] = (
        f"{case_id}/series/swot.json" if series.exists() else None)
    if not series.exists():
        reasons["swot_series"] = "no SWOT reach series for this case"
    return products, reasons


def _data_dates(event_id: str, case_dir: Path) -> dict:
    """Freshest date of each input actually used (best effort)."""
    dates: dict[str, str] = {}
    series = case_dir / "series" / "imerg_smap_daily.json"
    if series.exists():
        try:
            days = json.loads(series.read_text()).get("days", [])
        except (OSError, ValueError):
            days = []
        for key, field in (("imerg", "imerg_mm"), ("smap", "smap"), ("swot", "river_m")):
            vals = [d["date"] for d in days if d.get("date") and d.get(field) is not None]
            if vals:
                dates[key] = max(vals)
    try:
        frames = (load_event(event_id).get("nisar") or {}).get("frames") or []
    except (OSError, ValueError):
        frames = []
    for frame in frames:
        for key, fkey in (("nisar_pre", "pre"), ("nisar_post", "post")):
            if frame.get(fkey):
                dates[key] = max(dates.get(key, ""), frame[fkey])
    return dates


def build_case_node(event_id: str, data_root: Path | str = DATA_DIR) -> dict:
    """The manifest case node for one replay event (SCHEMA 'per-case node')."""
    data_root = Path(data_root)
    case_id = EVENT_TO_CASE.get(event_id, "_".join(event_id.split("-")[:2]))
    case_dir = data_root / case_id
    ref_date = _reference_date(event_id)

    layers, reasons = _layers(case_id, case_dir, ref_date)
    metrics = f"metrics.json#{case_id}" if (data_root / "metrics.json").exists() else None
    products, prod_reasons = _products(event_id, case_id, case_dir)
    reasons.update(prod_reasons)
    if metrics is None:
        reasons["metrics"] = "metrics.json not yet written"

    return {
        "event_id": event_id,
        "mode": "replay",
        "bounds": config_case(case_id)["aoi"],
        "forecast_source": "perfect-forecast",
        "data_basis": ("hindcast — IMERG_V07 + SMAP SPL4SMGP as archived in GEE "
                       "(no real-time latency modelled); not as-available"),
        "layers": layers,
        "metrics": metrics,
        "data_dates": _data_dates(event_id, case_dir),
        "products": products,
        "_null_reasons": reasons,
    }


def build_root(data_root: Path | str = DATA_DIR) -> dict:
    """The root manifest dict; `now` node = data/now/manifest.json verbatim."""
    data_root = Path(data_root)
    now_path = data_root / "now" / "manifest.json"
    now_node = json.loads(now_path.read_text()) if now_path.exists() else None
    cases = {
        case_key: build_case_node(event_id, data_root)
        for event_id, case_key in EVENT_TO_CASE.items()
    }
    cases["now"] = now_node
    return {"updated_at": _now_z(), "cases": cases}


def _prune_missing(root: dict, data_root: Path) -> list[str]:
    """Drop manifest references to files that were never written.

    Every `png`/`cog`/product path in the manifest must exist locally —
    the app 404s otherwise (wa_2025 forecast frames slipped out stale).
    Returns the dropped paths for the caller to report.
    """
    dropped = []

    def _rel(p):
        return p if isinstance(p, str) and not p.startswith("http") else None

    for key, case in root.get("cases", {}).items():
        if not isinstance(case, dict) or key == "now":
            # `now` is copied verbatim from data/now/manifest.json — the
            # live pipeline owns its paths (they resolve under data/now/).
            continue
        layers = []
        for layer in case.get("layers", []):
            for fld in ("png", "cog"):
                rel = _rel(layer.get(fld))
                if rel and not (data_root / rel).exists():
                    dropped.append(rel)
                    layer[fld] = None
            if layer.get("png") or layer.get("cog"):
                layers.append(layer)
        case["layers"] = layers
        for name, rel in list((case.get("products") or {}).items()):
            if _rel(rel) and not (data_root / rel).exists():
                dropped.append(rel)
                case["products"][name] = None
                case.setdefault("_null_reasons", {})[name] = (
                    f"{rel} not present at publish")
    return dropped


def write_root_manifest(data_root: Path | str = DATA_DIR) -> dict:
    """Build and atomically write data_root/manifest.json — always LAST."""
    data_root = Path(data_root)
    root = build_root(data_root)
    dropped = _prune_missing(root, data_root)
    if dropped:
        print(f"manifest: dropped {len(dropped)} missing refs: "
              + ", ".join(dropped[:8]), file=sys.stderr)
    path = data_root / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(root, ensure_ascii=False, indent=2) + "\n")
    os.replace(tmp, path)
    return root
