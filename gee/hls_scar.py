"""wa_2025 landslide scar CANDIDATES — server-side GEE port of tp/hls_scar.py.

    uv run python -m gee.hls_scar --case wa_2025 [--dry-run|--tasks]

HLS v2.0 lives in the Earth Engine catalog (L7): NASA/HLS/HLSL30/v002
(NIR=B05, SWIR2=B07) and NASA/HLS/HLSS30/v002 (NIR=B8A, SWIR2=B12), int16
reflectance at scale 0.0001 with fill -9999. Per case window the two
sensors are merged and median-composited on Fmask-clear pixels:
pre = event_date - window_before_days .. event_date,
post = event_date .. event_date + window_after_days.

dNBR = NBR_pre - NBR_post (positive = vegetation loss). A pixel is a
candidate when dNBR > dnbr_min AND the post composite had clear-sky data
(inherent to the Fmask-masked composite) AND NASADEM slope >
config.yaml slope_range_deg[0] AND its connected blob holds >=
min_blob_pixels pixels (ee.Image.connectedPixelCount). dnbr_min=0.1 is the
burn-severity convention — EXPLORATORY: no validated landslide dNBR
threshold exists, so the cut needs calibration. --ndvi-vote adds a second
vote: NDVI_post < NDVI_pre (a scar strips green vegetation).

Fmask (HLS v2.0, bits from LSB): bit1 cloud, bit2 cloud-adjacent, bit3
cloud shadow, bit4 snow/ice, bit5 water, bits6-7 aerosol; fill 255. Clear
for candidates = bits1-4 unset (snow gated — December storms leave cover);
water stays in (runout deposits reach channels). cloud_pct = mean of Fmask
bits1-3 over the AOI + full window — the "why radar" number (.crew/
research/r-fmask.md).

LIMITATION: the NISAR GUNW coherence pair spanning the 2025-12 event is
missing (GCOV track 172D frame 064 covers 2025-11-10 / 2025-12-04 /
2025-12-16 but no event-crossing GUNW pair exists), so the coherence path
in tp/landslide_coh.py does not apply. Every output here is a CANDIDATE —
there is no confirmation step. The local raster path stays available as
pipeline/tp/hls_scar.py + scripts/09_hls_scar.py.
"""
from __future__ import annotations

import argparse
from datetime import timedelta
from pathlib import Path

import ee
from tp.config import case as config_case
from tp.config import load_config, threshold
from tp.metrics import merge_metrics

try:
    from gee.common import (
        ASSET_ROOT,
        BAND_DEM,
        GEE_PROJECT,
        IMG_NASADEM,
        aoi_geom,
        asset_path,
        init,
        stamp,
    )
except ModuleNotFoundError as exc:  # gee.common lands with U4 — --dry-run needs none of it
    if exc.name != "gee.common":
        raise
    GEE_PROJECT = "<GEE_PROJECT>"
    ASSET_ROOT = f"projects/{GEE_PROJECT}/assets/tipping-point"
    IMG_NASADEM, BAND_DEM = "NASA/NASADEM_HGT/001", "elevation"
    aoi_geom = asset_path = init = stamp = None

PRODUCTS = {
    "HLSL30": ("NASA/HLS/HLSL30/v002", {"red": "B04", "nir": "B05", "swir2": "B07"}),
    "HLSS30": ("NASA/HLS/HLSS30/v002", {"red": "B04", "nir": "B8A", "swir2": "B12"}),
}
REFL_BANDS = ["RED", "NIR", "SWIR2"]
FMASK_BAD = 0b11110          # bits 1-4: cloud | adjacent | shadow | snow/ice
FMASK_CLOUDY = 0b1110        # bits 1-3 — the "why radar" cloud fraction
FMASK_FILL = 255
HLS_FILL = -9999             # int16 reflectance fill (scale 0.0001)
DNBR_MIN = 0.1               # exploratory burn-severity cut — needs calibration
SCALE_M = 30                 # HLS native
MAX_PIXELS = 1e9
MIN_BLOB_PX = int(threshold("min_blob_pixels", 10))
MIN_SLOPE_DEG = float(threshold("slope_range_deg", [15, 30])[0])


def _common_ready() -> None:
    if init is None:
        raise RuntimeError(
            "gee.common not on this branch yet (U4) — only --dry-run works")


