"""Response summary for the Now domain (unit R3, L27).

Inputs under data/now/:

* ``response/flood_dswx.geojson`` + ``dswx_meta.json`` (R1, OPERA DSWx-S1)
* ``response/flood_nisar.geojson`` + ``nisar_meta.json`` (R2, NISAR GCOV)
* ``geojson/critical_areas.geojson`` (Now TPI job — the "Critical TPI
  areas": class >= 2 features = Alto/Crítico, the live critical mask)
* ``summary.json`` (Now TPI job headline — merged in place, never rebuilt)
* ``static/gpw/gpw_popcount_2020_30sec.tif`` (GPW v4.11 2020 (unadjusted count)
  count raster, EPSG:4326, persons per 30 arc-second cell; gitignored —
  the now job / static step provides it). The ONLY people source.

Flood polygons may carry ``confidence: "standard" | "lower"`` (R1 marks
wetland/seasonal-water candidates "lower"); an absent property means
standard. Headline ``flooded_km2`` / ``people_flooded`` / exposure / zones
count standard confidence only; lower-confidence candidates not already
covered by a standard one are reported separately
(``flooded_km2_lower`` / ``people_flooded_lower``).

Areas vs people:

* Every ``*_km2`` is a GEODESIC polygon area (shapely union of the
  candidates -> pyproj.Geod on WGS84) — no raster inflation; overlapping
  sources are unioned before measuring, so nothing is double-counted.
* People: GPW v4.11 2020 population count (unadjusted) on its NATIVE 30"
  grid, each cell weighted by the exact fraction covered by the mapped
  polygons (``people_geom``); totals and municipality zones alike. The
  centre-sampled ``res``-deg grid is only a legacy fallback (tests).

Outputs: ``response/summary.json`` (headline + per-source blocks with
latest_acquired / latency_h copied from each meta + ``_null_reasons``),
``response/priority_zones.geojson`` and the app copy
``geojson/priority_zones.geojson`` (municipalities ranked by exposed
people then area; props ``name, code, uf, people_flooded, people_critical,
flooded_km2, critical_km2, rank, sources, status: "candidate"`` plus the
app-facing ``tpi, class, hazard, area_km2, people, label_lon, label_lat``),
``people_exposed`` / ``people_critical`` / ``response`` merged into
``summary.json`` without dropping keys, and ``response/manifest_node.json``
(the fragment lead-live merges into ``data/now/manifest.json``).

Missing input -> ``null`` + ``_null_reasons`` (never 0, never synthetic).
Peak RAM < 1.5 GB: the grid is ~18 MPx (a few bool/int32/float32 arrays).
"""
from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

ANALYSIS_RES_DEG = 0.0025           # same posting as the DSWx-S1 grid (~280 m)
GPW_NOW_REL = Path("static/gpw/gpw_popcount_2020_30sec.tif")   # under data/now/
GPW_LABEL = "GPW v4.11 2020 population count (unadjusted), 30 arc-sec"
# IBGE UFs intersecting the Now domain bbox (RS/SC/PR required; SP/MS corner).
UFS_DEFAULT = ("RS", "SC", "PR", "SP", "MS")
UF_CODE = {"41": "PR", "42": "SC", "43": "RS", "35": "SP", "50": "MS"}

SOURCE_DSWX = "opera-dswx-s1"
SOURCE_NISAR = "nisar-gcov"
SOURCE_CRIT = "tpi-critical"
# (short key, source id, geojson name, meta name)
FLOOD_SOURCES = (
    ("dswx", SOURCE_DSWX, "flood_dswx.geojson", "dswx_meta.json"),
    ("nisar", SOURCE_NISAR, "flood_nisar.geojson", "nisar_meta.json"),
)
CRIT_GEOJSON = Path("geojson") / "critical_areas.geojson"
ZONES_GEOJSON = Path("geojson") / "priority_zones.geojson"
MUNIS_NOW_REL = Path("static/ibge/municipalities_now.geojson")   # under data/now/ (static-v1)
CRIT_CLASS_CRITICAL = 3             # class 3 = "Crítico"; 2 = "Alto"
CLASS_TPI_FLOOR = {3: 0.6, 2: 0.4}  # TPI_CUTS lower edge of the class
KIND_HAZARD = {"slide": "landslide", "flood": "flood"}
ZONE_SIMPLIFY_DEG = 0.005           # ~500 m — keeps the file small


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_or_none(v) -> str | None:
    """Normalise an ISO timestamp string to ``YYYY-MM-DDTHH:MM:SSZ``."""
    if not isinstance(v, str) or not v.strip():
        return None
    try:
        dt = datetime.fromisoformat(v.strip())
    except ValueError:
        return v
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return iso(dt)


