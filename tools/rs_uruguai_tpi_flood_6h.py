"""rs_uruguai_2026 TPI-flood replay at 6-hour steps (observed + issued NOAA GFS).

Same index, grid, terrain, water mask and climatology as the daily replay
(tools/rs_uruguai_tpi_flood_replay.py); only the rain window moves in 6 h steps.

  frame T (00/06/12/18Z) : rain = 72 h ending at T, i.e. (T-72h, T]
    observed   : IMERG Early half-hourly (GPM_3IMERGHHE.07, mm/h x 0.5), the 144
                 half-hours starting in [T-72h, T)
    GFS issue I: half-hours before I 00Z from IMERG Early, then the 00Z run of I's
                 6 h APCP buckets (fh-6, fh] (L28b); lead_h = T - I 00Z = 6..72
  climatology : the 3-day clim_imerg_0p1 of doy (T + 12 h) — T 00Z/06Z use day T,
                12Z/18Z the next day (a 72 h window is the 3-day statistic)
  S           : SMAP L4 rootzone wetness daily mean of (T - 24 h)'s UTC day, held
                daily (observed); I-1 for a GFS issue (as the daily replay)
  datetime    : ISO UTC end of the 72 h window. A 6h frame at X T00Z has the
                same window as daily observed band X; daily GFS d_k date X ~
                6h datetime (X+1) T00Z.

Frames 6 h apart share 66 of their 72 rain hours: the slider is smooth by
construction; it times WHEN the 72 h rain crossed the thresholds (to ~6 h).

Inputs: ~/.cache/lead-analysis/uru-replay/imerg_hh/<YYYYMMDDTHHMM>.npy (mm/h,
[lon, lat asc], box = imerg_e/_meta.npy), GFS from the public NOAA AWS bucket
(live.fetch._gfs_apcp_grid), SMAP via Earth Engine NASA/SMAP/SPL4SMGP/008.

    PYTHONPATH=.:pipeline python tools/rs_uruguai_tpi_flood_6h.py [--no-gfs]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "pipeline"), str(REPO / "tools")]

import rs_uruguai_tpi_flood_replay as base

from live.run_now import now_water_mask
from live.tpi import classify, tpi

CASE = base.CASE
SHAPE, TR = base.SHAPE, base.TR
HH = base.CACHE / "imerg_hh"
STEP = dt.timedelta(hours=6)
WINDOW = dt.timedelta(hours=72)
T0 = dt.datetime(2026, 9, 28, 0)
T1 = dt.datetime(2026, 10, 12, 18)
OBS_T = [T0 + k * STEP for k in range(int((T1 - T0) / STEP) + 1)]          # 60 frames
ISSUES = base.ISSUES                                                         # Sep 28 .. Oct 6
LEADS = list(range(6, 73, 6))
NOTE = ("Frames 6 h apart share 66 of their 72 rain hours, so the map changes smoothly by "
        "construction — the slider shows WHEN the 72 h rain crossed the thresholds (to ~6 h), "
        "not new information. Half-hourly IMERG Early, noisier than daily.")
DATA_BASIS = ("72 h rain from half-hourly IMERG Early (Final not released) vs the IMERG Final "
              "0.1 deg 3-day climatology; SMAP L4 held daily")
GFS_BASIS = DATA_BASIS + "; NOAA GFS 00Z 6 h APCP buckets after the issue (L28b)"


def tag(t: dt.datetime) -> str:
    return f"{t:%Y%m%dT%H}"


def iso(t: dt.datetime) -> str:
    return f"{t:%Y-%m-%dT%H:%M}Z"


def imerg_hh_grid():
    """{start datetime: rain mm (0.5 h) on the replay grid}; regridded per half-hour."""
    i0, _i1, _j0, j1 = np.load(base.IMERG / "_meta.npy")
    tr = from_origin(-180 + i0 * 0.1, -90 + (j1 + 1) * 0.1, 0.1, 0.1)
    raw = {}
    for f in sorted(HH.glob("*.npy")):
        a = np.load(f).astype(np.float64).T[::-1]          # [lon, lat asc] -> north-up
        a[(a < 0) | ~np.isfinite(a)] = np.nan
        raw[dt.datetime.strptime(f.stem, "%Y%m%dT%H%M")] = a * 0.5
    return raw, tr


def window_sum(raw, start: dt.datetime, end: dt.datetime):
    """Sum of half-hours starting in [start, end) on the native 0.1 grid; None if any missing."""
    n = int((end - start) / dt.timedelta(minutes=30))
    keys = [start + k * dt.timedelta(minutes=30) for k in range(n)]
    if not keys:                                            # all-GFS window (lead 72 h)
        return np.zeros(next(iter(raw.values())).shape), []
    if any(k not in raw for k in keys):
        return None, [iso(k) for k in keys if k not in raw]
    return np.sum([raw[k] for k in keys], axis=0), []


GFS_CACHE = base.CACHE / "gfs6h"


def gfs_bucket(issue: dt.date, fh: int):
    """6 h APCP (fh-6, fh] of the 00Z run of issue, regridded; None if absent.
    Cached per (issue, fh) on disk; network errors retried."""
    import time

    from live.fetch import _gfs_apcp_grid
    f = GFS_CACHE / f"{issue:%Y%m%d}_f{fh:03d}.npy"
    if f.exists():
        return np.load(f)
    for attempt in range(5):
        try:
            got = _gfs_apcp_grid(issue, 0, fh)
            break
        except Exception:
            if attempt == 4:
                raise
            time.sleep(5 * (attempt + 1))
    if got is None:
        return None
    arr, lats, lons = got
    w, s, e, n = base.BOUNDS
    la = (lats >= s - 0.75) & (lats <= n + 0.75)
    lo = (lons >= w - 0.75) & (lons <= e + 0.75)
    sub = arr[np.ix_(la, lo)][::-1]                         # north-up
    dlat, dlon = lats[1] - lats[0], lons[1] - lons[0]
    tr = from_origin(lons[lo][0] - dlon / 2, lats[la][-1] + dlat / 2, dlon, dlat)
    out = base.regrid(np.clip(sub, 0, None), tr)
    GFS_CACHE.mkdir(parents=True, exist_ok=True)
    np.save(f, out)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--no-gfs", action="store_true")
    args = ap.parse_args(argv)
    from tp.bundle import _class_png_3857

    out_cog, out_png = REPO / "data" / CASE / "cog", REPO / "data" / CASE / "png"
    out_cog.mkdir(parents=True, exist_ok=True)
    t_slide, t_flood, hand = base.terrain()
    water, wbasis = now_water_mask(base.BOUNDS, *SHAPE, occ_path=base.GSW)
    water = np.zeros(SHAPE, bool) if water is None else water
    corridor = (np.nan_to_num(hand, nan=99) <= 5.0) & ~water
    lat_c = base.LATS[:, None] * np.ones((1, SHAPE[1]))
    km2 = (TR.a * 111.32) * (abs(TR.e) * 110.574) * np.cos(np.deg2rad(lat_c))
    raw, rtr = imerg_hh_grid()

    sm_days = sorted({(t - dt.timedelta(hours=24)).date() for t in OBS_T}
                     | {i - dt.timedelta(days=1) for i in ISSUES})
    sm = base.smap_days(sm_days)
    clim_cache = {}

    def clim(t):
        d = (t + dt.timedelta(hours=12)).date()
        if d not in clim_cache:
            clim_cache[d] = base.clim(d)
        return clim_cache[d]

    def frame(p72, s, t):
        o = tpi(p72, s, clim(t), t_slide, t_flood)
        tf = np.where(water | ~(np.isfinite(p72) & np.isfinite(s)), np.nan, o["tpi_flood"])
        return tf, classify(tf), o["r"]

    def stats(tf, cls):
        fin = np.isfinite(tf)
        cor = corridor & fin
        return {"critical_km2": round(float(km2[(cls >= 3) & fin].sum()), 2),
                "alert_km2": round(float(km2[(cls >= 2) & fin].sum()), 2),
                "corridor_max_class": int(cls[cor].max()) if cor.any() else None,
                "corridor_critical_km2": round(float(km2[(cls >= 3) & cor].sum()), 2)}

    def write_png(cls, tf, name):
        png, bounds = _class_png_3857(cls, np.isfinite(tf), TR, "EPSG:4326")
        (out_png / name).write_bytes(png)
        return bounds

    def write_cog(path, named):
        prof = {"driver": "GTiff", "height": SHAPE[0], "width": SHAPE[1], "count": len(named),
                    "dtype": "float32", "crs": "EPSG:4326", "transform": TR, "nodata": np.nan,
                    "compress": "deflate",
                    "predictor": 3, "zlevel": 9, "tiled": True, "blockxsize": 256, "blockysize": 256}
        with rasterio.open(path, "w", **prof) as dst:
            for i, (n, a) in enumerate(named, 1):
                dst.write(np.round(np.asarray(a, np.float32), 3), i)   # 0.001 is enough; keeps COGs small
                dst.set_band_description(i, n)

    rows, layers, missing = [], [], {}
    common = {"step_h": 6, "window_h": 72, "note": NOTE,
              "legend": ["Baixo", "Moderado", "Alto", "Crítico"]}
    for t in OBS_T:
        p, miss = window_sum(raw, t - WINDOW, t)
        if p is None:
            missing[iso(t)] = miss[:4] + (["…"] if len(miss) > 4 else [])
            continue
        p72 = base.regrid(p, rtr)
        s = sm.get((t - dt.timedelta(hours=24)).date())
        tf, cls, r = frame(p72, s, t)
        cog = out_cog / f"{CASE}_tpi_flood_6h_{tag(t)}.tif"
        write_cog(cog, (("tpi", tf), ("r", r), ("s", np.where(np.isfinite(tf), s, np.nan)),
                        ("t", t_flood)))
        bounds = write_png(cls, tf, f"tpi_flood_6h_{tag(t)}.png")
        st = stats(tf, cls)
        rows.append({"datetime": iso(t), "kind": "observed",
                     "p72_corridor_mm": round(float(np.nanmean(p72[corridor])), 1), **st})
        layers.append({"id": "tpi_flood_6h", "date": t.date().isoformat(), "datetime": iso(t),
                       "png": f"{CASE}/png/tpi_flood_6h_{tag(t)}.png", "cog": f"{CASE}/cog/{cog.name}",
                       "bounds": bounds, "forecast": False, "kind": "observed",
                       "critical_km2": st["critical_km2"], "data_basis": DATA_BASIS, **common})

    for issue in ([] if args.no_gfs else ISSUES):
        i0 = dt.datetime.combine(issue, dt.time(0))
        buckets = {fh: gfs_bucket(issue, fh) for fh in LEADS}
        s = sm.get(issue - dt.timedelta(days=1))
        bands = []
        for lead in LEADS:
            t = i0 + dt.timedelta(hours=lead)
            obs, miss = window_sum(raw, t - WINDOW, i0)
            fc = [buckets[fh] for fh in LEADS if fh <= lead]
            if obs is None or any(b is None for b in fc):
                missing[f"gfs {issue} +{lead}h"] = miss[:4] or ["gfs bucket missing"]
                continue
            p72 = base.regrid(obs, rtr) + np.sum(fc, axis=0)
            tf, cls, r = frame(p72, s, t)
            bands += [(f"h{lead:03d}_flood", tf), (f"h{lead:03d}_r", r)]
            pn = f"tpi_flood_gfs_6h_{issue:%Y%m%d}_{tag(t)}.png"
            bounds = write_png(cls, tf, pn)
            st = stats(tf, cls)
            rows.append({"datetime": iso(t), "kind": "gfs", "issue": issue.isoformat(), "lead_h": lead,
                         "p72_corridor_mm": round(float(np.nanmean(p72[corridor])), 1), **st})
            layers.append({"id": "tpi_flood_gfs_6h", "date": t.date().isoformat(), "datetime": iso(t),
                           "issued": issue.isoformat(), "lead_h": lead,
                           "png": f"{CASE}/png/{pn}",
                           "cog": f"{CASE}/cog/{CASE}_tpi_gfs_6h_{issue:%Y%m%d}.tif",
                           "bounds": bounds, "forecast": True, "forecast_source": "noaa-gfs",
                           "kind": "forecast", "critical_km2": st["critical_km2"],
                           "data_basis": GFS_BASIS, **common})
        if bands:
            write_cog(out_cog / f"{CASE}_tpi_gfs_6h_{issue:%Y%m%d}.tif", bands)

    res = {"rows": rows, "layers": layers, "missing": missing, "water_mask": wbasis}
    (base.CACHE / "replay_6h_result.json").write_text(json.dumps(res, indent=1, default=str))
    for r in rows:
        print(r["kind"], r.get("issue", ""), r["datetime"], "corridor class", r["corridor_max_class"],
              "P72", r["p72_corridor_mm"], "crit", r["critical_km2"], "corridor crit", r["corridor_critical_km2"])
    print("frames", len(layers), "missing", len(missing))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
