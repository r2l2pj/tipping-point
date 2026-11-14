from __future__ import annotations

import json
from datetime import UTC, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from tp.events import DATA_DIR

from .featureserver import bbox_params, query_features

SOURCE_NAME = "WA DNR WGS Clearinghouse (public)"
SOURCE_URL = (
    "https://services.arcgis.com/4x406oNViizbGo13/ArcGIS/rest/services/"
    "service_d63bc45f07fc4e859f0bdde6c847ec40/FeatureServer/0"
)
LICENSE = "Not specified in public FeatureServer metadata"


def _clean_properties(props: dict) -> dict:
    out = {"source": SOURCE_NAME, "url": SOURCE_URL, "license": LICENSE,
           "event_id": props.get("objectid"), "event_date": props.get("event_date"),
           "t_precision": "none"}
    for src, dest in (("event_time", "event_time"), ("location_desc", "location_description"),
                      ("landslide_type", "landslide_category"), ("obs_summary", "event_description")):
        if props.get(src) is not None:
            out[dest] = props[src]
    raw = props.get("event_date")
    if raw in (None, ""):
        return out
    try:
        numeric = isinstance(raw, (int, float))
        stamp = (datetime.fromtimestamp(raw / 1000, UTC) if numeric
                 else datetime.fromisoformat(str(raw).strip()))
        has_time = bool(stamp.hour or stamp.minute or stamp.second) if numeric else ":" in str(raw)
        out["event_date"] = stamp.date().isoformat()
        clock = props.get("event_time")
        if clock not in (None, ""):
            try:
                parsed = time.fromisoformat(str(clock).strip())
            except ValueError:
                try:
                    parsed = datetime.strptime(str(clock).strip(), "%I:%M %p").time()
                except ValueError:
                    parsed = None
            if parsed is not None:
                stamp = datetime.combine(stamp.date(), parsed)
                has_time = True
        if not has_time:
            stamp = stamp.replace(hour=12, minute=0, second=0, microsecond=0,
                                  tzinfo=ZoneInfo("America/Los_Angeles"))
        elif stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=ZoneInfo("America/Los_Angeles"))
        out["t_utc"] = stamp.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        out["t_precision"] = "hour" if has_time else "date"
    except (ValueError, OverflowError, OSError):
        pass
    return out


def wgs_catalog(event: dict) -> Path:
    if event["id"] != "wa-2025-12":
        raise ValueError("WGS validation inventory is only supported for wa_2025")
    params = {**bbox_params(event), "orderByFields": "objectid ASC", "resultRecordCount": 1000}
    records = query_features(SOURCE_URL + "/query", params)
    features = [{"type": "Feature", "geometry": f["geometry"],
                 "properties": _clean_properties(f.get("properties") or {})}
                for f in records if f.get("geometry")]
    out = DATA_DIR / "wa_2025" / "geojson" / "landslide_catalog.geojson"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"type": "FeatureCollection", "source": SOURCE_NAME,
                               "url": SOURCE_URL, "license": LICENSE,
                               "features": features}, indent=2) + "\n")
    return out
