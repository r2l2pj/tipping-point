"""Would our forecast have warned Uruguaiana before the Oct 2026 Uruguay River flood?

WHEN: basin-mean rain over the Uruguay River basin upstream of the Uruguaiana gauge
(HydroBASINS v1c lev07 upstream union, 192,578 km2; 0.5 deg lattice, 73 points).
For every NOAA GFS 00Z issue D (Sep 18 - Nov 12 2026), the 3-day and 7-day basin totals
ending on D..D+6 are built from observed IMERG Early (days < D) + the GFS run of D
(days >= D) and compared with the season (Sep-Nov) climatology percentiles of the same
totals from NASA POWER IMERG Final daily 2001-2025 at the same lattice. A flag
(>= p95 / >= p99) is a "river-rise warning". Truth: ANA telemetry daily median level at
Uruguaiana (77150000) and Itaqui (75900000); flood stage 8.50 m.

WHERE / WHO: the forecast-flagged river corridor (HAND <= threshold inside the AOI, active
when the upstream flag is on) vs the radar new-water candidate polygons at Uruguaiana:
share of candidate area and of people inside it covered, corridor share of the AOI, lift,
people inside the corridor (GPW v4.11 2020).

    PYTHONPATH=pipeline:. python tools/uruguai_forecast_proof.py --dir ~/.cache/lead-ml/uru
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

GAUGES = {"uruguaiana": "77150000", "itaqui": "75900000"}
FLOOD_STAGE_CM = 850.0
GFS_BBOX = (-58.0, -32.0, -49.0, -26.0)
ISSUES = pd.date_range("2026-09-18", "2026-11-12")
OBS_START = pd.Timestamp("2026-09-01")
HIT_WINDOWS = (10, 14)
AOI = (-57.25, -29.85, -56.45, -29.05)
HAND_LEVELS = (5.0, 10.0)
RELEASE = "https://github.com/r2l2pj/tipping-point-data/releases/download/static-v1"


def truth(d: Path) -> dict:
    out = {}
    for name, st in GAUGES.items():
        med = json.loads((d / f"daily_med_{st}.json").read_text())
        mx = json.loads((d / f"daily_max_{st}.json").read_text())
        days = sorted(med)
        runs, cur = [], None
        for k in days:
            if med[k] >= FLOOD_STAGE_CM:
                cur = [k, k] if cur is None else [cur[0], k]
            elif cur:
                runs.append(cur)
                cur = None
        if cur:
            runs.append(cur)
        crest = max(med, key=med.get)
        out[name] = {"station": st, "flood_stage_m": FLOOD_STAGE_CM / 100,
                     "crossings": [{"from": a, "to": b,
                                    "peak_median_m": round(max(med[x] for x in days if a <= x <= b) / 100, 2)}
                                   for a, b in runs],
                     "crest": {"date": crest, "median_m": round(med[crest] / 100, 2),
                               "max_m": round(max(mx.values()) / 100, 2)},
                     "daily_median_m": {k: round(med[k] / 100, 3) for k in days}}
    return out


def basin_series(d: Path):
    from ml.local import sources_dynamic as D
    pts = np.array(json.loads((d / "lattice.json").read_text()))
    lon, lat = pts[:, 0], pts[:, 1]
    pr = D.PowerRain.load()
    clim_days = pd.date_range("2001-01-01", "2025-12-31")
    mat = np.stack([pr.daily(lon, lat, np.full(len(lon), np.datetime64(x.date()))) for x in clim_days])
    clim = pd.Series(np.nanmean(mat, axis=1), index=clim_days)
    ie = D.ImergDaily.load()
    obs_days = pd.date_range(OBS_START, "2026-11-14")
    early = pd.Series([np.nanmean(ie.daily(lon, lat, np.full(len(lon), np.datetime64(x.date()))))
                       for x in obs_days], index=obs_days)
    # Early vs Final on the overlap cached for both (2001-2003 Early days, if any)
    ov = [x for x in ie.dates if pd.Timestamp(x) in clim.index and pd.Timestamp(x).year <= 2003]
    bias = None
    if ov:
        e = np.array([np.nanmean(ie.daily(lon, lat, np.full(len(lon), x))) for x in ov])
        f = clim.loc[pd.to_datetime(ov)].to_numpy()
        ok = np.isfinite(e) & np.isfinite(f)
        bias = {"days": int(ok.sum()), "early_over_final_mean": float(e[ok].mean() / f[ok].mean()),
                "corr": float(np.corrcoef(e[ok], f[ok])[0, 1])}
    gfs = {}
    for issue in ISSUES:
        fc = D.gfs_daily(lon, lat, np.full(len(lon), np.datetime64(issue.date())), 7, bbox=GFS_BBOX)
        gfs[issue] = np.nanmean(fc, axis=0) if np.isfinite(fc).any() else np.full(7, np.nan)
    return clim, early, gfs, bias


def thresholds(clim: pd.Series) -> dict:
    out = {}
    for n in (3, 7):
        s = clim.rolling(n).sum()
        season = s[(s.index.month >= 9) & (s.index.month <= 11)].dropna()
        out[n] = {"p90": float(np.percentile(season, 90)), "p95": float(np.percentile(season, 95)),
                  "p99": float(np.percentile(season, 99)), "n_days": len(season)}
    return out


def indicators(early: pd.Series, gfs: dict, thr: dict) -> pd.DataFrame:
    rows = []
    for issue, fc in gfs.items():
        days = pd.date_range(issue - pd.Timedelta(days=9), issue + pd.Timedelta(days=6))
        ser = pd.Series([early.get(x, np.nan) if x < issue else fc[(x - issue).days] for x in days],
                        index=days)
        r = {"issue": issue.date().isoformat(), "gfs_ok": bool(np.isfinite(fc).all())}
        for n in (3, 7):
            tot = ser.rolling(n).sum()[issue:issue + pd.Timedelta(days=6)]
            r[f"max{n}d_mm"] = float(tot.max())
            r[f"max{n}d_end"] = tot.idxmax().date().isoformat() if tot.notna().any() else None
            r[f"pct{n}d"] = ("p99" if tot.max() >= thr[n]["p99"] else "p95" if tot.max() >= thr[n]["p95"]
                             else "p90" if tot.max() >= thr[n]["p90"] else "")
        r["flag_p95"] = r["pct3d"] in ("p95", "p99") or r["pct7d"] in ("p95", "p99")
        r["flag_p99"] = r["pct3d"] == "p99" or r["pct7d"] == "p99"
        rows.append(r)
    return pd.DataFrame(rows)


def score(ind: pd.DataFrame, tr: dict, impacts: dict) -> dict:
    out = {}
    cross = [pd.Timestamp(c["from"]) for c in tr["uruguaiana"]["crossings"]]
    targets = {f"uruguaiana_flood_stage_{c.date()}": c for c in cross}
    targets.update({k: pd.Timestamp(v) for k, v in impacts.items()})
    issues = pd.to_datetime(ind.issue)
    for lvl in ("flag_p95", "flag_p99"):
        f = ind[lvl].to_numpy() & ind.gfs_ok.to_numpy()
        res = {}
        for name, t in targets.items():
            prev = [c for c in cross if c < t]
            start = max([t - pd.Timedelta(days=21)] + [p + pd.Timedelta(days=3) for p in prev])
            win = (issues >= start) & (issues < t) & f
            first = issues[win].min() if win.any() else None
            res[name] = {"target": t.date().isoformat(),
                         "first_flag_issue": first.date().isoformat() if first is not None else None,
                         "lead_days": int((t - first).days) if first is not None else None}
        fa = {}
        for w in HIT_WINDOWS:
            hit = np.array([any(0 < (c - i).days <= w for c in cross) for i in issues])
            n_flag = int(f.sum())
            fa[f"within_{w}d"] = {"flagged_issues": n_flag, "followed_by_crossing": int((f & hit).sum()),
                                  "false_alarm_issues": int((f & ~hit).sum()),
                                  "false_alarm_dates": [x.date().isoformat() for x in issues[f & ~hit]]}
        out[lvl] = {"targets": res, "false_alarms": fa}
    return out


def where_test(d: Path, nisar_geojson: Path) -> dict:
    import geopandas as gpd
    import rasterio
    from rasterio.mask import geometry_mask
    from rasterio.windows import from_bounds

    polys = gpd.read_file(nisar_geojson).to_crs(4326)
    res = {"nisar_candidates": len(polys), "nisar_area_km2": None, "levels": {}}
    with rasterio.open(f"/vsicurl/{RELEASE}/hand_90m.tif") as src:
        win = from_bounds(*AOI, transform=src.transform).round_offsets().round_lengths()
        hand = src.read(1, window=win, masked=True).filled(np.nan).astype(float)
        tr = src.window_transform(win)
    with rasterio.open(f"/vsicurl/{RELEASE}/gpw_popcount_2020_30sec.tif") as g:
        gw = from_bounds(*AOI, transform=g.transform).round_offsets().round_lengths()
        pop = g.read(1, window=gw, masked=True).filled(0).astype(float)
        gtr = g.window_transform(gw)
    # people per HAND cell: resample GPW (30") to the HAND grid by nearest + area share
    from rasterio.warp import Resampling, reproject
    pop_h = np.zeros_like(hand)
    reproject(pop, pop_h, src_transform=gtr, src_crs="EPSG:4326", dst_transform=tr,
              dst_crs="EPSG:4326", resampling=Resampling.average)
    ratio = (abs(tr.a) * abs(tr.e)) / (abs(gtr.a) * abs(gtr.e))
    pop_h *= ratio
    inside = ~geometry_mask(polys.geometry, out_shape=hand.shape, transform=tr, all_touched=False)
    valid = np.isfinite(hand)
    lat_c = AOI[1] + (AOI[3] - AOI[1]) / 2
    px_km2 = abs(tr.a) * 111.32 * np.cos(np.deg2rad(lat_c)) * abs(tr.e) * 110.57
    res["nisar_area_km2_on_grid"] = round(float(inside.sum() * px_km2), 3)
    res["people_in_nisar_on_grid"] = round(float(pop_h[inside].sum()), 1)
    for h in HAND_LEVELS:
        flag = valid & (hand <= h)
        hit = float((flag & inside).sum() / max(inside.sum(), 1))
        share = float(flag.sum() / max(valid.sum(), 1))
        res["levels"][f"hand_le_{h:g}m"] = {
            "share_of_nisar_area_flagged": round(hit, 3),
            "share_of_people_in_nisar_flagged": round(float(pop_h[flag & inside].sum() / max(pop_h[inside].sum(), 1e-9)), 3),
            "flagged_share_of_aoi": round(share, 3),
            "lift_vs_random": round(hit / share, 2) if share > 0 else None,
            "people_in_flagged_corridor": round(float(pop_h[flag].sum()), 0),
            "flagged_km2": round(float(flag.sum() * px_km2), 1),
        }
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", type=Path, required=True)
    ap.add_argument("--nisar", type=Path, required=True)
    a = ap.parse_args(argv)
    tr = truth(a.dir)
    clim, early, gfs, bias = basin_series(a.dir)
    thr = thresholds(clim)
    ind = indicators(early, gfs, thr)
    impacts = {"itaqui_displaced_2026-10-08": "2026-10-08"}
    sc = score(ind, tr, impacts)
    wt = where_test(a.dir, a.nisar)
    doc = {"case": "rs_uruguai_2026",
           "basin": {"name": "Uruguay River upstream of Uruguaiana gauge (HydroBASINS v1c lev07)",
                     "area_km2": 192578, "lattice_points": len(json.loads((a.dir / "lattice.json").read_text()))},
           "rain_basis": "observed IMERG Early (GPM_3IMERGDE V07; Final not yet released for Sep-Nov 2026) + "
                         "NOAA GFS 0.25 deg 00Z; climatology = NASA POWER IMERG Final daily 2001-2025, Sep-Nov",
           "early_vs_final": bias, "thresholds_mm": thr, "truth": tr, "impacts": impacts,
           "issues": ind.to_dict("records"), "score": sc, "where": wt,
           "basin_obs_early_mm": {k.date().isoformat(): (None if not np.isfinite(v) else round(float(v), 2))
                                  for k, v in early.items()}}
    (a.dir / "uruguai_forecast_proof.json").write_text(json.dumps(doc, indent=1, default=str))
    print(json.dumps({"thresholds": thr, "score": sc, "where": wt, "early_vs_final": bias}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
