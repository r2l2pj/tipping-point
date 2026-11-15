"""HAND proxy validation vs MERIT Hydro — local compute on one common 90 m grid.

MERIT Hydro 'hnd' is a validation REFERENCE only — never an index input
(NASA-only rule). The HAND proxy = NASADEM elevation minus its focal minimum
over a radius. For each radius (1000/1500/3000 m) the proxy is built
server-side, then MERIT 'hnd' plus all proxy bands are downloaded as ONE
multi-band NPY via getDownloadURL (scale 90 m, EPSG:4326, clipped to the case
AOI, tiled over its bounding box to stay under download limits) and compared
locally on identical pixels — server-side reduceRegion counts proved
inconsistent, so no statistic mixes grids:

* Pearson r over all pixels valid on both bands, plus a 5k-pixel sample
  (seed 1)
* IoU of the HAND<5 m zones (proxy <5 m vs MERIT hnd <5 m)
* each zone's share of the compared pixels

Merged per case into data/metrics.json via tp.metrics.merge_metrics:

* hand_validation_iou — IoU 0-1 at the best radius
* hand_validation_r — Pearson r (-1..1) at the best radius
* hand_validation_radius_m — best radius; highest IoU, ties -> smallest.
  Reported only: config.yaml hand_proxy_radius_m is never rewritten.
* hand_validation_radii — {radius_m: iou 0-1} (app contract shape)
* hand_validation_by_radius — {radius_m: {iou, r, r_5k, n_px, low_proxy_pct,
  low_merit_pct}}
* hand_validation_definition — method text
* hand_validation — legacy key, same best-radius IoU in 0-1 (was a 0-100
  percent before this schema; unit stated by hand_validation_definition)

Usage: python -m gee.hand --case <id> [--dry-run|--tasks]
"""
from __future__ import annotations

import argparse
import math
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
GRID_SCALE = 90
GRID_CRS = "EPSG:4326"
GRID_TILES = 3  # minimum per axis, over the AOI bounding box
MAX_TILE_DEG = 1.0  # ~1100 px of 90 m per tile side
SAMPLE_PIXELS = 5000
SAMPLE_SEED = 1
DEFINITION = (
    "IoU of HAND<5 m zones, NASADEM proxy z - min z within radius vs MERIT "
    "Hydro hnd (reference only), 90 m, case AOI; r = Pearson over pixels"
)


def _common_ready() -> None:
    if init is None:
        raise RuntimeError("gee.common not on this branch yet (U4) — only --dry-run works")


def _fetch(url) -> bytes:
    import requests

    r = requests.get(url, timeout=600)
    if r.status_code != 200 or not r.content.startswith(b"\x93NUMPY"):
        raise RuntimeError(f"NPY download failed (HTTP {r.status_code}): {r.content[:300]!r}")
    return r.content


def _grid(img, aoi) -> list:
    """NPY tiles of `img` over the AOI bounding box — one grid for all bands."""
    import io

    import numpy as np

    coords = aoi.bounds().getInfo()["coordinates"][0]
    xs = [p[0] for p in coords]
    ys = [p[1] for p in coords]
    w, e, s, n = min(xs), max(xs), min(ys), max(ys)
    # at least GRID_TILES per axis, and tiles of <= MAX_TILE_DEG so every
    # request stays under the getDownloadURL size cap (4 float bands, 90 m)
    tiles = max(GRID_TILES, math.ceil(max(e - w, n - s) / MAX_TILE_DEG))
    arrays = []
    for i in range(tiles):
        for j in range(tiles):
            region = ee.Geometry.Rectangle([
                w + (e - w) * j / tiles,
                s + (n - s) * (tiles - 1 - i) / tiles,
                w + (e - w) * (j + 1) / tiles,
                s + (n - s) * (tiles - i) / tiles,
            ])
            url = img.getDownloadURL({
                "region": region,
                "scale": GRID_SCALE,
                "crs": GRID_CRS,
                "format": "NPY",
            })
            arrays.append(np.load(io.BytesIO(_fetch(url))))
    return arrays


def _band(arrays, name):
    import numpy as np

    return np.concatenate([np.asarray(a[name]).ravel() for a in arrays]).astype(float)


