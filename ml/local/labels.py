"""Landslide inventories -> one labels table (PLAN Nível 3, 3a + 3b).

Sources
  * NASA COOLR / Global Landslide Catalog (GLC) — live ArcGIS FeatureServer,
    dated, with a location accuracy class. 3a keeps points <= 5 km inside
    South+Southeast Brazil; 3b keeps rain-triggered points <= 5 km dated since
    SMAP start, worldwide (LHASA-v2 style training area).
  * RS 2024 inventory (UFRGS/Cemaden, CC-BY-4.0) — 16,027 points from the
    committed ``data/rs_2024/geojson/landslide_catalog.geojson``. Undated per
    point: every point gets the fixed date ``RS2024_DATE`` (2024-05-01), and
    horizon labels use the triggering window Apr 29 - May 2 — never a date picked
    from rain (circular). ``assign_peak_day`` is kept for diagnostics only.

Output columns (``inventory.csv`` under ``DATA_DIR/labels``)::

    point_id, lon, lat, source, event_id, date, date_precision, acc_km,
    trigger, title

``date`` is ``datetime64[D]`` (NaT when undated). One row per landslide
point after deduplication to the 90 m grid.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable

import numpy as np
import pandas as pd
import requests

from . import settings as S

log = logging.getLogger(__name__)

GLC_URL = (
    "https://gis.earthdata.nasa.gov/portal/rest/services/"
    "Landslides/COOLR_Reports_Points/FeatureServer/0/query"
)
GLC_FIELDS = (
    "event_id,event_date,event_title,location_accuracy,landslide_trigger,"
    "landslide_category,latitude,longitude,country_name"
)
COLUMNS = ["point_id", "lon", "lat", "source", "event_id", "date",
           "date_precision", "acc_km", "trigger", "title"]
EARTH_R_KM = 6371.0088


# --------------------------------------------------------------------------- GLC
def _glc_query(bbox, *, get: Callable = requests.get, page: int = 2000) -> list[dict]:
    params = {
        "where": "1=1", "outFields": GLC_FIELDS, "returnGeometry": "true",
        "outSR": "4326", "f": "geojson", "resultRecordCount": page,
        "orderByFields": "objectid",
    }
    if bbox is not None:
        w, s, e, n = bbox
        params.update({
            "geometry": f"{w},{s},{e},{n}", "geometryType": "esriGeometryEnvelope",
            "inSR": "4326", "spatialRel": "esriSpatialRelIntersects",
        })
    out: list[dict] = []
    while True:
        resp = get(GLC_URL, params={**params, "resultOffset": len(out)}, timeout=120)
        resp.raise_for_status()
        payload = resp.json()
        if "error" in payload:
            raise RuntimeError(f"GLC query failed: {payload['error']}")
        page_feats = payload.get("features") or []
        out.extend(page_feats)
        more = payload.get("exceededTransferLimit") or (
            payload.get("properties") or {}).get("exceededTransferLimit")
        if not page_feats or (not more and len(page_feats) < page):
            return out


def glc_frame(features: list[dict]) -> pd.DataFrame:
    """GeoJSON features -> inventory columns (no filtering)."""
    rows = []
    for f in features:
        p = f.get("properties") or {}
        geom = f.get("geometry") or {}
        coords = geom.get("coordinates") or [p.get("longitude"), p.get("latitude")]
        lon, lat = coords[0], coords[1]
        if lon is None or lat is None:
            continue
        ms = p.get("event_date")
        date = (pd.Timestamp(ms, unit="ms", tz="UTC").tz_localize(None).normalize()
                if ms is not None else pd.NaT)
        acc = S.GLC_ACCURACY_KM.get(str(p.get("location_accuracy") or "").lower(), np.nan)
        rows.append({
            "point_id": f"glc_{p.get('event_id')}", "lon": float(lon), "lat": float(lat),
            "source": "glc", "event_id": None, "date": date,
            "date_precision": "day" if date is not pd.NaT else "none",
            "acc_km": acc, "trigger": (p.get("landslide_trigger") or "").lower(),
            "title": p.get("event_title") or "",
        })
    df = pd.DataFrame(rows, columns=COLUMNS)
    df["date"] = pd.to_datetime(df["date"]).astype("datetime64[s]")
    return df


def fetch_glc(bbox=None, *, force: bool = False, get: Callable = requests.get) -> pd.DataFrame:
    """All GLC points in ``bbox`` (None = global), cached as raw GeoJSON."""
    tag = "global" if bbox is None else "_".join(f"{v:g}" for v in bbox)
    cache = S.path("labels", f"glc_{tag}.geojson")
    if cache.exists() and not force:
        feats = json.loads(cache.read_text())["features"]
    else:
        feats = _glc_query(bbox, get=get)
        cache.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
        log.info("GLC: %d features -> %s", len(feats), cache)
    return glc_frame(feats)


def filter_glc(df: pd.DataFrame, *, max_acc_km: float = S.GLC_MAX_ACC_KM,
               since=None, rain_only: bool = False, bbox=None) -> pd.DataFrame:
    keep = df["acc_km"].le(max_acc_km)
    if since is not None:
        keep &= df["date"].ge(pd.Timestamp(since))
    if rain_only:
        keep &= df["trigger"].isin(S.GLC_RAIN_TRIGGERS)
    if bbox is not None:
        keep &= in_bbox(df["lon"], df["lat"], bbox)
    return df[keep].reset_index(drop=True)


# ----------------------------------------------------------------------- RS 2024
def load_rs2024(path=S.RS2024_CATALOG) -> pd.DataFrame:
    """RS 2024 inventory points (MultiPoint exploded), undated per point."""
    gj = json.loads(path.read_text())
    pts: list[tuple[float, float]] = []
    for f in gj["features"]:
        g = f["geometry"]
        if g["type"] == "Point":
            pts.append(tuple(g["coordinates"][:2]))
        elif g["type"] == "MultiPoint":
            pts.extend(tuple(c[:2]) for c in g["coordinates"])
    arr = np.asarray(pts, dtype=float)
    df = pd.DataFrame({
        "point_id": [f"rs2024_{i}" for i in range(len(arr))],
        "lon": arr[:, 0], "lat": arr[:, 1], "source": "rs2024",
        "event_id": S.RS2024_EVENT_ID, "date": pd.NaT,
        "date_precision": "event-window", "acc_km": 0.0, "trigger": "rain",
        "title": "RS 2024 inventory (UFRGS/Cemaden)",
    }, columns=COLUMNS)
    df["date"] = df["date"].astype("datetime64[s]")
    return df


def wa2025_frame(features: list[dict], window=S.WA2025_WINDOW) -> pd.DataFrame:
    """WGS report features -> inventory rows; only points dated inside ``window``."""
    rows = []
    lo, hi = pd.Timestamp(window[0]), pd.Timestamp(window[1])
    for f in features:
        ms = (f.get("properties") or {}).get("event_date")
        coords = (f.get("geometry") or {}).get("coordinates")
        if ms is None or not coords:
            continue
        date = pd.Timestamp(ms, unit="ms", tz="UTC").tz_localize(None).normalize()
        if not lo <= date <= hi:
            continue
        rows.append({
            "point_id": f"wa2025_{len(rows)}", "lon": float(coords[0]), "lat": float(coords[1]),
            "source": "wa2025", "event_id": S.WA2025_EVENT_ID, "date": date,
            "date_precision": "day", "acc_km": 0.0, "trigger": "rain",
            "title": "WA Dec 2025 atmospheric-river landslide report (Washington Geological Survey)",
        })
    df = pd.DataFrame(rows, columns=COLUMNS)
    df["date"] = pd.to_datetime(df["date"]).astype("datetime64[s]")
    return df


def fetch_wa2025(*, force: bool = False, get: Callable = requests.get) -> pd.DataFrame:
    """Dated WA Dec 2025 landslide reports (TEST-only event). Requests ONLY
    event_date + geometry: the layer also stores reporter names/emails."""
    cache = S.path("labels", "wa2025_reports.geojson")
    if cache.exists() and not force:
        feats = json.loads(cache.read_text())["features"]
    else:
        resp = get(S.WA2025_URL, params={
            "where": "event_date IS NOT NULL", "outFields": "event_date",
            "returnGeometry": "true", "outSR": "4326", "f": "geojson",
            "resultRecordCount": 2000}, timeout=120)
        resp.raise_for_status()
        payload = resp.json()
        if "error" in payload:
            raise RuntimeError(f"WA 2025 query failed: {payload['error']}")
        feats = [{"type": "Feature", "geometry": f.get("geometry"),
                  "properties": {"event_date": (f.get("properties") or {}).get("event_date")}}
                 for f in payload.get("features") or []]
        cache.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
        log.info("WA 2025: %d dated reports -> %s", len(feats), cache)
    return wa2025_frame(feats)


def assign_peak_day(df: pd.DataFrame, daily_rain: Callable, window=S.RS2024_WINDOW) -> pd.DataFrame:
    """DIAGNOSTICS ONLY — never for labels (dates from a model input are circular).
    Date each undated point with its peak-24 h rain day inside ``window``.

    ``daily_rain(lon, lat, dates) -> mm`` (``ImergDaily.daily`` or
    ``PowerRain.daily``). Ties -> earliest day. Points with no rain data keep NaT.
    """
    days = pd.date_range(window[0], window[1], freq="D")
    lon = df["lon"].to_numpy()
    lat = df["lat"].to_numpy()
    mat = np.full((len(df), len(days)), np.nan)
    for j, d in enumerate(days):
        mat[:, j] = daily_rain(lon, lat, np.full(len(df), d.date()))
    out = df.copy()
    has = np.isfinite(mat).any(axis=1)
    idx = np.nanargmax(np.where(np.isfinite(mat), mat, -np.inf), axis=1)
    peak = np.where(has, days.to_numpy()[idx], np.datetime64("NaT", "ns"))
    out["date"] = pd.to_datetime(peak).astype("datetime64[s]")
    return out


# ------------------------------------------------------------------- utilities
def in_bbox(lon, lat, bbox) -> np.ndarray:
    w, s, e, n = bbox
    lon = np.asarray(lon)
    lat = np.asarray(lat)
    return (lon >= w) & (lon <= e) & (lat >= s) & (lat <= n)


def unit_xyz(lon, lat) -> np.ndarray:
    """Points on the unit sphere — chord distances for cKDTree queries."""
    lo = np.radians(np.asarray(lon, dtype=float))
    la = np.radians(np.asarray(lat, dtype=float))
    return np.column_stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)])


def km_to_chord(km: float) -> float:
    return 2.0 * np.sin(km / (2.0 * EARTH_R_KM))


def chord_to_km(chord) -> np.ndarray:
    return 2.0 * EARTH_R_KM * np.arcsin(np.clip(np.asarray(chord) / 2.0, 0, 1))


def dedupe_grid(df: pd.DataFrame, res: float = S.RES_STATIC_DEG) -> pd.DataFrame:
    """One point per 90 m cell and date (first wins; undated points share one key)."""
    key = pd.DataFrame({
        "x": np.floor(df["lon"].to_numpy(float) / res).astype(np.int64),
        "y": np.floor(df["lat"].to_numpy(float) / res).astype(np.int64),
        "d": pd.to_datetime(df["date"]).to_numpy().astype("datetime64[D]").astype(np.int64),
    })
    return df.loc[~key.duplicated().to_numpy()].reset_index(drop=True)


def cluster_events(df: pd.DataFrame, *, days: int = S.EVENT_LINK_DAYS,
                   km: float = S.EVENT_LINK_KM) -> pd.Series:
    """Union-find event ids for rows without one: same event if within
    ``days`` and ``km`` of another point (transitively). Undated -> own event."""
    from scipy.spatial import cKDTree

    ids = df["event_id"].astype(object).copy()
    todo = ids.isna().to_numpy()
    sub = df.loc[todo]
    n = len(sub)
    parent = np.arange(n)

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    dated = sub["date"].notna().to_numpy()
    if dated.sum() > 1:
        pos = np.flatnonzero(dated)
        tree = cKDTree(unit_xyz(sub["lon"].to_numpy()[pos], sub["lat"].to_numpy()[pos]))
        dd = sub["date"].to_numpy()[pos].astype("datetime64[D]").astype(np.int64)
        for a, b in tree.query_pairs(km_to_chord(km)):
            if abs(dd[a] - dd[b]) <= days:
                ra, rb = find(pos[a]), find(pos[b])
                if ra != rb:
                    parent[rb] = ra
    roots = np.array([find(i) for i in range(n)])
    labels = []
    first_date = sub["date"].to_numpy()
    for i in range(n):
        r = roots[i]
        d = first_date[r]
        tag = pd.Timestamp(d).strftime("%Y%m%d") if not pd.isna(d) else "undated"
        labels.append(f"glc_{tag}_{r}")
    ids.loc[todo] = labels
    return ids


def finalize(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["event_id"] = cluster_events(df)
    return df[COLUMNS].reset_index(drop=True)


def all_known_points(glc: pd.DataFrame, rs: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Every known landslide (any accuracy) — used to keep negatives > 5 km away."""
    lon = np.concatenate([glc["lon"].to_numpy(), rs["lon"].to_numpy()])
    lat = np.concatenate([glc["lat"].to_numpy(), rs["lat"].to_numpy()])
    return lon, lat


