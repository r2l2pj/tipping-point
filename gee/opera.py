"""Validate NISAR flood polygons against the public EE OPERA DSWx catalog.

Prefer the flood polygon FeatureCollection listed in data/<case>/assets.json
(L10: vectors are ingested as table assets via Export.table.toAsset; raster
assets cannot exist). OPERA inputs are exclusively OPERA/DSWX/L3_V1/HLS and
OPERA/DSWX/L3_V1/S1 (ruling L10b). Catalog documentation confirms
WTR_Water_classification for BOTH products (not the downloadable granule's
B01_WTR). Live runs verify the schema via
ImageCollection(collection).first().bandNames().getInfo() before computing.
Live confirmation requires EE credentials; --dry-run makes no EE calls.

HLS WTR water classes are 1 (open water), 2 (partial water); 253 is cloud and
255 nodata. S1 WTR water classes are 1 (open water), 3 (inundated vegetation),
NOT 1/2: S1 CONF classes 1/2 instead describe high/moderate confidence.
Only observed water/nonwater classes enter IoU/F1. cloud_pct is the HLS cloud
class fraction over non-nodata pixels in the event..+5d window/AOI, for either
case; S1 is radar and cannot measure optical cloud cover.
Flat iou/f1/cloud_pct merge through tp.metrics.merge_metrics.

Usage: python -m gee.opera --case <id> [--dry-run|--tasks]
"""
from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

import ee
from tp.config import REPO_ROOT, load_config
from tp.config import case as config_case
from tp.metrics import merge_metrics
from tp.validation import opera_product

try:
    from gee.common import ASSET_ROOT, GEE_PROJECT, aoi_geom, case_cfg, data_dir, init
except ModuleNotFoundError as exc:  # gee.common lands with U4 — --dry-run needs none of it
    if exc.name != "gee.common":
        raise
    GEE_PROJECT = "<GEE_PROJECT>"
    ASSET_ROOT = f"projects/{GEE_PROJECT}/assets/tipping-point"
    aoi_geom = case_cfg = data_dir = init = None

HLS_COLLECTION = "OPERA/DSWX/L3_V1/HLS"
HLS_WATER_CLASSES = (1, 2)
S1_COLLECTION = "OPERA/DSWX/L3_V1/S1"
S1_WATER_CLASSES = (1, 3)
OPERA_CATALOG = {"rs_2024": HLS_COLLECTION, "wa_2025": S1_COLLECTION}
WATER_CLASSES = {HLS_COLLECTION: HLS_WATER_CLASSES, S1_COLLECTION: S1_WATER_CLASSES}
WTR_BAND = "WTR_Water_classification"
CLOUD_CLASS = 253
NODATA_CLASS = 255
SEARCH_DAYS = 5
SCALE = 30
MAX_PIXELS = 1e9


def _common_ready() -> None:
    if init is None:
        raise RuntimeError("gee.common not on this branch yet (U4) — only --dry-run works")


def _asset_root() -> str:
    """ASSET_ROOT with the {GEE_PROJECT} placeholder filled (or verbatim)."""
    return str(ASSET_ROOT).format(GEE_PROJECT=GEE_PROJECT or "<GEE_PROJECT>")


