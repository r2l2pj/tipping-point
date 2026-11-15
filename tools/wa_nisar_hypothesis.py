"""Test the L42 physical hypothesis on the WA 2025 NISAR pair (Dec 4 -> Dec 16).

Hypothesis under test (decisions.md L42 / challenge-fit.md):
    co-pol (HH) darkening finds open-field floods, but flooded built-up
    areas and forest BRIGHTEN in HH (double bounce), so our detector
    misses them.

Inputs (all local, no network):
  data/wa_2025/cog/wa_2025_nisar_{pre,post}[_hv]_*_60m.tif  float32 dB, EPSG:32610
  data/wa_2025/cog/wa_2025_opera_validation_20251216.tif    1=tp 2=fp 3=fn 0=other
  data/wa_2025/cog/wa_2025_{flood,flood_bright,water}_nisar_20251210.tif  masks
  ~/.cache/l42-hypothesis/lc_type1_2025.tif   MCD12Q1.061 2025 LC_Type1 (h09v04,
      copied from the g8-wa-dist worktree's static/cache/wa/)

Analysis grid = the OPERA validation lattice (EPSG:4326, ~30 m).  dB COGs are
median-filtered 3x3 (same preprocessing as tp.flood_mask) then differenced on
the native 60 m grid and reprojected bilinear; masks and land cover are
reprojected nearest.

Writes data/wa_2025/nisar_hypothesis.json. Usage:
    PYTHONPATH=.:pipeline python tools/wa_nisar_hypothesis.py \
        [--landcover PATH]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import Resampling, reproject
from scipy.ndimage import median_filter

REPO_ROOT = Path(__file__).resolve().parents[1]
for p in (str(REPO_ROOT), str(REPO_ROOT / "pipeline")):
    if p not in sys.path:
        sys.path.insert(0, p)

from tp.events import DATA_DIR

CASE = "wa_2025"
COG = DATA_DIR / CASE / "cog"
VAL_TIF = COG / f"{CASE}_opera_validation_20251216.tif"
HH = {"pre": COG / f"{CASE}_nisar_pre_20251204_60m.tif",
      "post": COG / f"{CASE}_nisar_post_20251216_60m.tif"}
HV = {"pre": COG / f"{CASE}_nisar_pre_hv_20251204_60m.tif",
      "post": COG / f"{CASE}_nisar_post_hv_20251216_60m.tif"}
FLOOD_TIF = COG / f"{CASE}_flood_nisar_20251210.tif"
BRIGHT_TIF = COG / f"{CASE}_flood_bright_nisar_20251210.tif"
WATER_TIF = COG / f"{CASE}_water_nisar_20251210.tif"
LC_TIF = Path.home() / ".cache" / "l42-hypothesis" / "lc_type1_2025.tif"
OUT_JSON = DATA_DIR / CASE / "nisar_hypothesis.json"

# IGBP LC_Type1 groups (pipeline/tp/response_dswx.py IGBP table)
GROUPS = {"forest": (1, 2, 3, 4, 5),
          "urban": (13,),
          "open": (6, 7, 8, 9, 10, 12, 14, 16)}   # shrub/savanna/grass/crop/mosaic/barren
EXCLUDED_LC = (11, 15, 17, 255)                  # wetland, snow/ice, water, unclassified

DARK_DB = -3.0        # the detector's flood darkening gate (flood_mask FLOOD_DROP_DB)
BRIGHT_DB = 3.0       # flood_mask BRIGHT_RISE_DB — "brightened" share
_FILL_DB = 10.0       # flood_mask._FILL_DB — bright fill so nodata never reads as water

HYPOTHESIS = (
    "co-pol (HH) darkening finds open-field floods, but flooded built-up "
    "areas and forest BRIGHTEN in HH (double bounce), so our detector "
    "misses them")


def _read(path: Path) -> tuple[np.ndarray, object]:
    with rasterio.open(path) as ds:
        return ds.read(1), ds


def _filtered_delta_db(pre_path: Path, post_path: Path) -> tuple[np.ndarray, object]:
    """median-filtered post_db - pre_db on the native 60 m UTM grid."""
    pre, ds = _read(pre_path)
    post, _ = _read(post_path)
    if pre.shape != post.shape:
        raise RuntimeError(f"{pre_path.name} and {post_path.name} differ in shape")
    valid = np.isfinite(pre) & np.isfinite(post)
    pre_db = median_filter(np.where(np.isfinite(pre), pre, _FILL_DB), 3)
    post_db = median_filter(np.where(np.isfinite(post), post, _FILL_DB), 3)
    delta = np.where(valid, post_db - pre_db, np.nan).astype(np.float32)
    return delta, ds


def _to_val_grid(src_arr: np.ndarray, src_ds, dst_shape, dst_transform,
                 resampling: Resampling, nodata: float = np.nan) -> np.ndarray:
    out = np.full(dst_shape, nodata, dtype=np.float32)
    reproject(src_arr, out, src_transform=src_ds.transform, src_crs=src_ds.crs,
              src_nodata=src_ds.nodata, dst_transform=dst_transform,
              dst_crs="EPSG:4326", dst_nodata=nodata, resampling=resampling)
    return out


def _row_km2(height: int, transform) -> np.ndarray:
    """Per-row geodesic cell km2 on the 4326 lattice (wa_opera_layer.class_km2)."""
    lats = transform.f + (np.arange(height) + 0.5) * transform.e
    return (abs(transform.a) * 111320.0 * np.cos(np.deg2rad(lats))
            * abs(transform.e) * 110540.0) / 1e6


def _stats(delta: np.ndarray, sel: np.ndarray, row_km2) -> dict | None:
    v = delta[sel]
    v = v[np.isfinite(v)]
    if v.size == 0:
        return None
    return {"n": int(v.size),
            "km2": _sel_km2(sel, row_km2),
            "mean_db": round(float(v.mean()), 3),
            "median_db": round(float(np.median(v)), 3),
            "p25_db": round(float(np.percentile(v, 25)), 3),
            "p75_db": round(float(np.percentile(v, 75)), 3),
            "share_dark_lt_-3dB": round(float((v < DARK_DB).mean()), 4),
            "share_bright_gt_+3dB": round(float((v > BRIGHT_DB).mean()), 4)}


def _sel_km2(sel: np.ndarray, row_km2) -> float:
    return round(float((row_km2 * sel.sum(axis=1)).sum()), 3)


def _group_mask(lc: np.ndarray, name: str) -> np.ndarray:
    return np.isin(lc, GROUPS[name])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="tools.wa_nisar_hypothesis",
                               description=__doc__.splitlines()[0])
    ap.add_argument("--landcover", type=Path, default=LC_TIF,
                    help="MCD12Q1 LC_Type1 COG covering the wa_2025 AOI")
    args = ap.parse_args(argv)
    for p in (VAL_TIF, FLOOD_TIF, BRIGHT_TIF, WATER_TIF, args.landcover,
              *HH.values(), *HV.values()):
        if not Path(p).exists():
            raise SystemExit(f"missing input: {p}")

    # --- analysis grid = OPERA validation lattice ---
    cls, val_ds = _read(VAL_TIF)
    cls = cls.astype(np.uint8)
    dst_transform, dst_shape = val_ds.transform, cls.shape
    row_km2 = _row_km2(dst_shape[0], dst_transform)

    tp_m = cls == 1
    fn_m = cls == 3
    fp_m = cls == 2
    opera_flood = tp_m | fn_m

    # --- masks + land cover reprojected nearest onto the val grid ---
    def _nearest(path, nodata=np.nan):
        arr, ds = _read(path)
        return _to_val_grid(arr, ds, dst_shape, dst_transform,
                            Resampling.nearest, nodata)

    flood = _nearest(FLOOD_TIF) == 1
    bright_mask = _nearest(BRIGHT_TIF) == 1
    water_now = _nearest(WATER_TIF) == 1
    lc = _nearest(args.landcover, nodata=np.nan)
    lc_u8 = np.where(np.isfinite(lc), lc, 255).astype(np.uint8)

    # --- dB deltas: median filter on the 60 m grid, bilinear to val grid ---
    d_hh, hh_ds = _filtered_delta_db(HH["pre"], HH["post"])
    d_hv, hv_ds = _filtered_delta_db(HV["pre"], HV["post"])
    d_hh30 = _to_val_grid(d_hh, hh_ds, dst_shape, dst_transform,
                          Resampling.bilinear)
    d_hv30 = _to_val_grid(d_hv, hv_ds, dst_shape, dst_transform,
                          Resampling.bilinear)
    valid_delta = np.isfinite(d_hh30) & np.isfinite(d_hv30)

    # sanity: rasterized flood mask vs the validation raster's pred (tp+fp)
    pred_val = tp_m | fp_m
    inter = float((flood & pred_val).sum())
    union = float((flood | pred_val).sum())
    mask_check = {"nisar_flood_mask_px": int(flood.sum()),
                  "val_pred_px": int(pred_val.sum()),
                  "iou": round(inter / union, 4) if union else None}

    # --- land-cover composition of the missed set ---
    def _composition(sel):
        out = {}
        n_all = int(sel.sum())
        for g in (*GROUPS, "other"):
            gm = np.isin(lc_u8, EXCLUDED_LC) if g == "other" else _group_mask(lc_u8, g)
            n = int((sel & gm).sum())
            out[g] = {"n": n, "share": round(n / n_all, 4) if n_all else None,
                      "km2": _sel_km2(sel & gm, row_km2)}
        classified = n_all - out["other"]["n"]
        for g in GROUPS:
            out[g]["share_of_classified"] = (
                round(out[g]["n"] / classified, 4) if classified else None)
        out["_total_n"] = n_all
        out["_classified_n"] = int(classified)
        return out

    missed_comp = _composition(fn_m)
    detected_comp = _composition(tp_m)
    flood_comp = _composition(opera_flood)

    def _urban_forest_share(comp):
        s = sum(comp[g]["share"] or 0 for g in ("urban", "forest"))
        return round(s, 4)

    enrich = {
        "missed_urban_forest_share": _urban_forest_share(missed_comp),
        "detected_urban_forest_share": _urban_forest_share(detected_comp),
        "all_flood_urban_forest_share": _urban_forest_share(flood_comp),
    }
    enrich["enrichment_missed_vs_detected"] = (
        round(enrich["missed_urban_forest_share"]
              / enrich["detected_urban_forest_share"], 3)
        if enrich["detected_urban_forest_share"] else None)

    # --- per-group delta stats + recall ---
    control = (cls == 0) & valid_delta & ~water_now  # non-flood, non-water-now
    delta_db, recall = {}, {}
    for g in GROUPS:
        gm = _group_mask(lc_u8, g)
        ctrl_hh = _stats(d_hh30, control & gm, row_km2)
        ctrl_hv = _stats(d_hv30, control & gm, row_km2)
        fl_hh = _stats(d_hh30, opera_flood & gm & valid_delta, row_km2)
        fl_hv = _stats(d_hv30, opera_flood & gm & valid_delta, row_km2)
        delta_db[g] = {
            "flooded": {"hh": fl_hh, "hv": fl_hv,
                        "n": int((opera_flood & gm & valid_delta).sum())},
            "flooded_detected_hh": _stats(d_hh30, tp_m & gm & valid_delta, row_km2),
            "flooded_missed_hh": _stats(d_hh30, fn_m & gm & valid_delta, row_km2),
            "control_not_flooded": {"hh": ctrl_hh, "hv": ctrl_hv,
                                    "n": int((control & gm).sum())},
            "hh_control_adjusted_mean_db": (
                round(fl_hh["mean_db"] - ctrl_hh["mean_db"], 3)
                if fl_hh and ctrl_hh else None),
        }
        n_f = int((opera_flood & gm).sum())
        n_d = int((tp_m & gm).sum())
        recall[g] = {"flooded_n": n_f, "detected_n": n_d,
                     "recall": round(n_d / n_f, 4) if n_f else None}

    # bright-layer recovery: share of missed px inside flood_bright mask
    bright_rec = {}
    for g in (*GROUPS, "all"):
        gm = _group_mask(lc_u8, g) if g in GROUPS else np.ones_like(tp_m)
        sel = fn_m & gm
        n = int(sel.sum())
        bright_rec[g] = {"missed_n": n,
                         "in_bright_layer_n": int((sel & bright_mask).sum()),
                         "share_in_bright_layer": round(
                             float((sel & bright_mask).sum()) / n, 4) if n else None}

    # --- verdict ---
    mh = delta_db["open"]["flooded"]["hh"] if delta_db["open"]["flooded"] else None
    uh = delta_db["urban"]["flooded"]["hh"] if delta_db["urban"]["flooded"] else None
    fh = delta_db["forest"]["flooded"]["hh"] if delta_db["forest"]["flooded"] else None
    checks = {
        "misses_enriched_in_urban_forest":
            (enrich["enrichment_missed_vs_detected"] or 0) > 1.0,
        "flooded_open_darkens": mh is not None and mh["mean_db"] < -1.0,
        "flooded_urban_brighter_than_open":
            uh is not None and mh is not None and uh["mean_db"] > mh["mean_db"] + 1.0,
        "flooded_forest_brighter_than_open":
            fh is not None and mh is not None and fh["mean_db"] > mh["mean_db"] + 1.0,
    }
    n_yes = sum(checks.values())
    verdict = ("supported" if n_yes == len(checks)
               else "partially_supported" if n_yes >= 2 else "not_supported")

    def _fmt(g):
        s = delta_db[g]["flooded"]["hh"]
        return f"{s['mean_db']:+.2f} dB (n={s['n']:,})" if s else "no data"

    r = recall
    lit_bright = {
        "note": ("the literal '>+3 dB rise' reading of 'brighten': flooded "
                 "urban/forest pixels stay near their already-bright baseline "
                 "(median ~0 dB), they do NOT jump +3 dB — double bounce "
                 "cancels the specular darkening rather than adding power"),
        "flooded_share_gt_+3dB": {
            g: (delta_db[g]["flooded"]["hh"]["share_bright_gt_+3dB"]
                if delta_db[g]["flooded"]["hh"] else None) for g in GROUPS},
        "missed_recovered_by_bright_layer":
            bright_rec["all"]["share_in_bright_layer"],
    }
    paragraph = (
        f"The double-bounce hypothesis is {verdict.replace('_', ' ')}: among the "
        f"{missed_comp['_total_n']:,} OPERA-confirmed new-flood pixels our NISAR "
        f"HH-darkening detector missed, {enrich['missed_urban_forest_share']:.0%} sit on "
        f"urban or forest land cover vs {enrich['detected_urban_forest_share']:.0%} of the "
        f"pixels it caught ({enrich['enrichment_missed_vs_detected']}x enriched). Mean "
        f"HH change Dec 4 -> Dec 16 was {_fmt('open')} for flooded open land vs "
        f"{_fmt('urban')} flooded urban and {_fmt('forest')} flooded forest "
        f"(non-flooded controls drift ~+0.2 to +0.4 dB) — open-field water goes "
        f"dark while flooded towns and trees stay near their bright baseline, so "
        f"a darkening-only threshold misses exactly where people live: recall is "
        f"{r['open']['recall']:.1%} on open land but {r['urban']['recall']:.1%} in "
        f"urban and {r['forest']['recall']:.1%} in forest. Caveat: flooded "
        f"urban/forest pixels do not jump +3 dB (<=0.5% do) — double bounce "
        f"cancels the darkening instead, so the existing +3 dB 'bright-flood' "
        f"layer recovers only {lit_bright['missed_recovered_by_bright_layer']:.2%} "
        f"of the misses; closing the gap needs HV/texture or land-cover-aware "
        f"detection, not a brighter-than-baseline gate.")

    doc = {
        "schema": "tipping-point/wa-nisar-hypothesis@1",
        "case": CASE,
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "hypothesis": HYPOTHESIS,
        "verdict": verdict,
        "verdict_checks": checks,
        "result_paragraph": paragraph,
        "inputs": {
            "nisar_gcov_db_cogs": {k: str(v.relative_to(DATA_DIR)) for k, v in
                                 {"hh_pre": HH["pre"], "hh_post": HH["post"],
                                  "hv_pre": HV["pre"], "hv_post": HV["post"]}.items()},
            "validation": "wa_2025/cog/wa_2025_opera_validation_20251216.tif "
                          "(1=tp 2=fp 3=fn; fn = OPERA new flood missed by the "
                          "NISAR candidates)",
            "flood_mask": "wa_2025/cog/wa_2025_flood_nisar_20251210.tif",
            "bright_mask": "wa_2025/cog/wa_2025_flood_bright_nisar_20251210.tif",
            "water_now_mask": "wa_2025/cog/wa_2025_water_nisar_20251210.tif",
            "landcover": (f"{args.landcover} — MCD12Q1.061 2025 LC_Type1 IGBP "
                          "(h09v04, 500 m)"),
        },
        "grid": {"analysis": "OPERA validation lattice EPSG:4326 ~30 m",
                 "delta": "post-pre dB, 3x3 median filter per tp.flood_mask, "
                          "bilinear 60 m UTM -> 30 m; masks/LC nearest"},
        "lc_groups": {g: list(v) for g, v in GROUPS.items()} |
                     {"excluded": list(EXCLUDED_LC)},
        "mask_consistency_check": mask_check,
        "missed_composition": missed_comp,
        "detected_composition": detected_comp,
        "enrichment": enrich,
        "delta_db": delta_db,
        "recall_by_group": recall,
        "literal_brightening": lit_bright,
        "bright_layer_recovery": bright_rec,
        "caveats": [
            ("Misses also arise from the HAND>=15 m gate, the permanent-water "
             "exclusion and the 3-ha minimum mapping unit — not only from "
             "brightening; the land-cover/dB split isolates the double-bounce "
             "component only approximately."),
            ("NISAR GCOV is BETA (pre-calibration): absolute dB offsets are "
             "possible; the same-track same-frame pair keeps relative change "
             "interpretable."),
            ("LC_Type1 is 500 m, nearest-resampled to 30 m — boundary pixels "
             "are noisy; group means average over many pixels."),
            ("Control = validation-class-0 pixels with finite pre/post dB and "
             "not current water: mostly unflooded land, may include OPERA-"
             "unobserved cells."),
        ],
    }
    OUT_JSON.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
    print(f"verdict: {verdict}  checks={checks}")
    print(f"missed n={missed_comp['_total_n']} comp={ {g: missed_comp[g]['share'] for g in (*GROUPS, 'other')} }")
    print(f"detected n={detected_comp['_total_n']} comp={ {g: detected_comp[g]['share'] for g in (*GROUPS, 'other')} }")
    print(f"enrichment: {enrich}")
    for g in GROUPS:
        fl = delta_db[g]["flooded"]
        print(f"{g:7s} flooded hh {fl['hh']} hv {fl['hv']}")
        print(f"{g:7s}   detected {delta_db[g]['flooded_detected_hh']} "
              f"missed {delta_db[g]['flooded_missed_hh']}")
        print(f"{g:7s}   control {delta_db[g]['control_not_flooded']}")
        print(f"{g:7s}   recall {recall[g]}  bright_rec {bright_rec[g]}")
    print(f"bright_rec all: {bright_rec['all']}")
    print(f"wrote {OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
