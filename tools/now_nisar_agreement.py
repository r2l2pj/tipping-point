"""L61 (2) Now: nisar_agreement for the live Now domain (lead-ml; per-case scoring lives in
tools/nisar_agreement.py, owned by the per-case leads).

Inputs, from a Now data dir (the job's data/now, or a checkout of origin/fallback data/now):
  cog/tpi_flood_nisar_today.tif   band 'water' (1 = NISAR water: HH < -18 dB, not permanent water,
                                  latest pass <= 12 d) and band 'observed' (1 = a NISAR frame saw the
                                  cell within 12 d) — written by live.now_nisar_tpi
  (or nisar_tpi/water.npz inside the Now job: water, observed, pass_dates, map_day)
  cog/tpi_flood_today.tif          today's TPI_flood (d0)
  nisar_tpi/layers.json, summary.json (nisar_water: pass date, track, age)
Scored on observed cells only (tools/nisar_agreement.agreement_day): hit % at High+ / Critical,
area %, lift. Limits, stated in the output:
  * today's TPI is compared with a pass up to 12 d old (age_days reported);
  * forecast leads (TPI issued 1-3 d before the pass) come from the per-run archive
    history/<run>/cog/tpi_flood_d<k>.tif (band description = target date; from 2026-11-15 on);
    null while the rolling history does not reach back that far;
  * no Now T_flood grid here -> no M1 ratio for Now; no HAND -> outside_floodplain_pct only if
    the mask carries a floodplain array.
NISAR water = candidates, never "confirmed".

    PYTHONPATH=.:pipeline python tools/now_nisar_agreement.py --now-dir data/now [--out data/now/nisar_agreement.json]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
for p in (str(REPO), str(REPO / "pipeline"), str(REPO / "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

LEAD_REASON = "no archived Now run issued that many days before the pass (history/<run>/cog/tpi_flood_d<k>.tif)"


def lead_frames(now: Path, pass_date: str | None, shape) -> dict:
    """{L: (array, [info]) | (None, reason)}: the latest archived run issued on pass_date - L whose
    tpi_flood_d<L>.tif targets pass_date (band description = target date)."""
    import rasterio
    out = {}
    if not pass_date:
        return {L: (None, "no pass date") for L in (1, 2, 3)}
    pd_ = dt.date.fromisoformat(pass_date[:10])
    runs = sorted((now / "history").glob("*/cog/tpi_flood_d*.tif"))
    for L in (1, 2, 3):
        issue = (pd_ - dt.timedelta(days=L)).strftime("%Y%m%d")
        cands = [f for f in runs if f.parts[-3].startswith(issue) and f.stem == f"tpi_flood_d{L}"]
        hit = None
        for f in reversed(cands):
            with rasterio.open(f) as s:
                tgt = (s.descriptions[0] or "").replace("-", "")[:8]
                if tgt == pd_.strftime("%Y%m%d") and s.shape == tuple(shape):
                    hit = (s.read(1).astype(np.float64), [f"history/{f.parts[-3]}/cog/{f.name} -> {pass_date[:10]}"])
                    break
        out[L] = hit or (None, LEAD_REASON)
    return out


def _read_bands(path: Path) -> dict:
    import rasterio
    with rasterio.open(path) as s:
        return {(d or f"b{i + 1}"): s.read(i + 1) for i, d in enumerate(s.descriptions)}


def load_mask(now: Path) -> tuple[dict | None, str | None]:
    npz = now / "nisar_tpi" / "water.npz"
    if npz.exists():
        import nisar_agreement as na
        return na.load_water(npz), None
    cog = now / "cog" / "tpi_flood_nisar_today.tif"
    if not cog.exists():
        return None, "no NISAR water layer in this Now run (cog/tpi_flood_nisar_today.tif absent)"
    b = _read_bands(cog)
    if "water" not in b:
        return None, f"{cog.name} has no 'water' band"
    if "observed" not in b:
        return None, f"{cog.name} has no 'observed' band: agreement needs the observed footprint"
    meta = {}
    sp = now / "summary.json"
    if sp.exists():
        meta = json.loads(sp.read_text()).get("nisar_water") or {}
    pdate = meta.get("pass_date")
    return {"water": b["water"] == 1, "observed": b["observed"] == 1,
            "pass_ids": [str(x) for x in meta.get("pass_ids", [])],
            "pass_dates": [pdate] if pdate else [], "map_day": meta.get("map_day") or dt.date.today().isoformat(),
            "floodplain": None, "_meta": meta}, None


def run(now: Path) -> dict:
    import nisar_agreement as na
    w, why = load_mask(now)
    if w is None:
        return {"status": "pending", "null_reasons": [why]}
    tpi = _read_bands(now / "cog" / "tpi_flood_today.tif")
    tpi = next(iter(tpi.values())).astype(np.float64)
    if tpi.shape != w["water"].shape:
        return {"status": "pending", "null_reasons": [f"grid mismatch: TPI {tpi.shape} vs water {w['water'].shape}"]}
    t = np.where(np.isfinite(tpi), 1.0, np.nan)    # placeholder T: ratio_vs_terrain is dropped below
    res = na.agreement_day(w, tpi, lead_frames(now, (w["pass_dates"] or [None])[-1], tpi.shape), t, None)
    for sub in [res.get("observed")] + list((res.get("forecast") or {}).values()):
        for c in na.CUTS:
            if isinstance(sub, dict) and isinstance(sub.get(c), dict):
                sub[c].pop("ratio_vs_terrain", None)
    meta = w.get("_meta") or {}
    age = meta.get("age_days")
    if age is None and w["pass_dates"]:
        try:
            age = (dt.date.fromisoformat(res["map_day"][:10]) - dt.date.fromisoformat(w["pass_dates"][-1][:10])).days
        except ValueError:
            age = None
    res |= {"status": "scored" if res["water_cells"] else "no water", "pass_age_days": age,
            "pass_track": meta.get("pass_track"),
            "limits": ["today's TPI vs a NISAR pass up to 12 d old (pass_age_days)",
                       "forecast leads need archived runs issued 1-3 d before the pass (history keeps a rolling window)",
                       "no M1 terrain ratio for Now (no T_flood grid in the Now outputs)"],
            "reference": "NISAR water: HH < -18 dB, not permanent water, latest pass <= 12 d (candidates)"}
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--now-dir", type=Path, default=REPO / "data" / "now")
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args(argv)
    res = run(a.now_dir)
    out = a.out or a.now_dir / "nisar_agreement.json"
    out.write_text(json.dumps(res, indent=1, default=str) + "\n")
    o = res.get("observed", {})
    print(json.dumps({k: res.get(k) for k in ("status", "map_day", "pass_dates", "pass_age_days", "water_cells",
                                              "observed_cells", "null_reasons")}),
          {c: o.get(c) for c in ("alto_plus", "critico")} if isinstance(o, dict) else o)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
