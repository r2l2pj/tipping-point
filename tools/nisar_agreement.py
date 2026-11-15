"""L61 (2): nisar_agreement — does the TPI flag the ground NISAR sees under water?

Reference (conductor spec, L61): the single-pass NISAR WATER mask of a map day = HH < -18 dB on the
fixed-stretch frames, outside permanent water, from the latest pass <= 12 d before (never later);
masks by lead-analysis (live/nisar_water.py):
  cases: ~/.cache/lead-analysis/l61/<case>/water_<YYYYMMDD>.npz   (case TPI grid)
  Now:   data/now/nisar_tpi/water.npz                               (Now 0.01 deg grid)
  keys: water, observed (pixels a pass saw), transform, crs, pass_ids, pass_dates, map_day.
Scored only on observed cells:
  observed  TPI_flood of the map day (observed replay frame)
  lead_Ld   the GFS run issued L = 1, 2, 3 days before the map day, frame dL (valid on the map day)
At High+ (> 0.4) and Critical (> 0.6): hit % (share of NISAR-water cells flagged), area % (share
of observed cells flagged), lift = hit / area, and the M1 ratio vs a same-area terrain-only map
(tools/lift_vs_terrain.py; > 1 = rain and soil add to terrain). Next to it:
outside_floodplain_pct = share of NISAR-water cells with HAND > 5 m (rice paddies, irrigation and
reservoirs show as radar water but are not floods). NISAR water = candidates, never "confirmed".

    PYTHONPATH=.:pipeline python tools/nisar_agreement.py [--case all|wa_2025|rs_uruguai_2026|now]
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

CUTS = {"alto_plus": 0.4, "critico": 0.6}
LEADS = (1, 2, 3)


def _ymd(d: dt.date) -> str:
    return d.strftime("%Y%m%d")


def _day(s: str) -> dt.date:
    return dt.datetime.strptime(s.replace("-", "")[:8], "%Y%m%d").date()


def score(tpi: np.ndarray, t: np.ndarray, ref_idx: np.ndarray, domain: np.ndarray | None) -> dict:
    import m4_cut_calibration as m4
    s = m4.Scorer(tpi.astype(np.float64), t.astype(np.float64), ref_idx, domain)
    out = {"n_ref": s.n_ref}
    for name, cut in CUTS.items():
        a = s.at(cut)
        out[name] = {"hit_pct": a.get("hit_pct"), "area_pct": a.get("area_pct"), "lift": a.get("lift"),
                     "ratio_vs_terrain": a.get("ratio")}
    return out


def load_water(path: Path) -> dict:
    z = np.load(path, allow_pickle=False)
    return {"water": z["water"].astype(bool), "observed": z["observed"].astype(bool),
            "pass_ids": [str(x) for x in np.ravel(z["pass_ids"])] if "pass_ids" in z.files else [],
            "pass_dates": [str(x) for x in np.ravel(z["pass_dates"])] if "pass_dates" in z.files else [],
            "map_day": str(np.ravel(z["map_day"])[0]) if "map_day" in z.files else path.stem.split("_")[-1],
            "floodplain": (z["floodplain"].astype(bool) if "floodplain" in z.files
                           and z["floodplain"].shape == z["water"].shape else None),
            "transform": [float(x) for x in np.ravel(z["transform"])] if "transform" in z.files else None}


def cell_km2(transform, shape) -> np.ndarray:
    """Geodesic-ish cell area (km²) of an EPSG:4326 grid (a, b, c, d, e, f affine)."""
    a, _b, _c, _d, e, f = transform[:6]
    lat = f + (np.arange(shape[0]) + 0.5) * e
    return (abs(a) * 111.32 * np.cos(np.deg2rad(lat)) * abs(e) * 110.574)[:, None] * np.ones((1, shape[1]))


def agreement_day(w: dict, tpi_obs, fc: dict, t, floodplain, domain=None) -> dict:
    """One map day: observed + forecast leads vs the NISAR water mask (observed cells only)."""
    if w.get("floodplain") is not None:   # lead-analysis's floodplain on the mask grid wins (one definition)
        floodplain = w["floodplain"]
    dom = w["observed"] if domain is None else (w["observed"] & domain)
    water = w["water"] & dom
    ref = np.flatnonzero(water.ravel())
    n = int(water.sum())
    km2 = (round(float(cell_km2(w["transform"], water.shape)[water].sum()), 2)
           if w.get("transform") else None)
    out = {"map_day": w["map_day"], "passes": w["pass_ids"], "pass_dates": w["pass_dates"],
           "pass_tracks": w.get("pass_tracks", []),
           "water_cells": n, "water_km2": km2, "observed_cells": int(dom.sum()),
           "outside_floodplain_pct": (round(100 * float((~floodplain[water]).mean()), 1)
                                      if n and floodplain is not None else None)}
    if n == 0:
        return out | {"null_reason": "no NISAR water on observed cells"}
    out["observed"] = score(tpi_obs, t, ref, dom) if tpi_obs is not None else {"null_reason": "no replay frame"}
    out["forecast"] = {f"lead_{L}d": (score(a, t, ref, dom) | {"frames": info} if a is not None
                                      else {"null_reason": info}) for L, (a, info) in fc.items()}
    return out


def _tracks(data: Path, case: str) -> dict:
    """NISAR frame id -> track from the case png/_index.json (pass_tracks in the rows)."""
    try:
        ix = json.loads((data / case / "png" / "_index.json").read_text())
    except (OSError, ValueError):
        return {}
    return {e["id"]: e.get("track") for e in ix.get("layers", []) if e.get("track")}


def _gfs_valid_on(path: Path, day: dt.date, lead: int):
    import rasterio
    if not path.exists():
        return None, f"no GFS run {path.name}"
    with rasterio.open(path) as src:
        names = list(src.descriptions)
        b = f"d{lead}_flood"
        if b not in names:
            return None, f"{path.name} has no {b}"
        return src.read(names.index(b) + 1).astype(np.float64), [f"{path.name} {b} -> {day}"]


def _floodplain_wa(shape) -> np.ndarray | None:
    cache = Path.home() / ".cache/lead-ml/wa_terrain.npz"   # MERIT Hydro hnd on the WA TPI grid
    if not cache.exists():
        return None
    hand = np.load(cache)["hand"]
    return (hand <= 5.0) if hand.shape == tuple(shape) else None


def _floodplain_uru(tr, shape) -> np.ndarray:
    """0.01 deg cell = floodplain if the median 90 m HAND (static-v1 hand_90m) inside it is <= 5 m."""
    import t_flood_eval as tfe
    from rasterio.transform import from_origin
    k = 12
    fine = from_origin(tr.c, tr.f, tr.a / k, abs(tr.e) / k)
    hand, _ = tfe.static90_on(fine, (shape[0] * k, shape[1] * k))
    return np.nanmedian(hand.reshape(shape[0], k, shape[1], k), axis=(1, 3)) <= 5.0


def case_wa(data: Path, masks: Path) -> list[dict]:
    import rasterio
    files = sorted((masks / "wa_2025").glob("water_*.npz"))
    if not files:
        return []
    c = data / "wa_2025" / "cog"
    t = rasterio.open(c / "wa_2025_t_flood_20251210.tif").read(1).astype(np.float64)
    fp = _floodplain_wa(t.shape)
    out = []
    tracks = _tracks(data, "wa_2025")
    with rasterio.open(c / "wa_2025_tpi_flood_20251210.tif") as s:
        names = list(s.descriptions)
        for f in files:
            w = load_water(f)
            w["pass_tracks"] = [tracks.get(i) for i in w["pass_ids"]]
            day = _day(w["map_day"])
            obs = s.read(names.index(_ymd(day)) + 1).astype(np.float64) if _ymd(day) in names else None
            fc = {L: _gfs_valid_on(c / f"wa_2025_tpi_gfs_{_ymd(day - dt.timedelta(days=L))}.tif", day, L)
                  for L in LEADS}
            out.append(agreement_day(w, obs, fc, t, fp))
    return out


def case_uru(data: Path, masks: Path) -> list[dict]:
    import rasterio
    mfiles = sorted((masks / "rs_uruguai_2026").glob("water_*.npz"))
    if not mfiles:
        return []
    c = data / "rs_uruguai_2026" / "cog"
    files = {f.stem.split("_")[-1]: f for f in c.glob("rs_uruguai_2026_tpi_flood_obs_*.tif")}
    with rasterio.open(next(iter(files.values()))) as s:
        tr, shape = s.transform, s.shape
        t = s.read(list(s.descriptions).index("t") + 1).astype(np.float64)
    fp = _floodplain_uru(tr, shape)
    out = []
    tracks = _tracks(data, "rs_uruguai_2026")
    for f in mfiles:
        w = load_water(f)
        w["pass_tracks"] = [tracks.get(i) for i in w["pass_ids"]]
        day = _day(w["map_day"])
        obs = None
        if _ymd(day) in files:
            with rasterio.open(files[_ymd(day)]) as s:
                obs = s.read(list(s.descriptions).index("tpi") + 1).astype(np.float64)
        fc = {L: _gfs_valid_on(c / f"rs_uruguai_2026_tpi_gfs_{_ymd(day - dt.timedelta(days=L))}.tif", day, L)
              for L in LEADS}
        out.append(agreement_day(w, obs, fc, t, fp))
    return out


def case_now(data: Path) -> dict:
    """Today's Now TPI vs the latest-pass water mask; forecast leads need archived Now COGs (none yet)."""
    import rasterio
    wp = data / "now" / "nisar_tpi" / "water.npz"
    if not wp.exists():
        return {"status": "pending", "null_reasons": ["no data/now/nisar_tpi/water.npz yet (written by "
                                                      "live.now_nisar_tpi after the NISAR stack step)"]}
    w = load_water(wp)
    with rasterio.open(data / "now" / "cog" / "tpi_flood_today.tif") as s:
        tpi = s.read(1).astype(np.float64)
    if tpi.shape != w["water"].shape:
        return {"status": "pending",
                "null_reasons": [f"grid mismatch: TPI {tpi.shape} vs water {w['water'].shape}"]}
    t = np.where(np.isfinite(tpi), 1.0, np.nan)   # no Now T grid here: M1 ratio not defined for Now
    res = agreement_day(w, tpi, {L: (None, "Now TPI COGs are not archived per run (history keeps PNGs only)")
                                 for L in LEADS}, t, None)
    if isinstance(res.get("observed"), dict):
        for c in CUTS:
            if c in res["observed"]:
                res["observed"][c]["ratio_vs_terrain"] = None
    res["status"] = "scored" if res["water_cells"] else "no water"
    return res


