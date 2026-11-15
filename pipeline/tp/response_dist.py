"""Landslide scar CANDIDATES from OPERA DIST-ALERT-HLS vegetation loss.

Product: OPERA_L3_DIST-ALERT-HLS_V1 (LP DAAC), 30 m MGRS tiles, one granule
per HLS acquisition carrying the running disturbance state of every pixel.
For each MGRS tile intersecting the bbox the newest granule acquired in
[start, end] is read (VEG-DIST-STATUS + VEG-DIST-DATE) and a pixel is a
candidate when ALL hold:

* VEG-DIST-STATUS in STATUS_KEEP — provisional / confirmed / finished
  disturbance with vegetation-cover anomaly >= 50 % (landslides strip the
  vegetation; first single detections are noisy and are left out);
* VEG-DIST-DATE (days since 2020-12-31) inside [start, end] — new in the
  window, not an older clearing;
* slope >= 15 deg (the T_slide floor, config thresholds.slope_range_deg),
  from a 1-arc-second NASADEM mask (tools/make_steep_mask.py);
* MCD12Q1 2025 LC_Type1 not cropland (12, 14) nor water (17) — harvests and
  ploughing are the commonest vegetation loss.

Candidate pixels are polygonized per tile (8-connected, UTM), kept between
MIN_PX pixels and MAX_KM2 (bigger patches are clear-cuts / fires, counted as
excluded), de-duplicated across overlapping MGRS tiles, reprojected to
EPSG:4326, and given people (GPW v4.11 2020 density at the centroid x area)
and municipality (point in polygon). Every feature is status "candidate"
— never confirmed.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

SHORT_NAME = "OPERA_L3_DIST-ALERT-HLS_V1"
CMR = "https://cmr.earthdata.nasa.gov/search/granules.json"
SOURCE = "opera-dist-alert-hls"
STATUS_KEEP = (5, 6, 8)          # provisional/confirmed/finished, anomaly >= 50 %
LC_DROP = (12, 14, 17)           # cropland, cropland mosaic, water
DATE_BASE = dt.date(2020, 12, 31)
MIN_PX = 3                       # 0.0027 km2 at 30 m
MAX_KM2 = 1.0                    # larger patches: clear-cut / fire, not a scar
PX_KM2 = 0.0009


def _get_json(url, params, session=None):
    import requests

    s = session or requests
    r = s.get(url, params=params, timeout=120)
    r.raise_for_status()
    return r.json()


def search(bounds, start: dt.date, end: dt.date, session=None) -> list[dict]:
    """All DIST-ALERT granules intersecting `bounds` acquired in [start, end]."""
    w, s, e, n = bounds
    out, page = [], 1
    while True:
        feed = _get_json(CMR, {
            "short_name": SHORT_NAME, "bounding_box": f"{w},{s},{e},{n}",
            "temporal": f"{start.isoformat()}T00:00:00Z,{end.isoformat()}T23:59:59Z",
            "page_size": 2000, "page_num": page}, session)["feed"]["entry"]
        for g in feed:
            title = g["title"]
            links = [lk["href"] for lk in g.get("links", [])
                     if lk["href"].startswith("https") and lk["href"].endswith("_VEG-DIST-STATUS.tif")]
            if not links:
                continue
            out.append({"id": title, "tile": title.split("_")[3], "acquired": g["time_start"],
                        "base": links[0][: -len("VEG-DIST-STATUS.tif")]})
        if len(feed) < 2000:
            return out
        page += 1


def latest_per_tile(granules: list[dict]) -> dict[str, dict]:
    best: dict[str, dict] = {}
    for g in granules:
        cur = best.get(g["tile"])
        if cur is None or (g["acquired"], g["id"]) > (cur["acquired"], cur["id"]):
            best[g["tile"]] = g
    return best


def _signed(url: str, token: str | None) -> str:
    """LP DAAC: resolve the 303 to the pre-signed URL (GDAL must not send the
    bearer header to S3/CloudFront)."""
    import requests

    if not token:
        return url
    r = requests.get(url, headers={"Authorization": f"Bearer {token}", "Range": "bytes=0-0"},
                     allow_redirects=False, timeout=60)
    if r.status_code in (301, 302, 303, 307, 308):
        loc = r.headers.get("Location", "")
        if "urs.earthdata.nasa.gov" in loc:
            raise RuntimeError("Earthdata auth rejected (redirect to login)")
        return loc
    if r.status_code in (200, 206):
        return url
    raise RuntimeError(f"DIST granule HTTP {r.status_code}")


def _read(path_or_url: str):
    import rasterio

    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"):
        src = path_or_url if not path_or_url.startswith("http") else "/vsicurl/" + path_or_url
        with rasterio.open(src) as ds:
            return ds.read(1), ds.transform, ds.crs


def _onto(src_path, shape, transform, crs, fill=255):
    """Reproject a uint8 raster onto the tile grid (nearest)."""
    import rasterio
    from rasterio.warp import Resampling, reproject

    out = np.full(shape, fill, np.uint8)
    with rasterio.open(src_path) as src:
        reproject(rasterio.band(src, 1), out, dst_transform=transform, dst_crs=crs,
                  resampling=Resampling.nearest, src_nodata=src.nodata, dst_nodata=fill)
    return out


def tile_candidates(g: dict, start: dt.date, end: dt.date, steep_path, lc_path,
                    token: str | None = None, reader=None) -> dict:
    """Candidate polygons (EPSG:4326 GeoJSON geometries) + gate counts for one tile."""
    from pyproj import Transformer
    from rasterio.features import shapes
    from shapely.geometry import mapping, shape
    from shapely.ops import transform as shp_transform

    read = reader or (lambda layer: _read(_signed(g["base"] + layer + ".tif", token)))
    status, t, crs = read("VEG-DIST-STATUS")
    date, _, _ = read("VEG-DIST-DATE")
    d0, d1 = (start - DATE_BASE).days, (end - DATE_BASE).days
    loss = np.isin(status, STATUS_KEEP) & (date >= d0) & (date <= d1)
    n_loss = int(loss.sum())
    steep = _onto(steep_path, status.shape, t, crs) == 1
    lc = _onto(lc_path, status.shape, t, crs)
    flat = loss & ~steep
    m = loss & steep
    crop = m & np.isin(lc, LC_DROP)
    m &= ~crop
    stats = {"tile": g["tile"], "granule": g["id"], "acquired": g["acquired"],
             "loss_px": n_loss, "flat_px": int(flat.sum()), "crop_px": int(crop.sum()),
             "kept_px": int(m.sum()), "too_large": 0, "too_small": 0}
    feats = []
    if m.any():
        to_ll = Transformer.from_crs(crs, "EPSG:4326", always_xy=True).transform
        lbl_date = np.where(m, date, 0).astype(np.int32)
        for geom, _ in shapes(m.astype(np.uint8), mask=m, transform=t, connectivity=8):
            poly = shape(geom)
            n_px = round(poly.area / (abs(t.a) * abs(t.e)))
            if n_px < MIN_PX:
                stats["too_small"] += 1
                continue
            km2 = n_px * abs(t.a) * abs(t.e) / 1e6
            if km2 > MAX_KM2:
                stats["too_large"] += 1
                continue
            c = poly.representative_point()
            r, cc = ~t * (c.x, c.y)
            r, cc = int(cc), int(r)
            first = int(lbl_date[min(max(r, 0), lbl_date.shape[0] - 1),
                                 min(max(cc, 0), lbl_date.shape[1] - 1)])
            ll = shp_transform(to_ll, poly)
            feats.append({"geometry": mapping(ll), "area_km2": round(km2, 4), "n_px": n_px,
                          "date": (DATE_BASE + dt.timedelta(days=first)).isoformat() if first > 0 else None})
    return {"stats": stats, "features": feats}


def dedupe(features: list[dict]) -> list[dict]:
    """Drop polygons that mostly overlap an already kept one (MGRS tile overlap)."""
    from shapely.geometry import shape
    from shapely.strtree import STRtree

    kept, geoms = [], []
    for f in sorted(features, key=lambda f: -f["area_km2"]):
        g = shape(f["geometry"])
        if geoms:
            tree = STRtree(geoms)
            hit = any(g.intersection(geoms[i]).area > 0.5 * min(g.area, geoms[i].area)
                      for i in tree.query(g))
            if hit:
                continue
        kept.append(f)
        geoms.append(g)
    return kept


def attach_people_and_municipality(features: list[dict], gpw_path, munis_fc: dict | None) -> None:
    """people = GPW density at the representative point x area; municipality by PIP."""
    import rasterio
    from shapely.geometry import shape
    from shapely.strtree import STRtree

    mgeoms, mprops = [], []
    for f in (munis_fc or {}).get("features") or []:
        try:
            mgeoms.append(shape(f["geometry"]))
            mprops.append(f.get("properties") or {})
        except Exception:  # noqa: BLE001
            continue
    tree = STRtree(mgeoms) if mgeoms else None
    pts = [shape(f["geometry"]).representative_point() for f in features]
    dens = [None] * len(features)
    if gpw_path and Path(gpw_path).exists():
        with rasterio.open(gpw_path) as src:
            vals = [v[0] for v in src.sample([(p.x, p.y) for p in pts])] if pts else []
            lat_res, lon_res = abs(src.res[1]), abs(src.res[0])
        for i, (p, v) in enumerate(zip(pts, vals)):
            if v is None or not np.isfinite(v) or v < 0:
                continue
            cell_km2 = (lat_res * 111.32) * (lon_res * 111.32 * np.cos(np.radians(p.y)))
            dens[i] = float(v) / cell_km2
    for f, p, d in zip(features, pts, dens):
        f["people"] = None if d is None else round(d * f["area_km2"], 1)
        f["municipality"] = f["code"] = f["uf"] = None
        if tree is not None:
            for i in tree.query(p):
                if mgeoms[i].contains(p):
                    f["municipality"] = mprops[i].get("name")
                    f["code"] = mprops[i].get("code")
                    f["uf"] = mprops[i].get("uf")
                    break


SIMPLIFY_DEG = 0.00015         # ~15 m: half a 30 m pixel, removes stair-step vertices
COORD_DECIMALS = 4             # ~11 m (pixels are 30 m)


def _compact(geom: dict) -> dict:
    """Simplify (topology-preserving) + round coordinates: keeps files small."""
    from shapely.geometry import mapping, shape

    g = shape(geom)
    s = g.simplify(SIMPLIFY_DEG, preserve_topology=True)
    g = s if not s.is_empty else g

    def rnd(c):
        return [rnd(x) for x in c] if isinstance(c[0], (list, tuple)) else [round(v, COORD_DECIMALS) for v in c]

    m = mapping(g)
    return {"type": m["type"], "coordinates": rnd(m["coordinates"])}


def to_feature_collection(features: list[dict], kind: str = "landslide_candidate") -> dict:
    out = []
    for f in features:
        props = {"class": 1, "kind": kind, "status": "candidate", "date": f.get("date"),
                 "area_km2": f["area_km2"], "people": f.get("people"),
                 "municipality": f.get("municipality")}
        props.update({k: f[k] for k in ("code", "uf") if f.get(k)})   # only when known
        # source/granule/tile live in the meta (and metrics) to keep the file small
        out.append({"type": "Feature", "geometry": _compact(f["geometry"]), "properties": props})
    return {"type": "FeatureCollection", "source": SOURCE, "status": "candidate", "features": out}


def run(bounds, start: dt.date, end: dt.date, steep_path, lc_path, *, gpw_path=None,
        munis_fc=None, granules=None, token=None, workers: int = 8, reader_for=None,
        log=print) -> tuple[dict, dict]:
    """-> (FeatureCollection, meta). `granules`/`reader_for` are test seams."""
    token = token if token is not None else os.environ.get("EARTHDATA_TOKEN")
    gs = granules if granules is not None else search(bounds, start, end)
    tiles = latest_per_tile(gs)
    log(f"dist: {len(gs)} granules, {len(tiles)} tiles, window {start}..{end}")

    def work(g):
        try:
            res = tile_candidates(g, start, end, steep_path, lc_path, token,
                                  reader=reader_for(g) if reader_for else None)
            for f in res["features"]:
                f["granule"], f["tile"] = g["id"], g["tile"]
            return res
        except Exception as e:  # noqa: BLE001 — one bad tile must not sink the run
            return {"stats": {"tile": g["tile"], "granule": g["id"], "error": f"{type(e).__name__}: {str(e)[:120]}"},
                    "features": []}

    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(work, tiles.values()))
    feats = dedupe([f for r in results for f in r["features"]])
    attach_people_and_municipality(feats, gpw_path, munis_fc)
    fc = to_feature_collection(feats)
    stats = [r["stats"] for r in results]
    failed = [s for s in stats if "error" in s]
    tot = lambda k: int(sum(s.get(k, 0) for s in stats))  # noqa: E731
    reasons = {}
    if failed:
        reasons["tiles_failed"] = f"{len(failed)} of {len(tiles)} tiles unreadable (e.g. {failed[0]['error']})"
    people = [f["properties"]["people"] for f in fc["features"]]
    meta = {
        "source": SOURCE, "status": "candidate",
        "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "window": [start.isoformat(), end.isoformat()],
        "latest_acquired": max((g["acquired"] for g in tiles.values()), default=None),
        "n_tiles": len(tiles), "n_tiles_failed": len(failed),
        "features": len(fc["features"]),
        "candidates_km2": round(sum(f["properties"]["area_km2"] for f in fc["features"]), 3),
        "people": round(sum(p for p in people if p is not None), 1) if any(p is not None for p in people) else None,
        "excluded": {"loss_km2_total": round(tot("loss_px") * PX_KM2, 3),
                     "flat_km2": round(tot("flat_px") * PX_KM2, 3),
                     "cropland_or_water_km2": round(tot("crop_px") * PX_KM2, 3),
                     "too_large_polygons": tot("too_large"), "too_small_polygons": tot("too_small")},
        "params": {"status_keep": list(STATUS_KEEP), "slope_min_deg": 15, "lc_drop": list(LC_DROP),
                   "min_px": MIN_PX, "max_km2": MAX_KM2,
                   "date_rule": "VEG-DIST-DATE within window (first detection)"},
        "people_source": "GPW v4.11 2020 30 arc-sec count (density at representative point x area)"
                         if gpw_path else None,
        "tiles": stats,
        "_null_reasons": reasons,
    }
    if meta["people"] is None:
        reasons["people"] = "GPW raster unavailable" if not gpw_path else "no candidates"
    return fc, meta


def hit_rates(points_lonlat: list[tuple[float, float]], fc: dict, radii_m=(100, 500),
              epsg: int = 32722) -> dict:
    """Inventory hit rate: share of points within r m of a candidate polygon;
    precision proxy: share of candidates within 500 m of an inventory point."""
    from pyproj import Transformer
    from shapely.geometry import Point, shape
    from shapely.ops import transform as shp_transform
    from shapely.strtree import STRtree

    to_m = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True).transform
    polys = [shp_transform(to_m, shape(f["geometry"])) for f in fc["features"]]
    pts = [Point(to_m(x, y)) for x, y in points_lonlat]
    out = {"n_points": len(pts), "n_candidates": len(polys)}
    if not polys or not pts:
        for r in radii_m:
            out[f"hit_{r}m_pct"] = None
        out["precision_500m_pct"] = None
        return out
    ptree = STRtree(polys)
    for r in radii_m:
        hits = sum(1 for p in pts if len(ptree.query(p, predicate="dwithin", distance=r)) > 0)
        out[f"hit_{r}m_pct"] = round(100 * hits / len(pts), 2)
    qtree = STRtree(pts)
    near = sum(1 for g in polys if len(qtree.query(g, predicate="dwithin", distance=500)) > 0)
    out["precision_500m_pct"] = round(100 * near / len(polys), 2)
    return out


def write_json(path: Path, obj) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")
    os.replace(tmp, path)