def _windows(cfg: dict) -> tuple[tuple, tuple]:
    """((pre_start, event_day), (event_day, post_end)) as date objects."""
    day0 = cfg["event_date"]
    if hasattr(day0, "date"):  # datetime -> date
        day0 = day0.date()
    return ((day0 - timedelta(days=int(cfg["window_before_days"])), day0),
            (day0, day0 + timedelta(days=int(cfg["window_after_days"]))))


def _hls_col(case_id: str, start: str, end: str):
    """L30+S30 merged over the AOI window; bands renamed RED/NIR/SWIR2/Fmask."""
    aoi = aoi_geom(case_id)
    cols = [
        ee.ImageCollection(cid).select(
            [b["red"], b["nir"], b["swir2"], "Fmask"],
            ["RED", "NIR", "SWIR2", "Fmask"])
        for cid, b in PRODUCTS.values()
    ]
    return cols[0].merge(cols[1]).filterBounds(aoi).filterDate(start, end)


def _clear_reflectance(img):
    """RED/NIR/SWIR2 masked to Fmask-clear (bits1-4 unset), non-fill pixels."""
    fmask = img.select("Fmask")
    clear = fmask.bitwiseAnd(FMASK_BAD).eq(0).And(fmask.neq(FMASK_FILL))
    ok = (img.select("NIR").neq(HLS_FILL)
          .And(img.select("SWIR2").neq(HLS_FILL))
          .And(img.select("RED").neq(HLS_FILL)))
    return img.select(REFL_BANDS).updateMask(clear.And(ok))


def _cloud_fraction(col, aoi):
    """Mean Fmask bits1-3 over AOI + window, as a percent."""
    cloudy = col.map(lambda img: (
        img.select("Fmask")
        .updateMask(img.select("Fmask").neq(FMASK_FILL))
        .bitwiseAnd(FMASK_CLOUDY).neq(0)))
    mean = cloudy.mean().reduceRegion(
        reducer=ee.Reducer.mean(), geometry=aoi, scale=SCALE_M,
        maxPixels=MAX_PIXELS)
    return ee.Number(mean.get("Fmask")).multiply(100)


def compute(
    case_id: str,
    dnbr_min: float = DNBR_MIN,
    ndvi_vote: bool = False,
) -> dict:
    """All server-side objects (no getInfo): metrics + the candidate image."""
    cfg = config_case(case_id)
    aoi = aoi_geom(case_id)
    (ps, pe), (qs, qe) = _windows(cfg)
    col_pre = _hls_col(case_id, str(ps), str(pe))
    col_post = _hls_col(case_id, str(qs), str(qe))
    comp_pre = col_pre.map(_clear_reflectance).median()
    comp_post = col_post.map(_clear_reflectance).median()
    dnbr = (comp_pre.normalizedDifference(["NIR", "SWIR2"])
            .subtract(comp_post.normalizedDifference(["NIR", "SWIR2"]))
            .rename("dNBR"))
    slope = ee.Terrain.slope(ee.Image(IMG_NASADEM).select(BAND_DEM))
    cand = dnbr.gt(dnbr_min).And(slope.gt(MIN_SLOPE_DEG))
    if ndvi_vote:
        dv = (comp_post.normalizedDifference(["NIR", "RED"])
              .subtract(comp_pre.normalizedDifference(["NIR", "RED"])))
        cand = cand.And(dv.lt(0))
    pix = cand.selfMask().connectedPixelCount(maxSize=1024, eightConnected=True)
    cand = cand.updateMask(pix.gte(MIN_BLOB_PX))
    labels = cand.selfMask().connectedComponents(
        ee.Kernel.square(1), eightConnected=True).select("labels")
    cand_px = cand.unmask(0).reduceRegion(
        reducer=ee.Reducer.sum(), geometry=aoi, scale=SCALE_M,
        maxPixels=MAX_PIXELS)
    patches = labels.reduceRegion(
        reducer=ee.Reducer.max(), geometry=aoi, scale=SCALE_M,
        maxPixels=MAX_PIXELS)
    return {
        "cloud_pct": _cloud_fraction(col_pre.merge(col_post), aoi),
        "landslide_hls_candidates": ee.Number(patches.get("labels")),
        "landslide_hls_candidate_px": ee.Number(cand_px.get("dNBR")),
        "image": cand.selfMask().unmask(0).toUint8().clip(aoi),
    }


def _asset_name(case_id: str) -> str:
    return f"{case_id}_landslide_candidates"