def flat_keys(days: list[dict]) -> dict:
    """metrics.json flat keys: pooled over map days (water-cell weighted) for observed and each lead."""
    out = {"nisar_agreement_map_days": [d["map_day"] for d in days if "observed" in d]}
    fp = [(d["outside_floodplain_pct"], d["water_cells"]) for d in days
          if d.get("outside_floodplain_pct") is not None]
    out["nisar_agreement_outside_floodplain_pct"] = (
        round(sum(a * n for a, n in fp) / sum(n for _, n in fp), 1) if fp else None)
    for name in ("observed", "lead_1d", "lead_2d", "lead_3d"):
        tag = "obs" if name == "observed" else f"fc{name[5]}d"
        for cls, sfx in (("alto_plus", ""), ("critico", "_critical")):
            rows = []
            for d in days:
                x = d.get("observed") if name == "observed" else d.get("forecast", {}).get(name)
                if isinstance(x, dict) and cls in x and x[cls].get("hit_pct") is not None:
                    rows.append((x[cls], d["water_cells"]))
            n = sum(k for _, k in rows)
            out[f"nisar_agreement_{tag}{sfx}_hit_pct"] = (
                round(sum(r["hit_pct"] * k for r, k in rows) / n, 1) if n else None)
            out[f"nisar_agreement_{tag}{sfx}_lift"] = (
                round(sum((r["lift"] or 0) * k for r, k in rows) / n, 2) if n else None)
    return out


