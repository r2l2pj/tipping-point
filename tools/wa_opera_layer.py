"""WA-2025 NISAR vs OPERA DSWx-S1 like-for-like validation layer (TP/FP/FN).

Builds the categorical agreement map behind the metrics.json headline
iou/f1/confusion ("like-for-like", see data/SCHEMA.md 'Flood validation vs
OPERA DSWx'): the OPERA DSWx-S1 new flood — water on 2025-12-15/17 AND NOT
water on 2025-12-01..07 — vs the rasterized NISAR flood polygons, restricted
to pixels observed (not cloud/nodata) on BOTH dates, inside the AOI
(~ NISAR swath). Class values: 1=TP (both), 2=FP (NISAR only),
3=FN (OPERA only), 0=nodata.

The masks/expressions are imported from tools/validation_metrics.py
(`_opera_wtr`, `_opera_ref_obs`, `_nisar_mask`, `OPERA_WINDOW_WA`,
`OPERA_PRE_WINDOW_WA`) — not re-derived — so the layer matches the metrics
definition pixel-for-pixel (resampling aside).

Writes under data/wa_2025/:
  cog/wa_2025_opera_validation_20251216.tif  uint8 COG, EPSG:4326, 0=nodata
  png/opera_validation_20251216.png          EPSG:3857 RGBA: TP green #1a9850,
      FP orange #f46d43, FN blue #4575b4, nodata transparent
  opera_validation.json                    legend/definition/sources/bounds
and upserts the `opera_validation` entry in png/_index.json (same entry shape
the publisher emits for nisar_pre/nisar_post, plus `legend`).

    python tools/wa_opera_layer.py [--case wa_2025] [--scale-m 30|60] \
        [--check-only] [--dry-run]

--check-only recomputes the tp/fp/fn band sums server-side and prints them
next to metrics.json's confusion — no download, no writes.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
for p in (str(REPO_ROOT), str(REPO_ROOT / "pipeline")):
    if p not in sys.path:
        sys.path.insert(0, p)

from live.config import OPERA_CONFUSION_LABELS, OPERA_CONFUSION_PALETTE
from live.render import palette_png_bytes, warp_to_3857
from tp.events import DATA_DIR

from gee.common import aoi_geom, case_cfg, data_dir, init
from gee.opera import S1_COLLECTION, S1_WATER_CLASSES
from tools import validation_metrics as vm

CASE = "wa_2025"
LAYER_ID = "opera_validation"
POST_DATE = "2025-12-16"          # layer date = NISAR post pass (no OPERA granule)
POST_YMD = "20251216"
CLASSES = {"tp": 1, "fp": 2, "fn": 3}
LEGEND_LABELS = {"tp": "TP — both", "fp": "FP — NISAR only", "fn": "FN — OPERA only"}
SCALE_M = 30
CELL_M2 = float(SCALE_M * SCALE_M)
MAX_PIXELS = 1e9
TILE_SCALE = 4
DOWNLOAD_LIMIT = 32 * 1024 * 1024

DEFINITION = (
    "like-for-like: NISAR new-flood mask vs OPERA DSWx-S1 new flood "
    "(water on 2025-12-15/17 AND NOT water on 2025-12-01..07), observed "
    "cloud-free on both dates; within the NISAR swath ~ AOI")


def build_class_image(case_id: str = CASE) -> dict:
    """Server-side uint8 image: 1=tp, 2=fp, 3=fn, 0 elsewhere — the
    _opera_counts expressions on the like-for-like two-window reference."""
    import ee

    nisar, res = vm._nisar_mask(case_id)
    if nisar is None:
        raise RuntimeError(
            "NISAR flood polygons asset not found in data/wa_2025/assets.json")
    aoi = aoi_geom(case_id)
    wtr_post, col_post = vm._opera_wtr(S1_COLLECTION, *vm.OPERA_WINDOW_WA, aoi)
    wtr_pre, col_pre = vm._opera_wtr(S1_COLLECTION, *vm.OPERA_PRE_WINDOW_WA, aoi)
    ref_post, obs_post = vm._opera_ref_obs(wtr_post, S1_WATER_CLASSES)
    ref_pre, obs_pre = vm._opera_ref_obs(wtr_pre, S1_WATER_CLASSES)
    ref = ref_post.And(ref_pre.Not())       # new flood: post water AND NOT pre water
    obs = obs_post.And(obs_pre)             # observed cloud-free on both dates
    pred = nisar.And(obs)
    tp = ref.And(pred)
    fp = ref.Not().And(pred).updateMask(obs)
    fn = ref.And(pred.Not())
    cls = (ee.Image.constant(0).rename("confusion").byte()
           .where(tp.eq(1), CLASSES["tp"])
           .where(fp.eq(1), CLASSES["fp"])
           .where(fn.eq(1), CLASSES["fn"]))
    return {"image": cls, "bands": ee.Image.cat([tp.rename("tp"), fp.rename("fp"),
                                                 fn.rename("fn"), obs.rename("observed")]),
            "aoi": aoi, "col_post": col_post, "col_pre": col_pre,
            "nisar_asset": res["nisar"]}


def _granule_days(col) -> list[str]:
    """Sorted YYYY-MM-DD list of granule acquisition days in a collection."""
    import ee

    times = col.aggregate_array("system:time_start").getInfo() or []
    return sorted({ee.Date(t).format("YYYY-MM-dd").getInfo() for t in times})


def server_counts(built: dict) -> dict:
    """tp/fp/fn/observed sums on the native 30 m grid — same reducer as
    _opera_counts, so it must equal metrics.json's like-for-like confusion."""
    import ee

    stats = (built["bands"]
             .reduceRegion(reducer=ee.Reducer.sum(), geometry=built["aoi"],
                           scale=SCALE_M, maxPixels=MAX_PIXELS,
                           tileScale=TILE_SCALE)
             .getInfo()) or {}
    return {"tp": stats.get("tp") or 0, "fp": stats.get("fp") or 0,
            "fn": stats.get("fn") or 0, "observed": stats.get("observed") or 0,
            "scenes_post": int(built["col_post"].size().getInfo() or 0),
            "scenes_pre": int(built["col_pre"].size().getInfo() or 0),
            "days_post": _granule_days(built["col_post"]),
            "days_pre": _granule_days(built["col_pre"])}


