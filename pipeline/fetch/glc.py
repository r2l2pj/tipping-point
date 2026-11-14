"""NASA COOLR Global Landslide Catalog points for an event's AOI.

REFERENCE-ONLY layer (map footer credit) — never a TPI input. The legacy
data.nasa.gov GLC CSV ends in 2016; this queries the live COOLR/NASA Global
Landslide Catalog ArcGIS FeatureServer instead.

The default query is bbox-only (all-time catalog — community reports lag
weeks to months, so a window filter empties recent events). Set
event['glc_window'] or pass window= to glc_catalog to filter by event_date.
Each output feature's event_date is normalized to an ISO date for t_utc.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from tp.events import DATA_DIR

from .featureserver import bbox_params, query_features

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


def _query_params(event: dict, window: dict | None = None) -> dict:
    """ArcGIS query params: AOI bbox, all-time catalog by default.

    The GLC is a REFERENCE layer and community reports lag (259 all-time
    features in the wa bbox, zero in Dec-2025), so the default query has no
    event_date filter. A window ({start, end} ISO dates) applies only when
    passed explicitly or set as event['glc_window'].
    """
    win = window if window is not None else event.get("glc_window")
    where = "1=1"
    if win:
        where = (
            f"event_date >= DATE '{win['start']}' "
            f"AND event_date <= DATE '{win['end']}'"
        )
    return {**bbox_params(event), "where": where}


def _clean_properties(props: dict, timezone: str = "America/Los_Angeles") -> dict:
    out = {k: props[k] for k in KEEP_PROPS if props.get(k) is not None}
    raw = out.get("event_date")
    out["t_precision"] = "none"
    if raw not in (None, ""):
        try:
            if isinstance(raw, (int, float)):
                stamp = datetime.fromtimestamp(raw / 1000, tz=UTC)
                out["event_date"] = stamp.strftime("%Y-%m-%d")
                precision = "date"
            else:
                stamp = datetime.fromisoformat(str(raw).strip())
                precision = "hour" if ":" in str(raw) else "date"
            if precision == "date":
                stamp = stamp.replace(hour=12, minute=0, second=0, microsecond=0,
                                      tzinfo=ZoneInfo(timezone))
            elif stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=ZoneInfo(timezone))
            out["t_utc"] = stamp.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            out["t_precision"] = precision
        except (ValueError, OverflowError, OSError):
            pass
    out["source"] = SOURCE_NAME
    out["url"] = SOURCE_URL
    return out


def glc_catalog(event: dict, window: dict | None = None) -> Path:
    """Write data/<case>/geojson/landslide_catalog.geojson; return its path.

    <case> is the event id with - -> _ and without the month part
    ('wa-2025-12' -> 'wa_2025') — data/ dirs are per case, not event.
    An empty query result still writes a valid FeatureCollection.
    """
    records = query_features(SERVICE_URL, _query_params(event, window=window), get=requests.get)

    features = [
        {
            "type": "Feature",
            "geometry": f["geometry"],
            "properties": _clean_properties(
                f.get("properties") or {},
                "America/Sao_Paulo" if event["id"].startswith("rs-") else "America/Los_Angeles"),
        }
        for f in records
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