REFERENCE = ("NISAR water = HH < −18 dB (fixed) outside permanent water, single pass ≤ 12 d before "
             "the map day (never later)")
DEFINITION = ("hit = % of NISAR-water area the index WITHOUT NISAR flagged (High+ > 0.4 = alto_plus, "
              "Critical > 0.6 = critico); area = % of the NISAR-observed land cells flagged; lift = hit/area; "
              "ratio_vs_terrain = M1 (hit / hit of a same-area terrain-only map; > 1 = rain and soil add "
              "to terrain). observed = TPI of the map day; forecast.lead_Ld = NOAA GFS run issued L days "
              "before, valid on the map day")
NOTE = ("candidate water, never confirmed; single-date dark radar also lights flooded fields "
        "(rice, irrigation) and reservoirs — see outside_floodplain_pct (HAND > 5 m)")
HEADLINE_KEYS = {"nisar_agreement_obs_hit_pct": ("observed", "hit_pct"),
                 "nisar_agreement_obs_lift": ("observed", "lift"),
                 "nisar_agreement_fc1d_hit_pct": ("lead_1d", "hit_pct"),
                 "nisar_agreement_fc2d_hit_pct": ("lead_2d", "hit_pct"),
                 "nisar_agreement_fc3d_hit_pct": ("lead_3d", "hit_pct")}