def _deg_per_px(aoi_bounds: list[float], scale_m: float) -> float:
    """Degrees per pixel for a nominal meter scale — EE's meters->degrees
    conversion (scale/111320): the same lattice reduceRegion(scale=30) and the
    metrics.json confusion were counted on (~600 m2 cells at 48 deg N)."""
    return scale_m / 111320.0


def download_grid(img, aoi, aoi_bounds: list[float], scale_m: float = SCALE_M):
    """getDownloadURL GeoTIFF on an EPSG:4326 grid -> (uint8 array, transform,
    crs, deg_per_px). 32 MB payload cap like gee/lead_time's _download."""
    import rasterio
    import requests

    w, s, e, n = aoi_bounds
    deg = _deg_per_px(aoi_bounds, scale_m)
    url = img.clip(aoi).getDownloadURL({
        "region": aoi, "crs": "EPSG:4326",
        "crs_transform": [deg, 0.0, w, 0.0, -deg, n],
        "format": "GEO_TIFF"})
    blob = requests.get(url, timeout=600).content
    if len(blob) > DOWNLOAD_LIMIT:
        raise RuntimeError(
            f"download payload {len(blob)} B exceeds {DOWNLOAD_LIMIT} B — "
            "retry with --scale-m 60")
    if blob[:2] == b"PK":  # EE replies zip-wrapped GeoTIFF
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            name = next(nm for nm in z.namelist() if nm.endswith(".tif"))
            blob = z.read(name)
    with rasterio.io.MemoryFile(blob) as mem, mem.open() as ds:
        arr = np.asarray(ds.read(1), dtype=np.uint8)
        return arr, ds.transform, ds.crs, deg, (ds.width, ds.height)


