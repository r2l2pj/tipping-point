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
    ap.add_argument("--metrics-path", type=Path, default=None, help="metrics.json override (testing)")
    args = ap.parse_args(argv)
    if args.dry_run:
        print(plan(args.case))
        return 0
    out = run(args.case, metrics_path=args.metrics_path, merge=not args.tasks)
    for k, v in out["metrics"].items():
        print(f"  {k}: {v}")
    print(f"wrote {len(out['files'])} cog file(s)" + ("; metrics not merged (--tasks)" if args.tasks else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
