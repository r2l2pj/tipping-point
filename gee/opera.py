"""NISAR x OPERA DSWx validation metrics — Earth Engine compute (L7).

Server-side port of pipeline/scripts/04_validation.py (kept as the local
fallback): the uploaded water_nisar asset is compared against an OPERA DSWx
WTR mosaic and iou/f1/cloud_pct merge into data/metrics.json via
tp.metrics.merge_metrics.

Inputs, in resolution order:
  water_nisar — data/<case>/assets.json "water_nisar", else
      <ASSET_ROOT>/<case>/water_nisar when ee.data.getAsset finds it.
  OPERA WTR   — data/<case>/assets.json "opera_wtr", else
      <ASSET_ROOT>/<case>/opera_wtr when present, else a catalog mosaic of
      the OPERA_CATALOG collection (filterBounds+filterDate event..+5d)
      when that collection exists in the EE catalog.

WTR values: 0 land, 1 open water, 2 partial water (water = 1|2);
253 cloud/shadow and 255 no data are excluded. cloud_pct = px==253 / px!=255
— the "why radar" number.

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

# Candidate OPERA DSWx collection ids in the EE catalog — "if present": the
# uploaded mosaic asset is always preferred; the catalog is the fallback.
OPERA_CATALOG = {
    "rs_2024": "OPERA_L3_DSWX-HLS_V1",
    "wa_2025": "OPERA_L3_DSWX-S1_V1",
}
WTR_BAND = "B01_WTR"   # DSWx-HLS water-class band (DSWx-S1 also exposes WTR)
SEARCH_DAYS = 5        # catalog window: event_date .. event_date + 5d (as 04 --auto)
SCALE = 30             # OPERA/NISAR ~30 m
MAX_PIXELS = 1e9


def _common_ready() -> None:
    if init is None:
        raise RuntimeError("gee.common not on this branch yet (U4) — only --dry-run works")


def _asset_root() -> str:
    """ASSET_ROOT with the {GEE_PROJECT} placeholder filled (or verbatim)."""
    return str(ASSET_ROOT).format(GEE_PROJECT=GEE_PROJECT)


def _assets_map(case_id: str, data_root: Path | None = None) -> dict:
    """data/<case>/assets.json name->asset-path map; {} when absent/unreadable."""
    root = Path(data_root) if data_root is not None else (
        data_dir(case_id) if data_dir else REPO_ROOT / "data" / case_id)
    try:
        m = json.loads((root / "assets.json").read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return {k: v for k, v in m.items() if isinstance(v, str) and v}


def _asset_exists(path: str) -> bool:
    try:
        ee.data.getAsset(path)
        return True
    except Exception:  # noqa: BLE001 — any lookup failure = absent
        return False


def _resolve_asset(case_id: str, name: str, amap: dict) -> str | None:
    """assets.json entry wins; else <root>/<case>/<name> when it exists."""
    if name in amap:
        return amap[name]
    path = f"{_asset_root()}/{case_id}/{name}"
    return path if _asset_exists(path) else None


def resolve_inputs(case_id: str, data_root: Path | None = None) -> dict:
    """Paths for the two inputs + the OPERA source descriptor.

    {nisar, opera, opera_source}; paths are None when nothing resolves.
    opera_source is "asset:<path>" or "catalog:<collection id>".
    """
    amap = _assets_map(case_id, data_root)
    nisar = _resolve_asset(case_id, "water_nisar", amap)
    opera = _resolve_asset(case_id, "opera_wtr", amap)
    src = f"asset:{opera}" if opera else None
    if opera is None:
        col = OPERA_CATALOG.get(case_id)
        if col and _asset_exists(col):
            opera, src = col, f"catalog:{col}"
    return {"nisar": nisar, "opera": opera, "opera_source": src}


def _opera_image(case_id: str, opera_path: str, source: str, aoi):
    """ee.Image WTR mosaic: uploaded asset, or catalog collection mosaic."""
    if source.startswith("catalog:"):
        cfg = case_cfg(case_id)
        day = cfg["event_date"]
        if isinstance(day, str):
            day = date.fromisoformat(day)
        elif hasattr(day, "date"):
            day = day.date()
        start, end = str(day), str(day + timedelta(days=SEARCH_DAYS))
        return ee.ImageCollection(opera_path).filterBounds(aoi).filterDate(
            start, end).select(WTR_BAND).mosaic()
    return ee.Image(opera_path).select(0)


def compute(case_id: str, data_root: Path | None = None) -> dict:
    """Server-side stats image reduce (no getInfo in here).

    Returns {"stats", "nisar", "opera_source", "collections"} — stats is a
    one-shot reduceRegion dict {tp, fp, fn, cloud, valid} (empty when no
    OPERA source resolves).
    """
    aoi = aoi_geom(case_id)
    res = resolve_inputs(case_id, data_root)
    collections = {"opera_dswx": opera_product(case_id)}
    bands = []
    if res["opera"] is not None:
        wtr = _opera_image(case_id, res["opera"], res["opera_source"], aoi)
        collections["cloud_pct_source"] = res["opera_source"]
        cloud = wtr.eq(253).selfMask().rename("cloud")
        valid = wtr.neq(255).selfMask().rename("valid")
        bands += [cloud, valid]
        if res["nisar"] is not None:
            nisar = ee.Image(res["nisar"]).select(0).gt(0)
            obs = wtr.neq(253).And(wtr.neq(255))
            ref = wtr.eq(1).Or(wtr.eq(2)).And(obs)
            pred = nisar.And(obs)
            bands += [
                ref.And(pred).selfMask().rename("tp"),
                ref.Not().And(pred).selfMask().rename("fp"),
                ref.And(pred.Not()).selfMask().rename("fn"),
            ]
    stats = (
        ee.Image.cat(bands).reduceRegion(
            reducer=ee.Reducer.sum(), geometry=aoi,
            scale=SCALE, maxPixels=MAX_PIXELS,
        )
        if bands else ee.Dictionary({})
    )
    return {**res, "stats": stats, "collections": collections}


def _metrics(case_id: str, res: dict, counts: dict) -> tuple[dict, dict]:
    """flat metrics + null reasons from reduceRegion counts (client-side)."""
    flat: dict = {}
    reasons: dict = {}
    if res["opera"] is None:
        flat = {"iou": None, "f1": None, "cloud_pct": None}
        reason = "no OPERA DSWx WTR source — upload <case>/opera_wtr or check the catalog"
        reasons = {"iou": reason, "f1": reason, "cloud_pct": reason}
        return flat, reasons
    valid = counts.get("valid", 0) or 0
    flat["cloud_pct"] = 100.0 * (counts.get("cloud", 0) or 0) / valid if valid else 0.0
    if res["nisar"] is None:
        flat["iou"] = flat["f1"] = None
        why = (
            "no NISAR coverage — event predates launch (Jul 2025)"
            if case_id == "rs_2024"
            else "NISAR water mask asset not found — run 02_flood_mask.py and upload"
        )
        reasons = {"iou": why, "f1": why}
    else:
        tp = counts.get("tp", 0) or 0
        fp = counts.get("fp", 0) or 0
        fn = counts.get("fn", 0) or 0
        flat["iou"] = tp / (tp + fp + fn) if (tp + fp + fn) else 0.0
        flat["f1"] = 2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) else 0.0
    return flat, reasons


def run(
    case_id: str,
    metrics_path: Path | None = None,
    merge: bool = True,
    data_root: Path | None = None,
) -> dict:
    """getInfo the stats once; merge metrics unless opted out."""
    _common_ready()
    init()
    res = compute(case_id, data_root=data_root)
    counts = res["stats"].getInfo()
    counts = counts if isinstance(counts, dict) else {}
    flat, reasons = _metrics(case_id, res, counts)
    merged = (
        merge_metrics(
            case_id, flat=flat, reasons=reasons,
            collections=res["collections"], path=metrics_path,
        )
        if merge else None
    )
    return {"metrics": flat, "merged": merged, "resolved": res}


def plan(case_id: str) -> str:
    cfg = config_case(case_id)
    root = "projects/<GEE_PROJECT>/assets/tipping-point"
    col = OPERA_CATALOG.get(case_id, "OPERA_L3_DSWX-HLS_V1")
    return "\n".join([
        f"[opera] case={case_id} event_date={cfg['event_date']} (+{SEARCH_DAYS}d)",
        f"  nisar:  data/{case_id}/assets.json['water_nisar'] or {root}/{case_id}/water_nisar",
        f"  opera:  assets.json['opera_wtr'] or {root}/{case_id}/opera_wtr or catalog {col}",
        f"  compute: tp/fp/fn + cloud/valid reduceRegion sum (scale={SCALE}) -> iou/f1/cloud_pct",
        f"  merge -> data/metrics.json[{case_id}]: iou f1 cloud_pct (+collections opera_dswx)",
    ])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="gee.opera", description=__doc__.splitlines()[0])
    ap.add_argument("--case", required=True, choices=sorted(load_config()["cases"]))
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true",
                   help="print planned computes; no EE init")
    g.add_argument("--tasks", action="store_true",
                   help="run the computes; skip the metrics merge")
    ap.add_argument("--metrics-path", type=Path, default=None,
                    help="metrics.json override (testing)")
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