def recount(arr: np.ndarray) -> dict:
    """Local tp/fp/fn pixel recount on a downloaded/written class grid."""
    a = np.asarray(arr)
    return {k: int((a == v).sum()) for k, v in CLASSES.items()}


def class_km2(arr: np.ndarray, transform) -> dict:
    """Geodesic km2 per class — per-row cos(lat) cell area (same formula as
    tp.bundle._critical_stats)."""
    a = np.asarray(arr)
    lats = transform.f + (np.arange(a.shape[0]) + 0.5) * transform.e
    row_km2 = (abs(transform.a) * 111320.0 * np.cos(np.deg2rad(lats))
               * abs(transform.e) * 110540.0) / 1e6
    return {k: float((row_km2 * (a == v).sum(axis=1)).sum())
            for k, v in CLASSES.items()}


def legend_rows(counts: dict, km2: dict) -> list[dict]:
    """opera_validation.json legend entries {class,label,color,px,km2}."""
    return [{"class": CLASSES[k], "label": LEGEND_LABELS[k],
             "color": OPERA_CONFUSION_PALETTE[CLASSES[k]],
             "px": int(counts[k]), "km2": round(float(km2[k]), 3)}
            for k in ("tp", "fp", "fn")]


def confusion_metrics(counts: dict) -> dict:
    """iou/f1 from a {tp,fp,fn} recount (same formulas as _metrics)."""
    tp, fp, fn = (counts.get(k, 0) or 0 for k in ("tp", "fp", "fn"))
    return {"iou": tp / (tp + fp + fn) if tp + fp + fn else 0.0,
            "f1": 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else 0.0,
            "confusion": {"tp": tp, "fp": fp, "fn": fn}}


def write_cog(arr: np.ndarray, transform, crs, path: Path) -> Path:
    """uint8 single-band COG (EPSG:4326, nodata=0); plain tiled GTiff
    fallback when the GDAL COG driver is unavailable."""
    import rasterio
    from rasterio.crs import CRS

    profile = {"driver": "COG", "height": arr.shape[0], "width": arr.shape[1],
               "count": 1, "dtype": "uint8", "crs": CRS.from_user_input(crs),
               "transform": transform, "nodata": 0}
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.Env():
        try:
            with rasterio.open(path, "w", **profile) as dst:
                dst.write(arr, 1)
        except rasterio.errors.DriverRegistrationError:
            profile.update(driver="GTiff", tiled=True, compress="deflate")
            with rasterio.open(path, "w", **profile) as dst:
                dst.write(arr, 1)
    return path


def png_3857(arr: np.ndarray, transform):
    """EPSG:3857 RGBA PNG bytes + lon/lat bounds via live.render warp."""
    cls3857, _, bounds = warp_to_3857(np.asarray(arr, dtype=np.uint8), transform)
    valid = cls3857 != 0
    return palette_png_bytes(cls3857, valid, OPERA_CONFUSION_PALETTE), bounds


def index_entry(png_rel: str, cog_rel: str, bounds: list[float]) -> dict:
    """_index.json entry — the publisher's pinned shape + legend/kind."""
    return {"id": LAYER_ID, "date": POST_DATE, "png": png_rel, "cog": cog_rel,
            "bounds": bounds, "forecast": False, "kind": "observed",
            "legend": list(OPERA_CONFUSION_LABELS)}


def upsert_index(layers: list[dict], entry: dict) -> list[dict]:
    """Replace same id+date (idempotent re-run), else append — same merge
    pattern gee/impact.py uses for night_lights entries."""
    out = [ly for ly in layers
           if not (ly.get("id") == entry["id"] and ly.get("date") == entry["date"])]
    out.append(entry)
    return out


