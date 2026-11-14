"""Landslide inventory fetch — REFERENCE-ONLY layer (never a TPI input).

Downloads every sidecar file of two CC-BY-4.0 Zenodo inventories into
data/raw/inventory/{ufrgs,cemaden}/, then builds
data/<case_id>/geojson/landslide_catalog.geojson from the two POINT
shapefiles (initiation points), filtered to the event AOI and, when a date
field exists, to the event window. Catalog points are 'catalog' entries;
every output Feature carries {source, license, doi, event_date}.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests
from shapely.errors import GEOSException
from shapely.geometry import mapping
from tp.events import DATA_DIR, event_data_dir

HTTP_TIMEOUT = 300

LICENSE = "CC-BY-4.0"
ZENODO_RECORD_URL = "https://zenodo.org/api/records/{record}"

INVENTORIES = {
    "ufrgs": {
        "record": "15618462",
        "doi": "10.5281/zenodo.15618462",
        "source": "UFRGS",
        "point_stem": "Landslides_Initiation_RS_Brazil_2024",
    },
    "cemaden": {
        "record": "13225473",
        "doi": "10.5281/zenodo.13225473",
        "source": "Cemaden",
        "point_stem": "GEO_RS_CEMADEN_WGS84_PONTOS_MM",
    },
}

_DATE_COL_RE = re.compile(r"data|date", re.IGNORECASE)


def _get_json(url: str, **params):
    r = requests.get(url, params=params, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()


def record_files(record_id: str) -> list[dict]:
    """Zenodo record file list (all sidecars of every shapefile)."""
    return _get_json(ZENODO_RECORD_URL.format(record=record_id)).get("files", [])


def _fetch_file(url: str, target: Path) -> bool:
    try:
        r = requests.get(url, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        target.write_bytes(r.content)
    except OSError:
        return False
    return True


def download_inventories(raw_dir: Path) -> dict:
    """Download all files of each inventory into raw_dir/<name>/.

    Returns {inventory_name: [downloaded Path, ...]}; an unreachable or
    failed inventory maps to an empty (or partial) list — never raises.
    """
    raw_dir = Path(raw_dir)
    downloaded = {}
    for name, inv in INVENTORIES.items():
        dest = raw_dir / name
        dest.mkdir(parents=True, exist_ok=True)
        try:
            files = record_files(inv["record"])
        except (OSError, ValueError, KeyError):
            downloaded[name] = []
            continue
        paths = []
        for f in files:
            key = f["key"]
            target = dest / key
            if not target.exists():
                url = (f.get("links") or {}).get("self") or (
                    f"{ZENODO_RECORD_URL.format(record=inv['record'])}"
                    f"/files/{key}/content"
                )
                if not _fetch_file(url, target):
                    continue
            paths.append(target)
        downloaded[name] = paths
    return downloaded


def _date_column(gdf: gpd.GeoDataFrame) -> str | None:
    """First date-named column with a parseable value, including sparse dates."""
    for col in gdf.columns:
        if col == "geometry" or not _DATE_COL_RE.search(str(col)):
            continue
        parsed = pd.to_datetime(gdf[col], errors="coerce", format="mixed", utc=True)
        if len(gdf) == 0 or parsed.notna().any():
            return col
    return None


def catalog_filter(gdf: gpd.GeoDataFrame, event: dict) -> gpd.GeoDataFrame:
    """Clip to the event AOI; if a date field exists, keep the event window.

    Adds an 'event_date' column (ISO string or None).
    """
    west, south, east, north = event["aoi"]["bbox"]
    gdf = gdf.cx[west:east, south:north].copy()
    candidates = gdf.drop(columns="geometry")
    dates = pd.Series([None] * len(gdf), index=gdf.index, dtype=object)
    source_dates = dates.copy()
    precision = pd.Series("none", index=gdf.index)
    while (col := _date_column(candidates)) is not None:
        for idx, raw in candidates[col].items():
            if dates[idx] is not None or pd.isna(raw):
                continue
            stamp = pd.to_datetime(raw, errors="coerce")
            if pd.isna(stamp):
                continue
            source_dates[idx] = stamp.strftime("%Y-%m-%d")
            has_time = ":" in raw if isinstance(raw, str) else bool(
                stamp.hour or stamp.minute or stamp.second or stamp.microsecond)
            if not has_time:
                stamp = stamp.normalize() + pd.Timedelta(hours=12)
            if stamp.tzinfo is None:
                stamp = stamp.tz_localize("America/Sao_Paulo")
            dates[idx] = stamp
            precision[idx] = "hour" if has_time else "date"
        candidates = candidates.drop(columns=col)
    gdf["event_date"] = source_dates
    gdf["t_precision"] = precision
    gdf["t_utc"] = pd.Series(
        [t.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ") if t is not None else None for t in dates],
        index=gdf.index, dtype=object)
    start = event["window"]["start"]
    end = event["window"]["end"]
    in_window = gdf["event_date"].map(lambda d: d is None or start <= d <= end).astype(bool)
    return gdf[in_window]


def _load_point_shapefiles(raw_dir: Path) -> gpd.GeoDataFrame:
    frames = []
    for name, inv in INVENTORIES.items():
        shp = Path(raw_dir) / name / f"{inv['point_stem']}.shp"
        if not shp.exists():
            continue
        gdf = gpd.read_file(shp)
        gdf = gdf.to_crs(4326) if gdf.crs else gdf.set_crs(4326)
        gdf["source"] = inv["source"]
        gdf["doi"] = inv["doi"]
        frames.append(gdf)
    if not frames:
        return gpd.GeoDataFrame(columns=["source", "doi", "geometry"],
                                geometry="geometry", crs="EPSG:4326")
    return gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs=4326)


def _write_feature_collection(features: list[dict], out: Path,
                              fetch_error: str | None = None) -> None:
    if not features and out.exists():
        # Never clobber a populated catalog with an empty/failed fetch.
        try:
            old = json.loads(out.read_text())
            if any((f.get("geometry") or {}).get("coordinates")
                   for f in old.get("features", [])):
                return
        except (OSError, ValueError):
            pass
    fc = {"type": "FeatureCollection", "features": features}
    if fetch_error:
        fc["fetch_error"] = fetch_error
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(fc))


def inventory_catalog(event: dict) -> Path:
    """Write data/<case_id>/geojson/landslide_catalog.geojson; return Path.

    <case_id> is the config.yaml case key ('rs-2024-05' -> 'rs_2024') — data
    folders are per case, not per event (ruling L4).
    """
    case = "_".join(event["id"].split("-")[:2])
    out = event_data_dir(case) / "geojson" / "landslide_catalog.geojson"
    raw_dir = DATA_DIR / "raw" / "inventory"
    try:
        download_inventories(raw_dir)
        loaded = _load_point_shapefiles(raw_dir)
    except (OSError, ValueError, KeyError, RuntimeError, GEOSException) as exc:
        _write_feature_collection([], out, fetch_error=f"{type(exc).__name__}: {exc}")
        return out
    if loaded.empty:
        _write_feature_collection(
            [], out, fetch_error="no inventory point shapefiles available")
        return out
    gdf = catalog_filter(loaded, event)
    features = [
        {
            "type": "Feature",
            "geometry": mapping(geom),
            "properties": {
                "source": src,
                "license": LICENSE,
                "doi": doi,
                "event_date": event_date,
                "t_precision": precision,
                **({"t_utc": t_utc} if t_utc is not None else {}),
            },
        }
        for geom, src, doi, event_date, t_utc, precision in zip(
            gdf.geometry, gdf["source"], gdf["doi"], gdf["event_date"],
            gdf["t_utc"], gdf["t_precision"],
        )
    ]
    _write_feature_collection(features, out)
    return out
