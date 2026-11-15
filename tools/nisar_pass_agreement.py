"""L61 (2): nisar_agreement per NISAR pass for a case (Uruguaiana first) -> metrics.json.

For every fixed-stretch NISAR HH pass of the case (live/nisar_water.hh_frames), the
reference is that ONE pass's water mask on the TPI grid: HH < -18 dB, outside permanent
water (= where the served TPI is NaN), observed cells only (live/nisar_water.water_mask
with only this pass). It grades the index WITHOUT NISAR, so the radar never grades its own map:
  observed  plain TPI_flood of the pass day (observed replay frame, band 'tpi')
  lead_Ld   the NOAA GFS run issued L = 1, 2, 3 days before the pass day, band dL_flood
            (valid on the pass day)
At High+ (> 0.4, key alto_plus) and Critical (> 0.6, key critico): hit_pct (share of
NISAR-water cells flagged), area_pct (share of observed land flagged), lift = hit / area,
and ratio_vs_terrain (M1, tools/lift_vs_terrain: TPI hit / hit of a same-area terrain-only
map; > 1 = rain and soil add to terrain). outside_floodplain_pct = share of NISAR-water
area with HAND > 5 m (rice paddies / irrigation read as radar water). NISAR water =
candidate, never confirmed.

Writes data/metrics.json cases[<case>].nisar_agreement = the headline pass's record +
passes[] (every pass), and flat keys nisar_agreement_* from the headline pass.

    PYTHONPATH=.:pipeline:tools python tools/nisar_pass_agreement.py --case rs_uruguai_2026
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
for _p in (str(REPO), str(REPO / "pipeline"), str(REPO / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from live import nisar_water as nw  # noqa: E402

CUTS = {"alto_plus": nw.HIGH, "critico": nw.CRIT}
LEADS = (1, 2, 3)
WINDOW_DAYS = nw.MAX_AGE_DAYS
HEADLINE = {"rs_uruguai_2026": "2026-10-12", "wa_2025": "2025-12-16"}
HEADLINE_LABEL = "index without NISAR, up to 12 days before the pass"
REFERENCE = "NISAR water HH < -18 dB outside permanent water (single pass)"
DEFINITION = ("Per pass: hit_pct = share of that pass's NISAR-water cells (cells of the case TPI grid) the index without "
              "NISAR flagged High+ (> 0.4, alto_plus) / Critical (> 0.6, critico); area_pct = share of "
              "observed land cells flagged; lift = hit_pct / area_pct; ratio_vs_terrain (M1) = hit vs a "
              "same-area terrain-only map. observed = TPI_flood of the pass day; forecast.lead_Ld = NOAA "
              "GFS run issued L days before, valid on the pass day.")
NOTE = ("Scores the flood index WITHOUT NISAR (pass-day TPI and NOAA GFS runs issued 1-3 d "
        "before) against one NISAR pass's candidate water (HH < -18 dB, fixed-stretch frame, "
        "outside permanent water, observed cells only). Candidate water also lights flooded "
        "rice / irrigation: outside_floodplain_pct is the share of it with HAND > 5 m; rice ON "
        "the floodplain cannot be told apart. Single-pass threshold; towns/forest stay bright "
        "(double bounce) and are missed.")


def _ymd(d: dt.date) -> str:
    return d.strftime("%Y%m%d")


def cell_km2(tr, shape) -> np.ndarray:
    lat = tr.f + (np.arange(shape[0]) + 0.5) * tr.e
    return ((abs(tr.a) * 111.32 * np.cos(np.deg2rad(lat)))[:, None] * abs(tr.e) * 110.574
            * np.ones((1, shape[1])))


def score(tpi: np.ndarray, t: np.ndarray, water: np.ndarray, domain: np.ndarray) -> dict:
    """hit/area/lift/M1 at both cuts; reference = water cells inside the domain."""
    import m4_cut_calibration as m4
    dom = domain & np.isfinite(tpi) & np.isfinite(t)
    ref = np.flatnonzero((water & dom).ravel())
    s = m4.Scorer(tpi.astype(np.float64), t.astype(np.float64), ref, dom)
    out = {"water_cells_scored": s.n_ref}
    for name, cut in CUTS.items():
        a = s.at(cut)
        out[name] = {"hit_pct": a.get("hit_pct"), "area_pct": a.get("area_pct"), "lift": a.get("lift"),
                     "ratio_vs_terrain": a.get("ratio")}
    return out


def pass_record(frame: dict, root: Path, grid: dict, obs: dict, gfs: dict, fp) -> dict:
    """One pass: single-pass water mask vs the pass-day TPI and the GFS leads."""
    day = dt.date.fromisoformat(frame["date"])
    shape, tr, t, perm = grid["shape"], grid["transform"], grid["t"], grid["perm_water"]
    water, observed, _ = nw.water_mask([frame], day, root, shape, tr, perm_water=perm)
    dom = observed & np.isfinite(t)
    w = water & dom
    km2 = grid["km2"]
    wkm2 = float(km2[w].sum())
    db = nw._frame_db_on(frame, root, shape, tr)            # sanity: a frame that never reaches -18 dB
    fin = db[np.isfinite(db)]                                 # (e.g. old coarse PNGs) gives 0 water, not "no flood"
    rec = {"pass": {"date": frame["date"], "track": frame.get("track"), "id": frame["id"],
                    "png": frame["png"]},
           "decoded_db": {"p1": round(float(np.percentile(fin, 1)), 2) if fin.size else None,
                          "min": round(float(fin.min()), 2) if fin.size else None},
           "pass_dates": [frame["date"]], "pass_tracks": [frame.get("track")],   # t1br row layout
           "reference": REFERENCE, "map_day": frame["date"],
           "water_km2": round(wkm2, 2), "water_cells": int(w.sum()),
           "water_km2_single_pass": round(wkm2, 2),
           "water_km2_single_pass_floodplain": (round(float(km2[w & fp].sum()), 2) if fp is not None else None),
           "observed_km2": round(float(km2[dom].sum()), 1),
           "outside_floodplain_pct": (round(100 * float(km2[w & ~fp].sum()) / wkm2, 1)
                                      if fp is not None and wkm2 else None)}
    if not w.any():
        return rec | {"null_reason": "no NISAR water on observed cells"}
    ymd = _ymd(day)
    rec["observed"] = (score(obs[ymd], t, water, dom) if ymd in obs
                       else {"null_reason": f"no observed TPI replay frame on {day}"})
    fc = {}
    for L in LEADS:
        issue = _ymd(day - dt.timedelta(days=L))
        a = gfs.get(issue, {}).get(f"d{L}_flood")
        fc[f"lead_{L}d"] = (score(a, t, water, dom) | {"issued": (day - dt.timedelta(days=L)).isoformat()}
                            if a is not None else {"null_reason": f"no GFS run issued {issue} (d{L}_flood)"})
    rec["forecast"] = fc
    rec["window_12d"] = window(day, obs, t, water, dom)
    rec["gfs_window_12d"] = gfs_window(day, gfs, t, water, dom)
    return rec


def gfs_window(day: dt.date, gfs: dict, t, water, dom, days: int = WINDOW_DAYS) -> dict:
    """Best NOAA GFS frame (index without NISAR) from runs issued D-12..D-1, valid on a day in
    the same window (D-12..D-1): best = max skill (hit_pct - area_pct), earliest issue on ties.
    Published, not the headline."""
    lo, hi = day - dt.timedelta(days=days), day - dt.timedelta(days=1)
    rows = []
    for issue_s, bands in sorted(gfs.items()):
        issue = dt.datetime.strptime(issue_s, "%Y%m%d").date()
        if not lo <= issue <= hi:
            continue
        for name, arr in sorted(bands.items()):
            L = int(name[1:name.index("_")])
            valid = issue + dt.timedelta(days=L)
            if lo <= valid <= hi:
                rows.append((issue, L, valid, arr))
    if not rows:
        return {"null_reason": f"no GFS run issued and valid in the {days} d before {day}"}
    scored = []
    for issue, L, valid, arr in rows:
        sc = score(arr, t, water, dom)
        scored.append({"issued": issue.isoformat(), "lead_days": L, "valid": valid.isoformat(),
                       "issued_days_before": (day - issue).days, "valid_days_before": (day - valid).days,
                       **{c: dict(sc[c]) for c in CUTS}})
    best = {}
    for c in CUTS:
        top = max(scored, key=lambda r: (skill(r[c]), r["issued_days_before"]))
        best[c] = top if skill(top[c]) > 0 else {"hit_pct": 0.0, "null_reason": "no frame with hit > area"}
    return {"frames": len(scored), "best": best,
            "runs": [{k: v for k, v in r.items()} for r in scored]}


def skill(x: dict) -> float:
    """Peirce/Hanssen-Kuipers-like skill in % points: hit_pct - area_pct (area ~ false-alarm share)."""
    return round((x.get("hit_pct") or 0.0) - (x.get("area_pct") or 0.0), 3)


def window(day: dt.date, obs: dict, t, water, dom, days: int = WINDOW_DAYS) -> dict:
    """The index WITHOUT NISAR in the days BEFORE the pass (D-12..D-1, never the pass day or later):
    max plain TPI over the window (did it flag this water at any point beforehand?) plus each day's
    hit, and the best day = max skill (hit_pct - area_pct; earliest on ties) -> 'k days before'."""
    have = [day - dt.timedelta(days=k) for k in range(days, 0, -1)
            if _ymd(day - dt.timedelta(days=k)) in obs]
    if not have:
        return {"null_reason": f"no observed TPI replay frame in the {days} d before {day}"}
    mx = np.fmax.reduce([obs[_ymd(d)] for d in have])
    per = {}
    for d in have:
        sc = score(obs[_ymd(d)], t, water, dom)
        per[d.isoformat()] = {c: dict(sc[c]) for c in CUTS}
    best = {}
    for c in CUTS:     # best = max Peirce skill (hit - area), earliest on ties: flagging everything can't win
        top = max(skill(v[c]) for v in per.values())
        first = next((d for d, v in per.items() if top > 0 and skill(v[c]) == top), None)
        best[c] = {"day": first, "days_before": (day - dt.date.fromisoformat(first)).days if first else None,
                   **(per[first][c] if first else {"hit_pct": 0.0})}
    return {"days": [d.isoformat() for d in have], "max_tpi": score(mx, t, water, dom),
            "best_day": best, "per_day": per}


def flat_keys(rec: dict) -> dict:
    out = {"nisar_agreement_pass_date": rec["pass"]["date"],
           "nisar_agreement_pass_track": rec["pass"]["track"],
           "nisar_agreement_water_km2": rec["water_km2"],
           "nisar_agreement_water_km2_single_pass": rec.get("water_km2_single_pass"),
           "nisar_agreement_water_km2_single_pass_floodplain": rec.get("water_km2_single_pass_floodplain"),
           "nisar_agreement_observed_km2_single_pass": rec.get("observed_km2"),
           "nisar_water_km2_case_box": (rec.get("map_12d") or {}).get("water_km2_case_box"),
           "nisar_raised_km2_floodplain": (rec.get("map_12d") or {}).get("raised_km2_floodplain"),
           "nisar_agreement_outside_floodplain_pct": rec["outside_floodplain_pct"]}
    srcs = [("obs", rec.get("observed"))] + [(f"fc{L}d", (rec.get("forecast") or {}).get(f"lead_{L}d"))
                                              for L in LEADS]
    for tag, x in srcs:
        for cls, sfx in (("alto_plus", ""), ("critico", "_critical")):
            v = x.get(cls) if isinstance(x, dict) else None
            v = v or {}
            out[f"nisar_agreement_{tag}{sfx}_hit_pct"] = v.get("hit_pct")
            out[f"nisar_agreement_{tag}{sfx}_area_pct"] = v.get("area_pct")
            out[f"nisar_agreement_{tag}{sfx}_lift"] = v.get("lift")
            out[f"nisar_agreement_{tag}{sfx}_m1"] = v.get("ratio_vs_terrain")
    w = rec.get("window_12d") or {}
    for cls, sfx in (("alto_plus", ""), ("critico", "_critical")):
        b = (w.get("best_day") or {}).get(cls) or {}
        mx = (w.get("max_tpi") or {}).get(cls) or {}
        bd = b.get("day")
        bs = score_of(w, bd, cls)
        out[f"nisar_agreement_win{sfx}_best_day"] = bd
        out[f"nisar_agreement_win{sfx}_days_before"] = b.get("days_before")
        out[f"nisar_agreement_win{sfx}_hit_pct"] = bs.get("hit_pct")
        out[f"nisar_agreement_win{sfx}_area_pct"] = bs.get("area_pct")
        out[f"nisar_agreement_win{sfx}_lift"] = bs.get("lift")
        out[f"nisar_agreement_win{sfx}_m1"] = bs.get("ratio_vs_terrain")
        out[f"nisar_agreement_winmax{sfx}_hit_pct"] = mx.get("hit_pct")
        out[f"nisar_agreement_winmax{sfx}_lift"] = mx.get("lift")
    g = (rec.get("gfs_window_12d") or {}).get("best") or {}
    for cls, sfx in (("alto_plus", ""), ("critico", "_critical")):
        b = g.get(cls) or {}
        out[f"nisar_agreement_gfswin{sfx}_issued"] = b.get("issued")
        out[f"nisar_agreement_gfswin{sfx}_valid"] = b.get("valid")
        out[f"nisar_agreement_gfswin{sfx}_days_before"] = b.get("issued_days_before")
        out[f"nisar_agreement_gfswin{sfx}_hit_pct"] = (b.get(cls) or {}).get("hit_pct", b.get("hit_pct"))
        out[f"nisar_agreement_gfswin{sfx}_lift"] = (b.get(cls) or {}).get("lift")
    return out


def score_of(w: dict, day: str | None, cls: str) -> dict:
    return ((w.get("per_day") or {}).get(day) or {}).get(cls) or {} if day else {}


def slots(rec: dict) -> dict:
    """lead-submission slots (uy_*): ONE best day = the Critical best day of window_12d (High+ best
    day if nothing reached Critical); High+ and Critical values both read on that same day."""
    k = flat_keys(rec)
    w = rec.get("window_12d") or {}
    b = (w.get("best_day") or {})
    day = (b.get("critico") or {}).get("day") or (b.get("alto_plus") or {}).get("day")
    d = ((w.get("per_day") or {}).get(day) or {}) if day else {}
    hp, cr = d.get("alto_plus") or {}, d.get("critico") or {}
    fc = [k[f"nisar_agreement_fc{L}d_hit_pct"] for L in LEADS]
    return {"label": HEADLINE_LABEL, "uy_pass": rec["pass"]["date"], "uy_track": rec["pass"]["track"],
            "uy_best_day": day,
            "uy_k": (dt.date.fromisoformat(rec["pass"]["date"]) - dt.date.fromisoformat(day)).days if day else None,
            "uy_high": hp.get("hit_pct"), "uy_crit": cr.get("hit_pct"),
            "uy_area": hp.get("area_pct"), "uy_area_crit": cr.get("area_pct"),
            "uy_lift": hp.get("lift"), "uy_lift_crit": cr.get("lift"),
            "uy_m1": hp.get("ratio_vs_terrain"), "uy_m1_crit": cr.get("ratio_vs_terrain"),
            "uy_passday": k["nisar_agreement_obs_hit_pct"], "uy_passday_crit": k["nisar_agreement_obs_critical_hit_pct"],
            "uy_gfs": {f"{L}d": v for L, v in zip(LEADS, fc)},
            "uy_gfswin": {"issued": k["nisar_agreement_gfswin_critical_issued"], "valid": k["nisar_agreement_gfswin_critical_valid"],
                          "hit_crit": k["nisar_agreement_gfswin_critical_hit_pct"], "lift_crit": k["nisar_agreement_gfswin_critical_lift"]},
            "uy_outside_fp": k["nisar_agreement_outside_floodplain_pct"], "uy_water_km2": k["nisar_agreement_water_km2"]}


def _load_wa_obs(cog: Path):
    """WA: observed TPI = bands named YYYYMMDD of wa_2025_tpi_flood_20251210.tif, T =
    wa_2025_t_flood_20251210.tif; permanent water = NaN in the served TPI."""
    import rasterio
    obs = {}
    with rasterio.open(cog / "wa_2025_tpi_flood_20251210.tif") as s:
        for i, d in enumerate(s.descriptions):
            if d and re.fullmatch(r"\d{8}", d):
                obs[d] = s.read(i + 1).astype(np.float64)
        tr, shape = s.transform, s.shape
    with rasterio.open(cog / "wa_2025_t_flood_20251210.tif") as s:
        t = s.read(1).astype(np.float64)
    perm = ~np.isfinite(next(iter(obs.values())))
    return obs, t, tr, shape, perm


def load_case(case: str, root: Path):
    """(frames, grid, obs tpi by ymd, gfs bands by issue ymd) for a replay case."""
    import rasterio
    ix = json.loads((root / case / "png" / "_index.json").read_text())
    frames = nw.hh_frames(ix["layers"])
    cog = root / case / "cog"
    obs, t, tr, shape, perm = {}, None, None, None, None
    if case == "wa_2025":
        obs, t, tr, shape, perm = _load_wa_obs(cog)
    for f in sorted(cog.glob(f"{case}_tpi_flood_obs_*.tif")):
        if not re.fullmatch(rf"{case}_tpi_flood_obs_\d{{8}}", f.stem):   # skip the _6h family
            continue
        with rasterio.open(f) as s:
            d = list(s.descriptions)
            obs[f.stem.split("_")[-1]] = s.read(d.index("tpi") + 1).astype(np.float64)
            if t is None:
                t = s.read(d.index("t") + 1).astype(np.float64)
                tr, shape = s.transform, s.shape
                perm = ~np.isfinite(obs[f.stem.split("_")[-1]])
    gfs = {}
    for f in sorted(cog.glob(f"{case}_tpi_gfs_*.tif")):
        if not re.fullmatch(rf"{case}_tpi_gfs_\d{{8}}", f.stem):   # daily runs only, not tpi_gfs_6h_*
            continue
        with rasterio.open(f) as s:
            d = list(s.descriptions)
            gfs[f.stem.split("_")[-1]] = {n: s.read(i + 1).astype(np.float64)
                                          for i, n in enumerate(d) if n and n.endswith("_flood")}
    grid = {"shape": shape, "transform": tr, "t": t, "perm_water": perm, "km2": cell_km2(tr, shape)}
    return frames, grid, obs, gfs


def run(case: str, root: Path, metrics: Path | None, headline: str | None = None) -> dict:
    from nisar_tpi_replay import floodplain
    frames, grid, obs, gfs = load_case(case, root)
    if not frames:
        return {"status": "pending", "null_reason": "no fixed-stretch HH frames (stretch_db) in the index"}
    fp = floodplain(case, grid["shape"], grid["transform"])
    recs = []
    for fr in frames:
        r = pass_record(fr, root, grid, obs, gfs, fp)
        recs.append(r)
        o = r.get("observed") or {}
        print(fr["date"], fr.get("track"), "water km2", r["water_km2"], "outside_fp", r["outside_floodplain_pct"],
              "obs", {c: o.get(c) for c in CUTS} if "alto_plus" in o else o.get("null_reason", r.get("null_reason")),
              flush=True)
    want = headline or HEADLINE.get(case)
    scored = [r for r in recs if isinstance(r.get("observed"), dict) and "alto_plus" in r["observed"]]
    head = next((r for r in scored if r["pass"]["date"] == want), None) or \
        (max(scored, key=lambda r: r["water_km2"]) if scored else None)
    if head:   # the served map of the pass day = 12-d composite (latest pass per pixel), same threshold/grid
        day = dt.date.fromisoformat(head["pass"]["date"])
        wc, oc, used = nw.water_mask(frames, day, root, grid["shape"], grid["transform"],
                                     perm_water=grid["perm_water"])
        k2 = grid["km2"]
        head["map_12d"] = {
            "what": "water on the served tpi_flood_nisar map of the pass day: latest pass <= 12 d per pixel "
                    "(same HH < -18 dB, same grid, full case box); raised = the HAND <= 5 m floodplain part",
            "passes": [f"{u['date']}/{u.get('track')}" for u in used],
            "water_km2_case_box": round(float(k2[wc].sum()), 2),
            "raised_km2_floodplain": round(float(k2[wc & fp].sum()), 2) if fp is not None else None,
            "observed_km2": round(float(k2[oc].sum()), 1)}
    block = {**(head or {}), "reference": REFERENCE, "definition": DEFINITION, "note": NOTE,
             "headline_rule": f"pass {want} (conductor/lead-analysis)",
             "headline": head, "headline_basis": "window_12d.best_day (critico)",
             "headline_label": HEADLINE_LABEL, "passes": recs, "days": []}
    full = Path.home() / ".cache/lead-nisar" / f"{case}_pass_agreement_full.json"
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(json.dumps(block, indent=1, default=str))
    for r in [block] + recs:                      # metrics.json keeps the best GFS run + the count only
        (r.get("gfs_window_12d") or {}).pop("runs", None)
    if metrics is not None:
        from tp.metrics import merge_metrics
        flat = flat_keys(head) if head else {}
        reasons = {k: "no scored NISAR pass / no GFS run for this lead" for k, v in flat.items() if v is None}
        merge_metrics(case, {"nisar_agreement": block, **flat}, reasons=reasons, path=metrics)
    return block


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--case", required=True)
    ap.add_argument("--data", type=Path, default=REPO / "data")
    ap.add_argument("--headline", default=None, help="pass date YYYY-MM-DD for the headline record")
    ap.add_argument("--no-metrics", action="store_true", help="dry run: print, do not write metrics.json")
    a = ap.parse_args(argv)
    b = run(a.case, a.data, None if a.no_metrics else a.data / "metrics.json", a.headline)
    if b.get("headline"):
        print("SLOTS", json.dumps(slots(b["headline"]), indent=1))
    else:
        print(b)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