def min_dist_km(lon, lat, ref_lon, ref_lat) -> np.ndarray:
    from scipy.spatial import cKDTree

    if len(ref_lon) == 0:
        return np.full(len(lon), np.inf)
    tree = cKDTree(unit_xyz(ref_lon, ref_lat))
    d, _ = tree.query(unit_xyz(lon, lat), k=1)
    return chord_to_km(d)


def report_region(lon, lat) -> np.ndarray:
    """First matching settings.REPORT_REGIONS name, else 'other'."""
    out = np.full(len(lon), "other", dtype=object)
    for name, bbox in reversed(list(S.REPORT_REGIONS.items())):
        out[in_bbox(lon, lat, bbox)] = name
    return out


# ------------------------------------------------------------ extra TEST events
# Generic loaders for the held-out TEST events of ``test_events.yaml`` (events.py).
# Only geometry + the configured date field are read; polygons -> representative
# point (inside the polygon, unlike a centroid).
def _resolve_source(ev, *, get: Callable = requests.get):
    """Local path (repo-relative ok) or URL downloaded once to DATA_DIR/events/<id>/."""
    from pathlib import Path
    from urllib.parse import urlparse

    src = ev.source
    if not src:
        raise ValueError(f"{ev.event_id}: no inventory source in test_events.yaml yet")
    if src.startswith(("http://", "https://")):
        name = Path(urlparse(src).path).name or "inventory"
        dest = S.path("events", ev.event_id, name)
        if not dest.exists():
            resp = get(src, timeout=300)
            resp.raise_for_status()
            # APIs like Dataverse (/api/access/dataset/:persistentId/) name the
            # payload only in Content-Disposition; use it when the URL does not.
            cd = resp.headers.get("Content-Disposition", "")
            fname = cd.rsplit("filename=", 1)[-1].strip(" ;\"'") if "filename=" in cd else ""
            if fname and not Path(name).suffix:
                dest = S.path("events", ev.event_id, Path(fname).name)
                if dest.exists():
                    return dest
            dest.write_bytes(resp.content)
            log.info("%s: downloaded %s (%d bytes)", ev.event_id, src, len(resp.content))
        return dest
    p = Path(src).expanduser()
    return p if p.is_absolute() else S.REPO_ROOT / p


