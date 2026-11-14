"""Impact metrics + VIIRS night-light COGs — Python port of gee/09_impact.js.

Run AFTER `python -m gee.tpi` (it exports the <case>_tpi_slide_series asset
this reads). Per case:

  people_critical      GPWv4 population sum inside event-day TPI_slide > 0.6
  people_flooded       GPWv4 population sum inside the NISAR flood mask (wa only)
  flooded_km2          km2 of the NISAR flood mask (wa only)
  night_light_drop_pct VIIRS Black Marble: mean of the 7 nights after the event
                       vs the 30 nights before, inside the affected area
                       (critical ∪ flood)
  gpw_year             GPWv4 epoch closest to the event date

people_flooded/flooded_km2 are wa_2025-only and gated on the NISAR flood
polygon FeatureCollection existing (the flood_nisar* table asset listed in
data/<case>/assets.json, else the convention path under
projects/<GEE_PROJECT>/assets/tipping-point/wa_2025/); L10: vectors are
ingested as table assets and rasterized here in GEE — raster assets cannot
exist, so this is never ee.Image() on an uploaded COG;
when the gate fails they merge as null + reason. Numbers merge into
data/metrics.json (keyed by case) via tp.metrics.merge_metrics; the pre/post
night-light means are pulled locally as COGs (<caso>_night_lights_{pre,post}_
<AAAAMMDD>.tif → data/<case>/cog/ via getDownloadURL — same mechanism as
live/build_static.py) — the publisher renders their PNGs.

Usage: python -m gee.impact --case <id> [--dry-run|--tasks]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import ee
from tp.config import case as config_case
from tp.config import event_id as tp_event_id
from tp.config import load_config, threshold
from tp.events import DATA_DIR, artifact_stem
from tp.metrics import merge_metrics

try:
    from gee.common import ASSET_ROOT, GEE_PROJECT, aoi_geom, case_cfg, event_dates, init
except ModuleNotFoundError as exc:  # gee.common lands with U4 — --dry-run needs none of it
    if exc.name != "gee.common":
        raise
    GEE_PROJECT = "<GEE_PROJECT>"
    ASSET_ROOT = f"projects/{GEE_PROJECT}/assets/tipping-point"
    aoi_geom = case_cfg = event_dates = init = None

COL_GPW = "CIESIN/GPWv411/GPW_Population_Count"  # epochs every 5 yr, latest 2020
COL_VNP46A2 = "NASA/VIIRS/002/VNP46A2"  # DNB_BRDF_Corrected_NTL
BAND_NTL = "DNB_BRDF_Corrected_NTL"
BAND_MQF = "Mandatory_Quality_Flag"
BAND_POP = "population_count"
CRITICAL = threshold("tpi_classes", [0.6])[-1]  # tpi_classes cut for Crítico
NTL_PRE_NIGHTS = 30
NTL_POST_NIGHTS = 7
REDUCE_SCALE = 1000  # TPI series exported at 1 km; GPW is ~30 arc-second
NTL_SCALE = 500  # VNP46A2 is ~15 arc-second
MAX_PIXELS = 1e9


def _common_ready() -> None:
    if init is None:
        raise RuntimeError("gee.common not on this branch yet (U4) — only --dry-run works")


def _event_id(case_id: str) -> str:
    """'wa_2025' -> 'wa-2025-12' (the events/*.json entry for the case)."""
    return tp_event_id(case_id)


def _asset_root() -> str:
    # common.ASSET_ROOT is the template "projects/{GEE_PROJECT}/assets/tipping-point"
    if "{" in ASSET_ROOT:
        return ASSET_ROOT.format(GEE_PROJECT=GEE_PROJECT or "<GEE_PROJECT>")
    return ASSET_ROOT


def _assets_map(case_id: str) -> dict:
    """data/<case>/assets.json name->gee_id map; supports the ingest manifest
    ({"assets": [{name, gee_id, ...}]}) and the legacy flat name->path map."""
    assets_json = DATA_DIR / case_id / "assets.json"
    try:
        doc = json.loads(assets_json.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(doc, dict):
        return {}
    out = {k: v for k, v in doc.items() if isinstance(v, str) and v}
    for row in doc.get("assets", []):
        if isinstance(row, dict) and row.get("name") and row.get("gee_id"):
            out[row["name"]] = row["gee_id"]
    return out


def _asset(case_id: str, name: str) -> str:
    """EE asset path (L7): data/<case>/assets.json name->path when present,
    else the convention projects/<GEE_PROJECT>/assets/tipping-point/<case>/<name>."""
    mapped = _assets_map(case_id).get(name)
    if mapped:
        return mapped
    return f"{_asset_root()}/{case_id}/{name}"


def _flood_asset(case_id: str) -> str:
    """NISAR flood polygon FeatureCollection id (L10): the flood_nisar* table
    asset recorded by ingest_assets.py, else the convention path."""
    assets_json = DATA_DIR / case_id / "assets.json"
    try:
        doc = json.loads(assets_json.read_text())
    except (OSError, json.JSONDecodeError):
        doc = {}
    rows = doc.get("assets", []) if isinstance(doc, dict) else []
    for row in rows:  # manifest rows: table rows first (L10 flood polygons)
        if (isinstance(row, dict) and "flood" in str(row.get("name", ""))
                and row.get("gee_id") and row.get("kind") == "table"):
            return row["gee_id"]
    for name, gee_id in _assets_map(case_id).items():
        if "flood" in name:
            return gee_id
    return f"{_asset_root()}/{case_id}/flood_nisar"


def _event_band_index(case_id: str, cfg: dict) -> int:
    """Band of the event day in the TPI series (= window_before_days = 10)."""
    day = str(cfg["event_date"])
    dates = event_dates(case_id)
    return dates.index(day) if day in dates else int(cfg.get("window_before_days", 10))


def _gpw_nearest(ref):
    """GPW epoch closest to the event year; .date().get('year') -> gpw_year."""
    nearest = ee.ImageCollection(COL_GPW).map(
        lambda img: img.set("d", img.date().difference(ref, "year").abs())
    ).sort("d").first()
    return ee.Image(nearest)


def _ntl_masked():
    return ee.ImageCollection(COL_VNP46A2).select([BAND_NTL, BAND_MQF]).map(
        lambda img: img.select(BAND_NTL).updateMask(img.select(BAND_MQF).eq(0))
    )


def _ntl_mean(ref, days_before: int, days_after: int):
    return _ntl_masked().filterDate(
        ref.advance(-days_before, "day"), ref.advance(days_after, "day")
    ).mean()


def _region_mean(img, mask, aoi):
    return img.updateMask(mask).reduceRegion(
        reducer=ee.Reducer.mean(), geometry=aoi, scale=NTL_SCALE, maxPixels=MAX_PIXELS
    ).get(BAND_NTL)


def _pop_sum(pop, mask, aoi):
    return pop.updateMask(mask).reduceRegion(
        reducer=ee.Reducer.sum(), geometry=aoi, scale=REDUCE_SCALE, maxPixels=MAX_PIXELS
    ).get(BAND_POP)


def flood_source(asset_id: str | None) -> str | None:
    if asset_id is None:
        return None
    name = asset_id.lower()
    if "nisar" in name:
        return "nisar"
    if "opera" in name or "dswx" in name:
        return "opera"
    return "observed"


def _flood_mask(case_id: str):
    """NISAR flood mask for wa_2025 when the polygon table asset exists;
    rasterized in GEE by painting the FeatureCollection (L10); else None."""
    if case_id != "wa_2025":
        return None
    asset = _flood_asset(case_id)
    try:
        ee.data.getAsset(asset)
    except Exception:  # noqa: BLE001 — any lookup failure closes the gate
        return None
    return ee.Image.constant(0).byte().paint(ee.FeatureCollection(asset), 1).gt(0)


def _null_reasons(case_id: str) -> dict:
    if case_id != "wa_2025":
        reason = (
            "no NISAR coverage — event predates launch (Jul 2025)"
            if case_id == "rs_2024"
            else "no NISAR flood mask for this case"
        )
    else:
        reason = (
            f"NISAR flood polygons asset {_flood_asset(case_id)} not found — "
            "run 06_polygonize.py and tools/ingest_assets.py first"
        )
    return {"people_flooded": reason, "flooded_km2": reason, "flood_source": reason}


def compute(case_id: str) -> dict:
    """All server-side objects (no getInfo yet); None = gated-off key."""
    cfg = case_cfg(case_id)
    aoi = aoi_geom(case_id)
    ref = ee.Date(str(cfg["event_date"]))
    band = _event_band_index(case_id, cfg)
    tpi_day = ee.Image(_asset(case_id, "tpi_slide_series")).select(band).rename("TPI_slide")
    critical = tpi_day.gt(CRITICAL).selfMask()
    gpw = _gpw_nearest(ref)
    pop = gpw.select(0)
    flood = _flood_mask(case_id)
    affected = critical.unmask().add(flood.unmask()).gt(0).selfMask() if flood is not None else critical
    before = _region_mean(_ntl_mean(ref, NTL_PRE_NIGHTS, 0), affected, aoi)
    after = _region_mean(_ntl_mean(ref, 0, NTL_POST_NIGHTS), affected, aoi)
    out = {
        "gpw_year": gpw.date().get("year"),
        "people_critical": _pop_sum(pop, critical, aoi),
        "people_flooded": _pop_sum(pop, flood, aoi) if flood is not None else None,
        "flooded_km2": None,
        "flood_source": flood_source(_flood_asset(case_id)) if flood is not None else None,
        "night_light_drop_pct": ee.Number(before)
        .subtract(ee.Number(after))
        .divide(ee.Number(before))
        .multiply(100),
    }
    if flood is not None:
        area = ee.Image.pixelArea().updateMask(flood).reduceRegion(
            reducer=ee.Reducer.sum(), geometry=aoi, scale=REDUCE_SCALE, maxPixels=MAX_PIXELS
        )
        out["flooded_km2"] = ee.Number(area.get("area")).divide(1e6)
    return out


def _fetch(url) -> bytes:
    import requests

    return requests.get(url, timeout=600).content


# ---------------------------------------------------------------------------
# Client-side run: no <case>_tpi_slide_series asset — the critical mask comes
# from the local 90 m COG (data/<case>/cog/<case>_tpi_slide_*.tif event-day
# band); NTL means are pulled via getDownloadURL and everything is reduced
# locally with rasterio/numpy. Metrics land in data/<case>/impact_metrics.json
# (NOT data/metrics.json); PNGs + _index.json layer entries go to png/.
# ---------------------------------------------------------------------------


def _tpi_slide_critical(case_id: str):
    """(uint8 critical mask, transform, crs) event-day TPI_slide > CRITICAL
    from the local COG — replaces the missing tpi_slide_series EE asset."""
    import numpy as np
    import rasterio

    cfg = case_cfg(case_id)
    band = _event_band_index(case_id, cfg) + 1  # rasterio bands are 1-based
    path = DATA_DIR / case_id / "cog" / f"{artifact_stem(_event_id(case_id), 'tpi_slide')}.tif"
    with rasterio.open(path) as ds:
        arr = ds.read(band, masked=True).filled(np.nan)
        return (arr > CRITICAL).astype(np.uint8), ds.transform, ds.crs


def _flood_geojson_path(case_id: str) -> Path | None:
    """Local geojson that backs the flood_nisar* table asset."""
    from tp.config import REPO_ROOT

    candidates = []
    try:
        doc = json.loads((DATA_DIR / case_id / "assets.json").read_text())
        for row in doc.get("assets", []):
            if (isinstance(row, dict) and "flood" in str(row.get("name", ""))
                    and row.get("kind") == "table" and row.get("source")):
                candidates.append(Path(row["source"]))
    except (OSError, json.JSONDecodeError):
        pass
    cands = [REPO_ROOT / c if not c.is_absolute() else c for c in candidates]
    cands += sorted((REPO_ROOT / "intermediate" / case_id).glob("*flood*.geojson"))
    cands += sorted((DATA_DIR / case_id / "geojson").glob("*flood*.geojson"))
    return next((p for p in cands if p.exists()), None)


def _flood_mask_native(case_id: str):
    """(uint8 mask, transform, crs) NISAR flood polygons rasterized on the
    local flood COG grid (10 m); None when the case has no flood polygons."""
    if case_id != "wa_2025":
        return None
    import numpy as np
    import rasterio
    from rasterio.features import rasterize

    gj = _flood_geojson_path(case_id)
    if gj is None:
        return None
    feats = json.loads(gj.read_text()).get("features", [])
    geoms = [f["geometry"] for f in feats if f.get("geometry")]
    if not geoms:
        return None
    cog = DATA_DIR / case_id / "cog" / f"{artifact_stem(_event_id(case_id), 'flood_nisar')}.tif"
    if cog.exists():
        with rasterio.open(cog) as ds:
            shape, transform, crs = (ds.height, ds.width), ds.transform, ds.crs
    else:
        from rasterio.features import bounds as feat_bounds
        from rasterio.transform import from_origin

        w, s, e, n = feat_bounds({"type": "FeatureCollection", "features": feats})
        res = 0.0001  # ~10 m
        transform = from_origin(w, n, res, res)
        shape = (max(1, int((n - s) / res)), max(1, int((e - w) / res)))
        crs = rasterio.crs.CRS.from_epsg(4326)
    mask = rasterize(geoms, out_shape=shape, transform=transform, fill=0,
                     dtype="uint8", all_touched=True)
    return mask, transform, crs


def _frac_on(src, src_transform, src_crs, dst_shape, dst_transform, dst_crs):
    """Fraction of each dst cell covered by src>0 (average resampling)."""
    import numpy as np
    from rasterio.warp import Resampling, reproject

    dst = np.zeros(dst_shape, dtype=np.float32)
    reproject(source=src.astype(np.float32), destination=dst,
              src_transform=src_transform, src_crs=src_crs,
              dst_transform=dst_transform, dst_crs=dst_crs,
              resampling=Resampling.average)
    return dst


def _ntl_array(img, aoi):
    """getDownloadURL GeoTIFF -> (masked array, transform, crs, bounds)."""
    import io
    import zipfile

    import numpy as np
    import rasterio

    url = img.clip(aoi).getDownloadURL(
        {"region": aoi, "scale": NTL_SCALE, "format": "GEO_TIFF"})
    blob = _fetch(url)
    if blob[:2] == b"PK":  # older responses come back zipped
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            name = next(n for n in z.namelist() if n.endswith(".tif"))
            blob = z.read(name)
    raw = blob
    with rasterio.io.MemoryFile(raw) as mem, mem.open() as ds:
        arr = ds.read(1, masked=True)
        arr = np.ma.masked_invalid(np.ma.filled(arr, np.nan))
        return arr, ds.transform, ds.crs, list(ds.bounds)


def _pop_sum_frac(pop_arr, frac) -> float:
    import numpy as np

    pop = np.asarray(pop_arr, dtype=np.float64)
    pop = np.where(np.isfinite(pop) & (pop >= 0), pop, 0.0)
    return float(np.sum(pop * frac))


def _cell_area_km2(transform, height: int) -> float:
    """km2 of one cell of a EPSG:4326 grid at its row-centre latitude (mean)."""
    import math

    res = abs(transform.a)
    lats = [transform.f + (i + 0.5) * transform.e for i in range(height)]
    import numpy as np

    m = 111320.0
    areas = [(res * m) * (res * m * math.cos(math.radians(lat))) for lat in lats]
    return float(np.mean(areas) / 1e6)


def run_local(case_id: str, metrics_path: Path | None = None) -> dict:
    """Client-side impact run: NTL means via getDownloadURL, all masks local."""
    import numpy as np
    import rasterio

    from tp.bundle import _gray_png_3857

    _common_ready()
    init()
    cfg = case_cfg(case_id)
    eid = _event_id(case_id)
    aoi = aoi_geom(case_id)
    ref = ee.Date(str(cfg["event_date"]))
    event_day = str(cfg["event_date"])
    ymd = event_day.replace("-", "")
    case_dir = DATA_DIR / case_id

    crit, crit_tr, crit_crs = _tpi_slide_critical(case_id)
    flood_native = _flood_mask_native(case_id)
    flood_reason = None
    if case_id == "wa_2025" and flood_native is None:
        flood_reason = ("NISAR flood polygons geojson not found — "
                        "run 06_polygonize.py first")

    # -- Task A: NTL pre/post means over the affected area (crit ∪ flood) ----
    ntl = {}
    for name, (b, a) in (("pre", (NTL_PRE_NIGHTS, 0)), ("post", (0, NTL_POST_NIGHTS))):
        ntl[name] = _ntl_array(_ntl_mean(ref, b, a), aoi)
    arr0, ntl_tr, ntl_crs, ntl_bounds = ntl["pre"]
    shape = arr0.shape
    crit_frac = _frac_on(crit, crit_tr, crit_crs, shape, ntl_tr, ntl_crs)
    aff = crit_frac > 0
    flood_frac_ntl = None
    if flood_native is not None:
        fm, ftr, fcrs = flood_native
        flood_frac_ntl = _frac_on(fm, ftr, fcrs, shape, ntl_tr, ntl_crs)
        aff |= flood_frac_ntl > 0
    means = {}
    for name, (arr, *_rest) in ntl.items():
        vals = arr.filled(np.nan)[aff]
        vals = vals[np.isfinite(vals)]
        means[name] = float(np.mean(vals)) if vals.size else None
    drop = None
    if means["pre"] and means["post"] is not None and means["pre"] > 0:
        drop = (means["pre"] - means["post"]) / means["pre"] * 100.0

    # -- Task B: grey-stretch PNGs + _index.json layer entries ---------------
    png_dir = case_dir / "png"
    png_dir.mkdir(parents=True, exist_ok=True)
    index_path = png_dir / "_index.json"
    layers = []
    if index_path.exists():
        layers = json.loads(index_path.read_text()).get("layers", [])
    layers = [ly for ly in layers
              if not (ly.get("id") in ("night_lights_pre", "night_lights_post")
                      and ly.get("date") == event_day)]
    for name, (arr, tr, crs, _bounds) in ntl.items():
        png_bytes, bounds = _gray_png_3857(arr.filled(np.nan), tr, crs, None)
        png_path = png_dir / f"night_lights_{name}_{ymd}.png"
        png_path.write_bytes(png_bytes)
        layers.append({"id": f"night_lights_{name}", "date": event_day,
                       "png": str(png_path.relative_to(DATA_DIR)),
                       "cog": None, "bounds": bounds})
    index_path.write_text(json.dumps({"layers": layers}, indent=2) + "\n")

    # -- population sums on the local GPW COG --------------------------------
    pop_path = case_dir / "cog" / f"{artifact_stem(eid, 'pop_gpw')}.tif"
    flat = {"gpw_year": 2020,  # GPWv411 latest epoch; nearest for both events
          "people_critical": None,
          "people_flooded": None,
          "flooded_km2": None,
          "flood_source": None,
          "ntl_mean_before": means["pre"],
          "ntl_mean_after": means["post"],
          "night_light_drop_pct": drop}
    reasons = dict(_null_reasons(case_id)) if case_id == "wa_2025" else {}
    if pop_path.exists():
        with rasterio.open(pop_path) as ds:
            pop = ds.read(1)
            pshape, ptr, pcrs = (ds.height, ds.width), ds.transform, ds.crs
        flat["people_critical"] = _pop_sum_frac(
            pop, _frac_on(crit, crit_tr, crit_crs, pshape, ptr, pcrs))
        if flood_native is not None:
            fm, ftr, fcrs = flood_native
            flood_frac = _frac_on(fm, ftr, fcrs, pshape, ptr, pcrs)
            flat["people_flooded"] = _pop_sum_frac(pop, flood_frac)
            flat["flooded_km2"] = float(np.sum(fm > 0)) * _cell_area_km2(
                ftr, fm.shape[0])
            flat["flood_source"] = "nisar"
    else:
        reasons["people_critical"] = f"local GPW COG missing: {pop_path}"
        if case_id == "wa_2025":
            reasons.setdefault("people_flooded", reasons["people_critical"])
            reasons.setdefault("flooded_km2", reasons["people_critical"])
    if case_id == "wa_2025":
        if flood_reason:
            reasons.update({"people_flooded": flood_reason,
                            "flooded_km2": flood_reason,
                            "flood_source": flood_reason})
    else:
        reasons = _null_reasons(case_id)

    out = {"case": case_id, "event": eid,
           **{k: v for k, v in flat.items()},
           "_null_reasons": {k: r for k, r in reasons.items()
                             if flat.get(k) is None}}
    mpath = Path(metrics_path) if metrics_path else case_dir / "impact_metrics.json"
    mpath.parent.mkdir(parents=True, exist_ok=True)
    mpath.write_text(json.dumps(out, indent=2) + "\n")
    return out


def _write_cog(zip_bytes: bytes, path: Path) -> None:
    """getDownloadURL payload (zipped GeoTIFF) -> COG at `path` (plain tif if
    the GDAL COG driver is unavailable)."""
    import io
    import zipfile

    import rasterio
    import rasterio.shutil

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        name = next(n for n in z.namelist() if n.endswith(".tif"))
        raw = z.read(name)
    try:
        with rasterio.io.MemoryFile(raw) as mem, mem.open() as src:
            rasterio.shutil.copy(src, path, driver="COG")
    except rasterio.errors.DriverRegistrationError:
        path.write_bytes(raw)


def pull_night_lights(case_id: str, out_dir: Path | None = None) -> list:
    """Direct local write of the pre/post NTL means as COGs in data/<case>/cog/."""
    cfg = case_cfg(case_id)
    aoi = aoi_geom(case_id)
    ref = ee.Date(str(cfg["event_date"]))
    eid = _event_id(case_id)
    out_dir = Path(out_dir or DATA_DIR / case_id / "cog")
    out_dir.mkdir(parents=True, exist_ok=True)
    imgs = {"pre": _ntl_mean(ref, NTL_PRE_NIGHTS, 0), "post": _ntl_mean(ref, 0, NTL_POST_NIGHTS)}
    written = []
    for name, img in imgs.items():
        url = img.clip(aoi).getDownloadURL({"region": aoi, "scale": NTL_SCALE, "format": "GEO_TIFF"})
        path = out_dir / f"{artifact_stem(eid, f'night_lights_{name}')}.tif"
        _write_cog(_fetch(url), path)
        written.append(path)
    return written


def run(
    case_id: str,
    metrics_path: Path | None = None,
    merge: bool = True,
    pull: bool = True,
    out_dir: Path | None = None,
) -> dict:
    """getInfo all computes; merge metrics + pull COGs locally unless opted out."""
    _common_ready()
    init()
    res = compute(case_id)
    flat = {k: (v if v is None or isinstance(v, str) else v.getInfo()) for k, v in res.items()}
    collections = {
        "GPWv4": f"{COL_GPW} (epoch {flat['gpw_year']})",
        "VIIRS": f"{COL_VNP46A2} ({BAND_NTL}, {BAND_MQF}==0)",
        "tpi_slide_series": _asset(case_id, "tpi_slide_series"),
    }
    if flat["people_flooded"] is not None:  # L8: record the concrete asset ids used
        collections["flood_nisar"] = _flood_asset(case_id)
    merged = (
        merge_metrics(
            case_id,
            flat=flat,
            reasons=_null_reasons(case_id),
            collections=collections,
            path=metrics_path,
        )
        if merge
        else None
    )
    files = pull_night_lights(case_id, out_dir=out_dir) if pull else []
    return {"metrics": flat, "merged": merged, "files": files}


def plan(case_id: str) -> str:
    cfg = config_case(case_id)
    eid = _event_id(case_id)
    band = int(cfg.get("window_before_days", 10))
    lines = [
        f"[impact] case={case_id} event={eid} event_date={cfg['event_date']}",
        f"  compute: tpi_day = ee.Image({_asset(case_id, 'tpi_slide_series')}).select({band})",
        f"  compute: people_critical = {COL_GPW} nearest-epoch sum where TPI_slide>{CRITICAL}"
        + f" (scale={REDUCE_SCALE})",
        f"  compute: night_light_drop_pct = {COL_VNP46A2} {BAND_NTL} masked {BAND_MQF}==0, mean"
        + f" {NTL_POST_NIGHTS} nights after vs {NTL_PRE_NIGHTS} before (scale={NTL_SCALE})",
    ]
    if case_id == "wa_2025":
        lines.append(
            f"  compute: people_flooded/flooded_km2 gated on asset {_flood_asset(case_id)}"
            " (null + reason if absent); light mask becomes critical∪flood"
        )
    else:
        lines.append(f"  compute: people_flooded/flooded_km2 = null — {_null_reasons(case_id)['people_flooded']}")
    lines.append(f"  pull (getDownloadURL GeoTIFF → COG → data/{case_id}/cog/):")
    for name, nights in (("pre", NTL_PRE_NIGHTS), ("post", NTL_POST_NIGHTS)):
        lines.append(f"    {artifact_stem(eid, f'night_lights_{name}')}.tif  ({nights}-night mean)")
    lines.append(
        f"  merge → data/metrics.json[{case_id}]: gpw_year people_critical people_flooded"
        f" flooded_km2 night_light_drop_pct (+collections GPWv4, VIIRS)"
    )
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="gee.impact", description=__doc__.splitlines()[0])
    ap.add_argument("--case", required=True, choices=sorted(load_config()["cases"]))
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="print planned computes+exports; no EE init")
    g.add_argument("--tasks", action="store_true", help="run + pull COGs; skip metrics merge")
    g.add_argument("--local", action="store_true",
                 help="client-side run: local COG masks + getDownloadURL NTL; "
                      "writes data/<case>/impact_metrics.json + night_lights PNGs")
    ap.add_argument("--metrics-path", type=Path, default=None, help="metrics.json override (testing)")
    args = ap.parse_args(argv)
    if args.dry_run:
        print(plan(args.case))
        return 0
    if args.local:
        out = run_local(args.case, metrics_path=args.metrics_path)
        for k, v in out.items():
            print(f"  {k}: {v}")
        return 0
    out = run(args.case, metrics_path=args.metrics_path, merge=not args.tasks)
    for k, v in out["metrics"].items():
        print(f"  {k}: {v}")
    print(f"wrote {len(out['files'])} cog file(s)" + ("; metrics not merged (--tasks)" if args.tasks else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
