"""Per-case validation metrics via interactive Earth Engine reads.

Writes data/<case>/validation_metrics.json (flat metrics + _null_reasons +
collections). Separate from data/metrics.json on purpose — concurrent
producers merge there and per-case files avoid the write race.

    uv run python tools/validation_metrics.py [--case wa_2025|rs_2024]

Task A (wa_2025): rasterize the flood_nisar_polygons GEE table asset and
compare against OPERA DSWx on the post date 2025-12-16 — catalog
OPERA/DSWX/L3_V1/S1 (primary; radar sees through cloud) and
OPERA/DSWX/L3_V1/HLS on cloud-free pixels only (WTR 253 = cloud). These are
all-water comparisons: S1 lands under iou_raw/f1_raw/confusion_raw, HLS under
hls_*. The headline iou/f1/precision/recall are the like-for-like new-flood
numbers (see data/SCHEMA.md) and are never written here.

Task B (rs_2024): fraction of AOI pixels HLS Fmask-clear at the event peak
2024-05-01..03 (L30+S30 union: a pixel counts cloud-free when any scene
observed it with Fmask bits1-4 unset — the "optical saw X%" number).

Task C (wa_2025): HAND proxy flood extent (NASADEM - focalMin(radius) < 5m)
vs the rasterized NISAR flood mask across radii 1000/1500/3000m. Emits
hand_nisar_iou (best IoU 0-1), hand_nisar_iou_radii {radius_m: iou 0-1} and
hand_nisar_radius_m. Keys carry the `hand_nisar_*` prefix on purpose: this is
an observed-flood-extent comparison, NOT the MERIT Hydro hand_validation_*
schema in data/metrics.json (gee/hand.py) — the bare names once polluted the
root metrics with an ambiguous percent.

Read-only: interactive ee + reduceRegion only — no Export tasks.
"""
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import ee

from gee.common import BAND_DEM, IMG_NASADEM, aoi_geom, case_cfg, data_dir, init
from gee.opera import (
    CLOUD_CLASS,
    HLS_COLLECTION,
    HLS_WATER_CLASSES,
    NODATA_CLASS,
    S1_COLLECTION,
    S1_WATER_CLASSES,
    WTR_BAND,
    resolve_inputs,
)

POST_DATE_WA = "2025-12-16"          # NISAR post date — no OPERA granule exists
OPERA_WINDOW_WA = ("2025-12-15", "2025-12-18")  # nearest DSWx days: 15th + 17th
OPERA_PRE_WINDOW_WA = ("2025-12-01", "2025-12-08")  # like-for-like pre-water days: 1st..7th
PEAK_RS = ("2024-05-01", "2024-05-04")  # filterDate end is exclusive -> ..03
SCALE = 30
HAND_SCALE = 90            # focalMin cost scales with radius/resolution (gee.hand)
MAX_PIXELS = 1e9
TILE_SCALE = 4

HLS_PRODUCTS = (
    ("NASA/HLS/HLSL30/v002", "B04"),
    ("NASA/HLS/HLSS30/v002", "B04"),
)
FMASK_BAD = 0b11110      # bits1-4: cloud | adjacent | shadow | snow/ice
FMASK_FILL = 255

HAND_RADII_M = (1000, 1500, 3000)
LOW_HAND_M = 5

WATER_CLASSES = {HLS_COLLECTION: HLS_WATER_CLASSES, S1_COLLECTION: S1_WATER_CLASSES}


def _nisar_mask(case_id: str):
    """0/1 image from the rasterized NISAR flood-polygons table asset."""
    res = resolve_inputs(case_id)
    if res["nisar"] is None or res["nisar_kind"] != "polygons":
        return None, res
    mask = ee.Image.constant(0).byte().paint(ee.FeatureCollection(res["nisar"]), 1)
    return mask, res


def _opera_wtr(collection: str, start: str, end: str, aoi):
    """(mosaicked WTR image, filtered collection) for an OPERA date window."""
    col = (ee.ImageCollection(collection).filterBounds(aoi)
           .filterDate(start, end))
    empty = ee.Image.constant(NODATA_CLASS).rename(WTR_BAND).updateMask(ee.Image.constant(0))
    wtr = ee.Image(ee.Algorithms.If(col.size(), col.select(WTR_BAND).mosaic(), empty))
    return wtr, col


def _opera_ref_obs(wtr, classes):
    """(ref=water mask, obs=observed mask) for a WTR mosaic — the same
    expressions the like-for-like layer builds on (tools/wa_opera_layer.py)."""
    ref = ee.Image.constant(0)
    for c in classes:
        ref = ref.Or(wtr.eq(c))
    return ref, wtr.eq(0).Or(ref)