def build_json(counts: dict, km2: dict, bounds: list[float], deg: float,
               dims: tuple[int, int], sources: dict, scale_m: float,
               metrics_conf: dict | None) -> dict:
    """data/<case>/opera_validation.json payload."""
    m = confusion_metrics(counts)
    out = {"layer": LAYER_ID, "case": CASE, "date": POST_DATE,
           "generated_at": datetime.now(UTC).isoformat(),
           "definition": DEFINITION,
           "classes": {"0": "nodata", "1": "tp (both)", "2": "fp (NISAR only)",
                       "3": "fn (OPERA only)"},
           "legend": legend_rows(counts, km2),
           "grid": {"crs": "EPSG:4326", "nominal_m": scale_m, "deg_per_px": deg,
                    "note": "EE reduceRegion scale lattice (deg=m/111320); "
                            "km2 computed geodesically per row",
                    "width": dims[0], "height": dims[1]},
           "sources": sources,
           "bounds": bounds,
           "iou": m["iou"], "f1": m["f1"],
           "confusion": m["confusion"]}
    if metrics_conf:
        out["metrics_json"] = metrics_conf
        out["ratios_vs_metrics"] = {
            k: (round(counts[k] / metrics_conf[k], 4) if metrics_conf.get(k)
                else None) for k in ("tp", "fp", "fn")}
    return out


def _metrics_confusion() -> dict | None:
    """data/metrics.json wa_2025 confusion {tp,fp,fn} + iou/f1, or None."""
    path = DATA_DIR / "metrics.json"
    try:
        blob = json.loads(path.read_text())["cases"][CASE]
    except (OSError, json.JSONDecodeError, KeyError):
        return None
    conf = blob.get("confusion") or {}
    out = {k: conf.get(k) for k in ("tp", "fp", "fn")}
    out["iou"] = blob.get("iou")
    out["f1"] = blob.get("f1")
    return out if all(v is not None for v in out.values()) else None


def _print_compare(local: dict, ref: dict | None, tag: str) -> None:
    for k in ("tp", "fp", "fn"):
        line = f"  {k}: {local[k]}"
        if ref and ref.get(k) is not None:
            r = local[k] / ref[k] if ref[k] else float("nan")
            line += f"  vs metrics.json {ref[k]}  (ratio {r:.4f})"
        print(line)
    if ref:
        print(f"  iou: {confusion_metrics(local)['iou']:.4f} "
              f"vs {ref.get('iou')}   f1: {confusion_metrics(local)['f1']:.4f} "
              f"vs {ref.get('f1')}")


def plan(case_id: str = CASE, scale_m: float = SCALE_M) -> str:
    aoi_bounds = list(case_cfg(case_id)["aoi"])
    deg = _deg_per_px(aoi_bounds, scale_m)
    w, s, e, n = aoi_bounds
    return "\n".join([
        f"[{LAYER_ID}] case={case_id} scale_m={scale_m} "
        f"({deg:.7f} deg/px, ~{math.ceil((e - w) / deg)}x{math.ceil((n - s) / deg)} px)",
        f"  ref: OPERA DSWx-S1 ({S1_COLLECTION}) water "
        f"{vm.OPERA_WINDOW_WA[0]}..{vm.OPERA_WINDOW_WA[1]} AND NOT water "
        f"{vm.OPERA_PRE_WINDOW_WA[0]}..{vm.OPERA_PRE_WINDOW_WA[1]}, "
        "observed both dates",
        "  pred: rasterized NISAR flood polygons (data/wa_2025/assets.json)",
        f"  out: cog/wa_2025_opera_validation_{POST_YMD}.tif + "
        f"png/{LAYER_ID}_{POST_YMD}.png + opera_validation.json; "
        "upsert png/_index.json",
    ])