def _parse_dates(vals, fmt: str | None) -> pd.Series:
    s = pd.Series(vals, dtype=object)
    if fmt:
        out = pd.to_datetime(s, format=fmt, errors="coerce")
    else:
        num = pd.to_numeric(s, errors="coerce")
        if num.notna().all() and len(num) and num.abs().min() > 1e11:   # epoch ms (ArcGIS)
            out = pd.to_datetime(num, unit="ms", utc=True)
        else:
            out = pd.to_datetime(s, errors="coerce", utc=True)
    if getattr(out.dt, "tz", None) is not None:
        out = out.dt.tz_convert("UTC").dt.tz_localize(None)
    return out.dt.normalize()


def _geom_points(geoms) -> np.ndarray:
    from shapely.geometry import shape

    out = []
    for g in geoms:
        if g is None:
            out.append((np.nan, np.nan))
            continue
        geom = shape(g) if isinstance(g, dict) else g
        if geom.is_empty:
            out.append((np.nan, np.nan))
        elif geom.geom_type == "Point":
            out.append((geom.x, geom.y))
        else:  # MultiPoint/Polygon/MultiPolygon/Line -> one point inside it
            p = geom.representative_point()
            out.append((p.x, p.y))
    return np.asarray(out, dtype=float).reshape(-1, 2)


