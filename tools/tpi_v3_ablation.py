"""L59 TPI v3 ablation: each review change alone vs v2, scored like M4 (tools/m4_cut_calibration.py).

Components come from lead-analysis (~/.cache/lead-analysis/v3/<case>/components.npz:
r_v2, r_m2, r_m3, r_v3, s_v2, s_v3 as [frame,row,col] on the 90 m grid; T in frames.npz).
Every variant is TPI = cbrt(R·S·T) (uniform weights, live.tpi), NaN where the served v2 TPI is
NaN (water/nodata); M5 and v3 flood are also NaN on the GSW 10-50 % wetland mask.

  v2   r_v2 s_v2 t_v2         (must reproduce frames.npz tpi_v2_* — checked)
  M2   r_m2 s_v2 t_v2         liquid-phase IMERG + liquid climatology
  M3   r_m3 s_v2 t_v2         R beyond P99 (log2 cap at 4xP99)
  M5   r_v2 s_v2 t_v3         flood only: HAND radius 1000 m + wetland NaN (slide = v2)
  M6   r_v2 s_v3 t_v2         S ramp from 0.3
  v3   r_v3 s_v3 t_v3         all of the above (must reproduce tpi_v3_*)
  M4   cut calibration — run on every variant (cross-case RS<->WA, flood also on Uruguaiana)

    PYTHONPATH=.:pipeline python tools/tpi_v3_ablation.py --cache ~/.cache/lead-analysis/v3
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

VARIANTS = {"v2": ("r_v2", "s_v2", "v2"), "M2": ("r_m2", "s_v2", "v2"), "M3": ("r_m3", "s_v2", "v2"),
            "M5": ("r_v2", "s_v2", "v3"), "M6": ("r_v2", "s_v3", "v2"), "v3": ("r_v3", "s_v3", "v3")}
METRICS = ("area_pct", "hit_pct", "lift", "ratio")


def _select(z, case: str, frames: str) -> np.ndarray:
    import m4_cut_calibration as m4
    import warned_before as wb
    dates, kind = z["frame_dates"], z["frame_kind"]
    if frames == "event":
        lo, hi = m4.WINDOWS.get(case, ("20260930", "20260930"))
        return (kind == "obs") & (dates >= lo) & (dates <= hi)
    onset = wb.ANCHORS[case][0]["utc"].strftime("%Y%m%d")
    return np.char.startswith(kind, "gfs") & (dates <= onset)


def compose(fr, co, variant: str, hazard: str, sel: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(event-max TPI of the variant over the selected frames, its T)."""
    rk, sk, tv = VARIANTS[variant]
    t = fr[f"t_{tv}_{hazard}"].astype(np.float32)
    base_nan = np.isnan(fr[f"tpi_v2_{hazard}"][sel]).all(0)
    r, s = co[rk][sel].astype(np.float32), co[sk][sel].astype(np.float32)
    tpi = np.fmax.reduce(np.cbrt(np.clip(r, 0, None) * np.clip(s, 0, None) * t[None]), axis=0)
    tpi[base_nan] = np.nan
    if hazard == "flood" and tv == "v3" and "wetland" in fr.files:
        tpi[fr["wetland"]] = np.nan
    return tpi.astype(np.float64), t.astype(np.float64)


def check_reproduces(fr, co, hazard: str, sel: np.ndarray) -> dict:
    out = {}
    for v in ("v2", "v3"):
        mine, _ = compose(fr, co, v, hazard, sel)
        ref = np.fmax.reduce(fr[f"tpi_{v}_{hazard}"][sel].astype(np.float64), axis=0)
        both = np.isfinite(mine) & np.isfinite(ref)
        out[v] = {"max_abs_err": round(float(np.abs(mine[both] - ref[both]).max()), 4) if both.any() else None,
                  "nan_mismatch_cells": int((np.isfinite(mine) != np.isfinite(ref)).sum())}
    return out


def variant_inputs(base: dict, cache: Path, variant: str, checks: dict) -> dict:
    out = {}
    for (case, hz, frames), (_, _, idx, dom, ref) in base.items():
        fd = cache / case
        if not (fd / "frames.npz").exists() or not (fd / "components.npz").exists():
            continue
        fr, co = np.load(fd / "frames.npz"), np.load(fd / "components.npz")
        sel = _select(fr, case, frames)
        if not sel.any():
            continue
        if variant == "v2":
            checks[f"{case}|{hz}|{frames}"] = check_reproduces(fr, co, hz, sel)
        tpi, t = compose(fr, co, variant, hz, sel)
        out[(case, hz, frames)] = (tpi, t, idx, dom, ref)
    return out


def main(argv=None) -> int:
    import m4_cut_calibration as m4
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, default=Path.home() / ".cache/lead-analysis/v3")
    ap.add_argument("--out", type=Path, default=REPO / "ml/models/tpi_v3_ablation.json")
    a = ap.parse_args(argv)
    base = m4.inputs(REPO / "data")
    res, checks = {"variants": {k: dict(zip(("r", "s", "t"), v, strict=True)) for k, v in VARIANTS.items()},
                   "reproduction_check": {}}, {}
    for v in VARIANTS:
        data = variant_inputs(base, a.cache, v, checks)
        missing = [k for k in base if k not in data]
        res[v] = m4.run(REPO / "data", data=data | {k: base[k] for k in missing if v == "v2"})
        res[v]["scored_from_served_cogs"] = ["|".join(k) for k in missing] if v == "v2" else []
        res[v]["not_scored"] = [] if v == "v2" else ["|".join(k) for k in missing]
    res["reproduction_check"] = checks
    a.out.write_text(json.dumps(res, indent=1))
    print("reproduction:", json.dumps(checks))
    for key in res["v2"]["curves"]:
        print(f"\n{key}")
        for v in VARIANTS:
            c = {x["cut"]: x for x in res[v]["curves"].get(key, [])}
            if c:
                print(f"  {v:3s}", " | ".join(f"{cut}: " + " ".join(str(c[cut].get(m)) for m in METRICS)
                                             for cut in (0.4, 0.6)))
    for v in VARIANTS:
        print(f"\nM4 {v}:", [(r["hazard"], r["frames"], r["calibrated_on"], r["new_critico_cut"], r["area_only_cut"])
                             for r in res[v]["splits"]])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
