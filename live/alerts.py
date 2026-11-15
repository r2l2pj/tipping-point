"""Alert candidates for Modo Agora (data/now/alerts.json; no messaging channel — L55).

Doc "Regras de envio": only when the class upgrades to Alto or Crítico
(considering today and D+1), never between 23h and 6h unless Crítico, at most
one alert per 6h per area.

This emits data/now/alerts.json: the regions that trigger an alert, each with a
`hold` flag for quiet hours. The app shows them on its Public Alerts page.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
from scipy.ndimage import label

from .config import ALERT_MIN_CLASS, ASSET_ROOT, LABELS, NOW_BOUNDS, NOW_DIR, QUIET_HOURS


def _clusters(upgraded: np.ndarray, worst: np.ndarray, cls_today: np.ndarray,
              transform, cluster_px: int) -> list[dict]:
    """Connected regions of `upgraded`; with cluster_px > 0, pixels within
    that many cells of each other merge into one region (a scatter of
    single-pixel upgrades becomes one candidate, not hundreds)."""
    from scipy.ndimage import binary_dilation

    grow = (binary_dilation(upgraded, structure=np.ones((3, 3), bool),
                            iterations=cluster_px)
            if cluster_px > 0 else upgraded)
    lab, n = label(grow, structure=np.ones((3, 3), bool))
    lab = np.where(upgraded, lab, 0)
    res = abs(transform.a)
    out = []
    for i in range(1, n + 1):
        rows, cols = np.where(lab == i)
        if rows.size == 0:
            continue
        lon, lat = transform * (cols.mean() + 0.5, rows.mean() + 0.5)
        w, n_ = transform * (cols.min(), rows.min())
        e, s_ = transform * (cols.max() + 1, rows.max() + 1)
        cls = int(worst[rows, cols].max())
        km = 111.195 * res
        out.append({
            "class": cls,
            "pixels": int(rows.size),
            "critical_pixels": int((worst[rows, cols] == 3).sum()),
            "area_km2": round(float(rows.size * km * km
                                    * np.cos(np.deg2rad(lat))), 2),
            # the day the published (worst) class occurs: a Crítico that only
            # exists on D+1 must not be labelled 'today'
            "class_today": int(cls_today[rows, cols].max()),
            "when": ("today" if int(cls_today[rows, cols].max()) >= cls
                     else "d1"),
            "centroid": [round(float(lon), 4), round(float(lat), 4)],
            "bbox": [round(float(v), 4) for v in (w, s_, e, n_)],
        })
    return out


def _candidates(prev_cls, cls_today, cls_d1, transform, quiet: bool,
                hazard: str | None, cluster_px: int) -> list[dict]:
    worst = np.maximum(cls_today, cls_d1).astype(np.int16)
    prev = (np.zeros_like(worst) if prev_cls is None
            else np.asarray(prev_cls, dtype=np.int16))
    upgraded = (worst >= ALERT_MIN_CLASS) & (worst > prev)
    out = []
    for region in _clusters(upgraded, worst, np.asarray(cls_today),
                            transform, cluster_px):
        cls = region["class"]
        cand = {"class": cls, "label": LABELS[cls]}
        if hazard is not None:
            cand["hazard"] = hazard
        cand.update({k: v for k, v in region.items() if k != "class"})
        cand["hold"] = bool(quiet and cls < 3)
        out.append(cand)
    return out


def _payload(now_utc: dt.datetime, quiet: bool, candidates: list[dict]) -> dict:
    candidates.sort(key=lambda c: (-c["class"], -c["pixels"]))
    return {
        "generated_at": now_utc.isoformat().replace("+00:00", "Z"),
        "quiet_hours": quiet,
        "rule": "upgrade to Alto/Crítico today or D+1; quiet 23h-6h unless Crítico",
        "candidates": candidates,
    }


def _quiet(now_utc: dt.datetime) -> bool:
    return now_utc.astimezone(ZoneInfo("America/Sao_Paulo")).hour in QUIET_HOURS


def emit_alerts(prev_cls: np.ndarray | None, cls_today: np.ndarray, cls_d1: np.ndarray,
                now_utc: dt.datetime, transform, cluster_px: int = 0) -> dict:
    """Compare today's and D+1 worst class per pixel against the previous run.

    prev_cls: worst class array from the previous run (None -> everything that
    is Alto/Crítico now counts as an upgrade). One candidate per connected
    upgraded region, class = the region's worst class.
    """
    quiet = _quiet(now_utc)
    return _payload(now_utc, quiet, _candidates(
        prev_cls, cls_today, cls_d1, transform, quiet, None, cluster_px))


ALERT_CLUSTER_PX = 3   # ~3 km on the 0.01 deg grid


def emit_hazard_alerts(prev: dict | None,
                       layers: dict[str, tuple[np.ndarray, np.ndarray]],
                       now_utc: dt.datetime, transform,
                       cluster_px: int = ALERT_CLUSTER_PX) -> dict:
    """Per-hazard candidates: layers = {"landslide": (today, d1),
    "flood": (today, d1)}; prev = {hazard: previous worst class array}
    (None or a missing hazard -> everything counts as an upgrade). Each
    candidate carries `hazard`; nearby upgraded pixels are clustered."""
    quiet = _quiet(now_utc)
    cands = []
    for hazard, (today, d1) in layers.items():
        cands += _candidates((prev or {}).get(hazard), today, d1, transform,
                             quiet, hazard, cluster_px)
    return _payload(now_utc, quiet, cands)


def active_alerts(layers: dict[str, tuple[np.ndarray, np.ndarray]],
                  now_utc: dt.datetime, transform, state: dict | None,
                  cluster_px: int = ALERT_CLUSTER_PX) -> tuple[list[dict], dict]:
    """PUBLIC list: every current Alto/Crítico area (today or D+1),
    regardless of upgrades, rate limits or quiet hours — those rules govern
    SENDING (notifications), not what the page shows.

    state: {key: first_issued_at} from the previous run; an area keeps its
    first_issued_at while it stays active (key = hazard|uf|municipality|n,
    n = rank among that municipality's areas). Returns (active, new_state);
    callers add municipality before keys are made (see keyed_active)."""
    now_iso = now_utc.isoformat().replace("+00:00", "Z")
    act = emit_hazard_alerts(None, layers, now_utc, transform,
                             cluster_px)["candidates"]
    for a in act:
        a.pop("hold", None)           # holding is a notification concern
        a["last_confirmed_at"] = now_iso
    return act, dict(state or {})


def keyed_active(active: list[dict], state: dict, now_iso: str) -> dict:
    """Assign first_issued_at from `state` (or now) and return the new state
    holding only the areas still active."""
    seen: dict[str, int] = {}
    new_state = {}
    for a in sorted(active, key=lambda a: (-a["class"], -a["pixels"])):
        base = f"{a.get('hazard')}|{a.get('uf')}|{a.get('municipality')}"
        n = seen.get(base, 0)
        seen[base] = n + 1
        key = f"{base}|{n}"
        a["first_issued_at"] = state.get(key, now_iso)
        a["id"] = key
        new_state[key] = a["first_issued_at"]
    return new_state


# --------------------------------------------------------------------------
# L8 GEE path — alert regions read from the exported data/now bundle.
# The grid-based diff above stays for the --local fallback.

_BUNDLE_SKIP = {"alerts.json", "manifest.json", "summary.json"}


def _class_index(key) -> int | None:
    if isinstance(key, str) and key in LABELS:
        return LABELS.index(key)
    try:
        idx = int(key)
    except (TypeError, ValueError):
        return None
    return idx if 0 <= idx < len(LABELS) else None


def _features_with_class(path: Path) -> list[dict]:
    """GeoJSON/JSON features carrying a 'class' property, else []."""
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, dict) and data.get("type") == "FeatureCollection":
        feats = data.get("features", [])
    elif isinstance(data, list):
        feats = data
    elif isinstance(data, dict) and isinstance(data.get("features"), list):
        feats = data["features"]
    else:
        return []
    out = []
    for feat in feats:
        if not isinstance(feat, dict):
            continue
        props = feat.get("properties") or feat
        cls = _class_index(props.get("class")) if isinstance(props, dict) else None
        if cls is None:
            continue
        region = dict(props)
        region["class"] = cls
        region["label"] = LABELS[cls]
        if "geometry" in feat:
            region["geometry"] = feat["geometry"]
        out.append(region)
    return out


def _max_class_region(layer: str, counts: dict) -> dict | None:
    best = None
    for key, val in counts.items():
        idx = _class_index(key)
        if idx is not None and isinstance(val, (int, float)) and val > 0:
            best = idx if best is None else max(best, idx)
    if best is None:
        return None
    return {"layer": layer, "class": best, "label": LABELS[best]}


def _regions_from_summary(path: Path) -> list[dict]:
    try:
        summary = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(summary, dict):
        return []
    sources: dict[str, dict] = {}
    if isinstance(summary.get("layers"), dict):
        sources.update({k: v for k, v in summary["layers"].items()
                        if isinstance(v, dict)})
    sources.setdefault("now", summary)
    regions = []
    for layer, data in sources.items():
        counts = data.get("classes_km2") or data.get("class_counts") or {}
        if isinstance(counts, dict):
            region = _max_class_region(layer, counts)
            if region:
                regions.append(region)
    return regions


def critical_areas_from_assets(ee_module=None) -> list[dict]:
    """Vectorize Alto-or-higher d0 TPI regions directly from each GEE hazard asset."""
    if ee_module is None:
        import ee as ee_module

        try:
            from gee.common import ee_init
        except ImportError:
            key_value = os.environ["GCP_SA_KEY"]
            if key_value.lstrip().startswith("{"):
                key = json.loads(key_value)
                with tempfile.NamedTemporaryFile("w", suffix=".json") as key_file:
                    json.dump(key, key_file)
                    key_file.flush()
                    creds = ee_module.ServiceAccountCredentials(
                        key["client_email"], key_file.name)
            else:
                creds = ee_module.ServiceAccountCredentials(None, key_value)
            ee_module.Initialize(creds, project=os.environ.get("GEE_PROJECT"))
        else:
            ee_init()

    geometry = ee_module.Geometry.Rectangle(list(NOW_BOUNDS))
    regions = []
    for kind in ("slide", "flood"):
        for day in (0, 1):
            image = ee_module.Image(f"{ASSET_ROOT}/now/tpi_{kind}_d{day}")
            for threshold, label_name in ((0.6, "Crítico"), (0.4, "Alto")):
                cls = _class_index(label_name)
                features = image.gte(threshold).selfMask().reduceToVectors(
                    scale=1000, geometry=geometry, maxPixels=1e9).getInfo()["features"]
                for feature in features:
                    region = dict(feature.get("properties") or {})
                    region["class"] = cls
                    region["label"] = LABELS[cls]
                    region["kind"] = kind
                    region["geometry"] = feature["geometry"]
                    regions.append(region)
    return regions


def load_now_regions(now_dir: Path | str = NOW_DIR) -> list[dict]:
    """Alert regions from the exported data/now bundle (GEE path).

    L8: export shape pending — see lead-analysis tools/export_fallback.py.
    Prefers a class-bearing critical-areas GeoJSON/JSON under now_dir;
    else derives per-layer max-class regions from summary.json class counts;
    returns [] when neither exists.
    """
    now_dir = Path(now_dir)
    if not now_dir.is_dir():
        return []
    cands = [p for p in now_dir.rglob("*")
             if p.is_file() and p.suffix.lower() in (".geojson", ".json")
             and p.name not in _BUNDLE_SKIP]
    cands.sort(key=lambda p: (
        "critical" not in p.name.lower() and "areas" not in p.name.lower(),
        str(p)))
    for path in cands:
        regions = _features_with_class(path)
        if regions:
            return regions
    return _regions_from_summary(now_dir / "summary.json")
