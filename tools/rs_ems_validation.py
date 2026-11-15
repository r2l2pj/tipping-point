"""RS 2024 flood reference from Copernicus EMS Rapid Mapping activation EMSR720
("Flood in Rio Grande Do Sul State, Brazil", activated 2024-05-03, event GLIDE
FL-2024-000063-BRA) — fills gap G1 (no RS 2024 flood map) without the ASF EULA.

  build  — read the downloaded product zips (raw/<aoiXX>/<pkg>/pkg.zip or already
           extracted */extracted/*.shp), keep event_type '5-Flood' polygons from
           observedEventA + maximumFloodExtentA (notation 'Flooded area',
           'Flood trace', 'Maximum flood extent'), subtract the BLP hydrographyA
           reference/permanent-water polygons, dissolve per AOI+product+image
           date -> data/rs_2024/geojson/flood_ems.geojson (EPSG:4326);
           '6-Mass Movement' polygons -> landslide_ems.geojson; activation/AOI/
           product/image metadata -> data/rs_2024/flood_ems_meta.json.
  score  — rasterize the EMS flood union per satellite image date E on the
           ~90 m TPI_flood grid (all_touched=False; area-weighted variant =
           8x supersampled coverage fraction >= 0.5), restrict to the EMS AOI
           extents mapped that date, and compute IoU / F1 / precision / recall /
           km2 for TPI Alto+ (> 0.4) and Critico (> 0.6) using (a) the observed
           series band dated E and (b) the max over the d1..d3 frames depicting
           dates <= E of the GFS run issued 24 h before E. Also 'flagged before
           onset': % of the EMS flood area already Alto+/Critico in the observed
           band dated 2024-04-29 and in the GFS runs issued 2024-04-26/27/28
           (onset = 2024-04-29 03:00Z, L37; reuses tools/warned_before.py
           evaluate/by_lead). --write merges flood_ems* keys under
           cases.rs_2024 in data/metrics.json.

COVERAGE LIMIT: EMSR720 mapped only five small AOIs in the Taquari valley
(Guapore, Encantado, Roca Sales, Das Antas Dam, Santa Tereza, ~51.6-51.9 W,
29.0-29.3 S). Porto Alegre, Guaiba, Canoas, Eldorado do Sul and Sao Leopoldo —
the metropolitan flood epicentre — were NOT mapped by any EMS activation, so
every score here is Taquari-valley-only evidence, not AOI-wide.

    PYTHONPATH=.:pipeline .venv/bin/python tools/rs_ems_validation.py build [--raw-root DIR]
    PYTHONPATH=.:pipeline .venv/bin/python tools/rs_ems_validation.py score [--data-root data] [--write]
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
import warned_before as wb

ALTO, CRIT = wb.ALTO, wb.CRIT
RAW_ROOT = Path.home() / ".cache" / "lead-analysis" / "rs-ems" / "raw"
ACTIVATION_JSON = Path.home() / ".cache" / "lead-analysis" / "rs-ems" / "emsr720_activation.json"
ONSET = dt.datetime(2024, 4, 29, 3, tzinfo=dt.UTC)  # L37 anchor
GFS_ONSET_ISSUES = (dt.date(2024, 4, 28), dt.date(2024, 4, 27), dt.date(2024, 4, 26))
OBS_ONSET_BAND = "20240429"
LICENCE = (
    "Copernicus EMS — free use with attribution: © European Union, Copernicus Emergency Management Service"
)
SOURCE = "Copernicus EMS Rapid Mapping EMSR720"
STATUS = "reference (Copernicus EMS, observed)"
COVERAGE = (
    "EMSR720 covers only five Taquari-valley AOIs (Guapore, Encantado, Roca Sales, "
    "Das Antas Dam, Santa Tereza); Porto Alegre/Guaiba metro flood not mapped by EMS — "
    "scores are Taquari-valley-only"
)

FLOOD_EVENT = "5-Flood"
SLIDE_EVENT = "6-Mass Movement"
FLOOD_NOTATIONS = {"Flooded area", "Flood trace", "Maximum flood extent"}
SUPERSAMPLE = 8  # area-weighted rasterization: subpixel grid factor
COVERAGE_MIN = 0.5


def _day(s: str) -> dt.date:
    return dt.date.fromisoformat(s) if "-" in s else wb._day(s)


def _tpi_grid(series: Path):
    with rasterio.open(series) as src:
        return src.transform, src.shape, {d: i + 1 for i, d in enumerate(src.descriptions)}


def pixel_km2(transform, shape) -> np.ndarray:
    """Per-pixel area km2 (cos(lat) correction, same convention as warned_before.flood_pixels)."""
    rows = np.arange(shape[0])
    ys = transform.f + (rows + 0.5) * transform.e
    return (abs(transform.a * transform.e) * wb.KM_PER_DEG**2 * np.cos(np.deg2rad(ys)))[:, None] * np.ones(
        (1, shape[1])
    )


def rasterize_mask(geoms, transform, shape, all_touched=False) -> np.ndarray:
    """Binary mask: cell centre inside a polygon (all_touched=False default)."""
    if not geoms:
        return np.zeros(shape, bool)
    return rasterize(
        geoms,
        out_shape=shape,
        transform=transform,
        fill=0,
        default_value=1,
        all_touched=all_touched,
        dtype="uint8",
    ).astype(bool)


def rasterize_area_weighted(geoms, transform, shape, factor=SUPERSAMPLE, min_frac=COVERAGE_MIN) -> np.ndarray:
    """Cell counts as flooded when >= min_frac of its area is covered: rasterize on a
    `factor`x finer grid (all_touched=True), block-mean -> coverage fraction >= 0.5."""
    if not geoms:
        return np.zeros(shape, bool)
    h, w = shape
    fine_t = transform @ rasterio.Affine.scale(1 / factor, 1 / factor)
    fine = rasterize(
        geoms,
        out_shape=(h * factor, w * factor),
        transform=fine_t,
        fill=0,
        default_value=1,
        all_touched=True,
        dtype="uint8",
    )
    frac = fine.reshape(h, factor, w, factor).mean(axis=(1, 3))
    return frac >= min_frac


def confusion(pred: np.ndarray, truth: np.ndarray, domain: np.ndarray) -> dict:
    p, t = pred[domain], truth[domain]
    tp = int((p & t).sum())
    fp = int((p & ~t).sum())
    fn = int((~p & t).sum())
    union = tp + fp + fn
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "iou": round(tp / union, 4) if union else None,
        "f1": round(2 * tp / (2 * tp + fp + fn), 4) if (2 * tp + fp + fn) else None,
        "precision": round(tp / (tp + fp), 4) if (tp + fp) else None,
        "recall": round(tp / (tp + fn), 4) if (tp + fn) else None,
    }


def _shp(root: Path, *patterns) -> list[Path]:
    return sorted({p for pat in patterns for p in root.glob(pat)})


def _product_name(pkg_dir: Path) -> str:
    # EMSR720_AOI01_DEL_MONIT01_v1 -> DEL_MONIT01
    parts = pkg_dir.name.split("_")
    return "_".join(parts[2:4]) if len(parts) >= 4 else pkg_dir.name


def _read(shp: Path):
    import geopandas as gpd

    return gpd.read_file(shp)


def _dissolve(geoms):
    import shapely

    g = shapely.union_all(list(geoms))
    if g.is_empty:
        return None
    if g.geom_type == "Polygon":
        g = shapely.MultiPolygon([g])
    return g


def _geojson_fc(features) -> dict:
    return {"type": "FeatureCollection", "features": features}


def _feat(geom, **props) -> dict:
    import shapely.geometry

    return {"type": "Feature", "geometry": shapely.geometry.mapping(geom), "properties": props}


def _wkt_to_geom(wkt: str):
    import shapely.wkt

    return shapely.wkt.loads(wkt)


def _area_km2(geom) -> float:
    """Geodesic area via local equirectangular approximation (same family as
    warned_before: cos(lat) on sphere-ish degrees)."""
    import shapely

    return (
        float(shapely.area(geom))
        * wb.KM_PER_DEG**2
        * np.cos(np.deg2rad((geom.bounds[1] + geom.bounds[3]) / 2))
    )


def build(raw_root: Path, activation_json: Path, out_dir: Path, simplify_m: float | None = None) -> dict:
    """Extract flood + landslide vectors from the downloaded EMS zips."""
    raw_root = Path(raw_root)
    act = json.loads(Path(activation_json).read_text())["results"][0]
    # per (aoi_number, product) -> {images, version, downloadPath, extent, stats}
    meta_prod: dict[tuple[str, str], dict] = {}
    for a in act["aois"]:
        n = f"AOI{int(a['number']):02d}"
        for p in a["products"]:
            # product code from downloadPath when present, else from layer names
            name = None
            if p.get("downloadPath"):
                # EMSR720_AOI01_DEL_MONIT01_v1 -> DEL_MONIT01
                stem = Path(p["downloadPath"]).name.removesuffix(".zip")
                name = "_".join(stem.split("_")[2:4])
            if not name:
                name = (
                    f"{p['type']}_MONIT{int(p.get('monitoringNumber') or 0):02d}"
                    if p.get("monitoring")
                    else f"{p['type']}_PRODUCT"
                )
            meta_prod[(n, name)] = p

    flood_feats, slide_feats, aoi_extents, meta_products = [], [], {}, []
    for pkg_dir in sorted(d for d in raw_root.glob("aoi*/EMSR720_*") if d.is_dir()):
        root = pkg_dir / "extracted" if (pkg_dir / "extracted").is_dir() else pkg_dir
        aoi = pkg_dir.parent.name.upper()  # aoi01 -> AOI01
        product = _product_name(pkg_dir)
        p = meta_prod.get((aoi, product), {})
        imgs = p.get("images") or []
        acq = sorted({i.get("acquisitionTime") for i in imgs if i.get("acquisitionTime")})
        image_dt = acq[0] if acq else None
        sensors = sorted({i.get("sensorName") for i in imgs if i.get("sensorName")})
        ver = p.get("version") or {}
        meta_products.append(
            {
                "aoi": aoi,
                "product": product,
                "type": p.get("type"),
                "monitoring_number": p.get("monitoringNumber"),
                "version": ver.get("number"),
                "delivery_time_utc": ver.get("deliveryTime"),
                "status_code": ver.get("statusCode"),
                "version_reason": ver.get("reason") or None,
                "download_url": p.get("downloadPath") or None,
                "image_acquisition_utc": acq,
                "sensors": sensors,
                "sensor_types": sorted({i.get("sensorType") for i in imgs if i.get("sensorType")}),
                "expected_delivery": p.get("expectedDelivery"),
                "stats": p.get("stats"),
            }
        )
        aoi_shp = _shp(root, "*areaOfInterestA*.shp")
        if aoi_shp:
            g = _read(aoi_shp[0])
            geom = _dissolve(g.geometry)
            if geom is not None:
                aoi_extents[aoi] = geom if aoi not in aoi_extents else aoi_extents[aoi].union(geom)

        # reference/permanent water from the same AOI's BLP package
        hydro = []
        for h in _shp(
            raw_root / aoi.lower(), "*BLP*/extracted/*hydrographyA*.shp", "*BLP*/*hydrographyA*.shp"
        ):
            hydro += list(_read(h).geometry)
        hydro_u = _dissolve(hydro) if hydro else None

        flood_geoms = []
        for shp in _shp(root, "*observedEventA*.shp", "*maximumFloodExtentA*.shp"):
            g = _read(shp)
            ev = g["event_type"].astype(str) if "event_type" in g.columns else g["obj_desc"].astype(str)
            fl = g[
                [
                    e.startswith(FLOOD_EVENT) or d == "Riverine flood"
                    for e, d in zip(ev, g.get("obj_desc", ev).astype(str))
                ]
            ]
            if "notation" in fl.columns:
                fl = fl[fl["notation"].isin(FLOOD_NOTATIONS) | fl["notation"].isna()]
            flood_geoms += list(fl.geometry)
            sl = g[
                [
                    e.startswith(SLIDE_EVENT) or d == "Landslide"
                    for e, d in zip(ev, g.get("obj_desc", ev).astype(str))
                ]
            ]
            for geom in sl.geometry:
                slide_feats.append(
                    (
                        geom,
                        {
                            "aoi": aoi,
                            "product": product,
                            "image_date_utc": (image_dt or "")[:10],
                            "acquisition_utc": image_dt,
                            "sensor": "+".join(sensors) or None,
                            "source": SOURCE,
                            "status": STATUS,
                            "obj_desc": "Landslide",
                        },
                    )
                )
        if flood_geoms:
            u = _dissolve(flood_geoms)
            if hydro_u is not None:
                u = u.difference(hydro_u)
                if u.is_empty:
                    u = None
            if u is not None:
                if simplify_m:
                    tol = simplify_m / 111320.0
                    u = u.simplify(tol, preserve_topology=True)
                notations = set()
                for shp in _shp(root, "*observedEventA*.shp", "*maximumFloodExtentA*.shp"):
                    g = _read(shp)
                    ev = g["event_type"].astype(str) if "event_type" in g.columns else None
                    fl = g[[e.startswith(FLOOD_EVENT) for e in ev]] if ev is not None else g
                    notations |= set(fl.get("notation", []).astype(str))
                flood_feats.append(
                    (
                        u,
                        {
                            "aoi": aoi,
                            "product": product,
                            "image_date_utc": (image_dt or "")[:10],
                            "acquisition_utc": image_dt,
                            "sensor": "+".join(sensors) or None,
                            "source": SOURCE,
                            "status": STATUS,
                            "notations": sorted(notations),
                            "permanent_water_excluded": hydro_u is not None,
                            "area_km2": round(_area_km2(u), 3),
                        },
                    )
                )

    geo_dir = out_dir / "geojson"
    geo_dir.mkdir(parents=True, exist_ok=True)
    flood_fc = _geojson_fc([_feat(g, **pr) for g, pr in flood_feats])
    slide_fc = _geojson_fc([_feat(g, **pr) for g, pr in slide_feats])
    # dissolve landslides per aoi+product+date too
    if slide_feats:
        groups: dict[tuple, list] = {}
        props: dict[tuple, dict] = {}
        for g, pr in slide_feats:
            k = (pr["aoi"], pr["product"], pr["image_date_utc"])
            groups.setdefault(k, []).append(g)
            props[k] = {**pr, "n_polygons": 0}
        slide_fc = _geojson_fc(
            [
                _feat(
                    _dissolve(groups[k]),
                    **{
                        **props[k],
                        "n_polygons": len(groups[k]),
                        "area_km2": round(_area_km2(_dissolve(groups[k])), 3),
                    },
                )
                for k in sorted(groups)
            ]
        )
    (geo_dir / "flood_ems.geojson").write_text(json.dumps(flood_fc, ensure_ascii=False))
    (geo_dir / "landslide_ems.geojson").write_text(json.dumps(slide_fc, ensure_ascii=False))

    meta = {
        "activation": {
            "id": act["code"],
            "name": act["name"],
            "category": act["category"],
            "event_time_utc": act["eventTime"],
            "activation_time_utc": act["activationTime"],
            "activator": act.get("activator"),
            "gdacs_id": act.get("gdacsId"),
            "glide": "FL-2024-000063-BRA",
            "report_link": act.get("reportLink"),
            "closed": act.get("closed"),
        },
        "licence": LICENCE,
        "urls": {
            "activation_page": f"https://mapping.emergency.copernicus.eu/activations/{act['code']}/",
            "public_api": f"https://mapping.emergency.copernicus.eu/backend/dashboard-api/"
            f"public-activations/?code={act['code']}",
            "products_zip": act.get("productsPath"),
            "viewer_bucket": act.get("aws_bucket"),
        },
        "coverage_limit": COVERAGE,
        "simplify_tolerance_m": simplify_m,
        "aois": [
            {
                "aoi": f"AOI{int(a['number']):02d}",
                "name": a["name"],
                "extent_wkt": a["extent"],
                "extent_geojson": _feat(
                    _wkt_to_geom(a["extent"]), aoi=f"AOI{int(a['number']):02d}", name=a["name"]
                )["geometry"],
                "n_products": len(a["products"]),
                "blp_url": a.get("blpPath"),
            }
            for a in act["aois"]
        ],
        "products": meta_products,
        "image_dates": sorted(
            {
                (mp["image_acquisition_utc"] or [None])[0][:10]
                for mp in meta_products
                if mp["image_acquisition_utc"]
            }
        ),
        "layers_used": {
            "flood": "observedEventA + maximumFloodExtentA, event_type '5-Flood', "
            "notations Flooded area / Flood trace / Maximum flood extent",
            "permanent_water_excluded": "BLP hydrographyA (reference lakes/rivers) "
            "subtracted from flood polygons",
            "landslide": "observedEventA event_type '6-Mass Movement' (-> landslide_ems.geojson, not scored)",
        },
        "retrieved_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
    }
    (out_dir / "flood_ems_meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
    # AOI extents geojson for the scorer (kept beside meta as features too)
    aoi_fc = _geojson_fc([_feat(g, aoi=k) for k, g in sorted(aoi_extents.items())])
    (geo_dir / "flood_ems_aoi.geojson").write_text(json.dumps(aoi_fc, ensure_ascii=False))
    return {
        "n_flood_features": len(flood_feats),
        "n_slide_groups": len(slide_fc["features"]),
        "aois": sorted(aoi_extents),
        "meta": meta,
    }


def _geoms_by_date(flood_geojson: Path) -> dict[str, list]:
    fc = json.loads(Path(flood_geojson).read_text())
    by: dict[str, list] = {}
    import shapely.geometry

    for f in fc["features"]:
        d = f["properties"]["image_date_utc"]
        if d:
            by.setdefault(d, []).append(shapely.geometry.shape(f["geometry"]))
    return by


def _aoi_geoms(meta: dict) -> dict[str, object]:
    return {a["aoi"]: _wkt_to_geom(a["extent_wkt"]) for a in meta["aois"]}


def _aoi_by_date(flood_geojson: Path) -> dict[str, set]:
    fc = json.loads(Path(flood_geojson).read_text())
    by: dict[str, set] = {}
    for f in fc["features"]:
        d = f["properties"]["image_date_utc"]
        if d:
            by.setdefault(d, set()).add(f["properties"]["aoi"])
    return by


def gfs_frames_until(cog: Path, case: str, issue: dt.date, upto: dt.date, shape, transform):
    """Max over d1..d3 _flood frames of issue `issue` depicting dates <= `upto`.
    Returns (frame names, array or None)."""
    p = cog / f"{case}_tpi_gfs_{issue:%Y%m%d}.tif"
    if not p.exists():
        return [], None
    names, arrays = [], []
    with rasterio.open(p) as src:
        if (src.transform, src.shape) != (transform, shape):
            raise SystemExit(f"{p.name}: grid differs from the observed series")
        for b, name in enumerate(src.descriptions, start=1):
            if not name.endswith("_flood"):
                continue
            k = int(name[1])
            if issue + dt.timedelta(days=k) <= upto:
                names.append(f"issued {issue} {name} -> {issue + dt.timedelta(days=k)}")
                arrays.append(src.read(b))
    return names, (wb._nanmax(arrays) if arrays else None)


def score(
    data_root: Path,
    flood_geojson: Path | None = None,
    meta_path: Path | None = None,
    aoi_geojson: Path | None = None,
) -> dict:
    rs = data_root / "rs_2024"
    flood_geojson = flood_geojson or rs / "geojson" / "flood_ems.geojson"
    meta_path = meta_path or rs / "flood_ems_meta.json"
    aoi_geojson = aoi_geojson or rs / "geojson" / "flood_ems_aoi.geojson"
    meta = json.loads(Path(meta_path).read_text())
    series = rs / "cog" / "rs_2024_tpi_flood_20240502.tif"
    cog = rs / "cog"
    transform, shape, bands = _tpi_grid(series)
    km2 = pixel_km2(transform, shape)
    by_date = _geoms_by_date(flood_geojson)
    aoi_by_date = _aoi_by_date(flood_geojson)
    aoi_geoms = _aoi_geoms(meta)

    obs = rasterio.open(series)
    rows = []
    for day in sorted(by_date):
        e = _day(day)
        flood_geoms = by_date[day]
        domain_g = [aoi_geoms[a] for a in sorted(aoi_by_date[day]) if a in aoi_geoms]
        domain = rasterize_mask(domain_g, transform, shape, all_touched=False)
        variants = {
            "center": rasterize_mask(flood_geoms, transform, shape, all_touched=False),
            "area_weighted": rasterize_area_weighted(flood_geoms, transform, shape),
        }
        obs_band = bands.get(day.replace("-", ""))
        obs_arr = obs.read(obs_band) if obs_band else None
        g_issue = e - dt.timedelta(days=1)
        g_names, g_arr = gfs_frames_until(cog, "rs_2024", g_issue, e, shape, transform)
        for vname, ems_mask in variants.items():
            truth = ems_mask & domain
            truth_km2 = float(km2[truth].sum())
            for thr_name, thr in (("alto_plus", ALTO), ("critico", CRIT)):
                for src_name, arr in (("observed", obs_arr), ("gfs_issued_24h", g_arr)):
                    if arr is None:
                        row = {
                            "iou": None,
                            "f1": None,
                            "precision": None,
                            "recall": None,
                            "tp": None,
                            "fp": None,
                            "fn": None,
                        }
                    else:
                        row = confusion(np.nan_to_num(arr, nan=-1.0) > thr, truth, domain)
                    pred = (arr > thr) & domain if arr is not None else np.zeros(shape, bool)
                    rows.append(
                        {
                            "image_date_utc": day,
                            "rasterization": vname,
                            "threshold": thr_name,
                            "tpi_source": src_name,
                            "gfs_issue": g_issue.isoformat() if src_name == "gfs_issued_24h" else None,
                            "gfs_frames": g_names if src_name == "gfs_issued_24h" else None,
                            "observed_band": day.replace("-", "") if src_name == "observed" else None,
                            **row,
                            "ems_flooded_km2": round(truth_km2, 3),
                            "tpi_flagged_km2": round(float(km2[pred].sum()), 3),
                            "overlap_km2": round(float(km2[(pred & truth)].sum()), 3),
                            "domain_km2": round(float(km2[domain].sum()), 3),
                            "aois": sorted(aoi_by_date[day]),
                            "scope": COVERAGE,
                        }
                    )
    obs.close()

    # flagged before onset: union of the full EMS flood footprint
    all_flood = _dissolve([g for gs in by_date.values() for g in gs])
    all_aoi = _dissolve(list(aoi_geoms.values()))
    domain_all = rasterize_mask([all_aoi], transform, shape, all_touched=False)
    ems_all = rasterize_mask([all_flood], transform, shape, all_touched=False) & domain_all
    rows_idx = np.nonzero(ems_all.ravel())[0]
    w = km2.ravel()[rows_idx]
    ev = wb.evaluate(cog, "rs_2024", "flood", series, rows_idx, w, ONSET)
    bl = wb.by_lead(ev, ONSET)
    obs_band_row = next((c for c in ev["by_date_observed"] if c["date"] == "2024-04-29"), None)
    gfs_rows = {}
    for issue in GFS_ONSET_ISSUES:
        r = next((g for g in ev["by_issue_gfs"] if g["issue"] == issue.isoformat()), None)
        gfs_rows[issue.isoformat()] = (
            {
                "alto_or_critico_pct": r["alto_or_critico_pct"],
                "critico_pct": r["critico_pct"],
                "frames": r["frames"],
            }
            if r
            else None
        )
    alto_max = max((r["alto_or_critico_pct"] for r in gfs_rows.values() if r), default=None)
    crit_max = max((r["critico_pct"] for r in gfs_rows.values() if r), default=None)
    flagged = {
        "anchor_utc": ONSET.isoformat(),
        "anchor_local": "2024-04-29 00:00 BRT (UTC-3)",
        "denominator_km2": round(float(w.sum()), 3),
        "denominator_note": "union of all EMS-mapped flood polygons (all dates/AOIs), "
        "rasterized centre-in on the ~90 m grid",
        "observed_band_20240429": (
            {
                "alto_or_critico_pct": obs_band_row["alto_or_critico_pct"],
                "critico_pct": obs_band_row["critico_pct"],
            }
            if obs_band_row
            else None
        ),
        "gfs_issued": gfs_rows,
        "gfs_any_issue_alto_or_critico_pct": alto_max,
        "gfs_any_issue_critico_pct": crit_max,
        "by_lead": bl,
        "scope": COVERAGE,
        "definition": "% of EMS-mapped flood area whose ~90 m TPI cell was already Alto+ (>0.4) / "
        "Critico (>0.6) in: observed = series band dated 2024-04-29 (rain Apr 26-28, "
        "all information before the 04-29 03Z onset); gfs = the GFS 00Z runs issued "
        "2024-04-26/27/28, max over d1..d3 frames depicting dates <= the onset day "
        "(tools/warned_before.py evaluate/by_lead logic)",
    }

    headline_date = max(by_date, key=lambda d: len(aoi_by_date[d]))
    head = next(
        r
        for r in rows
        if r["image_date_utc"] == headline_date
        and r["rasterization"] == "center"
        and r["threshold"] == "alto_plus"
        and r["tpi_source"] == "observed"
    )
    return {
        "activation": meta["activation"]["id"],
        "licence": meta["licence"],
        "coverage_limit": COVERAGE,
        "sources": [
            {
                k: mp[k]
                for k in (
                    "aoi",
                    "product",
                    "type",
                    "version",
                    "image_acquisition_utc",
                    "sensors",
                    "download_url",
                )
            }
            for mp in meta["products"]
        ],
        "headline": {
            "image_date_utc": headline_date,
            "rasterization": "center",
            "threshold": "alto_plus",
            "tpi_source": "observed",
            **head,
        },
        "definitions": {
            "rasterization": {
                "center": "cell centre inside an EMS flood polygon (rasterio all_touched=False)",
                "area_weighted": "cell flooded when >= 0.5 of its area is covered "
                "(8x supersampled rasterize)",
            },
            "threshold": {"alto_plus": "TPI_flood > 0.4", "critico": "TPI_flood > 0.6"},
            "tpi_source": {
                "observed": "observed series band dated E (rain days E-3..E-1, docs/conventions-dates.md)",
                "gfs_issued_24h": "GFS 00Z run issued E-1, max over d1..d3 frames depicting dates <= E",
            },
            "domain": "cells inside the EMS AOI extents mapped on date E only",
            "flood_reference": "union of EMS '5-Flood' polygons (Flooded area + Flood trace + "
            "Maximum flood extent), BLP hydrographyA permanent water excluded",
        },
        "flagged_before_onset": flagged,
        "rows": rows,
        "scope": COVERAGE,
    }


METHOD = (
    "Copernicus EMS Rapid Mapping activation EMSR720 (event GLIDE FL-2024-000063-BRA, "
    "activated 2024-05-03) mapped the May 2024 Rio Grande do Sul floods with semi-automatic "
    "SAR delineation (DEL products: COSMO-SkyMed, RADARSAT-2, PAZ) and photo-interpreted "
    "optical grading (GRA: Pleiades Neo, GeoEye-1, WorldView-3) on five small Taquari-valley "
    "AOIs — Guapore, Encantado, Roca Sales, Das Antas Dam, Santa Tereza. All '5-Flood' "
    "polygons (observedEventA 'Flooded area' and 'Flood trace', plus DEL_MONIT "
    "'Maximum flood extent') were unioned per AOI+image date, minus the BLP hydrographyA "
    "permanent/reference-water layer, rasterized centre-in onto the ~90 m TPI_flood grid "
    "(area-weighted variant at >=50% coverage reported alongside), and scored ONLY inside "
    "the EMS AOI extents. IMPORTANT COVERAGE LIMIT: the EMS AOIs cover ~51.6-51.9 W, "
    "29.0-29.3 S — Porto Alegre, Guaiba, Canoas, Eldorado do Sul and Sao Leopoldo were never "
    "mapped, so every score is Taquari-valley-only evidence, not AOI-wide. For each EMS "
    "image date E, IoU/F1/precision/recall compare EMS flood cells with TPI_flood Alto+ "
    "(>0.4) and Critico (>0.6) in (a) the observed band dated E (rain days E-3..E-1) and "
    "(b) the GFS 00Z run issued 24 h before E restricted to frames depicting dates <= E. "
    "'Flagged before onset' measures the % of the EMS flood footprint already Alto+/Critico "
    "in the observed band dated 2024-04-29 and in the GFS runs issued 2024-04-26/27/28 "
    "(onset 2024-04-29 03:00Z; tools/warned_before.py evaluate/by_lead). Caveats: EMS "
    "polygons are imagery-mapped extent at a moment — they miss flooding under cloud/"
    "vegetation, GRA 'Flood trace' marks earlier water no longer present, and the EMS "
    "image date E pairs loosely with our frame convention (observed band E = rain "
    "E-3..E-1, GFS frames depicting E = rain E-2..E; docs/conventions-dates.md)."
)


def merge(data_root: Path, res: dict) -> None:
    path = data_root / "metrics.json"
    m = json.loads(path.read_text())
    rsb = m["cases"]["rs_2024"]
    h = res["headline"]
    rsb["flood_ems_iou"] = h["iou"]
    rsb["flood_ems_f1"] = h["f1"]
    rsb["flood_ems_precision"] = h["precision"]
    rsb["flood_ems_recall"] = h["recall"]
    rsb["flood_ems_flooded_km2"] = h["ems_flooded_km2"]
    rsb["flood_ems_flagged_before_onset_pct"] = res["flagged_before_onset"][
        "gfs_any_issue_alto_or_critico_pct"
    ]
    rsb["flood_ems_flagged_before_onset_critical_pct"] = res["flagged_before_onset"][
        "gfs_any_issue_critico_pct"
    ]
    rsb["flood_ems"] = res
    rsb["flood_ems_method"] = METHOD
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(m, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="extract EMS zips -> flood_ems.geojson + meta")
    b.add_argument("--raw-root", type=Path, default=RAW_ROOT)
    b.add_argument("--activation-json", type=Path, default=ACTIVATION_JSON)
    b.add_argument("--data-root", type=Path, default=REPO / "data")
    b.add_argument(
        "--simplify-m",
        type=float,
        default=None,
        help="simplify tolerance in metres (only if geojson too large)",
    )
    s = sub.add_parser("score", help="score TPI_flood vs the EMS reference")
    s.add_argument("--data-root", type=Path, default=REPO / "data")
    s.add_argument("--write", action="store_true", help="merge into metrics.json")
    args = ap.parse_args(argv)
    if args.cmd == "build":
        out = args.data_root / "rs_2024"
        res = build(args.raw_root, args.activation_json, out, args.simplify_m)
        gj = out / "geojson" / "flood_ems.geojson"
        size = gj.stat().st_size / 1e6
        print(json.dumps({k: v for k, v in res.items() if k != "meta"}, indent=2))
        print(f"flood_ems.geojson {size:.2f} MB")
        if size > 5:
            print("WARNING >5 MB — rerun with --simplify-m 20")
        return 0
    res = score(args.data_root)
    h = res["headline"]
    print("headline", json.dumps(h))
    for r in res["rows"]:
        if r["rasterization"] == "center":
            print(
                f"  {r['image_date_utc']} {r['threshold']:>10} {r['tpi_source']:>15} "
                f"IoU={r['iou']} F1={r['f1']} P={r['precision']} R={r['recall']} "
                f"ems={r['ems_flooded_km2']}km2"
            )
    print(
        "flagged_before_onset",
        json.dumps(
            {
                k: v
                for k, v in res["flagged_before_onset"].items()
                if k not in ("by_lead", "definition", "scope")
            }
        ),
    )
    if args.write:
        merge(args.data_root, res)
        print("merged into", args.data_root / "metrics.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
