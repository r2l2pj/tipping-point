"""Alert candidates for Modo Agora — stub, no Telegram sending.

Doc "Regras de envio": only when the class upgrades to Alto or Crítico
(considering today and D+1), never between 23h and 6h unless Crítico, at most
one alert per 6h per person (rate limiting lives in the bot, not here).

This emits data/now/alerts.json: the regions that WOULD trigger an alert, each with
a `hold` flag for quiet hours. The related alerts-spec agent owns the real
subscriber loop and message text.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
from pathlib import Path

import numpy as np
from scipy.ndimage import label

from .config import ALERT_MIN_CLASS, ASSET_ROOT, LABELS, NOW_BOUNDS, NOW_DIR, QUIET_HOURS


def _regions(arr: np.ndarray, transform) -> list[dict]:
    lab, n = label(arr)
    out = []
    for i in range(1, n + 1):
        rows, cols = np.where(lab == i)
        r, c = rows.mean(), cols.mean()
        lon, lat = transform * (c + 0.5, r + 0.5)
        out.append({"pixels": int(rows.size), "centroid": [round(float(lon), 4), round(float(lat), 4)]})
    return out


def emit_alerts(prev_cls: np.ndarray | None, cls_today: np.ndarray, cls_d1: np.ndarray,
                now_utc: dt.datetime, transform) -> dict:
    """Compare today's and D+1 worst class per pixel against the previous run.

    prev_cls: worst class array from the previous run (None -> everything that
    is Alto/Crítico now counts as an upgrade).
    """
    worst = np.maximum(cls_today, cls_d1).astype(np.int16)
    prev = np.zeros_like(worst) if prev_cls is None else np.asarray(prev_cls, dtype=np.int16)
    upgraded = (worst >= ALERT_MIN_CLASS) & (worst > prev)
    quiet = now_utc.hour in QUIET_HOURS

    candidates = []
    for cls in (3, 2):
        m = upgraded & (worst == cls)
        if not m.any():
            continue
        for region in _regions(m, transform):
            candidates.append({
                "class": int(cls),
                "label": LABELS[cls],
                "centroid": region["centroid"],
                "pixels": region["pixels"],
                "hold": bool(quiet and cls < 3),
            })
    return {
        "generated_at": now_utc.isoformat().replace("+00:00", "Z"),
        "quiet_hours": quiet,
        "rule": "upgrade to Alto/Crítico today or D+1; quiet 23h-6h unless Crítico",
        "candidates": candidates,
    }


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
        image = ee_module.Image(f"{ASSET_ROOT}/now/tpi_{kind}_d0")
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
