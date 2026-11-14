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
from scipy.ndimage import label

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))  # `live` is importable only from the repo root

from live.render import classes_png_bytes, warp_to_3857
from live.tpi import classify
from tp.events import DATA_DIR, load_event
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
    "night_lights_pre": (("*_night_lights_pre_*.tif",), "gray"),
    "night_lights_post": (("*_night_lights_post_*.tif",), "gray"),
    "t_ml": (("*_t_ml_*.tif", "*_hazard_classes_*.tif"), "class"),
    "opera_validation": (("*_opera_validation_*.tif", "*_opera_*.tif"), "gray"),
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

    def record(layer: str, date_iso: str, png_rel: str, cog: Path, bounds, forecast: bool):
        # pinned _index.json entry shape for U8b's manifest consumer
        out["layers"].append({"id": layer, "date": date_iso, "png": png_rel,
                              "cog": str(cog.relative_to(data_dir)),
                              "bounds": bounds, "forecast": forecast})

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

    # ---- PNGs: single-date reference layers ---------------------------
    for layer, (patterns, kind) in SINGLE_LAYERS.items():
        cog = _first(cog_dir, patterns)
        if cog is None:
            skip(layer, f"no COG matching {'/'.join(patterns)}")
            continue
        day = _stem_date(cog) or ref
        png_path = png_dir / f"{layer}_{day:%Y%m%d}.png"
        rel = str(png_path.relative_to(data_dir))
        if dry_run:
            out["planned"].append(rel)
            record(layer, day.isoformat(), rel, cog, None, False)
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
        else:
            png, bounds = _gray_png_3857(arr, transform, crs, nodata)
        png_path.parent.mkdir(parents=True, exist_ok=True)
        png_path.write_bytes(png)
        out["written"].append(rel)
        record(layer, day.isoformat(), rel, cog, bounds, False)

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
        if not places:
            null("priority_zones.name", "no geojson/places.geojson")
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
            name = (min(places,
                        key=lambda p: (p[0] - c.x) ** 2 + (p[1] - c.y) ** 2)[2]
                    if places else None)
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
