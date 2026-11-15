"""Calibrate the TPI_flood weights (wR, wS, wT) against NISAR flood maps. Author: Jhonatan (team).

Why: the TPI is R^wR · S^wS · T^wT with uniform weights (config.yaml tpi_weights) that were
never fitted. NISAR new-water maps are an independent, cloud-free observation of where the
ground actually flooded, so they are the natural reference to choose the weights. This puts
NISAR inside the index as its calibration target ("NISAR trains the index"), without making
the live index depend on a NISAR pass (12-day revisit, partial swath).

What it does
  1. Builds R, S (per day) and T on each case's scoring grid:
       wa_2025          R, S series (1 km, bilinear to the 90 m T grid), T = t_flood,
                        event days Dec 8-12, reference = NISAR flood candidates
                        (wa_2025_flood_nisar_20251210.tif), domain = NISAR raster extent.
       rs_uruguai_2026  per-day files rs_uruguai_2026_tpi_flood_obs_<date>.tif with bands
                        tpi, r, s, t (0.01 deg), days Sep 28 - Oct 12, reference = the NISAR
                        new-water polygons (flood_confirmed.geojson, all touched cells),
                        domain = Uruguaiana-Itaqui corridor (same box as lift_vs_terrain.py).
       rs_2024 (check)  R, S series (1 km -> 90 m), T = t_flood, days Apr 29 - May 5,
                        reference = Copernicus EMS EMSR720 flood extent (NOT NISAR: this case
                        predates NISAR), domain = EMS AOIs. Used only as an out-of-sample test.
  2. For every weight triple on a simplex grid (each weight >= --wmin, step --step):
       TPI_w = max over event days of R^wR · S^wS · T^wT   (computed in log space)
  3. Scores each triple per case with threshold-free metrics on the domain cells:
       AP   average precision (area under precision-recall), label = cell holds reference
       AUC  ROC area
     plus, at the served class cuts, lift and lift_vs_terrain (tools/lift_vs_terrain.py).
  4. Chooses weights by leave-one-case-out between the two NISAR cases (fit on one, test on
     the other) and by the pooled objective (geometric mean over NISAR cases of AP / AP of the
     uniform weights). RS 2024 vs EMS is reported for the chosen triples as an independent test.

It never edits config.yaml. It writes ml/models/tpi_weights_nisar.json and prints a summary.

Caveats printed with the result
  - Fixed class cuts (0.2/0.4/0.6) mean new weights also change class areas: compare
    Critical km2 before switching.
  - Uruguai has few reference cells (7 small polygons); its AP is noisy. Weights that win
    only there should not be served.
  - NISAR candidates are themselves candidates (provisional GCOV, thresholded), not truth.

    PYTHONPATH=.:pipeline python tools/calibrate_weights_nisar.py [--step 0.05] [--wmin 0.1]
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
for p in (str(REPO), str(REPO / "pipeline"), str(REPO / "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

CUTS = {"alto_plus": 0.4, "critico": 0.6}
UNIFORM = (1 / 3, 1 / 3, 1 / 3)


# ---------------------------------------------------------------- inputs per case
def _upsample_series(series_path: Path, grid_ds, bands: list[str]) -> list[np.ndarray]:
    """1 km R or S bands -> T grid (bilinear), as the replay combines them."""
    import rasterio
    from rasterio.warp import Resampling, reproject
    out = []
    with rasterio.open(series_path) as src:
        names = list(src.descriptions)
        for b in bands:
            a = src.read(names.index(b) + 1).astype(np.float32)
            a[~np.isfinite(a)] = np.nan
            dst = np.full((grid_ds.height, grid_ds.width), np.nan, np.float32)
            reproject(a, dst, src_transform=src.transform, src_crs=src.crs,
                      dst_transform=grid_ds.transform, dst_crs=grid_ds.crs,
                      resampling=Resampling.bilinear, src_nodata=np.nan, dst_nodata=np.nan)
            out.append(np.clip(dst, 0, 1))
    return out


def _days(series_path: Path, lo: str, hi: str) -> list[str]:
    import rasterio
    with rasterio.open(series_path) as src:
        return [d for d in src.descriptions if lo <= d <= hi]


def case_wa(data: Path) -> dict:
    import rasterio
    import warned_before as wb
    c = data / "wa_2025" / "cog"
    tpath = c / "wa_2025_t_flood_20251210.tif"
    grid = rasterio.open(tpath)
    t = grid.read(1).astype(np.float32)
    t[~np.isfinite(t)] = np.nan
    days = _days(c / "wa_2025_r_series_20251210.tif", "20251208", "20251212")
    R = _upsample_series(c / "wa_2025_r_series_20251210.tif", grid, days)
    S = _upsample_series(c / "wa_2025_s_series_20251210.tif", grid, days)
    # permanent water and nodata follow the published TPI (NaN there)
    with rasterio.open(c / "wa_2025_tpi_flood_20251210.tif") as s:
        pub = s.read(list(s.descriptions).index(days[0]) + 1)
    nis = c / "wa_2025_flood_nisar_20251210.tif"
    idx, _ = wb.flood_pixels(nis, grid)
    with rasterio.open(nis) as s:
        b = s.bounds
    rr, cc = np.indices(t.shape)
    lon, lat = grid.transform * (cc + 0.5, rr + 0.5)
    domain = (lon >= b.left) & (lon <= b.right) & (lat >= b.bottom) & (lat <= b.top) & np.isfinite(pub)
    return {"name": "wa_2025", "reference": "NISAR flood candidates", "nisar": True,
            "days": days, "R": R, "S": S, "T": t, "domain": domain, "ref_idx": np.unique(idx)}


def case_uru(data: Path) -> dict:
    import rasterio
    from lift_vs_terrain import _rasterize
    c = data / "rs_uruguai_2026" / "cog"
    files = sorted(p for p in c.glob("rs_uruguai_2026_tpi_flood_obs_*.tif")
                   if "20260928" <= p.stem.split("_")[-1] <= "20261012")
    R, S, days, t, tr, tpi0 = [], [], [], None, None, None
    for f in files:
        with rasterio.open(f) as s:
            d = {n: s.read(i + 1).astype(np.float32) for i, n in enumerate(s.descriptions)}
            tr = s.transform
        R.append(np.clip(d["r"], 0, 1))
        S.append(np.clip(d["s"], 0, 1))
        days.append(f.stem.split("_")[-1])
        t = d["t"] if t is None else t
        tpi0 = d["tpi"] if tpi0 is None else tpi0
    poly = _rasterize(data / "rs_uruguai_2026" / "geojson" / "flood_confirmed.geojson", t.shape, tr,
                      all_touched=True)
    rr, cc = np.indices(t.shape)
    lon, lat = tr * (cc + 0.5, rr + 0.5)
    domain = ((lon >= -57.25) & (lon <= -56.45) & (lat >= -29.85) & (lat <= -29.05)
              & np.isfinite(tpi0) & np.isfinite(t))
    return {"name": "rs_uruguai_2026", "reference": "NISAR new-water polygons", "nisar": True,
            "days": days, "R": R, "S": S, "T": t, "domain": domain,
            "ref_idx": np.flatnonzero(poly.ravel())}


def case_rs(data: Path) -> dict:
    import rasterio
    from lift_vs_terrain import _rasterize
    c = data / "rs_2024" / "cog"
    tpath = c / "rs_2024_t_flood_20240502.tif"
    grid = rasterio.open(tpath)
    t = grid.read(1).astype(np.float32)
    t[~np.isfinite(t)] = np.nan
    days = _days(c / "rs_2024_r_series_20240502.tif", "20240429", "20240505")
    R = _upsample_series(c / "rs_2024_r_series_20240502.tif", grid, days)
    S = _upsample_series(c / "rs_2024_s_series_20240502.tif", grid, days)
    with rasterio.open(c / "rs_2024_tpi_flood_20240502.tif") as s:
        pub = s.read(list(s.descriptions).index(days[0]) + 1)
    g = data / "rs_2024" / "geojson"
    aoi = _rasterize(g / "flood_ems_aoi.geojson", t.shape, grid.transform)
    ems = _rasterize(g / "flood_ems.geojson", t.shape, grid.transform) & aoi
    return {"name": "rs_2024", "reference": "Copernicus EMS EMSR720 (not NISAR)", "nisar": False,
            "days": days, "R": R, "S": S, "T": t, "domain": aoi & np.isfinite(pub),
            "ref_idx": np.flatnonzero(ems.ravel())}


# ---------------------------------------------------------------- scoring
def _prep(case: dict) -> dict:
    """Flatten to domain cells and take logs once (log 0 = -inf, so a zero factor stays zero)."""
    m = case["domain"].ravel() & np.isfinite(case["T"]).ravel()
    with np.errstate(divide="ignore"):
        logR = np.stack([np.log(r.ravel()[m]) for r in case["R"]])
        logS = np.stack([np.log(s.ravel()[m]) for s in case["S"]])
        logT = np.log(case["T"].ravel()[m])
    label = np.zeros(case["T"].size, bool)
    label[case["ref_idx"]] = True
    pos = np.flatnonzero(m)
    return {"m": m, "pos": pos, "logR": logR, "logS": logS, "logT": logT,
            "y": label.ravel()[m], "shape": case["T"].shape}


def tpi_weighted(p: dict, w: tuple[float, float, float]) -> np.ndarray:
    wr, ws, wt = w
    lr = np.nan_to_num(p["logR"], nan=-np.inf)
    ls = np.nan_to_num(p["logS"], nan=-np.inf)
    day = wr * lr + ws * ls                      # rain x soil, per day
    best = np.max(day, axis=0)                   # event max (T is static)
    with np.errstate(invalid="ignore"):
        out = np.exp(best + wt * p["logT"])
    return np.nan_to_num(out, nan=0.0)


def scores(p: dict, tpi: np.ndarray) -> dict:
    from sklearn.metrics import average_precision_score, roc_auc_score
    y = p["y"]
    n_pos = int(y.sum())
    if n_pos == 0 or n_pos == y.size:
        return {"ap": None, "auc": None, "n_pos": n_pos, "n": int(y.size)}
    return {"ap": float(average_precision_score(y, tpi)), "auc": float(roc_auc_score(y, tpi)),
            "base_rate": n_pos / y.size, "n_pos": n_pos, "n": int(y.size)}


def class_metrics(case: dict, p: dict, tpi: np.ndarray) -> dict:
    from lift_vs_terrain import lift_vs_terrain
    full = np.full(int(np.prod(p["shape"])), np.nan)
    full[p["pos"]] = tpi
    full = full.reshape(p["shape"])
    out = {}
    for k, cut in CUTS.items():
        r = lift_vs_terrain(full, case["T"], case["ref_idx"], case["domain"], cut)
        out[k] = {x: r.get(x) for x in ("area_share_pct", "hit_pct", "lift", "lift_terrain", "ratio")}
    return out


def simplex(step: float, wmin: float):
    n = round(1 / step)
    for i, j in itertools.product(range(n + 1), repeat=2):
        k = n - i - j
        if k < 0:
            continue
        w = (i * step, j * step, k * step)
        if min(w) >= wmin - 1e-9:
            yield tuple(round(x, 4) for x in w)


# ---------------------------------------------------------------- main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", type=float, default=0.05)
    ap.add_argument("--wmin", type=float, default=0.1, help="no factor may be switched off")
    ap.add_argument("--no-rs", action="store_true", help="skip the RS 2024 EMS out-of-sample check")
    ap.add_argument("--out", type=Path, default=REPO / "ml/models/tpi_weights_nisar.json")
    a = ap.parse_args(argv)
    data = REPO / "data"

    cases = [case_wa(data), case_uru(data)] + ([] if a.no_rs else [case_rs(data)])
    preps = {c["name"]: _prep(c) for c in cases}
    nisar = [c["name"] for c in cases if c["nisar"]]

    grid = list(simplex(a.step, a.wmin))
    if UNIFORM not in grid:
        grid.append(UNIFORM)
    table = []
    for w in grid:
        row = {"w": w}
        for c in cases:
            row[c["name"]] = scores(preps[c["name"]], tpi_weighted(preps[c["name"]], w))
        table.append(row)

    base = next(r for r in table if r["w"] == UNIFORM)
    terrain_only = {}
    for c in cases:
        p = preps[c["name"]]
        terrain_only[c["name"]] = scores(p, np.nan_to_num(np.exp(p["logT"]), nan=0.0))

    def rel(row, name):
        b, x = base[name]["ap"], row[name]["ap"]
        return (x / b) if (b and x) else None

    for r in table:
        rels = [rel(r, n) for n in nisar]
        r["pooled"] = float(np.exp(np.mean(np.log(rels)))) if all(rels) else None

    best_pooled = max((r for r in table if r["pooled"]), key=lambda r: r["pooled"])
    loco = {}
    for fit in nisar:
        test = [n for n in nisar if n != fit][0]
        best = max((r for r in table if r[fit]["ap"]), key=lambda r: r[fit]["ap"])
        loco[f"fit_{fit}"] = {"w": best["w"], "fit_ap": best[fit]["ap"],
                              "test_case": test, "test_ap": best[test]["ap"],
                              "test_ap_uniform": base[test]["ap"],
                              "test_gain": rel(best, test)}

    chosen = {"uniform": UNIFORM, "best_pooled": best_pooled["w"],
              **{k: v["w"] for k, v in loco.items()}}
    detail = {}
    for label, w in chosen.items():
        detail[label] = {"w": w}
        for c in cases:
            p = preps[c["name"]]
            tpi = tpi_weighted(p, w)
            detail[label][c["name"]] = {**scores(p, tpi), **class_metrics(c, p, tpi)}

    result = {
        "what": "TPI_flood weights (wR, wS, wT) calibrated against NISAR flood maps",
        "grid": {"step": a.step, "wmin": a.wmin, "n": len(grid)},
        "cases": {c["name"]: {"reference": c["reference"], "days": c["days"],
                              "n_domain_cells": int(preps[c["name"]]["y"].size),
                              "n_reference_cells": int(preps[c["name"]]["y"].sum())} for c in cases},
        "terrain_only": terrain_only,
        "leave_one_case_out": loco,
        "chosen": detail,
        "recommendation": None,
        "caveats": [
            "Class cuts are fixed, so new weights change class areas; compare Critical km2 before serving.",
            "rs_uruguai_2026 has few reference cells; its AP is noisy.",
            "NISAR flood maps are candidates (provisional GCOV, thresholded), not ground truth.",
            "rs_2024 is scored against Copernicus EMS, not NISAR (event predates NISAR).",
            "Replay R and S here are the published 1 km series upsampled bilinearly; replay v2 builds R on 90 m directly.",
        ],
    }
    # Serve a change only if it beats uniform on BOTH NISAR cases out of sample and does not hurt RS-EMS
    bp = detail["best_pooled"]
    ok_nisar = all(bp[n]["ap"] and base[n]["ap"] and bp[n]["ap"] > base[n]["ap"] for n in nisar)
    ok_loco = all((v["test_gain"] or 0) > 1.0 for v in loco.values())
    ok_rs = ("rs_2024" not in bp) or (bp["rs_2024"]["ap"] or 0) >= 0.95 * (detail["uniform"]["rs_2024"]["ap"] or 0)
    result["recommendation"] = {
        "serve": bool(ok_nisar and ok_loco and ok_rs),
        "weights": bp["w"] if (ok_nisar and ok_loco and ok_rs) else UNIFORM,
        "checks": {"beats_uniform_on_both_nisar_cases": ok_nisar,
                   "wins_out_of_sample_both_ways": ok_loco,
                   "keeps_rs_ems_within_5pct": ok_rs},
    }

    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(result, indent=1, default=float))

    print(f"grid: {len(grid)} weight triples (step {a.step}, each >= {a.wmin})")
    for c in cases:
        n = c["name"]
        print(f"  {n:16s} ref cells {result['cases'][n]['n_reference_cells']:>7} / domain {result['cases'][n]['n_domain_cells']:>9}"
              f"  AP terrain-only {terrain_only[n]['ap']:.4f}")
    print("\n  weights (R,S,T)        " + "  ".join(f"{c['name']:>18s}" for c in cases))
    for label, d in detail.items():
        cells = []
        for c in cases:
            s = d[c["name"]]
            cells.append(f"AP {s['ap']:.4f} AUC {s['auc']:.3f}" if s["ap"] is not None else "n/a")
        print(f"  {label:12s} {str(d['w']):18s} " + "  ".join(f"{x:>18s}" for x in cells))
    print("\nleave-one-case-out:")
    for k, v in loco.items():
        print(f"  {k}: w={v['w']}  test {v['test_case']} AP {v['test_ap']:.4f} vs uniform {v['test_ap_uniform']:.4f}"
              f"  gain x{(v['test_gain'] or 0):.2f}")
    print("\nrecommendation:", json.dumps(result["recommendation"]))
    print(f"written {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