def block(days: list[dict], headline_day: str) -> tuple[dict, dict, dict]:
    """(cases.<case>.nisar_agreement, headline flat keys, _null_reasons) — t1b8's metrics schema.
    passes[] = the rows whose map day IS a NISAR pass day (age 0); headline = the row of
    `headline_day`. Flat keys are alto_plus (High+) from the headline row."""
    hd = headline_day.replace("-", "")
    head = next((d for d in days if d["map_day"].replace("-", "") == hd), None)
    passes = [d for d in days if d.get("pass_dates")
              and d["map_day"].replace("-", "") == max(x.replace("-", "") for x in d["pass_dates"])]
    blk = {"reference": REFERENCE, "definition": DEFINITION, "note": NOTE,
           "headline": head, "passes": passes, "days": days}
    flat, why = {}, {}
    for k, (src, f) in HEADLINE_KEYS.items():
        x = None if head is None else (head.get("observed") if src == "observed"
                                       else head.get("forecast", {}).get(src))
        v = x.get("alto_plus", {}).get(f) if isinstance(x, dict) else None
        flat[k] = v
        if v is None:
            why[k] = (f"no row for headline map day {headline_day}" if head is None else
                      (x or {}).get("null_reason") or head.get("null_reason") or f"{src} {f} undefined")
    for k, f in (("nisar_agreement_water_km2", "water_km2"),
                 ("nisar_agreement_outside_floodplain_pct", "outside_floodplain_pct")):
        flat[k] = None if head is None else head.get(f)
        if flat[k] is None:
            why[k] = "no headline row" if head is None else f"{f} undefined (no floodplain/transform)"
    return blk, flat, why


def write_metrics(case: str, days: list[dict], headline_day: str, path: Path) -> dict:
    from tp.metrics import merge_metrics
    blk, flat, why = block(days, headline_day)
    merge_metrics(case, {"nisar_agreement": blk, **flat}, reasons=why, path=path)
    return flat


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", default="all")
    ap.add_argument("--data", type=Path, default=REPO / "data")
    ap.add_argument("--masks", type=Path, default=Path.home() / ".cache/lead-analysis/l61")
    ap.add_argument("--out", type=Path, default=REPO / "ml/models/nisar_agreement.json")
    ap.add_argument("--write", action="store_true",
                    help="merge cases.<case>.nisar_agreement + headline flat keys into <data>/metrics.json")
    ap.add_argument("--headline", help="headline map day YYYY-MM-DD (the headline NISAR pass day)")
    a = ap.parse_args(argv)
    if a.write and (a.case not in ("wa_2025", "rs_uruguai_2026") or not a.headline):
        ap.error("--write needs --case wa_2025|rs_uruguai_2026 and --headline")
    res = {"what": "TPI_flood vs single-pass NISAR water (HH < -18 dB, not permanent water): map-day TPI and "
                   "GFS runs issued 1-3 d before; High+ > 0.4, Critical > 0.6; NISAR water = candidates",
           "cases": {}}
    for case, fn in (("wa_2025", case_wa), ("rs_uruguai_2026", case_uru)):
        if a.case in ("all", case):
            days = fn(a.data, a.masks)
            res["cases"][case] = ({"days": days, "flat": flat_keys(days)} if days else
                                  {"status": "pending", "null_reasons": [f"no masks in {a.masks / case}"]})
    if a.case in ("all", "now"):
        res["cases"]["now"] = case_now(a.data)
    a.out.write_text(json.dumps(res, indent=1, default=str))
    if a.write and "days" in res["cases"].get(a.case, {}):
        flat = write_metrics(a.case, res["cases"][a.case]["days"], a.headline, a.data / "metrics.json")
        print("merged cases.%s.nisar_agreement into %s:" % (a.case, a.data / "metrics.json"), json.dumps(flat))
    for k, v in res["cases"].items():
        if "days" not in v:
            print(k, v.get("status"), v.get("null_reasons"))
            continue
        print(k, json.dumps(v["flat"]))
        for d in v["days"]:
            o = d.get("observed", {})
            print(f"  {d['map_day']} water {d['water_cells']} outside_fp {d['outside_floodplain_pct']} "
                  f"obs {[(c, o.get(c)) for c in CUTS] if 'alto_plus' in o else o}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