def num_or_none(v) -> float | None:
    """Finite float from a JSON value (int/float/numeric string), else None."""
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def load_json(path: Path) -> dict | None:
    path = Path(path)
    if not path.is_file():
        return None
    try:
        v = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return v if isinstance(v, dict) else None


def _atomic_write(path: Path, text: str) -> Path:
    """Write via a temp file + rename; the result is 0644 (mkstemp gives 0600)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


def _write_fc(path: Path, features: list[dict]) -> Path:
    return _atomic_write(path, json.dumps(
        {"type": "FeatureCollection", "features": features}, allow_nan=False) + "\n")


def _write_json(path: Path, obj: dict) -> Path:
    return _atomic_write(path, json.dumps(obj, indent=2, allow_nan=False) + "\n")


def is_lower(props: dict) -> bool:
    """True for a lower-confidence candidate; absent property = standard."""
    return str((props or {}).get("confidence") or "standard").strip().lower() == "lower"


# ---------------------------------------------------------------------------
# geodesic areas
# ---------------------------------------------------------------------------
def _polygons(geom) -> list:
    if geom is None or geom.is_empty:
        return []
    if geom.geom_type == "Polygon":
        return [geom]
    if hasattr(geom, "geoms"):
        return [p for g in geom.geoms for p in _polygons(g)]
    return []                        # points/lines carry no area


def geodesic_km2(geom) -> float:
    """WGS84 geodesic area (km2) of the polygonal parts of `geom`.

    Each part is oriented CCW first so pyproj's signed area never cancels
    across parts of a MultiPolygon.
    """
    from pyproj import Geod
    from shapely.geometry.polygon import orient

    geod = Geod(ellps="WGS84")
    total = 0.0
    for p in _polygons(geom):
        a, _ = geod.geometry_area_perimeter(orient(p, 1.0))
        total += abs(a)
    return total / 1e6


def km2(geom) -> float | None:
    """Rounded geodesic km2; None for an absent input (not for an empty one)."""
    return None if geom is None else round(geodesic_km2(geom), 3)


def union_of(geoms: list):
    """Valid union of `geoms` (an empty Polygon for an empty list)."""
    import shapely
    from shapely.geometry import Polygon

    if not geoms:
        return Polygon()
    return shapely.union_all([shapely.make_valid(g) for g in geoms])


def _union2(a, b):
    """Union of two optional geometries (None = absent)."""
    if a is None:
        return b
    if b is None:
        return a
    return a.union(b)


# ---------------------------------------------------------------------------
# analysis grid + GPW (people only)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Grid:
    """North-up EPSG:4326 grid over the domain (west, south, east, north)."""

    west: float
    south: float
    east: float
    north: float
    res: float

    @property
    def shape(self) -> tuple[int, int]:
        return (round((self.north - self.south) / self.res),
                round((self.east - self.west) / self.res))

    @property
    def transform(self):
        from rasterio.transform import from_origin

        return from_origin(self.west, self.north, self.res, self.res)


def mask_of(geoms: list, grid: Grid) -> np.ndarray:
    """Centre-sampled (all_touched=False) bool mask of `geoms` on `grid`.

    Used for people sums only — areas come from geodesic_km2.
    """
    from rasterio.features import rasterize

    if not geoms:
        return np.zeros(grid.shape, bool)
    return rasterize(geoms, out_shape=grid.shape, transform=grid.transform,
                     fill=0, dtype="uint8", all_touched=False).astype(bool)


def load_gpw_pop(src: str | Path, grid: Grid) -> np.ndarray:
    """GPW count raster reprojected to `grid` as people-per-analysis-pixel.

    A pixel smaller than a GPW cell gets the cell count scaled by the area
    ratio (Resampling.nearest × ratio); a coarser pixel sums cells
    (Resampling.sum) — same convention as tp.response_dswx.
    """
    import rasterio
    from rasterio.warp import Resampling, reproject

    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"), rasterio.open(src) as ds:
        if ds.crs is None or not ds.crs.is_geographic:
            raise ValueError("GPW raster must be geographic (EPSG:4326)")
        ratio = (grid.res / abs(ds.res[0])) * (grid.res / abs(ds.res[1]))
        band = ds.read(1).astype(np.float32)
        if ds.nodata is not None and not np.isnan(ds.nodata):
            band = np.where(band == ds.nodata, np.nan, band)
        band = np.nan_to_num(band, nan=0.0)
        band[band < 0] = 0.0
        dst = np.zeros(grid.shape, np.float32)
        reproject(band, dst, src_transform=ds.transform, src_crs=ds.crs,
                  dst_transform=grid.transform, dst_crs="EPSG:4326",
                  resampling=Resampling.nearest if ratio < 1 else Resampling.sum)
    return dst * ratio if ratio < 1 else dst


def people_in(mask: np.ndarray | None, pop: np.ndarray | None) -> int | None:
    """GPW people inside `mask`; None when either input is absent."""
    if mask is None or pop is None:
        return None
    return round(float(pop[mask].sum()))


PEOPLE_METHOD = ("GPW v4.11 2020 population count (unadjusted) on its native 30 arc-sec grid, each cell "
                 "weighted by the fraction of its area covered by the mapped polygons "
                 "(exact polygon-cell intersection); unions deduplicate overlaps")


def load_gpw_native(src: str | Path):
    """(count array float64 >= 0, affine) of the GPW raster on its native grid."""
    import rasterio

    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"), rasterio.open(src) as ds:
        if ds.crs is None or not ds.crs.is_geographic:
            raise ValueError("GPW raster must be geographic (EPSG:4326)")
        band = ds.read(1).astype(np.float64)
        if ds.nodata is not None and not np.isnan(ds.nodata):
            band = np.where(band == ds.nodata, np.nan, band)
        band = np.nan_to_num(band, nan=0.0)
        band[band < 0] = 0.0
        return band, ds.transform


def people_geom(geom, gpw) -> float | None:
    """GPW people inside `geom` (lon/lat polygons), area-weighted on the native
    GPW grid: sum over cells of count × area(cell ∩ geom) / area(cell). None
    when GPW is absent; 0 for an empty/absent geometry."""
    import shapely

    if gpw is None:
        return None
    if geom is None or geom.is_empty:
        return 0.0
    arr, tr = gpw
    a, e = tr.a, tr.e                       # e < 0 (north-up)
    cell = abs(a * e)
    total = 0.0
    for part in getattr(geom, "geoms", [geom]):
        if part.is_empty or part.area == 0:
            continue
        minx, miny, maxx, maxy = part.bounds
        c0 = max(int(np.floor((minx - tr.c) / a)), 0)
        c1 = min(int(np.floor((maxx - tr.c) / a)), arr.shape[1] - 1)
        r0 = max(int(np.floor((maxy - tr.f) / e)), 0)
        r1 = min(int(np.floor((miny - tr.f) / e)), arr.shape[0] - 1)
        if c1 < c0 or r1 < r0:
            continue
        rr, cc = np.mgrid[r0:r1 + 1, c0:c1 + 1]
        rr, cc = rr.ravel(), cc.ravel()
        cnt = arr[rr, cc]
        keep = cnt > 0
        if not keep.any():
            continue
        rr, cc, cnt = rr[keep], cc[keep], cnt[keep]
        x0 = tr.c + cc * a
        y0 = tr.f + rr * e
        boxes = shapely.box(x0, y0 + e, x0 + a, y0)
        frac = shapely.area(shapely.intersection(boxes, part)) / cell
        total += float((np.clip(frac, 0, 1) * cnt).sum())
    return total


def _people(geom, mask, pop, gpw) -> int | None:
    """Area-weighted native-grid count when the native GPW is loaded; else the
    legacy centre-sampled grid sum (tests / no native raster)."""
    if mask is None:                 # input absent -> null (as before)
        return None
    if gpw is not None:
        v = people_geom(geom, gpw)
        return None if v is None else round(v)
    return people_in(mask, pop)


# ---------------------------------------------------------------------------
# GeoJSON loading
# ---------------------------------------------------------------------------
def fc_geoms(fc: dict | None) -> tuple[list, list[dict]]:
    """(shapely geometries, properties) of a FeatureCollection; skips bad geoms."""
    from shapely.geometry import shape

    geoms, props = [], []
    for f in (fc or {}).get("features") or []:
        try:
            g = shape(f.get("geometry"))
            ok = not g.is_empty
        except Exception:  # noqa: BLE001 — malformed geometry is skipped, not fatal
            ok = False
        if not ok:
            continue
        geoms.append(g)
        props.append(f.get("properties") or {})
    return geoms, props


# ---------------------------------------------------------------------------
# municipal boundaries (IBGE malhas, minima quality + intrarregiao=municipio)
# ---------------------------------------------------------------------------
def ibge_municipalities(bbox, ufs=UFS_DEFAULT, get_json=None) -> dict:
    """FeatureCollection of IBGE municipalities intersecting `bbox`.

    Props: {name, code, uf, source, license, url}. `get_json(url, **params)`
    is the injectable HTTP seam (tests stay hermetic). A fetch failure
    raises — the caller degrades zones to an empty FC + _null_reasons.
    """
    from fetch.boundaries import IBGE_MALHAS_URL, IBGE_MUNICIPIOS_URL, SOURCES, intersects_aoi
    from shapely.geometry import box

    if get_json is None:
        def get_json(url, **params):
            import requests

            r = requests.get(url, params=params, timeout=120)
            r.raise_for_status()
            return r.json()

    aoi = box(*bbox)
    features: list[dict] = []
    for uf in ufs:
        malhas_url = IBGE_MALHAS_URL.format(uf=uf)
        malhas = get_json(malhas_url, formato="application/vnd.geo+json",
                          qualidade="minima", intrarregiao="municipio")
        municipios = get_json(IBGE_MUNICIPIOS_URL.format(uf=uf))
        names = {str(m["id"]): m["nome"] for m in municipios}
        for f in malhas.get("features", []):
            if not intersects_aoi(f, aoi):
                continue
            code = str((f.get("properties") or {}).get("codarea", ""))
            geom = f.get("geometry")
            if geom is None:
                continue
            features.append({
                "type": "Feature",
                "geometry": geom,
                "properties": {
                    "name": names.get(code, code),
                    "code": code,
                    "uf": UF_CODE.get(code[:2], uf),
                    "source": "IBGE",
                    "license": SOURCES["IBGE"]["license"],
                    "url": malhas_url,
                },
            })
    return {"type": "FeatureCollection", "features": features}


# ---------------------------------------------------------------------------
# priority zones
# ---------------------------------------------------------------------------
def _clip_km2(muni, geom) -> float:
    """Geodesic km2 of muni ∩ geom (0 for absent/disjoint)."""
    if geom is None or geom.is_empty or not muni.intersects(geom):
        return 0.0
    return geodesic_km2(muni.intersection(geom))


def zone_features(boundaries_fc: dict | None, grid: Grid,
                  unions: dict, masks: dict, pop: np.ndarray | None,
                  crit_geoms: list | None = None,
                  crit_props: list[dict] | None = None, gpw=None) -> list[dict]:
    """Ranked municipality features for priority_zones.geojson.

    `unions` (shapely, standard confidence): dswx, nisar, flood, flood_lower
    (lower-confidence not covered by standard), crit, crit3, exposed.
    Membership and every km2 are geodesic polygon intersections; people
    come from `masks` (same keys, centre-sampled) × a centre-sampled
    municipality label raster.
    """
    import shapely
    from rasterio.features import rasterize
    from shapely.geometry import mapping

    geoms, props = fc_geoms(boundaries_fc)
    exposed = unions.get("exposed")
    if not geoms or exposed is None or exposed.is_empty:
        return []
    shapely.prepare(exposed)
    geoms = [shapely.make_valid(g) for g in geoms]

    rows = []
    for i, g in enumerate(geoms):
        if not g.intersects(exposed):
            continue
        a_exp = _clip_km2(g, exposed)
        if a_exp <= 0:
            continue                 # touches the boundary only
        rows.append({
            "i": i, "a_exp": a_exp,
            "a_flood": _clip_km2(g, unions.get("flood")),
            "a_flood_lower": _clip_km2(g, unions.get("flood_lower")),
            "a_crit": _clip_km2(g, unions.get("crit")),
            "a_dswx": _clip_km2(g, unions.get("dswx")),
            "a_nisar": _clip_km2(g, unions.get("nisar")),
            "a_crit3": _clip_km2(g, unions.get("crit3")),
        })
    if not rows:
        return []

    # people: centre-sampled label raster of the member municipalities only
    p_by = {}
    if gpw is not None:                  # area-weighted on the native GPW grid
        n = len(rows) + 1
        for key in ("flood", "flood_lower", "crit", "crit3", "exposed"):
            u = unions.get(key)
            if masks.get(key) is None:
                p_by[key] = None
                continue
            if u is None:
                p_by[key] = np.zeros(n)
                continue
            arr = np.zeros(n)
            for k, r in enumerate(rows):
                g = geoms[r["i"]]
                if not u.is_empty and g.intersects(u):
                    arr[k + 1] = people_geom(g.intersection(u), gpw)
            p_by[key] = arr
    elif pop is not None:
        labels = rasterize(((geoms[r["i"]], k + 1) for k, r in enumerate(rows)),
                           out_shape=grid.shape, transform=grid.transform,
                           fill=0, dtype="int32", all_touched=False)
        n = len(rows) + 1
        sel = labels > 0
        for key in ("flood", "flood_lower", "crit", "crit3", "exposed"):
            m = masks.get(key)
            if m is None:
                p_by[key] = None
                continue
            mm = m & sel
            p_by[key] = np.bincount(labels[mm], weights=pop[mm], minlength=n)
        del labels, sel

    def _ppl(key, k):
        arr = p_by.get(key)
        return None if arr is None else round(float(arr[k + 1]))

    for k, r in enumerate(rows):
        r["p_exp"] = _ppl("exposed", k)
        r["k"] = k

    rows.sort(key=lambda r: (
        -(r["p_exp"] if r["p_exp"] is not None else -1.0),
        -r["a_exp"], props[r["i"]].get("code") or ""))

    feats = []
    for rank, r in enumerate(rows, start=1):
        i, k = r["i"], r["k"]
        g = geoms[i]
        p = dict(props[i])
        geom = g.simplify(ZONE_SIMPLIFY_DEG, preserve_topology=True)
        if geom.is_empty:
            geom = g
        pt = geom.representative_point()
        srcs = ([SOURCE_DSWX] if r["a_dswx"] > 0 else []) + \
               ([SOURCE_NISAR] if r["a_nisar"] > 0 else []) + \
               ([SOURCE_CRIT] if r["a_crit"] > 0 else [])
        hz: set[str] = set()
        for cg, cp in zip(crit_geoms or [], crit_props or [], strict=False):
            hazard = KIND_HAZARD.get(str(cp.get("kind")))
            if hazard and _clip_km2(g, cg) > 0:
                hz.add(hazard)
        if r["a_flood"] > 0:
            hz.add("flood")
        cls = "Critical" if r["a_crit3"] > 0 else "High"
        tpi = CLASS_TPI_FLOOR[3] if r["a_crit3"] > 0 else (
            CLASS_TPI_FLOOR[2] if r["a_crit"] > 0 else None)
        p.update({
            "code": p.get("code"), "uf": p.get("uf"),
            "people_flooded": _ppl("flood", k),
            "people_flooded_lower": _ppl("flood_lower", k),
            # A8: "critical" = Crítico (TPI > 0.6) only; Alto+ is "alert"
            "people_critical": _ppl("crit3", k),
            "people_alert": _ppl("crit", k),
            "flooded_km2": round(r["a_flood"], 3),
            "flooded_km2_lower": round(r["a_flood_lower"], 3),
            "critical_km2": round(r["a_crit3"], 3),
            "alert_km2": round(r["a_crit"], 3),
            "rank": rank, "sources": srcs, "status": "candidate",
            "tpi": tpi, "class": cls,
            "hazard": "+".join(sorted(hz)) if hz else "flood",
            "area_km2": round(r["a_exp"], 3),
            "people": r["p_exp"],
            "label_lon": round(float(pt.x), 5),
            "label_lat": round(float(pt.y), 5),
        })
        feats.append({"type": "Feature",
                      "geometry": mapping(shapely.set_precision(geom, 1e-5)),
                      "properties": p})
    return feats


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------
def _load_flood_source(key: str, sid: str, geo_name: str, meta_name: str,
                       resp_dir: Path, grid: Grid, reasons: dict) -> dict:
    fc = load_json(resp_dir / geo_name)
    meta = load_json(resp_dir / meta_name)
    if fc is None:
        reasons[f"flood_{key}"] = f"response/{geo_name} missing — {sid} candidates absent"
        return {"sid": sid, "fc": None, "meta": meta, "n": None, "n_lower": None,
                "u_std": None, "u_low": None, "m_std": None, "m_low": None}
    geoms, props = fc_geoms(fc)
    std = [g for g, p in zip(geoms, props, strict=True) if not is_lower(p)]
    low = [g for g, p in zip(geoms, props, strict=True) if is_lower(p)]
    return {"sid": sid, "fc": fc, "meta": meta, "n": len(geoms), "n_lower": len(low),
            "u_std": union_of(std), "u_low": union_of(low),
            "m_std": mask_of(std, grid), "m_low": mask_of(low, grid)}


def _source_block(key: str, src: dict, pop: np.ndarray | None,
                  reasons: dict, gpw=None) -> dict:
    """Per-source block: geodesic areas, GPW people, meta timing copied."""
    meta = src["meta"]
    m = meta or {}
    present = src["fc"] is not None
    u_low_only = (src["u_low"].difference(src["u_std"]) if present else None)
    m_low_only = (src["m_low"] & ~src["m_std"] if present else None)
    block = {
        "geojson": f"response/flood_{key}.geojson" if present else None,
        "meta": f"response/{key}_meta.json" if meta is not None else None,
        "features": src["n"],
        "features_lower": src["n_lower"],
        "flooded_km2": km2(src["u_std"]),
        "flooded_km2_lower": km2(u_low_only),
        "people_flooded": _people(src["u_std"], src["m_std"], pop, gpw),
        "people_flooded_lower": _people(u_low_only, m_low_only, pop, gpw),
        "meta_flooded_km2": num_or_none(m.get("flooded_km2")),
        "latest_acquired": iso_or_none(m.get("latest_acquired")),
        "latency_h": num_or_none(m.get("latency_h")),
        "n_granules": m.get("n_granules"),
        "new_km2": num_or_none(m.get("new_km2")),
        "receded_km2": num_or_none(m.get("receded_km2")),
        "status": ("ok" if src["n"] else "empty") if present else "missing",
    }
    meta_reasons = m.get("_null_reasons") if isinstance(m.get("_null_reasons"), dict) else {}
    for fld in ("latest_acquired", "latency_h"):
        if block[fld] is None:
            if meta is None:
                why = f"response/{key}_meta.json missing"
            else:
                why = meta_reasons.get(fld) or f"{fld} absent in {key}_meta.json"
            reasons[f"{src['sid']}.{fld}"] = why
    return block


def _nisar_block(meta: dict | None) -> dict | None:
    """Latest processed pair -> {granule ids, dates, track, frame, look}."""
    pairs = (meta or {}).get("pairs") or []
    if not pairs:
        return None
    p = max(pairs, key=lambda x: x.get("post_acquired") or "")
    return {"pre_granule": p.get("pre"), "post_granule": p.get("post"),
            "pre_acquired": iso_or_none(p.get("pre_acquired")),
            "post_acquired": iso_or_none(p.get("post_acquired")),
            "track": p.get("track"), "look": p.get("look"), "frame": p.get("frame")}


def _gpw_rel(src: Path, now_dir: Path) -> str:
    """Repo-relative path of the GPW raster (basename when outside data/now)."""
    try:
        return (Path("data/now") / Path(src).resolve()
                .relative_to(Path(now_dir).resolve())).as_posix()
    except ValueError:
        return Path(src).name


def people_label(src: Path, now_dir: Path) -> str:
    """Human label for the people source — never a local absolute path."""
    return f"{GPW_LABEL} ({_gpw_rel(src, now_dir)})"


def run_summary(bbox, now_dir: Path, *, load_boundaries=None,
                gpw_src: str | Path | None = None, res: float = ANALYSIS_RES_DEG,
                now: datetime | None = None, log=print) -> dict:
    """Union flood + Critical TPI, count people, rank municipalities, write outputs.

    `load_boundaries(bbox) -> FeatureCollection` is the boundary seam —
    the script passes ibge_municipalities; tests pass a synthetic FC; None
    skips zones (empty FC + reason). `gpw_src` defaults to
    ``<now_dir>/static/gpw/gpw_popcount_2020_30sec.tif`` (tests override).
    Writes under `now_dir`:

      response/priority_zones.geojson  geojson/priority_zones.geojson
      response/summary.json            summary.json (merged in place)
      response/manifest_node.json      (for lead-live -> manifest.json)

    Returns the response summary dict (== response/summary.json content).
    """
    import shapely

    now = now or datetime.now(UTC)
    now_dir = Path(now_dir)
    resp_dir = now_dir / "response"
    resp_dir.mkdir(parents=True, exist_ok=True)
    grid = Grid(float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]), res)
    reasons: dict[str, str] = {}

    # ---- inputs -----------------------------------------------------------
    floods = {key: _load_flood_source(key, sid, geo, meta, resp_dir, grid, reasons)
              for key, sid, geo, meta in FLOOD_SOURCES}
    crit_fc = load_json(now_dir / CRIT_GEOJSON)
    if crit_fc is None:
        reasons["critical_areas"] = ("geojson/critical_areas.geojson missing — "
                                     "Critical TPI areas absent")
    crit_geoms, crit_props = fc_geoms(crit_fc)
    crit3_geoms = [g for g, p in zip(crit_geoms, crit_props, strict=True)
                   if p.get("class") == CRIT_CLASS_CRITICAL]

    # ---- geodesic unions (areas) ------------------------------------------
    present = [f for f in floods.values() if f["fc"] is not None]
    u_flood = u_low_any = None
    for f in present:
        u_flood = _union2(u_flood, f["u_std"])
        u_low_any = _union2(u_low_any, f["u_low"])
    u_flood_lower = u_low_any.difference(u_flood) if present else None
    u_crit = union_of(crit_geoms) if crit_fc is not None else None
    u_crit3 = union_of(crit3_geoms) if crit_fc is not None else None
    u_exposed = _union2(u_flood, u_crit)
    unions = {"dswx": floods["dswx"]["u_std"], "nisar": floods["nisar"]["u_std"],
              "flood": u_flood, "flood_lower": u_flood_lower,
              "crit": u_crit, "crit3": u_crit3, "exposed": u_exposed}

    flooded_km2 = km2(u_flood)
    flooded_km2_lower = km2(u_flood_lower)
    critical_km2 = km2(u_crit3)          # A8: Crítico only
    alert_km2 = km2(u_crit)              # Alto or Crítico
    exposed_km2 = km2(u_exposed)
    d_u, n_u = floods["dswx"]["u_std"], floods["nisar"]["u_std"]
    overlap_km2 = (km2(shapely.intersection(d_u, n_u))
                   if d_u is not None and n_u is not None else None)

    # ---- centre-sampled masks (people) -------------------------------------
    m_flood = m_low_any = None
    for f in present:
        m_flood = f["m_std"] if m_flood is None else (m_flood | f["m_std"])
        m_low_any = f["m_low"] if m_low_any is None else (m_low_any | f["m_low"])
    m_flood_lower = (m_low_any & ~m_flood) if present else None
    m_crit = mask_of(crit_geoms, grid) if crit_fc is not None else None
    m_crit3 = mask_of(crit3_geoms, grid) if crit_fc is not None else None
    if m_flood is not None and m_crit is not None:
        m_exposed = m_flood | m_crit
    else:
        m_exposed = m_flood if m_flood is not None else m_crit
    masks = {"flood": m_flood, "flood_lower": m_flood_lower,
             "crit": m_crit, "crit3": m_crit3, "exposed": m_exposed}

    # ---- people (GPW now-static raster only) -------------------------------
    pop = None
    gpw = None
    gpw_path = Path(gpw_src) if gpw_src else now_dir / GPW_NOW_REL
    people_src = None
    if not gpw_path.is_file():
        reasons["people"] = (f"GPW raster {_gpw_rel(gpw_path, now_dir)} missing; "
                             "people_* null")
    else:
        try:
            pop = load_gpw_pop(gpw_path, grid)
            gpw = load_gpw_native(gpw_path)
            people_src = people_label(gpw_path, now_dir)
        except Exception as e:  # noqa: BLE001 — GPW optional: people -> null + reason
            reasons["people"] = f"GPW unreadable ({type(e).__name__}); people_* null"

    people_flooded = _people(u_flood, m_flood, pop, gpw)
    people_flooded_lower = _people(u_flood_lower, m_flood_lower, pop, gpw)
    people_critical = _people(u_crit3, m_crit3, pop, gpw)   # A8: Crítico only
    people_alert = _people(u_crit, m_crit, pop, gpw)        # Alto or Crítico
    people_exposed = _people(u_exposed, m_exposed, pop, gpw)
    if m_flood is None:
        reasons.setdefault("people_flooded", "no flood-candidate inputs present")
    if m_crit is None:
        reasons.setdefault("people_critical", "critical_areas.geojson missing")
    if m_exposed is None:
        reasons.setdefault("people_exposed", "no flood or critical input present")

    # ---- priority zones -----------------------------------------------------
    boundaries_fc = None
    if load_boundaries is not None:
        try:
            boundaries_fc = load_boundaries(tuple(bbox))
        except Exception as e:  # noqa: BLE001 — boundary fetch must not sink the job
            reasons["priority_zones"] = (f"municipal boundaries unavailable "
                                         f"({type(e).__name__}: {str(e)[:120]})")
    else:
        reasons["priority_zones"] = "municipal boundaries not provided"
    if boundaries_fc is not None and not (boundaries_fc.get("features") or []):
        reasons["priority_zones"] = "municipal boundaries empty (0 features): zones not computed"
    zones = zone_features(boundaries_fc, grid, unions, masks, pop, gpw=gpw,
                          crit_geoms=crit_geoms, crit_props=crit_props)
    _write_fc(resp_dir / "priority_zones.geojson", zones)
    _write_fc(now_dir / ZONES_GEOJSON, zones)

    # ---- response/summary.json --------------------------------------------
    sources = {}
    for key, sid, _, _ in FLOOD_SOURCES:
        sources[sid] = _source_block(key, floods[key], pop, reasons, gpw)
    crit_days = sorted({str(p.get("day")) for p in crit_props if p.get("day")})
    crit_classes: dict[str, int] = {}
    for p in crit_props:
        lbl = str(p.get("label", p.get("class", "?")))
        crit_classes[lbl] = crit_classes.get(lbl, 0) + 1
    sources[SOURCE_CRIT] = {
        "geojson": str(CRIT_GEOJSON) if crit_fc is not None else None,
        "features": len(crit_geoms) if crit_fc is not None else None,
        "days": crit_days, "classes": crit_classes,
        "critical_km2": critical_km2,
        "alert_km2": alert_km2,
        "people_critical": people_critical,
        "people_alert": people_alert,
        "status": "ok" if crit_geoms else ("empty" if crit_fc is not None else "missing"),
    }
    nisar = _nisar_block(floods["nisar"]["meta"])

    summary = {
        "schema": "tipping-point/response@1",
        "generated_at": iso(now),
        "bbox": [float(x) for x in bbox],
        "people_flooded": people_flooded,
        "people_flooded_lower": people_flooded_lower,
        "people_critical": people_critical,
        "people_alert": people_alert,
        "people_exposed": people_exposed,
        "flooded_km2": flooded_km2,
        "flooded_km2_lower": flooded_km2_lower,
        "critical_km2": critical_km2,
        "alert_km2": alert_km2,
        "critical_km2_def": "Crítico only (TPI > 0.6, A8); alert_km2 = Alto or Crítico (TPI > 0.4)",
        "exposed_km2": exposed_km2,
        "overlap_km2": overlap_km2,
        "sources": sources,
        "nisar": nisar,
        "priority_zones": {"geojson": str(ZONES_GEOJSON),
                           "municipalities": len(zones)},
        "people_source": people_src,
        "people_method": PEOPLE_METHOD if people_src else None,
        "method": {
            "area": "geodesic polygon area (shapely union, pyproj.Geod WGS84)",
            "people": PEOPLE_METHOD,
            "confidence": ("headline = standard confidence (absent = standard); "
                           "*_lower = lower-confidence not covered by standard"),
        },
        "res_deg": res,
        "_null_reasons": reasons,
    }
    _write_json(resp_dir / "summary.json", summary)

    # ---- merge into data/now/summary.json (never drop existing keys) -------
    data_dates: dict[str, str] = {}
    if nisar is not None:
        if nisar.get("pre_acquired"):
            data_dates["nisar_pre"] = nisar["pre_acquired"][:10]
        if nisar.get("post_acquired"):
            data_dates["nisar_post"] = nisar["post_acquired"][:10]
    for sid, blk in sources.items():
        if blk.get("latest_acquired"):
            data_dates[sid] = blk["latest_acquired"]
    if crit_days:
        data_dates["tpi_critical"] = crit_days[-1]
    data_dates["response"] = iso(now)

    base = load_json(now_dir / "summary.json") or {"generated_at": iso(now)}
    if people_critical is not None or "people_critical" not in base:
        base["people_critical"] = people_critical
    if people_exposed is not None or "people_exposed" not in base:
        base["people_exposed"] = people_exposed
    if people_alert is not None or "people_alert" not in base:
        base["people_alert"] = people_alert
    dd = dict(base.get("data_dates") or {})
    dd.update(data_dates)
    base["data_dates"] = dd
    base["response"] = summary
    _write_json(now_dir / "summary.json", base)

    # ---- manifest node for lead-live ---------------------------------------
    products = {
        "priority_zones": str(ZONES_GEOJSON),
        "response_summary": "response/summary.json",
        "flood_dswx": ("response/flood_dswx.geojson"
                       if floods["dswx"]["fc"] is not None else None),
        "flood_nisar": ("response/flood_nisar.geojson"
                        if floods["nisar"]["fc"] is not None else None),
    }
    node_reasons = dict(reasons)
    for key, _, geo_name, _ in FLOOD_SOURCES:
        if products[f"flood_{key}"] is None:
            node_reasons[f"flood_{key}"] = f"response/{geo_name} not published"
    node = {
        "_merge": ("data/now/manifest.json — merge data_dates/products/"
                   "_null_reasons key-wise; set 'response'"),
        "data_dates": data_dates,
        "products": products,
        "_null_reasons": node_reasons,
        "response": summary,
    }
    _write_json(resp_dir / "manifest_node.json", node)
    log(f"[response] flooded={flooded_km2} km2 (+{flooded_km2_lower} lower) "
        f"critical={critical_km2} km2 people: flooded={people_flooded} "
        f"(+{people_flooded_lower} lower) critical={people_critical} "
        f"exposed={people_exposed} zones={len(zones)}")
    return summary