def run(case_id: str = CASE, scale_m: float = SCALE_M, check_only: bool = False,
        dry_run: bool = False) -> dict:
    if dry_run:
        print(plan(case_id, scale_m))
        return {}
    init()
    built = build_class_image(case_id)
    srv = server_counts(built)
    ref = _metrics_confusion()
    print(f"[{case_id}] server-side reduceRegion (native 30 m):")
    _print_compare(srv, ref, "server")
    print(f"  scenes post {srv['scenes_post']} ({','.join(srv['days_post'])}) "
          f"pre {srv['scenes_pre']} ({','.join(srv['days_pre'])}) "
          f"observed_px {srv['observed']}")
    if check_only:
        return {"server": srv, "metrics_json": ref}

    aoi_bounds = list(case_cfg(case_id)["aoi"])
    arr, transform, crs, deg, dims = download_grid(
        built["image"], built["aoi"], aoi_bounds, scale_m)
    local = recount(arr)
    km2 = class_km2(arr, transform)
    print(f"[{case_id}] downloaded {dims[0]}x{dims[1]} px @ {deg:.7f} deg/px "
          f"({scale_m} m nominal — EE scale lattice):")
    _print_compare(local, ref, "local")

    case_dir = data_dir(case_id)
    cog_rel = f"{case_id}/cog/{case_id}_opera_validation_{POST_YMD}.tif"
    png_rel = f"{case_id}/png/{LAYER_ID}_{POST_YMD}.png"
    cog_path = write_cog(arr, transform, crs, case_dir / "cog" / Path(cog_rel).name)
    png_bytes, bounds = png_3857(arr, transform)
    png_path = case_dir / "png" / Path(png_rel).name
    png_path.parent.mkdir(parents=True, exist_ok=True)
    png_path.write_bytes(png_bytes)

    sources = {
        "opera_dswx_s1": (f"catalog:{S1_COLLECTION} post "
                          f"{','.join(srv['days_post'])} (no {POST_DATE} granule "
                          "exists); pre " + ",".join(srv["days_pre"])),
        "nisar_flood_polygons": built["nisar_asset"],
        "nisar_gcov": _nisar_gcov_source(case_dir),
    }
    doc = build_json(local, km2, bounds, deg, dims, sources, scale_m, ref)
    json_path = case_dir / "opera_validation.json"
    json_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")

    index_path = case_dir / "png" / "_index.json"
    layers = []
    if index_path.exists():
        layers = json.loads(index_path.read_text()).get("layers", [])
    index_path.write_text(json.dumps(
        {"layers": upsert_index(layers, index_entry(png_rel, cog_rel, bounds))},
        indent=2) + "\n")

    print(f"  cog  -> {cog_path}")
    print(f"  png  -> {png_path} ({len(png_bytes)} B, bounds {bounds})")
    print(f"  json -> {json_path}")
    print(f"  idx  -> {index_path} (upserted {LAYER_ID} @ {POST_DATE})")
    return {"server": srv, "local": local, "metrics_json": ref,
            "bounds": bounds, "png_bytes": len(png_bytes), "dims": dims}


def _nisar_gcov_source(case_dir: Path) -> str:
    """NISAR GCOV pre/post granule ids from nisar_products.json."""
    try:
        doc = json.loads((case_dir / "nisar_products.json").read_text())
        gcov = doc.get("GCOV", {})
        return (f"{gcov.get('collection')} post {gcov.get('post')} "
                f"({POST_DATE}); pre {gcov.get('pre')} (2025-12-04)")
    except (OSError, json.JSONDecodeError):
        return "NISAR_L2_GCOV_BETA_V1 post 2025-12-16 / pre 2025-12-04"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="tools.wa_opera_layer",
                               description=__doc__.splitlines()[0])
    ap.add_argument("--case", default=CASE, choices=(CASE,))
    ap.add_argument("--scale-m", type=float, default=SCALE_M,
                    help="nominal cell size of the EPSG:4326 download grid "
                         "(default 30; retry 60 if the download is too big)")
    ap.add_argument("--check-only", action="store_true",
                    help="server-side recount vs metrics.json only; no "
                         "download or writes")
    ap.add_argument("--dry-run", action="store_true",
                    help="print planned computes; no EE init")
    args = ap.parse_args(argv)
    run(case_id=args.case, scale_m=args.scale_m,
        check_only=args.check_only, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