def _read_geojson(path, ev) -> tuple[np.ndarray, list]:
    gj = json.loads(path.read_text())
    feats = gj.get("features") or []
    pts, dates = [], []
    for f in feats:
        g = f.get("geometry")
        if g and g.get("type") == "MultiPoint" and ev.dating == "fixed":
            pts.extend(tuple(c[:2]) for c in g["coordinates"])   # explode (RS-style)
            dates.extend([None] * len(g["coordinates"]))
            continue
        pts.append(tuple(_geom_points([g])[0]))
        dates.append((f.get("properties") or {}).get(ev.date_field) if ev.date_field else None)
    return np.asarray(pts, dtype=float).reshape(-1, 2), dates


def _read_vector(path, ev) -> tuple[np.ndarray, list]:
    import geopandas as gpd

    target = f"zip://{path}" if str(path).endswith(".zip") else path
    cols = [ev.date_field] if ev.date_field else []
    gdf = gpd.read_file(target, layer=ev.layer, columns=cols)
    if gdf.crs is not None and gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(4326)
    pts = _geom_points(list(gdf.geometry))
    dates = list(gdf[ev.date_field]) if ev.date_field else [None] * len(gdf)
    return pts, dates


def _read_csv(path, ev) -> tuple[np.ndarray, list]:
    cols = [ev.lon_field, ev.lat_field] + ([ev.date_field] if ev.date_field else [])
    df = pd.read_csv(path, usecols=cols)
    pts = df[[ev.lon_field, ev.lat_field]].to_numpy(dtype=float)
    dates = list(df[ev.date_field]) if ev.date_field else [None] * len(df)
    return pts, dates