def _opera_counts(collection: str, start: str, end: str, aoi, nisar) -> dict:
    """Server-side tp/fp/fn + cloud/valid counts for an OPERA date window."""
    wtr, col = _opera_wtr(collection, start, end, aoi)
    ref, obs = _opera_ref_obs(wtr, WATER_CLASSES[collection])
    pred = nisar.And(obs)
    img = ee.Image.cat([
        ref.And(pred).rename("tp"),
        ref.Not().And(pred).updateMask(obs).rename("fp"),
        ref.And(pred.Not()).rename("fn"),
        obs.rename("observed"),
        wtr.eq(CLOUD_CLASS).rename("cloud"),
        wtr.neq(NODATA_CLASS).rename("valid"),
    ])
    stats = img.reduceRegion(reducer=ee.Reducer.sum(), geometry=aoi,
                             scale=SCALE, maxPixels=MAX_PIXELS,
                             tileScale=TILE_SCALE)
    return {"stats": stats.set("scenes", col.size()),
            "dates": col.aggregate_array("system:time_start")}


def task_wa2025() -> tuple[dict, dict, dict]:
    """Tasks A + C for wa_2025. Returns (flat, reasons, collections)."""
    case_id = "wa_2025"
    aoi = aoi_geom(case_id)
    flat, reasons = {}, {}
    collections = {"nisar_flood": None}

    nisar, res = _nisar_mask(case_id)
    collections["nisar_flood"] = res["nisar"]
    if nisar is None:
        why = "NISAR flood polygons asset not found in data/wa_2025/assets.json"
        for k in ("iou", "f1", "hls_iou", "hls_f1", "hand_nisar_iou",
                  "hand_nisar_radius_m"):
            flat[k] = None
            reasons[k] = why
        return flat, reasons, collections

    # --- Task A: NISAR vs OPERA DSWx at the post date (nearest days) -------
    # All-water comparisons. S1 lands under *_raw: the headline iou/f1/
    # precision/recall are the like-for-like new-flood numbers (post water
    # AND NOT pre water), never overwritten by this all-water run.
    for prefix, suffix, col in (("", "_raw", S1_COLLECTION), ("hls_", "", HLS_COLLECTION)):
        q = _opera_counts(col, *OPERA_WINDOW_WA, aoi, nisar)
        counts = q["stats"].getInfo()
        counts = counts if isinstance(counts, dict) else {}
        days = sorted({ee.Date(d).format("YYYY-MM-dd").getInfo()
                       for d in (q["dates"].getInfo() or [])})
        collections["opera_dswx_s1" if col == S1_COLLECTION else "opera_dswx_hls"] = (
            f"catalog:{col} @ {','.join(days)} (no {POST_DATE_WA} granule exists)")
        def key(name, prefix=prefix, suffix=suffix):
            return f"{prefix}{name}{suffix}"
        flat[f"{prefix}opera_dates"] = days
        scenes = counts.get("scenes") or 0
        observed = counts.get("observed") or 0
        if not scenes or not observed:
            why = (f"no observed {col} coverage {OPERA_WINDOW_WA[0]}.."
                   f"{OPERA_WINDOW_WA[1]} in the AOI")
            flat[key("iou")] = flat[key("f1")] = None
            reasons[key("iou")] = reasons[key("f1")] = why
        else:
            tp, fp, fn = (counts.get(k, 0) or 0 for k in ("tp", "fp", "fn"))
            flat[key("iou")] = tp / (tp + fp + fn) if tp + fp + fn else 0.0
            flat[key("f1")] = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0
            flat[key("confusion")] = {"tp": tp, "fp": fp, "fn": fn,
                                      "observed_px": observed}
            flat[f"{prefix}opera_scenes"] = scenes
        if col == HLS_COLLECTION:
            valid = counts.get("valid") or 0
            if valid:
                flat["cloud_pct"] = 100.0 * (counts.get("cloud") or 0) / valid
            else:
                flat["cloud_pct"] = None
                reasons["cloud_pct"] = (
                    f"no valid DSWx-HLS pixels {OPERA_WINDOW_WA[0]}.."
                    f"{OPERA_WINDOW_WA[1]} in the AOI")

    # --- Task C: HAND proxy vs NISAR flood mask ----------------------------
    dem = ee.Image(IMG_NASADEM).select(BAND_DEM)
    radii, best_r, best_iou = {}, None, -1.0
    for r in HAND_RADII_M:
        proxy = dem.subtract(dem.focalMin(radius=r, units="meters"))
        low = proxy.lt(LOW_HAND_M)
        stats = (low.And(nisar).rename("both")
                 .addBands(low.Or(nisar).rename("any"))
                 .reduceRegion(reducer=ee.Reducer.sum(), geometry=aoi,
                               scale=HAND_SCALE, maxPixels=MAX_PIXELS,
                               tileScale=TILE_SCALE)
                 .getInfo())
        both, any_ = (stats or {}).get("both") or 0, (stats or {}).get("any") or 0
        iou = both / any_ if any_ else 0.0
        radii[str(r)] = iou
        if iou > best_iou:
            best_r, best_iou = r, iou
    flat["hand_nisar_iou_radii"] = radii
    if best_r is None:
        flat["hand_nisar_iou"] = flat["hand_nisar_radius_m"] = None
        reasons["hand_nisar_iou"] = reasons["hand_nisar_radius_m"] = (
            "no valid HAND/NISAR overlap samples")
    else:
        flat["hand_nisar_iou"] = best_iou
        flat["hand_nisar_radius_m"] = best_r
    collections["HAND_proxy"] = f"{IMG_NASADEM} elev - focalMin(radius) < {LOW_HAND_M}m"
    return flat, reasons, collections


