"""M4 (external review, L59): class cuts calibrated on one case, evaluated on the other.

No leakage: a cut is chosen on the calibration case only, then applied unchanged to
the evaluation case. Pairs: slide RS 2024 <-> WA 2025, flood RS 2024 (EMS) <-> WA 2025
(NISAR), and flood cuts are also applied to the Uruguaiana corridor (evaluation only).

Calibration rule for Crítico (review §M4): the lowest cut on a 0.01 grid whose
flagged area is <= 2 % of the domain AND whose M1 ratio (TPI hit / same-area
terrain-only hit, tools/lift_vs_terrain.py) is > 1. The area-only rule (lowest cut
with <= 2 % area) is reported too, to tell the two constraints apart. If no cut on
the calibration case passes, that is the finding.

Frames: "event" = observed replay, max TPI over the event window;
        "gfs24" = the GFS run issued >= 24 h before the L37 onset (prediction_lift.run_frames).
--suffix reads candidate rasters with the same names plus a suffix (e.g. _v3) so the
TPI v3 candidate (M2/M3/M5/M6) is scored by the same split.

    PYTHONPATH=.:pipeline python tools/m4_cut_calibration.py [--suffix _v3] [--out ml/models/m4_cuts.json]
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

GRID = np.round(np.arange(0.30, 1.00, 0.01), 2)
OLD = {"alto_plus": 0.4, "critico": 0.6}
MAX_AREA_PCT = 2.0
WINDOWS = {"rs_2024": ("20240429", "20240505"), "wa_2025": ("20251208", "20251212")}


class Scorer:
    """Hit, area share, lift and M1 ratio for any TPI cut, from one sort each of TPI and T."""

    def __init__(self, tpi, t, ref_idx, domain=None):
        valid = np.isfinite(tpi) & np.isfinite(t)
        if domain is not None:
            valid &= domain
            ref_idx = np.asarray(ref_idx)[domain.ravel()[ref_idx]]
        self.n_ref = int(np.asarray(ref_idx).size)
        rc = np.bincount(np.asarray(ref_idx), minlength=tpi.size)[valid.ravel()].astype(np.float64)
        tv, tt = tpi.ravel()[valid.ravel()], t.ravel()[valid.ravel()]
        self.n = tv.size
        o = np.argsort(tv, kind="stable")
        self.tpi_asc = tv[o]
        self.rc_asc_cum = np.concatenate([[0.0], np.cumsum(rc[o])])
        o = np.argsort(-tt, kind="stable")
        self.t_desc = tt[o]
        self.rc_t_cum = np.concatenate([[0.0], np.cumsum(rc[o])])
        self.neg_t_desc = -self.t_desc

    def at(self, cut: float) -> dict:
        i = int(np.searchsorted(self.tpi_asc, cut, side="right"))
        k = self.n - i
        out = {"cut": float(cut), "area_pct": round(100 * k / self.n, 3), "flagged_cells": k}
        if self.n_ref == 0:
            return out | {"hit_pct": None, "lift": None, "ratio": None}
        hit = (self.rc_asc_cum[-1] - self.rc_asc_cum[i]) / self.n_ref
        out["hit_pct"] = round(float(100 * hit), 2)
        if k == 0:
            return out | {"lift": None, "terrain_hit_pct": None, "ratio": None}
        kth = self.t_desc[k - 1]
        a = int(np.searchsorted(self.neg_t_desc, -kth, side="left"))
        b = int(np.searchsorted(self.neg_t_desc, -kth, side="right"))
        thit = (self.rc_t_cum[a] + (k - a) / (b - a) * (self.rc_t_cum[b] - self.rc_t_cum[a])) / self.n_ref
        share = k / self.n
        return out | {"lift": round(float(hit / share), 2), "terrain_hit_pct": round(float(100 * thit), 2),
                      "ratio": round(float(hit / thit), 3) if thit > 0 else None}


def calibrate(s: Scorer) -> dict:
    rows = [s.at(c) for c in GRID]
    area_ok = [r for r in rows if r["area_pct"] <= MAX_AREA_PCT]
    both = [r for r in area_ok if r["ratio"] is not None and r["ratio"] > 1 and r["flagged_cells"] > 0]
    return {"critico_area_and_ratio": both[0]["cut"] if both else None,
            "critico_area_only": area_ok[0]["cut"] if area_ok else None,
            "reason": None if both else "no cut on the 0.30-0.99 grid has <= 2 % area AND M1 ratio > 1",
            "curve": rows}


def _event_max(path: Path, lo: str, hi: str) -> np.ndarray:
    import rasterio
    with rasterio.open(path) as src:
        bands = [i + 1 for i, d in enumerate(src.descriptions) if lo <= d <= hi]
        return np.fmax.reduce([src.read(b).astype(np.float64) for b in bands])


def inputs(data_root: Path, suffix: str = "") -> dict:
    """{(case, hazard, frames): (tpi, t, ref_idx, domain, reference label)}."""
    import lift_vs_terrain as lvt
    import prediction_lift as pl
    import rasterio
    import warned_before as wb
    out = {}
    rs, wa = data_root / "rs_2024", data_root / "wa_2025"
    on = {"rs_2024": wb.ANCHORS["rs_2024"][0]["utc"], "wa_2025": wb.ANCHORS["wa_2025"][0]["utc"]}
    end = {"rs_2024": "20240502", "wa_2025": "20251210"}
    for case, root in (("rs_2024", rs), ("wa_2025", wa)):
        cog = root / "cog"
        grid = rasterio.open(cog / f"{case}_t_slide_{end[case]}.tif")
        t_sl = grid.read(1).astype(np.float64)
        t_fl = rasterio.open(cog / f"{case}_t_flood_{end[case]}.tif").read(1).astype(np.float64)
        sl_idx, _ = wb.inventory_points(root / "geojson" / "landslide_catalog.geojson", grid)
        tr = grid.transform
        if case == "rs_2024":
            dom = lvt._rasterize(root / "geojson" / "flood_ems_aoi.geojson", t_fl.shape, tr)
            fl_idx = np.flatnonzero((lvt._rasterize(root / "geojson" / "flood_ems.geojson", t_fl.shape, tr) & dom).ravel())
            fl_ref = "Copernicus EMS flood extent in the 5 EMS AOIs"
        else:
            nis = cog / "wa_2025_flood_nisar_20251210.tif"
            fl_idx, _ = wb.flood_pixels(nis, grid)
            with rasterio.open(nis) as s:
                b = s.bounds
            rr, cc = np.indices(t_fl.shape)
            lon, lat = tr * (cc + 0.5, rr + 0.5)
            dom = (lon >= b.left) & (lon <= b.right) & (lat >= b.bottom) & (lat <= b.top)
            fl_ref = "NISAR flood candidates (area-weighted) in the NISAR raster extent"
        lo, hi = WINDOWS[case]
        for hz, t, idx, d, ref in (("slide", t_sl, sl_idx, None, f"{case} landslide inventory points"),
                                   ("flood", t_fl, fl_idx, dom, fl_ref)):
            ev = _event_max(cog / f"{case}_tpi_{hz}_{end[case]}{suffix}.tif", lo, hi)
            out[(case, hz, "event")] = (ev, t, idx, d, ref)
            try:
                _, g24, _, _, _ = pl.run_frames(cog, case, hz, on[case], suffix)
                out[(case, hz, "gfs24")] = (g24, t, idx, d, ref)
            except (OSError, IndexError, ValueError):
                pass
    uf = data_root / "rs_uruguai_2026" / "cog" / f"rs_uruguai_2026_tpi_flood_obs_20260930{suffix}.tif"
    with rasterio.open(uf) as s:
        b = {n: s.read(i + 1).astype(np.float64) for i, n in enumerate(s.descriptions)}
        utr = s.transform
    poly = lvt._rasterize(data_root / "rs_uruguai_2026" / "geojson" / "flood_confirmed.geojson",
                          b["tpi"].shape, utr, all_touched=True)
    rr, cc = np.indices(b["tpi"].shape)
    lon, lat = utr * (cc + 0.5, rr + 0.5)
    corr = (lon >= -57.25) & (lon <= -56.45) & (lat >= -29.85) & (lat <= -29.05)
    out[("rs_uruguai_2026", "flood", "event")] = (b["tpi"], b["t"], np.flatnonzero(poly.ravel()), corr,
                                                   "7 NISAR new-water polygons, corridor, Sep 30 replay")
    return out


def run(data_root: Path, suffix: str = "") -> dict:
    data = inputs(data_root, suffix)
    sc = {k: Scorer(*v[:4]) for k, v in data.items()}
    res = {"rule": f"Crítico cut = lowest cut with area <= {MAX_AREA_PCT} % and M1 ratio > 1, chosen on the "
                   "calibration case only", "suffix": suffix or "(served v2)", "references": {
                       "|".join(k): v[4] for k, v in data.items()}, "splits": []}
    pairs = [("slide", "rs_2024", "wa_2025"), ("slide", "wa_2025", "rs_2024"),
             ("flood", "rs_2024", "wa_2025"), ("flood", "wa_2025", "rs_2024")]
    for frames in ("event", "gfs24"):
        for hz, cal, ev in pairs:
            if (cal, hz, frames) not in sc or (ev, hz, frames) not in sc:
                continue
            c = calibrate(sc[(cal, hz, frames)])
            evals = [ev] + (["rs_uruguai_2026"] if hz == "flood" and frames == "event" else [])
            row = {"hazard": hz, "frames": frames, "calibrated_on": cal,
                   "n_ref_calibration": sc[(cal, hz, frames)].n_ref,
                   "new_critico_cut": c["critico_area_and_ratio"], "area_only_cut": c["critico_area_only"],
                   "reason": c["reason"],
                   "calibration_case": {"old_critico": sc[(cal, hz, frames)].at(OLD["critico"]),
                                        "old_alto_plus": sc[(cal, hz, frames)].at(OLD["alto_plus"])},
                   "evaluation": {}}
            if c["critico_area_and_ratio"] is not None:
                row["calibration_case"]["new_critico"] = sc[(cal, hz, frames)].at(c["critico_area_and_ratio"])
            for e in evals:
                if (e, hz, frames) not in sc:
                    continue
                s = sc[(e, hz, frames)]
                row["evaluation"][e] = {"n_ref": s.n_ref, "old_critico": s.at(OLD["critico"]),
                                        "old_alto_plus": s.at(OLD["alto_plus"])}
                for name, cut in (("new_critico", c["critico_area_and_ratio"]), ("area_only", c["critico_area_only"])):
                    if cut is not None:
                        row["evaluation"][e][name] = s.at(cut)
            res["splits"].append(row)
    res["curves"] = {"|".join(k): [s.at(c) for c in GRID[::5]] for k, s in sc.items()}
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suffix", default="")
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)
    res = run(REPO / "data", a.suffix)
    out = a.out or REPO / f"ml/models/m4_cuts{a.suffix}.json"
    out.write_text(json.dumps(res, indent=1))
    f = ("cut", "area_pct", "hit_pct", "lift", "ratio")
    for r in res["splits"]:
        print(f"\n{r['hazard']} {r['frames']} calibrate={r['calibrated_on']} (n={r['n_ref_calibration']}) "
              f"new_cut={r['new_critico_cut']} area_only={r['area_only_cut']} {r['reason'] or ''}")
        for k, v in r["calibration_case"].items():
            print("   cal ", k, {x: v.get(x) for x in f})
        for e, d in r["evaluation"].items():
            for k, v in d.items():
                if isinstance(v, dict):
                    print("   eval", e, k, {x: v.get(x) for x in f})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
