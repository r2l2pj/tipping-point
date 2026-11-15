"""RS 2024 landslide products vs the Copernicus EMS EMSR720 '6-Mass Movement'
polygons (data/rs_2024/geojson/landslide_ems.geojson — 497 polygons, ~6.4 km²,
dissolved per AOI+product+image date; built by tools/rs_ems_validation.py).

EVERY score here is restricted to the five EMSR720 AOI extents
(data/rs_2024/geojson/flood_ems_aoi.geojson) — the Taquari-valley AOIs only;
see COVERAGE.

  score — (A) TPI_slide before the L37 onset (2024-04-29 03:00Z): % of the EMS
          landslide polygons whose ~90 m TPI_slide cell(s) are Alto+ (> 0.4) /
          Crítico (> 0.6). A polygon counts when ANY cell it touches
          (all_touched rasterization, clipped to the AOI domain) qualifies; the
          centroid-cell variant is reported alongside. Both the polygon-count
          share and the area-weighted share (geodesic km² per member polygon).
          Sources: (a) the observed series bands dated 2024-04-29, 04-30,
          05-01, 05-02 (per-date curve), (b) the NOAA GFS 00Z runs issued
          24/48/72 h before the onset (issues 2024-04-28/27/26), max over the
          d1..d3 _slide frames depicting dates <= the onset day — the
          tools/warned_before.py by_lead/evaluate convention (ANCHORS import).
          Also compares the ordering with the published 16,027-point inventory
          numbers (cases.rs_2024.slide_warned_*).
          (B) OPERA DIST-ALERT-HLS scar candidates
          (geojson/landslide_candidates.geojson): hit rate = % of EMS polygons
          with a candidate within 100/500 m (polygon-to-polygon, UTM 22S);
          precision = % of candidates inside the AOI extents within 100/500 m
          of an EMS polygon. RANDOM BASELINE (science-review D2): the same
          number of random placements on steep terrain (T_slide > 0 — NASADEM
          slope >= 15 deg on the ~90 m grid, the same GEE slope/threshold the
          scar product gates on, via the case T_slide COG since the 1"
          steep15 mask is a gitignored static asset) inside the same AOIs,
          each buffered to a circle whose area is drawn from the in-AOI
          candidate area distribution; 200 seeds; mean + 95 % interval; lift =
          observed / baseline mean.
  --write merges landslide_ems*/scar_ems* keys under cases.rs_2024 in
          data/metrics.json (json load/modify/dump, rs_2024 blob only).

    PYTHONPATH=.:pipeline .venv/bin/python tools/rs_ems_landslide.py score [--data-root data] [--seeds 200] [--write]
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import rasterize

REPO = Path(__file__).resolve().parents[1]
TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
import rs_ems_validation as emsv
import warned_before as wb

ALTO, CRIT = wb.ALTO, wb.CRIT
ONSET = emsv.ONSET  # 2024-04-29 03:00Z (L37 anchor)
ONSET_ANCHOR = next(a for a in wb.ANCHORS["rs_2024"] if a["basis"] == "onset")
GFS_ISSUES = dict(zip((24, 48, 72), emsv.GFS_ONSET_ISSUES))  # 04-28 / 04-27 / 04-26
OBS_CURVE_DATES = ("20240429", "20240430", "20240501", "20240502")
COVERAGE = emsv.COVERAGE
SOURCE = emsv.SOURCE
LICENCE = emsv.LICENCE
RADII_M = (100, 500)
SEEDS = 200
BASE_SEED = 20240429
UTM_EPSG = 32722  # UTM 22S — EMS AOIs sit at ~51.6-51.9 W


def _shape(geom_dict):
    import shapely.geometry

    return shapely.geometry.shape(geom_dict)


def ems_polygons(path: Path) -> list[dict]:
    """One record per MEMBER polygon of each EMS landslide feature (the
    geojson stores them dissolved per AOI+product+image date)."""
    fc = json.loads(Path(path).read_text())
    out = []
    for f in fc["features"]:
        g = _shape(f["geometry"])
        members = list(g.geoms) if g.geom_type == "MultiPolygon" else [g]
        pr = f["properties"]
        for p in members:
            out.append(
                {
                    "geom": p,
                    "aoi": pr.get("aoi"),
                    "product": pr.get("product"),
                    "image_date_utc": pr.get("image_date_utc"),
                    "area_km2": emsv._area_km2(p),
                }
            )
    return out


def aoi_union(aoi_geojson: Path):
    """Union of the EMS AOI extent polygons."""
    import shapely

    fc = json.loads(Path(aoi_geojson).read_text())
    return shapely.union_all([_shape(f["geometry"]) for f in fc["features"]])


def _window(geom, transform, shape, pad=2) -> tuple[int, int, int, int]:
    from rasterio.windows import from_bounds

    win = from_bounds(*geom.bounds, transform).round_offsets().round_lengths()
    r0 = max(int(win.row_off) - pad, 0)
    c0 = max(int(win.col_off) - pad, 0)
    r1 = min(int(win.row_off + win.height) + pad, shape[0])
    c1 = min(int(win.col_off + win.width) + pad, shape[1])
    return r0, r1, c0, c1


def touched_cells(geom, transform, shape, domain_flat: np.ndarray) -> np.ndarray:
    """Flat indices of every ~90 m cell the polygon touches (all_touched=True),
    restricted to the EMS AOI domain mask."""
    r0, r1, c0, c1 = _window(geom, transform, shape)
    sub_t = transform @ rasterio.Affine.translation(c0, r0)
    m = rasterize(
        [geom],
        out_shape=(r1 - r0, c1 - c0),
        transform=sub_t,
        fill=0,
        default_value=1,
        all_touched=True,
        dtype="uint8",
    ).astype(bool)
    rows, cols = np.nonzero(m)
    idx = (rows + r0) * shape[1] + (cols + c0)
    return idx[domain_flat[idx]]


def centroid_cell(geom, transform, shape) -> int:
    """Flat index of the cell containing the polygon centroid (-1 if off-grid)."""
    c = geom.centroid
    gr, gc = rasterio.transform.rowcol(transform, c.x, c.y)
    if 0 <= gr < shape[0] and 0 <= gc < shape[1]:
        return gr * shape[1] + gc
    return -1


def _any_hit(tpi_flat: np.ndarray, idx: np.ndarray, thr: float) -> bool:
    if idx.size == 0:
        return False
    v = tpi_flat[idx]
    v = v[np.isfinite(v)]
    return bool((v > thr).any())


def poly_shares(polys: list[dict], cell_lists, cent_idx, tpi_flat) -> dict:
    """% of polygons qualifying under one TPI frame, all four variants:
    {any_cell, centroid_cell} x threshold {alto_plus, critico} -> count + area."""
    n = len(polys)
    w = np.array([p["area_km2"] for p in polys])
    tot = float(w.sum())
    out = {}
    for variant in ("any_cell", "centroid_cell"):
        res = {}
        for thr_name, thr in (("alto_plus", ALTO), ("critico", CRIT)):
            hit = np.zeros(n, bool)
            for i in range(n):
                if variant == "any_cell":
                    idx = cell_lists[i]
                else:
                    idx = (
                        np.array([cent_idx[i]], dtype=np.int64)
                        if cent_idx[i] >= 0
                        else np.empty(0, dtype=np.int64)
                    )
                hit[i] = _any_hit(tpi_flat, idx, thr)
            res[thr_name] = {
                "n": n,
                "hit_n": int(hit.sum()),
                "pct": round(100 * float(hit.mean()), 2) if n else None,
                "hit_area_km2": round(float(w[hit].sum()), 3),
                "area_pct": round(100 * float(w[hit].sum()) / tot, 2) if tot else None,
            }
        out[variant] = res
    return out


def gfs_slide_frames(cog: Path, case: str, issue: dt.date, upto: dt.date, shape, transform,
                     cog_suffix: str = ""):
    """Max over d1..d3 _slide frames of GFS issue `issue` depicting dates <= `upto`
    (rs_ems_validation.gfs_frames_until for the _flood frames, mirrored for _slide)."""
    p = cog / f"{case}_tpi_gfs_{issue:%Y%m%d}{cog_suffix}.tif"
    if not p.exists():
        return [], None
    names, arrays = [], []
    with rasterio.open(p) as src:
        if (src.transform, src.shape) != (transform, shape):
            raise SystemExit(f"{p.name}: grid differs from the observed series")
        for b, name in enumerate(src.descriptions, start=1):
            if not name.endswith("_slide"):
                continue
            k = int(name[1])
            if issue + dt.timedelta(days=k) <= upto:
                names.append(f"issued {issue} {name} -> {issue + dt.timedelta(days=k)}")
                arrays.append(src.read(b).ravel())
    return names, (wb._nanmax(arrays) if arrays else None)


def score_tpi(data_root: Path, landslide_geojson: Path | None = None,
              aoi_geojson: Path | None = None, cog_suffix: str = "") -> dict:
    rs = data_root / "rs_2024"
    landslide_geojson = landslide_geojson or rs / "geojson" / "landslide_ems.geojson"
    aoi_geojson = aoi_geojson or rs / "geojson" / "flood_ems_aoi.geojson"
    series = rs / "cog" / f"rs_2024_tpi_slide_20240502{cog_suffix}.tif"
    cog = rs / "cog"

    polys = ems_polygons(landslide_geojson)
    aoi = aoi_union(aoi_geojson)
    transform, shape, bands = emsv._tpi_grid(series)
    domain = emsv.rasterize_mask([aoi], transform, shape, all_touched=False).ravel()
    cell_lists = [touched_cells(p["geom"], transform, shape, domain) for p in polys]
    cents = [centroid_cell(p["geom"], transform, shape) for p in polys]
    n_empty = int(sum(1 for c in cell_lists if c.size == 0))
    n_off = int(sum(1 for c in cents if c < 0))

    onset_day = ONSET.date()

    def h_before(day: dt.date) -> float:
        return round(
            (ONSET - dt.datetime.combine(day, dt.time(0), tzinfo=dt.UTC)).total_seconds() / 3600, 1
        )

    observed_rows = []
    with rasterio.open(series) as src:
        for d in OBS_CURVE_DATES:
            b = bands.get(d)
            if b is None:
                continue
            day = emsv._day(d)
            observed_rows.append(
                {
                    "band_date": day.isoformat(),
                    "hours_before_onset": h_before(day),
                    "tpi_source": "observed",
                    **poly_shares(polys, cell_lists, cents, src.read(b).ravel()),
                }
            )

    gfs_rows = []
    for lead_h, issue in GFS_ISSUES.items():
        names, arr = gfs_slide_frames(cog, "rs_2024", issue, onset_day, shape, transform, cog_suffix)
        row = {
            "lead_h": lead_h,
            "gfs_issue": issue.isoformat(),
            "hours_before_onset": h_before(issue),
            "gfs_frames": names,
            "tpi_source": "gfs",
        }
        if arr is not None:
            row.update(poly_shares(polys, cell_lists, cents, arr))
        gfs_rows.append(row)

    return {
        "n_polygons": len(polys),
        "area_km2": round(float(sum(p["area_km2"] for p in polys)), 3),
        "n_polygons_no_domain_cell": n_empty,
        "n_polygons_centroid_offgrid": n_off,
        "onset_utc": ONSET.isoformat(),
        "anchor_label": ONSET_ANCHOR["label"],
        "anchor_source": ONSET_ANCHOR["source"],
        "observed_by_date": observed_rows,
        "gfs_by_lead": gfs_rows,
    }


def _to_utm(geoms, epsg=UTM_EPSG):
    from pyproj import Transformer
    from shapely.ops import transform as shp_transform

    to_m = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True).transform
    return [shp_transform(to_m, g) for g in geoms]


def hit_precision(ems_m, cand_m, radii_m=RADII_M) -> dict:
    """hit = % of EMS polygons with a candidate within r m; precision = % of
    candidates within r m of an EMS polygon (polygon-to-polygon distance)."""
    from shapely.strtree import STRtree

    out = {"n_ems": len(ems_m), "n_candidates": len(cand_m)}
    ctree = STRtree(cand_m) if cand_m else None
    for r in radii_m:
        h = (
            sum(1 for g in ems_m if len(ctree.query(g, predicate="dwithin", distance=r)) > 0)
            if ctree
            else 0
        )
        out[f"hit_{r}m_pct"] = round(100 * h / len(ems_m), 2) if ems_m else None
    etree = STRtree(ems_m) if ems_m else None
    for r in radii_m:
        p = (
            sum(1 for g in cand_m if len(etree.query(g, predicate="dwithin", distance=r)) > 0)
            if etree
            else 0
        )
        out[f"precision_{r}m_pct"] = round(100 * p / len(cand_m), 2) if cand_m else None
    return out


def steep_pool(t_slide_path: Path, domain: np.ndarray, shape) -> np.ndarray:
    """Flat indices of steep cells (T_slide > 0 = NASADEM slope >= 15 deg, the
    scar product's terrain gate) inside the AOI domain."""
    with rasterio.open(t_slide_path) as src:
        if src.shape != shape:
            raise SystemExit(f"{t_slide_path.name}: grid differs from the TPI series")
        t = src.read(1).ravel()
    return np.nonzero(domain & np.isfinite(t) & (t > 0))[0]


def random_steep_polys(rng, pool, transform, width, aoi_prep, areas_m2):
    """n = len(areas_m2) random placements: a uniform point inside a random steep
    cell (rejected outside the AOI union), buffered to a circle of an area drawn
    with replacement from the in-AOI candidate area distribution."""
    from shapely.geometry import Point

    polys = []
    n = len(areas_m2)
    cells = rng.choice(pool, size=n, replace=True)
    areas = rng.choice(areas_m2, size=n, replace=True)
    for cell, a in zip(cells, areas):
        r, c = divmod(int(cell), width)
        while True:
            x = transform.c + (c + rng.uniform()) * transform.a
            y = transform.f + (r + rng.uniform()) * transform.e
            p = Point(x, y)
            if aoi_prep.contains(p):
                break
            cell = rng.choice(pool)
            r, c = divmod(int(cell), width)
        polys.append(p)
    pts_m = _to_utm(polys)
    return [pt.buffer(float(np.sqrt(a / np.pi)), quad_segs=24) for pt, a in zip(pts_m, areas)]


def score_scar(data_root: Path, polys: list[dict], aoi,
               candidates_geojson: Path | None = None,
               aoi_domain_flat: np.ndarray | None = None,
               shape=None, transform=None, seeds: int = SEEDS,
               steep_path: Path | None = None) -> dict:
    rs = data_root / "rs_2024"
    candidates_geojson = candidates_geojson or rs / "geojson" / "landslide_candidates.geojson"
    steep_path = steep_path or rs / "cog" / "rs_2024_t_slide_20240502.tif"
    from shapely.prepared import prep

    fc = json.loads(Path(candidates_geojson).read_text())
    cand_ll = [_shape(f["geometry"]) for f in fc["features"]]
    cand_areas = np.array(
        [f["properties"].get("area_km2") or emsv._area_km2(g) for f, g in zip(fc["features"], cand_ll)]
    )
    aoi_p = prep(aoi)
    rps = [g.representative_point() for g in cand_ll]
    keep = np.array([aoi_p.contains(p) for p in rps])
    cand_in = [g for g, k in zip(cand_ll, keep) if k]
    areas_in = cand_areas[keep]

    ems_m = _to_utm([p["geom"] for p in polys])
    cand_m = _to_utm(cand_in)
    observed = hit_precision(ems_m, cand_m)

    # random baseline: same count as in-AOI candidates, on steep cells, sizes
    # drawn from the in-AOI candidate area distribution
    pool = steep_pool(steep_path, aoi_domain_flat, shape)
    base = {f"{k}_{r}m": [] for k in ("hit", "precision") for r in RADII_M}
    for seed in range(seeds):
        rng = np.random.default_rng(BASE_SEED + seed)
        rnd_m = random_steep_polys(rng, pool, transform, shape[1], aoi_p, areas_in)
        b = hit_precision(ems_m, rnd_m)
        for r in RADII_M:
            base[f"hit_{r}m"].append(b[f"hit_{r}m_pct"])
            base[f"precision_{r}m"].append(b[f"precision_{r}m_pct"])
    baseline = {}
    for k, vals in base.items():
        v = np.array([x for x in vals if x is not None], float)
        baseline[k + "_pct"] = {
            "mean": round(float(v.mean()), 2) if v.size else None,
            "ci95": [round(float(np.percentile(v, 2.5)), 2), round(float(np.percentile(v, 97.5)), 2)]
            if v.size
            else None,
        }

    def lift(obs, key):
        m = baseline[key + "_pct"]["mean"]
        return round(obs / m, 2) if obs is not None and m else None

    return {
        "n_candidates_total": len(cand_ll),
        "n_candidates_in_aoi": len(cand_in),
        "candidates_in_aoi_km2": round(float(areas_in.sum()), 3),
        "radii_m": list(RADII_M),
        "observed": observed,
        "baseline": {
            "seeds": seeds,
            "n_random_per_seed": len(areas_in),
            "slope_source": (
                "T_slide > 0 (slope >= 15 deg, GEE NASADEM on the ~90 m grid; "
                "the same slope floor the scar product gates on, read from the case "
                "T_slide COG — the 1-arc-second steep15_1as.tif mask it actually "
                "used is a gitignored static asset)"
            ),
            "placement": "uniform point in a random steep cell, inside the AOI union",
            "sizes": "circle of area drawn with replacement from the in-AOI candidate areas",
            "method": "per seed: draw n steep cells with replacement, place a uniform "
            "point in each (rejected outside the AOI union), buffer to a circle of a "
            "sampled candidate area, recompute hit/precision; mean + 2.5/97.5 "
            "percentiles over seeds",
            **baseline,
        },
        "lift": {
            f"hit_{r}m": lift(observed[f"hit_{r}m_pct"], f"hit_{r}m") for r in RADII_M
        }
        | {f"precision_{r}m": lift(observed[f"precision_{r}m_pct"], f"precision_{r}m") for r in RADII_M},
    }


def inventory_comparison(data_root: Path, cases: dict | None = None) -> dict:
    """Published 16,027-point inventory numbers for the same leads."""
    m = cases if cases is not None else json.loads(
        (data_root / "metrics.json").read_text())
    rsb = m["cases"]["rs_2024"]
    return {
        "n_points": rsb.get("slide_warned_n"),
        "unit": "% of inventory points (point's TPI cell — not any-cell-touched)",
        "gfs_24h_pct": rsb.get("slide_warned_gfs_24h_pct"),
        "gfs_48h_pct": rsb.get("slide_warned_gfs_48h_pct"),
        "gfs_72h_pct": rsb.get("slide_warned_gfs_72h_pct"),
        "observed_29_pct": next(
            (
                c["alto_or_critico_pct"]
                for c in (rsb.get("slide_warned") or {}).get("by_date_observed", [])
                if c["date"] == "2024-04-29"
            ),
            None,
        ),
    }


METHOD = (
    "Copernicus EMS Rapid Mapping activation EMSR720 photo-interpreted 497 "
    "landslide ('6-Mass Movement') polygons (~6.4 km²) on the five "
    "Taquari-valley AOIs (Guapore, Encantado, Roca Sales, Das Antas Dam, Santa "
    "Tereza) from post-event optical imagery acquired 2024-05-06..05-15 "
    "(Pleiades Neo, GeoEye-1) — the slides are UNDATED: imagery shows the "
    "scars, not the failure time, so every polygon is scored against the L37 "
    "event onset 2024-04-29 03:00Z. (A) Each member polygon takes every ~90 m "
    "TPI_slide cell it touches (all_touched rasterization clipped to the EMS "
    "AOI extents; centroid-cell variant alongside) and counts as warned when "
    "ANY touched cell is Alto+ (> 0.4) [Crítico > 0.6]; shares are reported as "
    "polygon counts and geodesic-area weighted. Sources: the observed series "
    "bands dated 2024-04-29..05-02 (curve) and the GFS 00Z runs issued "
    "2024-04-28/27/26 (24/48/72 h before onset; max over d1..d3 _slide frames "
    "depicting dates <= the onset day — the warned_before by_lead convention). "
    "(B) Scar candidates = OPERA DIST-ALERT-HLS vegetation-loss polygons inside "
    "the same AOI extents (representative-point containment): hit rate = % of "
    "EMS polygons with a candidate within 100/500 m; precision = % of in-AOI "
    "candidates within 100/500 m of an EMS polygon (polygon-to-polygon, UTM "
    "22S). Random baseline (science-review item D2): the same number of random "
    "placements per seed on steep terrain (T_slide > 0, i.e. NASADEM slope "
    ">= 15 deg — the scar product's own terrain gate, read from the case "
    "T_slide COG because the 1\" steep15_1as.tif it used is a gitignored "
    "static asset) inside the AOI extents, each buffered to a circle whose "
    "area is drawn with replacement from the in-AOI candidate areas; 200 "
    "seeds; mean and 95 % interval (2.5/97.5 percentiles); lift = observed / "
    "baseline mean. Caveats: EMS mapped only five small AOIs — scores are "
    "Taquari-valley-only, not AOI-wide; slides are undated (post-event "
    "imagery); polygons are imagery-mapped extent at a moment and miss "
    "cloud/vegetation-hidden slides; any-cell-touched is more permissive than "
    "the published point-cell scoring of the 16,027-point inventory."
)


def score(data_root: Path, seeds: int = SEEDS, cog_suffix: str = "",
          cases: dict | None = None) -> dict:
    rs = data_root / "rs_2024"
    landslide_geojson = rs / "geojson" / "landslide_ems.geojson"
    aoi_geojson = rs / "geojson" / "flood_ems_aoi.geojson"
    meta = json.loads((rs / "flood_ems_meta.json").read_text())

    polys = ems_polygons(landslide_geojson)
    aoi = aoi_union(aoi_geojson)
    tpi = score_tpi(data_root, landslide_geojson, aoi_geojson, cog_suffix)
    series = rs / "cog" / f"rs_2024_tpi_slide_20240502{cog_suffix}.tif"
    transform, shape, _ = emsv._tpi_grid(series)
    domain = emsv.rasterize_mask([aoi], transform, shape, all_touched=False).ravel()
    scar = score_scar(data_root, polys, aoi, aoi_domain_flat=domain,
                      shape=shape, transform=transform, seeds=seeds)
    comp = inventory_comparison(data_root, cases)
    inv_vec = [comp["gfs_24h_pct"], comp["gfs_48h_pct"], comp["gfs_72h_pct"]]
    ems_vec = [
        (r.get("any_cell") or {}).get("alto_plus", {}).get("pct") for r in tpi["gfs_by_lead"]
    ]
    same_order = (
        sorted(range(3), key=lambda i: -(ems_vec[i] if ems_vec[i] is not None else -1))
        == sorted(range(3), key=lambda i: -(inv_vec[i] if inv_vec[i] is not None else -1))
        if all(v is not None for v in ems_vec + inv_vec)
        else None
    )
    return {
        "activation": meta["activation"]["id"],
        "licence": LICENCE,
        "source": SOURCE,
        "coverage_limit": COVERAGE,
        "scope": "scored ONLY inside the EMS AOI extents (flood_ems_aoi.geojson)",
        "reference": {
            "n_polygons": tpi["n_polygons"],
            "area_km2": tpi["area_km2"],
            "note": "member polygons of geojson/landslide_ems.geojson; imagery-mapped, undated",
        },
        "tpi_warned": tpi,
        "inventory_16027": {**comp, "ems_gfs_pct": ems_vec, "same_lead_order": same_order},
        "scar": scar,
        "definitions": {
            "warned_polygon": "polygon counts when ANY ~90 m TPI_slide cell it touches is "
            "Alto+ (>0.4) [Critico >0.6]; centroid_cell = only the cell containing the centroid; "
            "polygons with no in-domain cell / NaN TPI stay in the denominator",
            "area_weighted": "share weighted by geodesic polygon area km2 (equirectangular cos(lat))",
            "observed": "series band dated D (rain D-3..D-1; band 2024-04-29 = all information "
            "before the 04-29 03Z onset)",
            "gfs_lead": "GFS 00Z run issued on the UTC date (onset - lead_h); max over d1..d3 "
            "_slide frames depicting dates <= the onset day",
            "hit": "% of EMS polygons with >=1 candidate within r m (UTM 22S polygon distance)",
            "precision": "% of candidates inside the AOI extents within r m of an EMS polygon",
            "domain": "cells whose centre is inside an EMS AOI extent (all_touched=False)",
        },
    }


def apply(res: dict, m: dict) -> None:
    """Merge `res` into an in-memory metrics dict (no file IO)."""
    rsb = m["cases"]["rs_2024"]
    rsb["landslide_ems_n"] = res["reference"]["n_polygons"]
    rsb["landslide_ems_km2"] = res["reference"]["area_km2"]
    obs29 = next((r for r in res["tpi_warned"]["observed_by_date"] if r["band_date"] == "2024-04-29"), None)
    rsb["landslide_ems_warned_observed_pct"] = (
        obs29["any_cell"]["alto_plus"]["pct"] if obs29 else None
    )
    rsb["landslide_ems_warned_observed_critical_pct"] = (
        obs29["any_cell"]["critico"]["pct"] if obs29 else None
    )
    for row in res["tpi_warned"]["gfs_by_lead"]:
        h = row["lead_h"]
        ac = row.get("any_cell") or {}
        rsb[f"landslide_ems_warned_gfs_{h}h_pct"] = (ac.get("alto_plus") or {}).get("pct")
        rsb[f"landslide_ems_warned_gfs_{h}h_critical_pct"] = (ac.get("critico") or {}).get("pct")
    scar = res["scar"]
    for r in RADII_M:
        rsb[f"scar_ems_hit_{r}m_pct"] = scar["observed"][f"hit_{r}m_pct"]
        rsb[f"scar_ems_precision_{r}m_pct"] = scar["observed"][f"precision_{r}m_pct"]
        rsb[f"scar_ems_lift_{r}m"] = scar["lift"][f"hit_{r}m"]
    rsb["scar_ems_baseline"] = {
        **scar["baseline"],
        "lift_precision": {f"{r}m": scar["lift"][f"precision_{r}m"] for r in RADII_M},
        "definition": "random placements of the same count as the in-AOI candidates on steep "
        "terrain inside the EMS AOIs; hit/precision recomputed per seed; "
        "ci95 = 2.5/97.5 percentiles over seeds; lift = observed / baseline mean",
    }
    rsb["landslide_ems"] = res
    rsb["landslide_ems_method"] = METHOD


def merge(data_root: Path, res: dict) -> None:
    """json load/modify/dump — cases.rs_2024 only (same convention as
    rs_ems_validation.merge)."""
    path = data_root / "metrics.json"
    m = json.loads(path.read_text())
    apply(res, m)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(m, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def _pct(row, variant, thr):
    return ((row.get(variant) or {}).get(thr) or {}).get("pct")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("score", help="score landslide products vs the EMS reference")
    s.add_argument("--data-root", type=Path, default=REPO / "data")
    s.add_argument("--seeds", type=int, default=SEEDS)
    s.add_argument("--cog-suffix", default="")
    s.add_argument("--write", action="store_true", help="merge into metrics.json")
    args = ap.parse_args(argv)

    if args.write and args.cog_suffix:
        raise SystemExit("--write merges under the v1 keys only; re-scored "
                         "(_v2) results are merged via tools/replay_v2_score.py")
    res = score(args.data_root, seeds=args.seeds, cog_suffix=args.cog_suffix)
    t = res["tpi_warned"]
    print(f"EMS landslide polygons: {t['n_polygons']} ({t['area_km2']} km2) — {COVERAGE}")
    print("observed curve (any_cell alto+/critico pct | centroid alto+/critico):")
    for r in t["observed_by_date"]:
        print(
            f"  {r['band_date']} ({r['hours_before_onset']}h): "
            f"{_pct(r,'any_cell','alto_plus')} / {_pct(r,'any_cell','critico')} | "
            f"{_pct(r,'centroid_cell','alto_plus')} / {_pct(r,'centroid_cell','critico')}"
        )
    for r in t["gfs_by_lead"]:
        print(
            f"  gfs -{r['lead_h']}h issue {r['gfs_issue']}: "
            f"{_pct(r,'any_cell','alto_plus')} / {_pct(r,'any_cell','critico')} | "
            f"{_pct(r,'centroid_cell','alto_plus')} / {_pct(r,'centroid_cell','critico')} "
            f"frames={r['gfs_frames']}"
        )
    print("inventory_16027:", json.dumps(res["inventory_16027"]))
    print("scar observed:", json.dumps(res["scar"]["observed"]))
    print("scar baseline:", json.dumps(res["scar"]["baseline"]))
    print("scar lift:", json.dumps(res["scar"]["lift"]))
    if args.write:
        merge(args.data_root, res)
        print("merged into", args.data_root / "metrics.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
