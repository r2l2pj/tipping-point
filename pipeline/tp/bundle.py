"""Publish a finished case into the stable app-read bundle (doc v4, L4).

For one event id (``rs-2024-05``, ``wa-2025-12``), emits under
``data/<case>/`` (``rs_2024``, ``wa_2025`` — case-id directories, decision
L4) the stable names the app reads: EPSG:3857 PNGs per layer/day plus a
``png/_index.json`` sidecar for the manifest builder (U8b), the fixed-name
GeoJSON products, and ``series/imerg_smap_daily.json``; it also refreshes
``data/events.json`` ``layers[]`` at runtime (that file is never
committed).

Every missing source is recorded under ``skipped`` — never fatal — so the
manifest builder can emit ``null`` + reason for products a case lacks.
Class grids reuse ``live.tpi.classify`` + ``live.render`` (warp to
EPSG:3857, palette RGBA) instead of re-implementing the render path.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path

import numpy as np
import rasterio
import rasterio.features
import rasterio.warp
from scipy.ndimage import label

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))  # `live` is importable only from the repo root

from live.config import OPERA_CONFUSION_LABELS, OPERA_CONFUSION_PALETTE
from live.render import classes_png_bytes, palette_png_bytes, warp_to_3857
from live.tpi import classify
from tp.events import DATA_DIR, load_event
from tp.metrics import merge_metrics
from tp.polygonize import mask_to_geojson

EN_CLASS_LABELS = ["Low", "Moderate", "High", "Critical"]
TPI_THRESHOLD = 0.6            # Alto cut — config.yaml tpi_classes[-1]
FORECAST_DAYS = 3

# layer_id -> (cog glob patterns, render kind). Series/forecast layers are
# handled separately because their date comes from band names / the stem.
SINGLE_LAYERS = {
    "hillshade": (("*_hillshade_*.tif",), "gray"),
    "nisar_pre": (("*_nisar_pre_*.tif",), "gray"),
    "nisar_post": (("*_nisar_post_*.tif",), "gray"),
    "nisar_ref": (("*_nisar_ref_*.tif",), "gray"),
    "night_lights_pre": (("*_night_lights_pre_*.tif",), "gray"),
    "night_lights_post": (("*_night_lights_post_*.tif",), "gray"),
    "t_ml": (("*_t_ml_*.tif", "*_hazard_classes_*.tif"), "class"),
    "opera_validation": (("*_opera_validation_*.tif", "*_opera_*.tif"), "confusion"),
}

# product -> (cog glob patterns, mask values, feature "status" label,
#             min_area_km2, simplify_m). The tolerances keep display
# polygons small enough for the fallback branch (GitHub file cap).
MASK_PRODUCTS = {
    "permanent_water": (("*_water_nisar_*.tif",), (1,), None, 0.005, 25.0),
    "flood_confirmed": (("*_flood_nisar_*.tif",), (1,), None, 0.001, 10.0),
    # 1 candidate + 2 high-confidence — both stay "candidate", never confirmed
    "landslide_candidates": (
        ("*_landslide_candidates_*.tif", "*_hls_scar_*.tif"), (1, 2), "candidate",
        0.001, 10.0),
}

POP_PATTERNS = ("*_pop_*.tif", "*_gpw_*.tif", "*_population_*.tif")

_FORECAST_RE = re.compile(r"_tpi_forecast_d(\d)_(\d{8})")
_DATE_RE = re.compile(r"\d{8}")


def _dates_in(text: str) -> list[dt.date]:
    out = []
    for m in _DATE_RE.finditer(text or ""):
        try:
            out.append(dt.datetime.strptime(m.group(0), "%Y%m%d").date())
        except ValueError:
            continue
    return out


def _stem_date(path: Path) -> dt.date | None:
    """Last YYYYMMDD run in a filename stem (the v4 <AAAAMMDD> suffix)."""
    dates = _dates_in(path.stem)
    return dates[-1] if dates else None


def _band_date(src, b: int) -> dt.date | None:
    """Band name 'YYYYMMDD' -> date (GEE series exports, one band per day)."""
    for text in (src.descriptions[b - 1], *src.tags(b).values()):
        dates = _dates_in(str(text or ""))
        if dates:
            return dates[-1]
    return None


# Published-geojson budget: every product must be < 1 MB (L15 — the app
# was inlining multi-MB layers into the RSC payload). Per-product rules:
# `tol` simplify tolerance in metres (preserve topology), `min_km2`
# drops tiny polygons, `props` keeps only these property keys.
# Full-detail sources stay as COGs / GEE table assets, never geojson.
GEOJSON_COMPACT = {
    "critical_areas":      {"tol_m": 40, "min_km2": 0.02},
    "permanent_water":     {"tol_m": 80, "min_km2": 0.1},
    "flood_confirmed":     {"tol_m": 20, "min_km2": 0.005},
    "municipal_boundaries": {"tol_m": 80},
    "landslide_catalog":   {"tol_m": 0, "props": [], "multipoint": True},
    "priority_zones":      {"tol_m": 15, "min_km2": 0.01},
    # 30 m DIST-ALERT scar candidates: half a pixel, never drop small scars
    "landslide_candidates": {"tol_m": 15},
}
GEOJSON_BUDGET_BYTES = 980 * 1024
COORD_DECIMALS = 5                    # ~1 m — plenty for display


def _round_coords(coords, nd=COORD_DECIMALS):
    if isinstance(coords[0], (int, float)):
        return [round(float(v), nd) for v in coords]
    return [_round_coords(c, nd) for c in coords]


def _compact_geojson(path: Path, rules: dict) -> int:
    """Simplify + round a FeatureCollection in place; returns final size."""
    import geopandas as gpd
    from shapely.geometry import shape, mapping

    doc = json.loads(path.read_text())
    feats = doc.get("features", [])
    if not feats:
        return path.stat().st_size
    keep_props = rules.get("props")
    min_km2 = rules.get("min_km2")
    tol = rules.get("tol_m", 0) / 111320.0  # m -> approx degrees
    if rules.get("multipoint"):
        # Point catalogs: Feature-per-point boilerplate is ~60% of the
        # bytes — one MultiPoint keeps every coordinate in budget.
        coords = [_round_coords(f["geometry"]["coordinates"])
                  for f in feats if (f.get("geometry") or {}).get("type")
                  == "Point"]
        blob = json.dumps(
            {"type": "FeatureCollection", "features": [
                {"type": "Feature",
                 "geometry": {"type": "MultiPoint", "coordinates": coords},
                 "properties": {}}]},
            separators=(",", ":"), ensure_ascii=False) + "\n"
        path.write_text(blob)
        return len(blob)
    for _ in range(4):  # escalate tolerance if still over budget
        out = []
        for f in feats:
            geom = f.get("geometry")
            if not geom:
                continue
            g = shape(geom)
            if tol:
                g = g.simplify(tol, preserve_topology=True)
                if g.is_empty:
                    continue
            if min_km2:
                area = gpd.GeoSeries([g], crs="EPSG:4326") \
                    .to_crs("EPSG:6933").area.iloc[0] / 1e6
                if area < min_km2:
                    continue
            props = f.get("properties") or {}
            if keep_props is not None:
                props = {k: props[k] for k in keep_props if k in props}
            out.append({"type": "Feature",
                        "geometry": mapping(g),
                        "properties": props})
        for f in out:
            f["geometry"]["coordinates"] = _round_coords(
                f["geometry"]["coordinates"])
        blob = json.dumps({"type": "FeatureCollection", "features": out},
                          separators=(",", ":"), ensure_ascii=False) + "\n"
        if len(blob) <= GEOJSON_BUDGET_BYTES or not tol:
            break
        tol *= 2.5
    path.write_text(blob)
    return len(blob)


def _compact_geojsons(gj_dir: Path, out: dict, dry_run: bool) -> None:
    for path in sorted(gj_dir.glob("*.geojson")):
        if "_nisar_" in path.stem:
            # ingest intermediates — full detail stays in GEE/COGs,
            # never published (would also be mangled by simplification)
            continue
        rules = GEOJSON_COMPACT.get(path.stem, {"tol_m": 40})
        if dry_run:
            out["planned"].append(f"{path.name} compact")
            continue
        size = _compact_geojson(path, rules)
        if size > GEOJSON_BUDGET_BYTES:
            out["nulls"].append({"target": path.name,
                                 "reason": f"geojson {size}B > budget"})


def _valid(arr: np.ndarray, nodata) -> np.ndarray:
    v = np.isfinite(np.asarray(arr, dtype=np.float64))
    if nodata is not None:
        v &= arr != nodata
    return v


def _class_png_3857(class_arr: np.ndarray, valid: np.ndarray, transform, crs) -> tuple[bytes, list]:
    """Same pipeline as live.render.layer_png_3857 but honouring the COG CRS
    (contract allows NISAR-native CRSs besides EPSG:4326)."""
    cls3857, _, bounds = warp_to_3857(np.asarray(class_arr, dtype=np.uint8), transform, src_crs=crs)
    val3857, _, _ = warp_to_3857(np.asarray(valid, dtype=np.uint8), transform, src_crs=crs)
    return classes_png_bytes(cls3857, val3857.astype(bool)), bounds


def _confusion_png_3857(arr: np.ndarray, transform, crs, nodata) -> tuple[bytes, list]:
    """opera_validation 3-class TP/FP/FN grid -> RGBA PNG in 3857 with the
    pinned palette (0/nodata transparent)."""
    arr = np.asarray(arr)
    valid = _valid(arr, nodata) & (arr != 0)
    cls3857, _, bounds = warp_to_3857(np.asarray(arr, dtype=np.uint8), transform, src_crs=crs)
    val3857, _, _ = warp_to_3857(np.asarray(valid, dtype=np.uint8), transform, src_crs=crs)
    return palette_png_bytes(cls3857, val3857.astype(bool), OPERA_CONFUSION_PALETTE), bounds


def _gray_png_3857(arr: np.ndarray, transform, crs, nodata) -> tuple[bytes, list]:
    """Continuous raster (hillshade, dB, radiance) -> gray RGBA PNG in 3857,
    linear 2–98 percentile stretch, nodata transparent."""
    arr = np.asarray(arr, dtype=np.float64)
    valid = _valid(arr, nodata)
    g, _, bounds = warp_to_3857(np.where(valid, arr, 0.0), transform, src_crs=crs)
    v, _, _ = warp_to_3857(valid.astype(np.uint8), transform, src_crs=crs)
    v = v.astype(bool)
    vals = g[v]
    lo, hi = (np.percentile(vals, (2, 98)) if vals.size else (0.0, 1.0))
    if not hi > lo:
        hi = lo + 1.0
    u8 = np.clip((g - lo) / (hi - lo) * 255.0, 0, 255).astype(np.uint8)
    bands = np.stack([u8, u8, u8, np.where(v, 255, 0).astype(np.uint8)])
    with rasterio.MemoryFile() as mf:
        with mf.open(driver="PNG", height=u8.shape[0], width=u8.shape[1],
                     count=4, dtype="uint8") as dst:
            dst.write(bands)
        return mf.read(), bounds


def _forecast_hazards(src) -> list[tuple[int, str]]:
    """(band, hazard) for a forecast COG: 'flood'/'slide' in the band name
    wins; an unnamed single band is slide, two unnamed bands slide+flood."""
    out = []
    for i in range(1, src.count + 1):
        desc = str(src.descriptions[i - 1] or "").lower()
        if "flood" in desc:
            haz = "flood"
        elif "slide" in desc or src.count == 1 or i == 1:
            haz = "slide"
        elif i == 2:
            haz = "flood"
        else:
            continue
        out.append((i, haz))
    return out


def _first(cog_dir: Path, patterns: tuple[str, ...]) -> Path | None:
    for pat in patterns:
        hits = sorted(cog_dir.glob(pat))
        if hits:
            return hits[0]
    return None


def _load_places(path: Path) -> list[tuple[float, float, str]]:
    if not path.exists():
        return []
    fc = json.loads(path.read_text())
    out = []
    for f in fc.get("features", []):
        try:
            x, y = f["geometry"]["coordinates"][:2]
            out.append((float(x), float(y), str(f["properties"].get("name", ""))))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _load_municipal(path: Path):
    """[(shapely geom, name)] from municipal_boundaries.geojson — the
    containing polygon is the zone's display name (prod showed the
    nearest-place heuristic labelling far-south WA pins 'Everett')."""
    if not path.exists():
        return []
    from shapely.geometry import shape
    out = []
    for f in json.loads(path.read_text()).get("features", []):
        try:
            out.append((shape(f["geometry"]),
                        str((f.get("properties") or {}).get("name", ""))))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _critical_stats(bands_by_date: dict, pop_cog: Path | None,
                    people: bool = True) -> dict:
    """{iso_date: {critical_km2, critical_slide_km2, critical_flood_km2,
    people_critical}} — ONE definition shared by the series rows and the
    metrics scalar: area of pixels with TPI > TPI_THRESHOLD (strict — doc class table), per hazard,
    geodesic on the source lon/lat grid (per-row cos(lat) correction);
    critical_km2 is the slide∪flood union. people_critical = GPWv4 count
    under the union via coverage-fraction reproject to the ~1 km GPW grid
    (GPW -3.4e38 water/nodata values masked)."""
    import rasterio.warp

    stats = {}
    days = sorted(set(bands_by_date.get("slide", {}))
                  | set(bands_by_date.get("flood", {})))
    for day in days:
        mask, tr, crs = None, None, None
        per_hazard = {}
        for h in ("slide", "flood"):
            cb = bands_by_date.get(h, {}).get(day)
            if cb is None:
                continue
            with rasterio.open(cb[0]) as src:
                a = np.asarray(src.read(cb[1]), dtype=np.float64)
                # doc class table: Crítico = "acima de 0,6" — strict, same
                # cut classify() uses for class 3
                m = _valid(a, src.nodata) & (a > TPI_THRESHOLD)
                tr, crs = src.transform, src.crs
            per_hazard[h] = m
            mask = m if mask is None else (mask | m)
        if mask is None:
            continue
        lats = tr.f + (np.arange(mask.shape[0]) + 0.5) * tr.e
        px_km2 = (abs(tr.a) * 111320.0 * np.cos(np.deg2rad(lats))
                  * abs(tr.e) * 110540.0) / 1e6
        hz_km2 = {h: round(float((m * px_km2[:, None]).sum()), 3)
                  for h, m in per_hazard.items()}
        n_people = None
        if pop_cog is not None and people:
            with rasterio.open(pop_cog) as ps:
                frac = np.zeros((ps.height, ps.width), dtype=np.float32)
                rasterio.warp.reproject(
                    mask.astype(np.float32), frac,
                    src_transform=tr, src_crs=crs,
                    dst_transform=ps.transform, dst_crs=ps.crs,
                    resampling=rasterio.warp.Resampling.average)
                parr = np.asarray(ps.read(1), dtype=np.float64)
                # GPW marks water/uninhabited as -3.4e38 with no nodata tag
                pvalid = _valid(parr, ps.nodata) & (parr >= 0)
                n_people = int(round(float(
                    (frac * np.where(pvalid, parr, 0.0)).sum())))
        stats[day.isoformat()] = {
            "critical_km2": round(float((mask * px_km2[:, None]).sum()), 3),
            "critical_slide_km2": hz_km2.get("slide"),
            "critical_flood_km2": hz_km2.get("flood"),
            "people_critical": n_people}
    return stats


def _pop_sum(pop_path: Path, geom: dict) -> float:
    import rasterio.features

    with rasterio.open(pop_path) as src:
        mask = rasterio.features.geometry_mask(
            [geom], out_shape=(src.height, src.width),
            transform=src.transform, invert=True)
        arr = np.asarray(src.read(1), dtype=np.float64)
        vals = arr[mask & _valid(arr, src.nodata)]
    return float(vals.sum())


def _stamp_status(path: Path, status: str) -> None:
    fc = json.loads(path.read_text())
    for f in fc.get("features", []):
        f.setdefault("properties", {})["status"] = status
    path.write_text(json.dumps(fc, ensure_ascii=False, indent=2) + "\n")


def _swot_by_date(ser_dir: Path, case: str) -> dict[str, float]:
    """date -> river level from series/<case>_swot_*.json records
    (tp/swot.py writes df.to_json(orient='records'): time_str + wse)."""
    out = {}
    for f in sorted(ser_dir.glob(f"{case}_swot_*.json")):
        recs = json.loads(f.read_text())
        if isinstance(recs, dict):
            recs = recs.get("records", [])
        for rec in recs if isinstance(recs, list) else []:
            if not isinstance(rec, dict):
                continue
            when = str(rec.get("time_str") or rec.get("date") or rec.get("time") or "")
            level = rec.get("wse", rec.get("river_m"))
            dates = _dates_in(when.replace("-", ""))
            if dates and level is not None:
                out[dates[0].isoformat()] = float(level)
    return out


def publish_case(event_id: str, dry_run: bool = False,
                 data_dir: Path | None = None,
                 events_path: Path | None = None) -> dict:
    """Emit the stable app bundle for ``event_id``; return a summary dict.

    ``dry_run`` enumerates planned writes and missing sources without
    writing anything. ``data_dir`` / ``events_path`` default to the repo
    ``data/`` tree; tests pass tmp dirs.
    """
    data_dir = Path(data_dir) if data_dir else DATA_DIR
    events_path = Path(events_path) if events_path else data_dir / "events.json"
    ev = load_event(event_id)
    ref = dt.date.fromisoformat(ev["reference_date"])
    case = "_".join(event_id.split("-")[:2])   # rs-2024-05 -> rs_2024 (L4: case dirs)
    edir = data_dir / case
    cog_dir, png_dir, gj_dir, ser_dir = (edir / s for s in ("cog", "png", "geojson", "series"))

    out: dict = {"event": event_id, "dry_run": dry_run, "planned": [],
                 "written": [], "skipped": [], "nulls": [],
                 "layers": [], "series_days": 0}
    tpi_max: dict[str, float] = {}
    bands_by_date: dict[str, dict[dt.date, tuple[Path, int]]] = {"slide": {}, "flood": {}}

    def skip(target: str, reason: str) -> None:
        out["skipped"].append({"target": target, "reason": reason})

    def null(target: str, reason: str) -> None:
        out["nulls"].append({"target": target, "reason": reason})

    def record(layer: str, date_iso: str, png_rel: str, cog: Path, bounds,
               forecast: bool, extra: dict | None = None):
        # pinned _index.json entry shape for U8b's manifest consumer
        entry = {"id": layer, "date": date_iso, "png": png_rel,
                 "cog": str(cog.relative_to(data_dir)),
                 "bounds": bounds, "forecast": forecast}
        if extra:
            entry.update(extra)
        out["layers"].append(entry)

    def emit_png(layer: str, day: dt.date, cog: Path, band: int, forecast: bool):
        png_path = png_dir / f"{layer}_{day:%Y%m%d}.png"
        rel = str(png_path.relative_to(data_dir))
        if dry_run:
            out["planned"].append(rel)
            record(layer, day.isoformat(), rel, cog, None, forecast)
            return
        with rasterio.open(cog) as src:
            arr = np.asarray(src.read(band), dtype=np.float64)
            valid = _valid(arr, src.nodata)
            png, bounds = _class_png_3857(
                classify(np.where(valid, arr, np.nan)), valid, src.transform, src.crs)
        png_path.parent.mkdir(parents=True, exist_ok=True)
        png_path.write_bytes(png)
        out["written"].append(rel)
        record(layer, day.isoformat(), rel, cog, bounds, forecast)
        if valid.any():
            iso = day.isoformat()
            tpi_max[iso] = max(tpi_max.get(iso, 0.0), float(arr[valid].max()))

    # ---- PNGs: per-day TPI series -------------------------------------
    for hazard in ("slide", "flood"):
        layer = f"tpi_{hazard}"
        cogs = sorted(cog_dir.glob(f"{case}_tpi_{hazard}_*.tif"))
        if not cogs:
            skip(layer, f"no {cog_dir.name}/{case}_tpi_{hazard}_*.tif")
        for cog in cogs:
            try:
                src = rasterio.open(cog)
            except rasterio.errors.RasterioIOError as e:
                skip(cog.name, str(e))
                continue
            with src:
                for b in range(1, src.count + 1):
                    day = _band_date(src, b)
                    if day is None:
                        skip(f"{cog.name}#b{b}", "band name has no YYYYMMDD date")
                        continue
                    bands_by_date[hazard][day] = (cog, b)
                    emit_png(layer, day, cog, b, forecast=False)

    # ---- perfect-forecast COGs ----------------------------------------
    # forecast_source="perfect-forecast": the D+k forecast issued on the
    # reference date is the observed band of ref+k (SCHEMA). Materialize
    # <case>_tpi_forecast_d<k>_<ref>.tif (2 bands: slide, flood) so the
    # emit loop below can render + record them like every other layer.
    for k in range(1, FORECAST_DAYS + 1):
        fc = cog_dir / f"{case}_tpi_forecast_d{k}_{ref:%Y%m%d}.tif"
        depicts = ref + dt.timedelta(days=k)
        if fc.exists() or dry_run:
            continue
        srcs = [(bands_by_date[h].get(depicts), h)
                for h in ("slide", "flood")]
        srcs = [(cb, h) for cb, h in srcs if cb is not None]
        if not srcs:
            null(f"tpi_*_d{k}", f"no observed bands for {depicts}")
            continue
        with rasterio.open(srcs[0][0][0]) as base:
            prof = base.profile
        prof.update(count=len(srcs))
        with rasterio.open(fc, "w", **prof) as dst:
            for i, ((cpath, b), haz) in enumerate(srcs, 1):
                with rasterio.open(cpath) as s:
                    dst.write(s.read(b), i)
                dst.set_band_description(i, haz)
        out["written"].append(str(fc.relative_to(data_dir)))

    # ---- PNGs: perfect-forecast D+1..D+3 ------------------------------
    for cog in sorted(cog_dir.glob(f"{case}_tpi_forecast_d*_*.tif")):
        m = _FORECAST_RE.search(cog.stem)
        if not m:
            skip(cog.name, "stem is not <case>_tpi_forecast_d<k>_<AAAAMMDD>")
            continue
        k = int(m.group(1))
        issue = dt.datetime.strptime(m.group(2), "%Y%m%d").date()
        if not 1 <= k <= FORECAST_DAYS:
            skip(cog.name, f"forecast day d{k} outside 1..{FORECAST_DAYS}")
            continue
        depicts = issue + dt.timedelta(days=k)
        try:
            with rasterio.open(cog) as src:
                hazards = _forecast_hazards(src)
        except rasterio.errors.RasterioIOError as e:
            skip(cog.name, str(e))
            continue
        for b, hazard in hazards:
            emit_png(f"tpi_{hazard}_d{k}", depicts, cog, b, forecast=True)

    # ---- per-day critical area + exposed population (L19 alert card) --
    crit_stats = _critical_stats(
        bands_by_date, _first(cog_dir, POP_PATTERNS), people=not dry_run)
    if not any(s["people_critical"] is not None for s in crit_stats.values()):
        null("series.people_critical", "no population COG (GPW)")

    # ---- PNGs: single-date reference layers ---------------------------
    for layer, (patterns, kind) in SINGLE_LAYERS.items():
        cog = _first(cog_dir, patterns)
        if cog is None:
            skip(layer, f"no COG matching {'/'.join(patterns)}")
            continue
        day = _stem_date(cog) or ref
        png_path = png_dir / f"{layer}_{day:%Y%m%d}.png"
        rel = str(png_path.relative_to(data_dir))
        extra = {"legend": list(OPERA_CONFUSION_LABELS)} if kind == "confusion" else None
        if dry_run:
            out["planned"].append(rel)
            record(layer, day.isoformat(), rel, cog, None, False, extra)
            continue
        try:
            with rasterio.open(cog) as src:
                arr = src.read(1)
                transform, crs, nodata = src.transform, src.crs, src.nodata
        except rasterio.errors.RasterioIOError as e:
            skip(cog.name, str(e))
            continue
        if kind == "class":
            valid = _valid(arr, nodata)
            png, bounds = _class_png_3857(arr, valid, transform, crs)
        elif kind == "confusion":
            png, bounds = _confusion_png_3857(arr, transform, crs, nodata)
        else:
            png, bounds = _gray_png_3857(arr, transform, crs, nodata)
        png_path.parent.mkdir(parents=True, exist_ok=True)
        png_path.write_bytes(png)
        out["written"].append(rel)
        record(layer, day.isoformat(), rel, cog, bounds, False, extra)

    # ---- geojson: mask products ---------------------------------------
    for product, (patterns, values, status, min_area, simplify) in MASK_PRODUCTS.items():
        cog = _first(cog_dir, patterns)
        if cog is None:
            skip(product, f"no COG matching {'/'.join(patterns)}")
            continue
        target = gj_dir / f"{product}.geojson"
        rel = str(target.relative_to(data_dir))
        if dry_run:
            out["planned"].append(rel)
            continue
        mask_to_geojson(cog, target, values=values,
                        min_area_km2=min_area, simplify_m=simplify)
        if status:
            _stamp_status(target, status)   # candidates — never 'confirmed'
        out["written"].append(rel)

    # ---- geojson: priority zones (event-day TPI >= Alto) --------------
    # Two products from the same dissolved blobs: `critical_areas` keeps
    # the full footprint, `priority_zones` is the ranked top-20 list the
    # app renders as points (L15 — raw per-cell polygons crashed it).
    zones = []
    for hazard, series in (("landslide", "slide"), ("flood", "flood")):
        hit = bands_by_date[series].get(ref)
        if hit is None:
            if bands_by_date[series]:
                skip(f"priority_zones/{hazard}",
                     f"series has no {ref:%Y%m%d} (event day) band")
            continue
        cog, b = hit
        with rasterio.open(cog) as src:
            tpi = np.asarray(src.read(b), dtype=np.float64)
            transform, nodata = src.transform, src.nodata
        valid = _valid(tpi, nodata)
        cls = classify(np.where(valid, tpi, np.nan))
        mask = (cls >= 2) & valid
        lab, n = label(mask, structure=np.ones((3, 3)))
        sizes = np.bincount(lab.ravel())
        # Footprint sieve ~0.05 km² (pixel dust); the ranked top-20 list
        # needs no stronger cut — score decides.
        px_km2 = abs(transform.a * transform.e) * 111320 ** 2 / 1e6
        min_px = max(1, int(np.ceil(0.05 / max(px_km2, 1e-9))))
        for i in range(1, n + 1):
            if sizes[i] < min_px:
                continue
            blob = lab == i
            geoms = list(rasterio.features.shapes(
                np.uint8(blob), mask=blob, transform=transform))
            if not geoms:
                continue
            zones.append({"geometry": geoms[0][0], "hazard": hazard,
                          "tpi": float(tpi[blob].max()),
                          "class": int(cls[blob].max())})
    if zones:
        import geopandas as gpd
        from shapely.geometry import shape

        gdf = gpd.GeoDataFrame(
            [{"geometry": shape(z["geometry"]), "hazard": z["hazard"],
              "tpi": z["tpi"], "class": z["class"]} for z in zones],
            geometry="geometry", crs="EPSG:4326")
        gdf["area_km2"] = gdf.to_crs(gdf.estimate_utm_crs()).area / 1e6
        places = _load_places(gj_dir / "places.geojson")
        muni = _load_municipal(gj_dir / "municipal_boundaries.geojson")
        if not muni and not places:
            null("priority_zones.name",
                 "no municipal_boundaries.geojson or places.geojson")
        pop = _first(cog_dir, POP_PATTERNS)
        if pop is None:
            null("priority_zones.people", "no population COG")

        def _people(geom):
            if pop is None:
                return None
            try:
                return int(_pop_sum(pop, geom.__geo_interface__))
            except (OSError, ValueError) as e:
                null("priority_zones.people", f"{pop.name}: {e}")
                return None

        # full footprint -> critical_areas.geojson
        gdf["people"] = [_people(g) for g in gdf["geometry"]]
        gdf["score"] = gdf["area_km2"] * gdf["tpi"]
        gdf = gdf.sort_values(
            "people" if gdf["people"].notna().any() else "score",
            ascending=False).reset_index(drop=True)

        def _feature(row, rank):
            geom = row["geometry"]
            c = geom.representative_point()
            name = next((nm for g, nm in muni if nm and g.contains(c)),
                        None)
            if name is None and places:
                name = min(
                    places,
                    key=lambda p: (p[0] - c.x) ** 2 + (p[1] - c.y) ** 2)[2]
            return {"type": "Feature", "geometry": geom.__geo_interface__,
                    "properties": {
                        "rank": rank, "name": name,
                        "tpi": round(float(row["tpi"]), 4),
                        "class": EN_CLASS_LABELS[min(int(row["class"]), 3)],
                        "hazard": row["hazard"],
                        "area_km2": round(float(row["area_km2"]), 4),
                        "people": (None if row["people"] is None
                                   else int(row["people"])),
                        "label_lon": round(float(c.x), 6),
                        "label_lat": round(float(c.y), 6)}}

        for target_name, rows in (
                ("critical_areas", gdf),
                ("priority_zones", gdf.head(20))):
            feats = [_feature(row, rank)
                     for rank, (_, row) in enumerate(rows.iterrows(), start=1)]
            target = gj_dir / f"{target_name}.geojson"
            rel = str(target.relative_to(data_dir))
            if dry_run:
                out["planned"].append(rel)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(
                    {"type": "FeatureCollection", "features": feats},
                    ensure_ascii=False, indent=2) + "\n")
                out["written"].append(rel)
    elif not any(bands_by_date[s] for s in ("slide", "flood")):
        skip("priority_zones", "no TPI series COG")
    elif any(bands_by_date[s].get(ref) for s in ("slide", "flood")):
        skip("priority_zones", "no Alto/Crítico pixels on the event day")
    # else: a per-hazard "no event-day band" skip was already recorded above

    # ---- series: imerg_smap_daily.json ---------------------------------
    days: dict[dt.date, dict] = {}
    rain_p95 = ev.get("rain_p95_mm")
    for f in sorted(ser_dir.glob(f"{case}_rain_soil_*.json")):
        rec = json.loads(f.read_text())
        dates = _dates_in(str(rec.get("date", "")).replace("-", "")) or _dates_in(f.stem)
        if not dates and not rec.get("days"):
            skip(f.name, "rain_soil record has no date")
            continue
        for row in rec.get("days", []):
            d = _dates_in(str(row.get("date", "")).replace("-", ""))
            if d:
                days[d[-1]] = {"imerg_mm": row.get("rain3_mm"),
                               "smap": row.get("smap")}
        if rec.get("days"):
            continue
        days[dates[-1]] = {"imerg_mm": rec.get("imerg_mm"), "smap": rec.get("smap")}
        rain_p95 = rain_p95 if rain_p95 is not None else rec.get("rain_p95_mm")
    if not days:
        skip("series/imerg_smap_daily.json", f"no {case}_rain_soil_*.json")
    else:
        river = _swot_by_date(ser_dir, case)
        if not river:
            null("imerg_smap_daily.river_m", f"no {case}_swot_*.json")
        if rain_p95 is None:
            null("imerg_smap_daily.rain_p95_mm", "no climatology scalar available")
        if not tpi_max:
            null("imerg_smap_daily.tpi", "no TPI series COG")
        rows = []
        for day in sorted(days):
            offset = (day - ref).days
            iso = day.isoformat()
            rows.append({"date": iso, "offset": offset,
                         "kind": "observed" if offset <= 0 else "forecast",
                         "imerg_mm": days[day]["imerg_mm"],
                         "smap": days[day]["smap"],
                         "tpi": tpi_max.get(iso),
                         "critical_km2": crit_stats.get(iso, {})
                                     .get("critical_km2"),
                         "critical_slide_km2": crit_stats.get(iso, {})
                                     .get("critical_slide_km2"),
                         "critical_flood_km2": crit_stats.get(iso, {})
                                     .get("critical_flood_km2"),
                         "people_critical": crit_stats.get(iso, {})
                                        .get("people_critical"),
                         "river_m": river.get(iso)})
        doc = {"event": event_id, "reference_date": ref.isoformat(),
               "rain_p95_mm": rain_p95, "tpi_threshold": TPI_THRESHOLD,
               "days": rows}
        target = ser_dir / "imerg_smap_daily.json"
        rel = str(target.relative_to(data_dir))
        if dry_run:
            out["planned"].append(rel)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
            out["written"].append(rel)
        out["series_days"] = len(rows)

        # peak-day critical footprint for the alert card
        flat, reasons = {}, {}
        first_crit = next(
            (d for d in sorted(tpi_max)
             if tpi_max[d] > TPI_THRESHOLD), None)
        flat["first_critical_at"] = first_crit
        peak = max(crit_stats.items(),
                   key=lambda kv: kv[1]["critical_km2"], default=None)
        flat["critical_km2"] = peak[1]["critical_km2"] if peak else None
        flat["critical_km2_date"] = peak[0] if peak else None
        flat["critical_slide_km2"] = \
            peak[1]["critical_slide_km2"] if peak else None
        flat["critical_flood_km2"] = \
            peak[1]["critical_flood_km2"] if peak else None
        flat["people_critical"] = peak[1]["people_critical"] if peak else None
        if not peak:
            reasons["critical_km2"] = "no TPI series"
        if peak and peak[1]["people_critical"] is None:
            reasons["people_critical"] = "no population COG (GPW)"
        if not dry_run:
            # the metrics of the data tree being published (tests pass a tmp tree)
            merge_metrics(event_id, flat, reasons=reasons, path=data_dir / "metrics.json")

    # ---- publish-size budget: compact every geojson product < 1 MB ----
    _compact_geojsons(gj_dir, out, dry_run)

    # ---- sidecar for the manifest builder (U8b) ------------------------
    index_path = png_dir / "_index.json"
    rel = str(index_path.relative_to(data_dir))
    if dry_run:
        out["planned"].append(rel)
    else:
        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(json.dumps(
            {"layers": out["layers"]}, ensure_ascii=False, indent=2) + "\n")
        out["written"].append(rel)

    # ---- refresh data/events.json layers[] (runtime only) --------------
    if dry_run:
        out["planned"].append(str(events_path.relative_to(data_dir)))
    elif events_path.exists():
        doc = json.loads(events_path.read_text())
        for e in doc.get("events", []):
            if e.get("id") == event_id:
                e["layers"] = [
                    {"id": ent["id"], "date": ent["date"], "png": ent["png"],
                     **({"forecast": True} if ent.get("forecast") else {})}
                    for ent in out["layers"]]
        events_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
        out["written"].append(str(events_path.relative_to(data_dir)))
    else:
        skip("events.json", f"{events_path} does not exist")

    return out