def task_rs2024() -> tuple[dict, dict, dict]:
    """Task B: HLS Fmask cloud-free fraction over the rs_2024 peak window."""
    case_id = "rs_2024"
    aoi = aoi_geom(case_id)
    flat, reasons = {}, {}
    col = (ee.ImageCollection(HLS_PRODUCTS[0][0]).select("Fmask")
           .merge(ee.ImageCollection(HLS_PRODUCTS[1][0]).select("Fmask"))
           .filterBounds(aoi).filterDate(*PEAK_RS))

    def _flags(img):
        fm = img.select("Fmask")
        valid = fm.mask().And(fm.neq(FMASK_FILL))
        clear = valid.And(fm.bitwiseAnd(FMASK_BAD).eq(0))
        return valid.rename("valid").addBands(clear.rename("clear"))

    union = col.map(_flags).max()  # pixel seen/clear in ANY scene
    stats = union.reduceRegion(reducer=ee.Reducer.sum(), geometry=aoi,
                               scale=SCALE, maxPixels=MAX_PIXELS)
    stats = (stats.set("hls_scenes", col.size())
             .set("hls_times", col.aggregate_array("system:time_start")).getInfo())
    stats = stats if isinstance(stats, dict) else {}
    valid = stats.get("valid") or 0
    flat["hls_scenes"] = int(stats.get("hls_scenes") or 0)
    dates = sorted({datetime.fromtimestamp(t / 1000, UTC).date().isoformat()
                    for t in (stats.get("hls_times") or [])})
    flat["hls_scene_dates"] = dates
    flat["cloud_pct_source"] = (
        f"NASA HLS v2.0 Fmask (HLSL30 + HLSS30), cloud/adjacent/"
        f"shadow/snow bits 1-4; {flat['hls_scenes']} scenes on "
        f"{', '.join(dates) or 'none'} ({PEAK_RS[0]}..2024-05-03) over the AOI; "
        f"a pixel counts clear if clear in ANY scene")
    flat["hls_observed_px"] = valid
    if valid:
        clear = stats.get("clear") or 0
        flat["cloud_free_pct"] = 100.0 * clear / valid
        flat["cloud_pct"] = 100.0 - flat["cloud_free_pct"]
    else:
        flat["cloud_free_pct"] = flat["cloud_pct"] = None
        reasons["cloud_free_pct"] = reasons["cloud_pct"] = (
            f"no HLS scenes {PEAK_RS[0]}..{PEAK_RS[1]} in the AOI")
    collections = {
        "cloud_pct_source": "NASA/HLS/HLSL30/v002 + NASA/HLS/HLSS30/v002 "
                            f"Fmask bits1-4 @ {PEAK_RS[0]}..2024-05-03",
    }
    return flat, reasons, collections


def _write(case_id: str, flat: dict, reasons: dict, collections: dict) -> Path:
    out = {
        "case": case_id,
        "event_date": str(case_cfg(case_id)["event_date"]),
        "computed_at": datetime.now(UTC).isoformat(),
        "metrics": flat,
        "collections": {k: v for k, v in collections.items() if v},
    }
    if reasons:
        out["_null_reasons"] = reasons
    path = data_dir(case_id) / "validation_metrics.json"
    path.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="tools.validation_metrics",
                               description=__doc__.splitlines()[0])
    ap.add_argument("--case", choices=("wa_2025", "rs_2024"), default=None,
                    help="limit to one case (default: both)")
    args = ap.parse_args(argv)
    init()
    for case_id, fn in (("wa_2025", task_wa2025), ("rs_2024", task_rs2024)):
        if args.case and args.case != case_id:
            continue
        flat, reasons, collections = fn()
        path = _write(case_id, flat, reasons, collections)
        print(f"[{case_id}] -> {path}")
        for k, v in flat.items():
            print(f"  {k}: {v}")
        for k, v in reasons.items():
            print(f"  ! {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