def _stats(proxy, merit) -> dict:
    """Local {iou, r, r_5k, n_px, low_proxy_pct, low_merit_pct} on common pixels."""
    import numpy as np

    ok = np.isfinite(proxy) & np.isfinite(merit) & (proxy > -1e30) & (merit > -1e30)
    p, m = proxy[ok], merit[ok]
    if p.size < 2:
        return {"iou": None, "r": None, "r_5k": None, "n_px": int(p.size),
                "low_proxy_pct": None, "low_merit_pct": None}
    low_p, low_m = p < LOW_HAND_M, m < LOW_HAND_M
    union = int((low_p | low_m).sum())
    rng = np.random.default_rng(SAMPLE_SEED)
    idx = rng.choice(p.size, min(SAMPLE_PIXELS, p.size), replace=False)
    return {
        "iou": float((low_p & low_m).sum() / union) if union else None,
        "r": float(np.corrcoef(p, m)[0, 1]),
        "r_5k": float(np.corrcoef(p[idx], m[idx])[0, 1]),
        "n_px": int(p.size),
        "low_proxy_pct": float(100 * low_p.mean()),
        "low_merit_pct": float(100 * low_m.mean()),
    }


def compute(case_id: str) -> dict:
    """Per-radius stats vs MERIT, all bands on one clipped 90 m grid."""
    aoi = aoi_geom(case_id)
    dem = ee.Image(IMG_NASADEM).select(BAND_DEM)
    merit = ee.Image(COL_MERIT).select("hnd").rename("merit")
    img = merit
    for r in RADII_M:
        img = img.addBands(
            dem.subtract(dem.focalMin(radius=r, units="meters")).rename(f"p{r}"))
    arrays = _grid(img.clip(aoi).toFloat(), aoi)
    m = _band(arrays, "merit")
    return {r: _stats(_band(arrays, f"p{r}"), m) for r in RADII_M}


def best_radius(stats: dict) -> int:
    """Radius with the highest IoU (ties -> smallest radius)."""
    return max(stats, key=lambda r: (stats[r]["iou"], -int(r)))


def run(case_id: str, metrics_path: Path | None = None, merge: bool = True) -> dict:
    _common_ready()
    init()
    stats = compute(case_id)
    valid = {r: s for r, s in stats.items()
             if all(isinstance(s.get(k), (float, int)) and math.isfinite(s[k])
                    for k in ("r", "iou"))}
    radius = best_radius(valid) if valid else None
    best = valid.get(radius) or {}
    flat = {
        "hand_validation": best.get("iou"),
        "hand_validation_iou": best.get("iou"),
        "hand_validation_r": best.get("r"),
        "hand_validation_radius_m": radius,
        "hand_validation_radii": {str(r): stats[r]["iou"] for r in RADII_M},
        "hand_validation_by_radius": {str(r): stats[r] for r in RADII_M},
        "hand_validation_definition": DEFINITION,
    }
    why = "No valid MERIT comparison samples"
    reasons = ({k: why for k in ("hand_validation", "hand_validation_iou",
                                 "hand_validation_r", "hand_validation_radius_m")}
               if radius is None else {})
    merged = (
        merge_metrics(
            case_id,
            flat=flat,
            reasons=reasons,
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
            f"  radii: {list(RADII_M)} — one NPY grid (scale={GRID_SCALE}, {GRID_CRS},"
            f" {GRID_TILES}x{GRID_TILES} tiles over the clipped AOI), local stats:"
            f" Pearson r (all px + {SAMPLE_PIXELS}px seed={SAMPLE_SEED})"
            + f" + IoU of HAND<{LOW_HAND_M} m zones + zone % of compared px",
            ("  best radius = argmax(IoU), ties -> smallest → hand_validation_iou/r/"
             "radius_m/radii/definition (+ legacy hand_validation = IoU 0-1);"
             " config.yaml NOT rewritten"),
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
    for r, s in out["metrics"]["hand_validation_by_radius"].items():
        print(f"  radius {r} m: r={s['r']} iou={s['iou']} n={s['n_px']}"
              f" low_proxy={s['low_proxy_pct']}% low_merit={s['low_merit_pct']}%")
    print(f"  hand_validation_radius_m: {out['metrics']['hand_validation_radius_m']}"
          " (reported only — config.yaml untouched)"
          + ("; metrics not merged (--tasks)" if args.tasks else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
