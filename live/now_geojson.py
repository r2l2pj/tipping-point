"""Reference map layers for the now/ bundle (Command Center Now map).

- geojson/municipal_boundaries.geojson: IBGE municipality meshes
  (qualidade=minima) for the UFs intersecting the NOW bbox, REFERENCE-ONLY,
  same feature shape as the case bundles. Refetched when missing or older
  than 7 days.
- geojson/permanent_water.geojson: MODIS MCD12Q1 v061 LC_Type1 class 17
  (IGBP water bodies, 500 m) polygonised and simplified — the same static
  raster R1 (response DSWx) uses to drop permanent water, so the map shows
  exactly what the flood candidates exclude. Regenerated when missing or
  older than 30 days.

Priority zones are NOT produced here: R3 (pipeline/scripts/
15_response_summary.py, lead-analysis) owns them.

All EPSG:4326 GeoJSON (doc v4 CRS convention).
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np

from live.config import REPO_ROOT

LC_TYPE1 = REPO_ROOT / "data" / "now" / "static" / "mcd12q1" / "lc_type1_2025.tif"
IGBP_WATER = 17
WATER_SIMPLIFY_DEG = 0.002   # ~200 m, well under the 500 m source pixel
WATER_MIN_KM2 = 0.5          # drop single-pixel specks (one pixel ~0.21 km2)
BOUNDARIES_MAX_AGE = dt.timedelta(days=7)
WATER_MAX_AGE = dt.timedelta(days=30)

_NOW_UFS = ("RS", "SC", "PR", "SP")


def _stale(path: Path, max_age: dt.timedelta) -> bool:
    if not path.exists():
        return True
    age = dt.datetime.now(dt.UTC) - dt.datetime.fromtimestamp(
        path.stat().st_mtime, dt.UTC)
    return age > max_age


def ensure_municipal_boundaries(gj_dir: Path,
                                bounds: tuple[float, ...]) -> Path:
    """IBGE municipality meshes intersecting the now bbox (cached)."""
    out = gj_dir / "municipal_boundaries.geojson"
    if not _stale(out, BOUNDARIES_MAX_AGE):
        return out
    from shapely.geometry import box

    from pipeline.fetch.boundaries import _fetch_ibge, _write_feature_collection

    aoi = box(*bounds)
    features = []
    for uf in _NOW_UFS:
        try:
            features += _fetch_ibge(aoi, uf)
        except (OSError, ValueError, KeyError, RuntimeError) as exc:
            print(f"[now_geojson] IBGE {uf} failed: {exc}", file=sys.stderr)
    _write_feature_collection(features, out)
    return out


def ensure_permanent_water(gj_dir: Path, bounds: tuple[float, ...],
                           lc_path: Path = LC_TYPE1) -> Path | None:
    """MCD12Q1 IGBP class 17 within bounds -> permanent_water.geojson."""
    import rasterio
    import rasterio.features
    from rasterio.windows import from_bounds
    from shapely.geometry import box, mapping, shape

    out = gj_dir / "permanent_water.geojson"
    if not _stale(out, WATER_MAX_AGE):
        return out
    if not Path(lc_path).exists():
        print(f"[now_geojson] {lc_path} missing — permanent_water skipped",
              file=sys.stderr)
        return out if out.exists() else None
    with rasterio.open(lc_path) as ds:
        if ds.crs and ds.crs.to_epsg() != 4326:
            raise ValueError(f"{lc_path} must be EPSG:4326, got {ds.crs}")
        win = (from_bounds(*bounds, ds.transform).round_offsets()
               .round_lengths().intersection(
                   rasterio.windows.Window(0, 0, ds.width, ds.height)))
        arr = ds.read(1, window=win)
        transform = ds.window_transform(win)
    mask = arr == IGBP_WATER
    aoi = box(*bounds)
    feats = []
    for geom, _ in rasterio.features.shapes(
            mask.astype(np.uint8), mask=mask, transform=transform):
        g = shape(geom).intersection(aoi)
        lat = g.centroid.y if not g.is_empty else 0.0
        km2 = g.area * (111.195 ** 2) * np.cos(np.deg2rad(lat))
        if g.is_empty or km2 < WATER_MIN_KM2:
            continue
        g = g.simplify(WATER_SIMPLIFY_DEG, preserve_topology=True)
        feats.append({"type": "Feature", "geometry": mapping(g),
                      "properties": {"area_km2": round(float(km2), 2),
                                     "class": "permanent_water"}})
    gj_dir.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "type": "FeatureCollection",
        "source": "MODIS MCD12Q1 v061 LC_Type1 2025, IGBP class 17 "
                  "(water bodies), 500 m",
        "features": feats}, separators=(",", ":")) + "\n")
    print(f"[now_geojson] permanent_water: {len(feats)} polygons")
    return out


def municipality_lookup(gj_dir: Path, max_dist_deg: float = 0.1):
    """-> fn(lon, lat) -> {"municipality", "uf"} | None, from
    municipal_boundaries.geojson. A point outside every polygon snaps to
    the nearest municipality within max_dist_deg (~10 km, coastline and
    edge cases); farther away (Argentina/Uruguay/Paraguay/ocean) -> None."""
    import re

    from shapely import STRtree
    from shapely.geometry import Point, shape

    p = gj_dir / "municipal_boundaries.geojson"
    if not p.exists():
        return lambda lon, lat: None
    geoms, props = [], []
    for f in json.loads(p.read_text()).get("features", []):
        try:
            geoms.append(shape(f["geometry"]))
        except (KeyError, ValueError, TypeError):
            continue
        pr = f.get("properties") or {}
        m = re.search(r"estados/([A-Z]{2})", pr.get("url") or "")
        props.append({"municipality": pr.get("name"),
                      "uf": m.group(1) if m else None})
    if not geoms:
        return lambda lon, lat: None
    tree = STRtree(geoms)

    def lookup(lon: float, lat: float):
        pt = Point(lon, lat)
        hits = tree.query(pt, predicate="intersects")
        if len(hits):
            return dict(props[int(hits[0])])
        i = tree.nearest(pt)
        if i is not None and geoms[int(i)].distance(pt) <= max_dist_deg:
            return dict(props[int(i)])
        return None

    return lookup