def _read_arcgis(ev, *, get: Callable = requests.get, page: int = 2000) -> tuple[np.ndarray, list]:
    """FeatureServer query: ONLY the date field + geometry (layers may hold contacts)."""
    cache = S.path("events", ev.event_id, "arcgis.geojson")
    if cache.exists():
        feats = json.loads(cache.read_text())["features"]
    else:
        feats, offset = [], 0
        while True:
            resp = get(ev.source, params={
                "where": ev.where, "outFields": ev.date_field or "", "returnGeometry": "true",
                "outSR": "4326", "f": "geojson", "resultOffset": offset,
                "resultRecordCount": page}, timeout=120)
            resp.raise_for_status()
            payload = resp.json()
            if "error" in payload:
                raise RuntimeError(f"{ev.event_id} ArcGIS query failed: {payload['error']}")
            got = payload.get("features") or []
            feats += [{"type": "Feature", "geometry": f.get("geometry"),
                       "properties": ({ev.date_field: (f.get("properties") or {}).get(ev.date_field)}
                                      if ev.date_field else {})} for f in got]
            more = payload.get("exceededTransferLimit") or (
                payload.get("properties") or {}).get("exceededTransferLimit")
            if not got or not more:
                break
            offset += len(got)
        cache.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    pts = _geom_points([f.get("geometry") for f in feats])
    dates = [(f.get("properties") or {}).get(ev.date_field) if ev.date_field else None
             for f in feats]
    return pts, dates


