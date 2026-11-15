"""L59 option 1: NISAR calibrates the flood TPI weights, out-of-sample only (nothing served).

TPI_flood(w) = R^(wR/3) · S^(wS/3) · T^(wT/3), wR + wS + wT = 3 on a 0.25 grid (91 triples;
v2 = 1,1,1 = live.tpi's uniform cube root). Class cuts fixed (High+ > 0.4, Critical > 0.6).
Frames: observed replay, max over the days inside each truth's acquisition window.

Cases (truth -> domain -> window):
  wa_2025   NISAR GCOV flood candidates (12 m pixels, area-weighted)  NISAR raster extent  Dec 4-16 (NISAR pair)
  rs_2024   Copernicus EMS flood extent (90 m cells)                  5 EMS AOIs           Apr 29 - May 5
  uru_poly  teammate NISAR polygons (7, 1.8 km², cells touched)       corridor             Sep 30 - Oct 12 (pair)
  uru_dark  our NISAR HH darkening (change map, ungated)              corridor             Oct 24 - Nov 1 (pair 10-24 -> 11-05; replay ends 11-01)

Fit = pick the weights with the best Critical score on the FIT case only; score = hit % - area %
(a Peirce-style skill: share of truth flagged minus share of the domain flagged, so it cannot be
gamed by flagging everything or almost nothing). Then report the held-out cases at those weights
next to v2. Fits: WA -> {Uru, RS}; Uru polygons -> {WA, RS} (tiny mask: overfit risk);
Uru darkening -> {WA, RS}.
"also v3 R": scored when lead-analysis's components.npz (r_v3) exists for the case; else listed.

    PYTHONPATH=.:pipeline python tools/nisar_weight_calibration.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
for p in (str(REPO), str(REPO / "pipeline"), str(REPO / "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

STEP = 0.25
CUTS = {"alto_plus": 0.4, "critico": 0.6}
CORRIDOR = (-57.25, -29.85, -56.45, -29.05)


def weight_grid(step: float = STEP) -> list[tuple[float, float, float]]:
    n = round(3 / step)
    return [(i * step, j * step, (n - i - j) * step) for i in range(n + 1) for j in range(n + 1 - i)]


def _to_grid(path: Path, band: int, transform, shape) -> np.ndarray:
    import rasterio
    from rasterio.warp import Resampling, reproject
    a = np.full(shape, np.nan, np.float32)
    with rasterio.open(path) as src:
        reproject(rasterio.band(src, band), a, dst_transform=transform, dst_crs="EPSG:4326",
                  resampling=Resampling.bilinear)
    return a


def _series_case(case: str, end: str, lo: str, hi: str, domain_fn, refs_fn) -> dict:
    """R, S per day (bilinear onto the 90 m TPI grid, as tools/t_flood_eval.py), T, published TPI."""
    import rasterio
    d = REPO / "data" / case / "cog"
    with rasterio.open(d / f"{case}_tpi_flood_{end}.tif") as src:
        tr, shape, dates = src.transform, (src.height, src.width), list(src.descriptions)
        days = [x for x in dates if lo <= x <= hi]
        pub = [src.read(dates.index(x) + 1).astype(np.float32) for x in days]
    with rasterio.open(d / f"{case}_r_series_{end}.tif") as src:
        rdates = list(src.descriptions)
    t = rasterio.open(d / f"{case}_t_flood_{end}.tif").read(1).astype(np.float32)
    dom = domain_fn(tr, shape)
    keep = dom & np.isfinite(t)
    r = np.stack([_to_grid(d / f"{case}_r_series_{end}.tif", rdates.index(x) + 1, tr, shape)[keep] for x in days])
    s = np.stack([_to_grid(d / f"{case}_s_series_{end}.tif", rdates.index(x) + 1, tr, shape)[keep] for x in days])
    water = np.stack([~np.isfinite(p[keep]) for p in pub]).all(0)
    pub_max = np.fmax.reduce(np.stack([p[keep] for p in pub]), axis=0)
    return {"r": r, "s": s, "t": t[keep], "water": water, "pub_v2": pub_max, "days": days,
            "keep": keep, "ref": refs_fn(tr, shape, keep), "transform": tr, "shape": shape}


def _remap_refs(idx_full: np.ndarray, keep: np.ndarray) -> np.ndarray:
    """Flat full-grid indices -> indices into the kept (domain) vector; refs outside are dropped."""
    pos = np.full(keep.size, -1, np.int64)
    pos[np.flatnonzero(keep.ravel())] = np.arange(int(keep.sum()))
    m = pos[idx_full]
    return m[m >= 0]


def load_cases() -> dict:
    import lift_vs_terrain as lvt
    import rasterio
    import warned_before as wb
    from rasterio.warp import Resampling, reproject
    out = {}
    wa = REPO / "data/wa_2025"
    nis = wa / "cog/wa_2025_flood_nisar_20251210.tif"
    with rasterio.open(nis) as s:
        nb = s.bounds

    def wa_dom(tr, shape):
        rr, cc = np.indices(shape)
        lon, lat = tr * (cc + 0.5, rr + 0.5)
        return (lon >= nb.left) & (lon <= nb.right) & (lat >= nb.bottom) & (lat <= nb.top)

    def wa_refs(tr, shape, keep):
        g = rasterio.open(wa / "cog/wa_2025_t_flood_20251210.tif")
        idx, _ = wb.flood_pixels(nis, g)
        return _remap_refs(idx, keep)

    out["wa_2025"] = _series_case("wa_2025", "20251210", "20251204", "20251216", wa_dom, wa_refs)
    out["wa_2025"]["label"] = "WA 2025 · NISAR flood candidates (27.9 km², Dec 4→16)"

    rs = REPO / "data/rs_2024"

    def rs_dom(tr, shape):
        return lvt._rasterize(rs / "geojson/flood_ems_aoi.geojson", shape, tr)

    def rs_refs(tr, shape, keep):
        ems = lvt._rasterize(rs / "geojson/flood_ems.geojson", shape, tr) & keep
        return _remap_refs(np.flatnonzero(ems.ravel()), keep)

    out["rs_2024"] = _series_case("rs_2024", "20240502", "20240429", "20240505", rs_dom, rs_refs)
    out["rs_2024"]["label"] = "RS 2024 · Copernicus EMS flood (5 Taquari AOIs)"

    ud = REPO / "data/rs_uruguai_2026"
    for key, lo, hi, label in (("uru_poly", "20260930", "20261012", "Uruguaiana · teammate NISAR polygons (7, 1.8 km²)"),
                               ("uru_dark", "20261024", "20261101", "Uruguaiana · our NISAR HH darkening (change map)")):
        files = sorted(f for f in (ud / "cog").glob("rs_uruguai_2026_tpi_flood_obs_*.tif")
                       if lo <= f.stem.split("_")[-1] <= hi)
        bands = []
        for f in files:
            with rasterio.open(f) as src:
                bands.append({n: src.read(i + 1).astype(np.float32) for i, n in enumerate(src.descriptions)})
                tr, shape = src.transform, (src.height, src.width)
        rr, cc = np.indices(shape)
        lon, lat = tr * (cc + 0.5, rr + 0.5)
        corr = (lon >= CORRIDOR[0]) & (lon <= CORRIDOR[2]) & (lat >= CORRIDOR[1]) & (lat <= CORRIDOR[3])
        t = bands[0]["t"]
        keep = corr & np.isfinite(t)
        if key == "uru_poly":
            truth = lvt._rasterize(ud / "geojson/flood_confirmed.geojson", shape, tr, all_touched=True)
        else:
            truth = np.zeros(shape, np.float32)
            with rasterio.open(ud / "cog/rs_uruguai_2026_darkening_nisar_20261105.tif") as src:
                reproject(rasterio.band(src, 1), truth, src_nodata=None, dst_transform=tr, dst_crs="EPSG:4326",
                          resampling=Resampling.max)
            truth = truth > 0
        out[key] = {"r": np.stack([b["r"][keep] for b in bands]), "s": np.stack([b["s"][keep] for b in bands]),
                    "t": t[keep], "water": np.stack([~np.isfinite(b["tpi"][keep]) for b in bands]).all(0),
                    "pub_v2": np.fmax.reduce(np.stack([b["tpi"][keep] for b in bands]), axis=0),
                    "days": [f.stem.split("_")[-1] for f in files], "keep": keep,
                    "ref": _remap_refs(np.flatnonzero((truth & keep).ravel()), keep), "label": label}
    return out


def tpi_w(c: dict, w, r_key: str = "r") -> np.ndarray:
    wr, ws, wt = (x / 3 for x in w)
    r, s, t = np.clip(c[r_key], 0, None), np.clip(c["s"], 0, None), np.clip(c["t"], 0, None)
    x = np.fmax.reduce(np.power(r, wr) * np.power(s, ws), axis=0) * np.power(t, wt)
    x[c["water"]] = np.nan
    return x.astype(np.float64)


def score(c: dict, w, r_key: str = "r") -> dict:
    """High+/Critical hit, area, lift and M1 ratio on the case domain (vectors: domain cells only)."""
    import m4_cut_calibration as m4
    x = tpi_w(c, w, r_key)
    sc = m4.Scorer(x[None, :], c["t"].astype(np.float64)[None, :], c["ref"])
    out = {"w": list(w), "n_ref": sc.n_ref}
    for name, cut in CUTS.items():
        a = sc.at(cut)
        out[name] = {k: a.get(k) for k in ("area_pct", "hit_pct", "lift", "ratio")}
        out[name]["skill"] = round(a["hit_pct"] - a["area_pct"], 2) if a.get("hit_pct") is not None else None
    return out


PAIRS = [("wa_2025", ("uru_poly", "uru_dark", "rs_2024")), ("uru_poly", ("wa_2025", "rs_2024")),
         ("uru_dark", ("wa_2025", "rs_2024"))]


def fit_block(cases: dict, grid, v2: dict, pairs) -> list:
    table = {k: [score(c, w) for w in grid] for k, c in cases.items()}
    out = []
    for fit, held in pairs:
        rows = sorted(table[fit], key=lambda r: -(r["critico"]["skill"] if r["critico"]["skill"] is not None else -1e9))
        best = rows[0]
        tied = [r["w"] for r in rows if r["critico"]["skill"] == best["critico"]["skill"]]
        entry = {"fit_on": fit, "best_w": best["w"], "tied_best_w": tied[:10], "n_tied": len(tied),
                 "fit_case": {"fitted": best, "v2": v2[fit]},
                 "top5": [(r["w"], r["critico"]["skill"]) for r in rows[:5]], "held_out": {}}
        for h in held:
            f = score(cases[h], best["w"])
            entry["held_out"][h] = {"fitted": f, "v2": v2[h],
                                    "beats_v2_critical_skill": f["critico"]["skill"] > v2[h]["critico"]["skill"]}
        out.append(entry)
    return out


def v3r_cases(cases: dict, cache: Path) -> dict:
    """Same domains/truths, R (and the v2-R comparator) from components.npz on its obs days."""
    out = {}
    for k, c in cases.items():
        case = {"uru_poly": "rs_uruguai_2026", "uru_dark": "rs_uruguai_2026"}.get(k, k)
        f = cache / case / "components.npz"
        if not f.exists():
            continue
        co, fr = np.load(f), np.load(cache / case / "frames.npz")
        dates, kind = fr["frame_dates"], fr["frame_kind"]
        sel = (kind == "obs") & (dates >= c["days"][0]) & (dates <= c["days"][-1])
        if not sel.any():
            continue
        keep = c["keep"]
        rv3 = co["r_v3"][sel][:, keep].astype(np.float32)
        out[k] = {**c, "r": rv3, "r_v2": co["r_v2"][sel][:, keep].astype(np.float32),
                  "s": co["s_v2"][sel][:, keep].astype(np.float32),
                  "water": np.isnan(fr["tpi_v2_flood"][sel][:, keep]).all(0), "days": list(dates[sel])}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=REPO / "ml/models/nisar_weight_calibration.json")
    ap.add_argument("--v3-cache", type=Path, default=Path.home() / ".cache/lead-analysis/v3")
    a = ap.parse_args(argv)
    cases = load_cases()
    grid = weight_grid()
    res = {"rule": "fit = argmax Critical (hit % - area %) on the fit case; class cuts fixed 0.4/0.6",
           "grid": f"wR+wS+wT=3, step {STEP} ({len(grid)} triples); exponent = w/3", "cases": {}, "fits": [],
           "v3_r": "not scored: lead-analysis components.npz (r_v3) not available"}
    for k, c in cases.items():
        x = tpi_w(c, (1, 1, 1))
        both = np.isfinite(x) & np.isfinite(c["pub_v2"])
        res["cases"][k] = {"label": c["label"], "days": c["days"], "domain_cells": int(c["t"].size),
                           "truth_refs": int(c["ref"].size),
                           "v2_rebuild_max_abs_err": round(float(np.abs(x[both] - c["pub_v2"][both]).max()), 4),
                           "v2_rebuild_mean_abs_err": round(float(np.abs(x[both] - c["pub_v2"][both]).mean()), 4),
                           "v2": score(c, (1, 1, 1))}
    res["fits"] = fit_block(cases, grid, {k: v["v2"] for k, v in res["cases"].items()}, PAIRS)
    v3 = v3r_cases(cases, a.v3_cache)
    if v3:
        base = {k: score(c, (1, 1, 1), "r_v2") for k, c in v3.items()}
        res["v3_r"] = {"note": "R swapped for lead-analysis r_v3 (M2 liquid + M3 4xP99); S, T = v2; frames = the obs days "
                               "in frames.npz inside each truth window; comparator = v2 R on the SAME days",
                       "days": {k: c["days"] for k, c in v3.items()},
                       "v2_same_days": base,
                       "v3r_uniform": {k: score(c, (1, 1, 1), "r") for k, c in v3.items()},
                       "fits": fit_block(v3, grid, base, [(f, tuple(h for h in hs if h in v3)) for f, hs in PAIRS
                                                          if f in v3])}
    a.out.write_text(json.dumps(res, indent=1))
    for k, v in res["cases"].items():
        print(k, "rebuild err max/mean", v["v2_rebuild_max_abs_err"], v["v2_rebuild_mean_abs_err"], "refs", v["truth_refs"],
              "v2 crit", v["v2"]["critico"], "high", v["v2"]["alto_plus"])
    blocks = [("v2 R", res["fits"])] + ([("v3 R", res["v3_r"]["fits"])] if isinstance(res["v3_r"], dict) else [])
    if isinstance(res["v3_r"], dict):
        for k in res["v3_r"]["v2_same_days"]:
            print("v3R days", k, res["v3_r"]["days"][k], "v2R crit", res["v3_r"]["v2_same_days"][k]["critico"],
                  "v3R(1,1,1) crit", res["v3_r"]["v3r_uniform"][k]["critico"])
    for tag, fits in blocks:
        for e in fits:
            print(f"\n{tag} FIT {e['fit_on']} best {e['best_w']} (tied {e['n_tied']}) top5 {e['top5']}\n"
                  f"   fit crit {e['fit_case']['fitted']['critico']}\n   v2  crit {e['fit_case']['v2']['critico']}")
            for h, d in e["held_out"].items():
                print(f"   held {h}: fitted crit {d['fitted']['critico']} high {d['fitted']['alto_plus']}\n"
                      f"          v2     crit {d['v2']['critico']} high {d['v2']['alto_plus']}"
                      f"  beats={d['beats_v2_critical_skill']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