def _assets_map(case_id: str, data_root: Path | None = None) -> dict:
    """Read the ingest manifest (or legacy name->path map) -> name->row."""
    root = Path(data_root) if data_root is not None else (
        data_dir(case_id) if data_dir else REPO_ROOT / "data" / case_id)
    try:
        doc = json.loads((root / "assets.json").read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(doc, dict):
        return {}
    assets = {k: {"gee_id": v} for k, v in doc.items() if isinstance(v, str) and v}
    for row in doc.get("assets", []):
        if isinstance(row, dict) and row.get("name") and row.get("gee_id"):
            assets[row["name"]] = row
    return assets


def _asset_exists(path: str) -> bool:
    try:
        ee.data.getAsset(path)
        return True
    except Exception:  # noqa: BLE001 — any lookup failure = absent
        return False


def _is_table_row(row: dict) -> bool:
    return row.get("kind") == "table" or row.get("type") in ("TABLE", "FEATURE_COLLECTION")


def resolve_inputs(case_id: str, data_root: Path | None = None) -> dict:
    """Resolve the listed flood polygons first, otherwise the flood raster.

    {nisar, nisar_kind, opera, opera_source}; nisar is None when nothing
    resolves. opera_source is always "catalog:<collection id>" (L10b).
    """
    amap = _assets_map(case_id, data_root)
    polygon = next((row["gee_id"] for name, row in amap.items()
                    if "flood" in name and ("polygon" in name or _is_table_row(row))), None)
    raster = next((row["gee_id"] for name, row in amap.items()
                   if "flood" in name and row.get("kind") == "image"
                   and row.get("status") != "local-only"), None)
    if raster is not None and not _asset_exists(raster):
        raster = None
    if polygon is None and raster is None:
        path = f"{_asset_root()}/{case_id}/flood_nisar"
        raster = path if _asset_exists(path) else None
    col = OPERA_CATALOG.get(case_id, HLS_COLLECTION)
    return {"nisar": polygon or raster, "nisar_kind": "polygons" if polygon else "raster",
            "opera": col, "opera_source": f"catalog:{col}"}


def _opera_collection(case_id: str, collection: str, aoi):
    cfg = case_cfg(case_id)
    day = cfg["event_date"]
    if isinstance(day, str):
        day = date.fromisoformat(day)
    elif hasattr(day, "date"):
        day = day.date()
    return ee.ImageCollection(collection).filterBounds(aoi).filterDate(
        str(day), str(day + timedelta(days=SEARCH_DAYS)))


def confirm_catalog_schema() -> dict:
    """Fail clearly if a real catalog band changes; invoked only after EE init."""
    schemas = {}
    for collection in (HLS_COLLECTION, S1_COLLECTION):
        bands = ee.ImageCollection(collection).first().bandNames().getInfo()
        if WTR_BAND not in bands:
            raise RuntimeError(f"{collection} lacks {WTR_BAND}: {bands}")
        schemas[collection] = bands
    return schemas


def compute(case_id: str, data_root: Path | None = None) -> dict:
    """Build server-side counts, including collection coverage counts."""
    aoi = aoi_geom(case_id)
    res = resolve_inputs(case_id, data_root)
    collection = _opera_collection(case_id, res["opera"], aoi)
    hls = collection if res["opera"] == HLS_COLLECTION else _opera_collection(case_id, HLS_COLLECTION, aoi)
    empty = ee.Image.constant(NODATA_CLASS).rename(WTR_BAND).updateMask(ee.Image.constant(0))
    wtr = ee.Image(ee.Algorithms.If(collection.size(), collection.select(WTR_BAND).mosaic(), empty))
    optical = ee.Image(ee.Algorithms.If(hls.size(), hls.select(WTR_BAND).mosaic(), empty))
    bands = [optical.eq(CLOUD_CLASS).rename("cloud"), optical.neq(NODATA_CLASS).rename("valid")]
    if res["nisar"] is not None:
        nisar = (ee.Image.constant(0).byte().paint(ee.FeatureCollection(res["nisar"]), 1)
                 if res["nisar_kind"] == "polygons" else ee.Image(res["nisar"]).select(0).unmask(0).gt(0))
        first, second = WATER_CLASSES[res["opera"]]
        ref = wtr.eq(first).Or(wtr.eq(second))
        obs = wtr.eq(0).Or(ref)
        pred = nisar.And(obs)
        bands += [
            ref.And(pred).rename("tp"),
            ref.Not().And(pred).updateMask(obs).rename("fp"),
            ref.And(pred.Not()).rename("fn"),
            obs.rename("observed"),
        ]
    stats = ee.Image.cat(bands).reduceRegion(
        reducer=ee.Reducer.sum(), geometry=aoi, scale=SCALE, maxPixels=MAX_PIXELS)
    stats = stats.set("opera_scenes", collection.size()).set("hls_scenes", hls.size())
    return {**res, "stats": stats, "collections": {
        "opera_dswx": opera_product(case_id), "opera_dswx_source": res["opera_source"],
        "cloud_pct_source": f"catalog:{HLS_COLLECTION}",
    }}


def _metrics(case_id: str, res: dict, counts: dict) -> tuple[dict, dict]:
    """Flat metrics + null reasons from reduceRegion counts (client-side)."""
    flat = {"iou": None, "f1": None, "cloud_pct": None}
    reasons = {}
    valid = counts.get("valid", 0) or 0
    if valid:
        flat["cloud_pct"] = 100.0 * (counts.get("cloud", 0) or 0) / valid
    else:
        reasons["cloud_pct"] = "no valid DSWx-HLS coverage in the window/AOI"
    why = None
    if res["nisar"] is None:
        why = ("no NISAR coverage — event predates launch (Jul 2025)" if case_id == "rs_2024"
               else "NISAR flood polygons asset not found — run 06_polygonize.py and ingest")
    elif not counts.get("opera_scenes") or not counts.get("observed"):
        why = "no observed OPERA DSWx coverage in the window/AOI"
    if why:
        reasons.update(iou=why, f1=why)
    else:
        tp, fp, fn = (counts.get(k, 0) or 0 for k in ("tp", "fp", "fn"))
        flat["iou"] = tp / (tp + fp + fn) if tp + fp + fn else 0.0
        flat["f1"] = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0
    return flat, reasons


def run(case_id: str, metrics_path: Path | None = None, merge: bool = True,
        data_root: Path | None = None) -> dict:
    """Confirm catalog schema, fetch counts once, and merge flat metrics."""
    _common_ready()
    init()
    confirm_catalog_schema()
    res = compute(case_id, data_root=data_root)
    counts = res["stats"].getInfo()
    flat, reasons = _metrics(case_id, res, counts if isinstance(counts, dict) else {})
    merged = merge_metrics(case_id, flat=flat, reasons=reasons,
                           collections=res["collections"], path=metrics_path) if merge else None
    return {"metrics": flat, "merged": merged, "resolved": res}


def plan(case_id: str) -> str:
    cfg = config_case(case_id)
    col = OPERA_CATALOG.get(case_id, HLS_COLLECTION)
    return "\n".join([
        f"[opera] case={case_id} event_date={cfg['event_date']} (+{SEARCH_DAYS}d)",
        (f"  nisar: data/{case_id}/assets.json flood polygons FeatureCollection"
         f" (fallback {_asset_root()}/{case_id}/flood_nisar)"),
        f"  opera: catalog {col}, band={WTR_BAND}, water={WATER_CLASSES[col]}",
        f"  cloud: catalog {HLS_COLLECTION}, class={CLOUD_CLASS}, nodata={NODATA_CLASS}",
        "  live schema: ImageCollection(id).first().bandNames() for both catalogs",
        f"  compute: tp/fp/fn + cloud/valid reduceRegion sum (scale={SCALE}) -> iou/f1/cloud_pct",
        f"  merge -> data/metrics.json[{case_id}]: iou f1 cloud_pct (+collections opera_dswx)",
    ])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="gee.opera", description=__doc__.splitlines()[0])
    ap.add_argument("--case", required=True, choices=sorted(load_config()["cases"]))
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="print planned computes; no EE init")
    g.add_argument("--tasks", action="store_true", help="run the computes; skip the metrics merge")
    ap.add_argument("--metrics-path", type=Path, default=None, help="metrics.json override (testing)")
    args = ap.parse_args(argv)
    if args.dry_run:
        print(plan(args.case))
        return 0
    out = run(args.case, metrics_path=args.metrics_path, merge=not args.tasks)
    for k, v in out["metrics"].items():
        print(f"  {k}: {v}")
    if args.tasks:
        print("metrics not merged (--tasks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
