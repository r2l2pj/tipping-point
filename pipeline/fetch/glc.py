"""NASA COOLR Global Landslide Catalog points for an event's AOI + window.

REFERENCE-ONLY layer (map footer credit) — never a TPI input. The legacy
data.nasa.gov GLC CSV ends in 2016; this queries the live COOLR/NASA Global
Landslide Catalog ArcGIS FeatureServer instead.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import requests
from tp.events import DATA_DIR

SERVICE_URL = (
    "https://gis.earthdata.nasa.gov/portal/rest/services/"
    "Landslides/COOLR_Reports_Points/FeatureServer/0/query"
)
SOURCE_NAME = "NASA COOLR Global Landslide Catalog"
SOURCE_URL = (
    "https://gis.earthdata.nasa.gov/portal/rest/services/"
    "Landslides/COOLR_Reports_Points/FeatureServer"
)

KEEP_PROPS = (
    "event_id", "event_date", "event_time", "event_title", "event_description",
    "location_description", "location_accuracy", "landslide_category",
    "landslide_trigger", "landslide_size", "landslide_setting",
    "fatality_count", "injury_count", "storm_name", "photo_link",
    "source_name", "source_link", "latitude", "longitude",
    "country_name", "country_code", "admin_division_name",
)


def _query_params(event: dict) -> dict:
    w, s, e, n = event["aoi"]["bbox"]
    win = event["window"]
    return {
        "where": (
            f"event_date >= DATE '{win['start']}' "
            f"AND event_date <= DATE '{win['end']}'"
        ),
        "geometry": f"{w},{s},{e},{n}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "*",
        "outSR": "4326",
        "f": "geojson",
    }


def _clean_properties(props: dict) -> dict:
    out = {k: props[k] for k in KEEP_PROPS if props.get(k) is not None}
    if isinstance(out.get("event_date"), (int, float)):
        out["event_date"] = datetime.fromtimestamp(
            out["event_date"] / 1000, tz=UTC
        ).strftime("%Y-%m-%d")
    out["source"] = SOURCE_NAME
    out["url"] = SOURCE_URL
    return out


def glc_catalog(event: dict) -> Path:
    """Write data/<case>/geojson/landslide_catalog.geojson; return its path.

    <case> is the event id with - -> _ and without the month part
    ('wa-2025-12' -> 'wa_2025') — data/ dirs are per case, not event.
    An empty query result still writes a valid FeatureCollection.
    """
    resp = requests.get(SERVICE_URL, params=_query_params(event), timeout=60)
    resp.raise_for_status()
    payload = resp.json()
    if "error" in payload:
        raise RuntimeError(f"COOLR query failed: {payload['error']}")

    features = [
        {
            "type": "Feature",
            "geometry": f["geometry"],
            "properties": _clean_properties(f.get("properties") or {}),
        }
        for f in payload.get("features") or []
        if f.get("geometry")
    ]
    fc = {
        "type": "FeatureCollection",
        "source": SOURCE_NAME,
        "url": SOURCE_URL,
        "features": features,
    }

    case = "_".join(event["id"].split("-")[:2])
    out = DATA_DIR / case / "geojson" / "landslide_catalog.geojson"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(fc, indent=2) + "\n")
    return out
