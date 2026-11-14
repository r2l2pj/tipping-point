"""HAND proxy validation vs MERIT Hydro — Python port of gee/04_hand_vs_merit.js.

MERIT Hydro 'hnd' is a validation REFERENCE only — never an index input
(NASA-only rule). The HAND proxy = NASADEM elevation minus its focal minimum
over a radius; for each of the three radii (1000/1500/3000 m) we validate
against MERIT with (1) Pearson r on a 5k-pixel sample and (2) IoU of the
HAND<5 m zone, then pick the radius with the highest combined r+iou.

Merges hand_validation {radius_m: {r, iou}} + hand_best_radius_m into
data/metrics.json (keyed by case) via tp.metrics.merge_metrics.

Usage: python -m gee.hand --case <id> [--dry-run|--tasks]
"""
from __future__ import annotations

import argparse
from pathlib import Path

import ee
from tp.config import case as config_case
from tp.config import load_config
from tp.metrics import merge_metrics

try:
    from gee.common import BAND_DEM, IMG_NASADEM, aoi_geom, init
except ModuleNotFoundError as exc:  # gee.common lands with U4 — --dry-run needs none of it
    if exc.name != "gee.common":
        raise
    IMG_NASADEM = BAND_DEM = aoi_geom = init = None

COL_MERIT = "MERIT/Hydro/v1_0_1"
RADII_M = (1000, 1500, 3000)
LOW_HAND_M = 5  # risk zone = HAND below 5 m
SAMPLE_SCALE = 90
SAMPLE_PIXELS = 5000
SAMPLE_SEED = 1
MAX_PIXELS = 1e9


def _common_ready() -> None:
    if init is None:
        raise RuntimeError("gee.common not on this branch yet (U4) — only --dry-run works")


def _radius_ee(dem, merit, aoi, radius_m: int) -> dict:
    """Server-side {r, iou} for one radius (no getInfo)."""
    proxy = dem.subtract(dem.focalMin(radius=radius_m, units="meters")).rename("proxy")
    pair = proxy.addBands(merit).clip(aoi)
    sample = pair.sample(region=aoi, scale=SAMPLE_SCALE, numPixels=SAMPLE_PIXELS, seed=SAMPLE_SEED)
    corr = sample.reduceColumns(ee.Reducer.pearsonsCorrelation(), ["proxy", "merit"])
    low_p, low_m = proxy.lt(LOW_HAND_M), merit.lt(LOW_HAND_M)
    stats = (
        low_p.And(low_m)
        .rename("both")
        .addBands(low_p.Or(low_m).rename("any"))
        .reduceRegion(reducer=ee.Reducer.sum(), geometry=aoi, scale=SAMPLE_SCALE, maxPixels=MAX_PIXELS)
    )
    return {
        "r": corr.get("correlation"),
        "iou": ee.Number(stats.get("both")).divide(stats.get("any")),
    }


def _radius_stats(dem, merit, aoi, radius_m: int) -> dict:
    ee_stats = _radius_ee(dem, merit, aoi, radius_m)
    return {"r": ee.Number(ee_stats["r"]).getInfo(), "iou": ee.Number(ee_stats["iou"]).getInfo()}


def best_radius(stats: dict) -> int:
    """Radius with the highest combined r + iou (ties -> smallest radius)."""
    return max(stats, key=lambda r: stats[r]["r"] + stats[r]["iou"])


def compute(case_id: str) -> dict:
    aoi = aoi_geom(case_id)
    dem = ee.Image(IMG_NASADEM).select(BAND_DEM)
    merit = ee.Image(COL_MERIT).select("hnd").rename("merit")
    return {r: _radius_stats(dem, merit, aoi, r) for r in RADII_M}


def run(case_id: str, metrics_path: Path | None = None, merge: bool = True) -> dict:
    _common_ready()
    init()
    stats = compute(case_id)
    flat = {
        "hand_validation": {str(r): stats[r] for r in RADII_M},
        "hand_best_radius_m": best_radius(stats),
    }
    merged = (
        merge_metrics(
            case_id,
            flat=flat,
            collections={"NASADEM": IMG_NASADEM, "MERIT_Hydro": COL_MERIT},
            path=metrics_path,
        )
        if merge
        else None
    )
    return {"metrics": flat, "merged": merged}


def plan(case_id: str) -> str:
    cfg = config_case(case_id)
    nasadem = IMG_NASADEM or "NASA/NASADEM_HGT/001"
    return "\n".join(
        [
            f"[hand] case={case_id} event_date={cfg['event_date']}",
            f"  proxy = {nasadem} elevation - focalMin(radius) vs {COL_MERIT} 'hnd' (reference only)",
            f"  radii: {list(RADII_M)} — per radius: Pearson r on"
            f" sample(scale={SAMPLE_SCALE}, numPixels={SAMPLE_PIXELS}, seed={SAMPLE_SEED})"
            + f" + IoU of HAND<{LOW_HAND_M} m zone",
            "  best radius = argmax(r + iou) → hand_validation{radius:{r,iou}}, hand_best_radius_m",
            f"  merge → data/metrics.json[{case_id}] (collections: {nasadem}, {COL_MERIT})",
        ]
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="gee.hand", description=__doc__.splitlines()[0])
    ap.add_argument("--case", required=True, choices=sorted(load_config()["cases"]))
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="print the three-radius plan; no EE init")
    g.add_argument("--tasks", action="store_true", help="run the validation computes; skip metrics merge")
    ap.add_argument("--metrics-path", type=Path, default=None, help="metrics.json override (testing)")
    args = ap.parse_args(argv)
    if args.dry_run:
        print(plan(args.case))
        return 0
    out = run(args.case, metrics_path=args.metrics_path, merge=not args.tasks)
    for r, s in out["metrics"]["hand_validation"].items():
        print(f"  radius {r} m: r={s['r']} iou={s['iou']}")
    print(f"  hand_best_radius_m: {out['metrics']['hand_best_radius_m']}"
          + ("; metrics not merged (--tasks)" if args.tasks else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