def load_event_inventory(ev, *, get: Callable = requests.get, seed: int = S.SEED) -> pd.DataFrame:
    """One TEST event (events.TestEvent) -> inventory rows (COLUMNS), deduped to
    90 m and capped at ``ev.max_points``. dating=fixed: every point gets ``ev.date``;
    per_point: each point keeps its own date, kept only inside the inventory window."""
    if ev.format == "arcgis":
        pts, raw_dates = _read_arcgis(ev, get=get)
    else:
        path = _resolve_source(ev, get=get)
        reader = {"geojson": _read_geojson, "vector": _read_vector, "csv": _read_csv}[ev.format]
        pts, raw_dates = reader(path, ev)
    n_raw = len(pts)
    if ev.dating == "per_point":
        dates = _parse_dates(raw_dates, ev.date_format)
        lo, hi = (pd.Timestamp(d) for d in ev.inventory_window)
        precision = "day"
    else:
        dates = pd.Series([pd.Timestamp(ev.date)] * n_raw, dtype="datetime64[ns]")
        lo = hi = None
        precision = "event-window"
    ok = np.isfinite(pts).all(axis=1) & dates.notna().to_numpy()
    ok &= (np.abs(pts[:, 0]) <= 180) & (np.abs(pts[:, 1]) <= 90)
    if lo is not None:
        ok &= ((dates >= lo) & (dates <= hi)).to_numpy()
    pts, dates = pts[ok], dates[ok].reset_index(drop=True)
    df = pd.DataFrame({
        "point_id": [f"{ev.event_id}_{i}" for i in range(len(pts))],
        "lon": pts[:, 0], "lat": pts[:, 1], "source": "event", "event_id": ev.event_id,
        "date": dates.astype("datetime64[s]"), "date_precision": precision, "acc_km": 0.0,
        "trigger": "rain", "title": ev.name,
    }, columns=COLUMNS)
    df = dedupe_grid(df)
    if len(df) > ev.max_points:
        df = df.sample(n=ev.max_points, random_state=seed).reset_index(drop=True)
    log.info("%s: %d raw features -> %d valid -> %d points (dedupe 90 m, cap %d)",
             ev.event_id, n_raw, int(ok.sum()), len(df), ev.max_points)
    return df


def leak_mask(glc: pd.DataFrame, test_pts: pd.DataFrame, windows: dict, *,
              days: int = S.LEAK_DAYS, km: float = S.LEAK_KM) -> tuple[np.ndarray, pd.DataFrame]:
    """GLC rows that may BE a TEST event: dated within ``days`` of the event's
    window and within ``km`` of any of its points. Returns (mask over ``glc``,
    report rows: event_id, glc point_id, date, km)."""
    mask = np.zeros(len(glc), bool)
    rep = []
    gd = pd.to_datetime(glc["date"]).to_numpy().astype("datetime64[D]")
    for ev, pts in test_pts.groupby("event_id"):
        if ev in windows:
            a, b = (np.datetime64(pd.Timestamp(x).date()) for x in windows[ev])
        else:
            d = pd.to_datetime(pts["date"]).dropna().to_numpy().astype("datetime64[D]")
            if not len(d):
                continue
            a, b = d.min(), d.max()
        near_t = (gd >= a - np.timedelta64(days, "D")) & (gd <= b + np.timedelta64(days, "D"))
        if not near_t.any():
            continue
        idx = np.flatnonzero(near_t)
        dist = min_dist_km(glc["lon"].to_numpy()[idx], glc["lat"].to_numpy()[idx],
                           pts["lon"].to_numpy(), pts["lat"].to_numpy())
        hit = idx[dist <= km]
        mask[hit] = True
        for i, dk in zip(hit, dist[dist <= km], strict=True):
            rep.append({"test_event": ev, "glc_point_id": glc["point_id"].iloc[i],
                        "glc_event_id": glc["event_id"].iloc[i],
                        "date": str(gd[i]), "km": round(float(dk), 1)})
    return mask, pd.DataFrame(rep, columns=["test_event", "glc_point_id", "glc_event_id", "date", "km"])