def export_task(case_id: str, image, project: str | None = None):
    """The candidate-mask asset export task (created, not started)."""
    # data_date = freshest source date — the post-window end (L8 stamp).
    (_, _), (_, qe) = _windows(config_case(case_id))
    img = image if stamp is None else stamp(image, qe.isoformat())
    return ee.batch.Export.image.toAsset(
        image=img, description=_asset_name(case_id),
        assetId=asset_path(_asset_name(case_id), project),
        region=aoi_geom(case_id), scale=SCALE_M, maxPixels=MAX_PIXELS)


def run(
    case_id: str,
    metrics_path: Path | None = None,
    merge: bool = True,
    exports: bool = True,
    dnbr_min: float = DNBR_MIN,
    ndvi_vote: bool = False,
) -> dict:
    """getInfo all computes; merge metrics + start the asset export."""
    _common_ready()
    proj = init()
    res = compute(case_id, dnbr_min=dnbr_min, ndvi_vote=ndvi_vote)
    flat = {k: v.getInfo() for k, v in res.items() if k != "image"}
    merged = (
        merge_metrics(
            case_id,
            flat=flat,
            reasons={
                "cloud_pct": "no HLS scenes in the case window",
                "landslide_hls_candidates": "no candidate blobs (cloudy window or quiet event)",
                "landslide_hls_candidate_px": "no candidate pixels",
            },
            collections={
                "HLS": f"{PRODUCTS['HLSL30'][0]} + {PRODUCTS['HLSS30'][0]}"
                       " (dNBR pre/post; Fmask bits1-4 masked)",
            },
            path=metrics_path,
        )
        if merge
        else None
    )
    tasks = [export_task(case_id, res["image"], proj)] if exports else []
    for task in tasks:
        task.start()
    return {"metrics": flat, "merged": merged, "tasks": tasks}


def plan(case_id: str) -> str:
    """Pure-python dry-run text: windows, gates, export, merged keys."""
    cfg = config_case(case_id)
    (ps, pe), (qs, qe) = _windows(cfg)
    bands = " + ".join(
        f"{name}={cid} (NIR {b['nir']}, SWIR2 {b['swir2']})"
        for name, (cid, b) in PRODUCTS.items())
    lines = [
        f"[hls_scar] case={case_id} event_date={cfg['event_date']}",
        f"  HLS: {bands}; fill {HLS_FILL}/{FMASK_FILL} masked",
        (f"  pre {ps}..{pe} | post {qs}..{qe} — median composites on"
         f" Fmask-clear pixels (bits1-4 = cloud/adjacent/shadow/snow)"),
        (f"  candidate = dNBR>{DNBR_MIN} (exploratory cut — needs calibration)"
         f" & NASADEM slope>{MIN_SLOPE_DEG}deg & blob>={MIN_BLOB_PX}px"
         " via connectedComponents; --ndvi-vote adds NDVI_post<pre"),
        f"  cloud_pct = mean Fmask bits1-3 over AOI+window @ {SCALE_M} m",
        (f"  export: asset {_asset_name(case_id)} = uint8 0/1 candidate mask"
         " — CANDIDATES only; missing GUNW pair is a declared limitation"),
        (f"  merge -> data/metrics.json[{case_id}]: cloud_pct"
         " landslide_hls_candidates landslide_hls_candidate_px (+collections HLS)"),
    ]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="gee.hls_scar", description=__doc__.splitlines()[0])
    ap.add_argument("--case", required=True, choices=sorted(load_config()["cases"]))
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true",
                   help="print planned computes+exports; no EE init")
    g.add_argument("--tasks", action="store_true",
                   help="run + start the asset export; skip metrics merge")
    ap.add_argument("--metrics-path", type=Path, default=None,
                    help="metrics.json override (testing)")
    ap.add_argument("--dnbr-min", type=float, default=DNBR_MIN,
                    help="dNBR candidate threshold (exploratory — needs calibration)")
    ap.add_argument("--ndvi-vote", action="store_true",
                    help="also require NDVI_post < NDVI_pre (second vote)")
    args = ap.parse_args(argv)
    if args.dry_run:
        print(plan(args.case))
        return 0
    out = run(args.case, metrics_path=args.metrics_path, merge=not args.tasks,
              dnbr_min=args.dnbr_min, ndvi_vote=args.ndvi_vote)
    for k, v in out["metrics"].items():
        print(f"  {k}: {v}")
    print(f"started {len(out['tasks'])} export task(s)"
          + ("; metrics not merged (--tasks)" if args.tasks else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
