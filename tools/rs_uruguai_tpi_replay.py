#!/usr/bin/env python3
"""rs_uruguai_2026 TPI replay (observed + GFS wiring), case bbox Uruguay corridor.

Builds the app's TPI frames for the Uruguaiana/Itaqui episode from the
repository's own primitives — same math as live.tpi / .herdr/gfs_replay_run.py:

  rain3  = IMERG Early/Late daily mm (pipeline.fetch.imerg.fetch_daily),
           rolling 3-day sum on the 0.1-degree IMERG grid;
  R      = live.tpi.rain_score(rain3, clim p90/p95/p99) where clim comes from
           data/static/south_brazil/clim/doy_<DOY>.tif (per-day-of-year rain3
           percentile climatology, same south_brazil statics the rs_2024 path
           uses — doy files exist only for DOY 284-341, so scored days start
           2026-10-11);
  S      = live.tpi.soil_score(SMAP SPL4SMGP sm_rootzone_wetness), latest
           granule per day via earthaccess (EARTHDATA_TOKEN env login);
  T      = live.tpi.terrain_slide(slope) / terrain_flood(HAND, slope) from
           data/static/south_brazil/terrain/{slope_deg,hand_m}.tif;
  TPI    = R^wR * S^wS * T^wT (config thresholds.tpi_weights), classified with
           live.tpi.classify -> per-day PNG tpi_{slide,flood}_<YYYYMMDD>.png,
           per-day 2-band COG <case>_tpi_<YYYYMMDD>.tif, and _index.json
           entries {id, date, png, cog, bounds, forecast: false};
  series = data/rs_uruguai_2026/series/imerg_smap_daily.json
           [{date, imerg_mm, rain3_mm, smap, r_mean, s_mean, tpi_slide_mean,
             tpi_flood_mean, cls_slide_pct, cls_flood_pct}] — same shape as
           data/wa_2025/series/imerg_smap_daily.json.

GFS forecast-rain cubes (data/<case>/forecast_rain/) are produced separately
by tools/regen_gfs_replay.py (WINDOWS entry added for this case); scored
d1-d3 forecast frames need per-issue climatology — run .herdr/gfs_replay_run.py
which now supports a `clim_dir` of doy files.

data_basis: IMERG Early (GPM_3IMERGDE V07 daily; Late half-hourly tail if a
day lacks a daily granule), not Final — Final for Sep-Nov 2026 is not yet
published, so rain_score vs climatology carries the known Early bias.
Days before 2026-10-11 are imerg/smap-only (no local doy climatology) — the
frames are still emitted with R set from a NaN clim -> classify all-zero;
those days are marked scored:false in the series json.

Usage:
  PYTHONPATH=.:pipeline python tools/rs_uruguai_tpi_replay.py [--skip-smap]
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject

REPO = Path(__file__).resolve().parent.parent
CASE = "rs_uruguai_2026"
BOUNDS = (-57.25, -29.85, -56.45, -29.05)
WINDOW_LO = dt.date(2026, 9, 18)
WINDOW_HI = dt.date(2026, 11, 15)
CLIM_DIR = REPO / "data/static/south_brazil/clim"
TERRAIN_DIR = REPO / "data/static/south_brazil/terrain"
CACHE = Path.home() / ".cache/lead-analysis/uruguai"
SMAP_CACHE = CACHE / "smap"
CASE_DIR = REPO / "data" / CASE

sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "pipeline"))


def imerg_daily():
    """IMERG daily mm on a canonical 0.1-degree grid over the window.

    fetch_daily() fails here: individual granule crops land on slightly
    different 0.1-degree edge cells (8x9 vs 9x8) so xr.concat join='exact'
    raises. Reindex every increment onto the canonical bounds grid first.
    """
    import xarray as xr
    from pipeline.fetch import imerg
    days = (WINDOW_HI - WINDOW_LO).days + 3      # D-2 lead-in for rain3
    incs = imerg.fetch_increments(BOUNDS, WINDOW_HI, days, CACHE / "imerg")
    w, s, e, n = BOUNDS
    lats = np.round(np.arange(s, n + 1e-9, 0.1), 6)
    lons = np.round(np.arange(w, e + 1e-9, 0.1), 6)
    norm = []
    for da in incs:
        la = next(c for c in da.coords if "lat" in c.lower())
        lo = next(c for c in da.coords if "lon" in c.lower())
        da = da.rename({la: "lat", lo: "lon"})
        da = da.assign_coords(lat=np.round(da["lat"].astype(float), 6),
                              lon=np.round(da["lon"].astype(float), 6))
        norm.append(da.reindex(lat=lats, lon=lons))
    inc = xr.concat(norm, dim="time", join="outer").sortby("time")
    daily, _rain3, _steps = imerg._accumulate(inc)
    times = list(pd.DatetimeIndex(pd.to_datetime(daily["time"].values)).date)
    return (daily.values.astype(np.float64), times,
            daily["lat"].values, daily["lon"].values)


def grid_transform(lats, lons):
    yres = float(abs(np.median(np.diff(lats))))
    xres = float(abs(np.median(np.diff(lons))))
    return from_origin(float(lons[0] - xres / 2),
                       float(max(lats[0], lats[-1]) + yres / 2), xres, yres)


def to_grid(src_path, band, lats, lons):
    """Reproject one band of a south_brazil static onto the IMERG grid."""
    h, w = len(lats), len(lons)
    dst = np.full((h, w), np.nan)
    with rasterio.open(src_path) as src:
        reproject(src.read(band).astype(np.float64), dst,
                  src_transform=src.transform, src_crs=src.crs,
                  src_nodata=np.nan, dst_transform=grid_transform(lats, lons),
                  dst_crs="EPSG:4326", dst_nodata=np.nan,
                  resampling=Resampling.bilinear)
    return dst if lats[0] > lats[-1] else dst[::-1]


def clim_for(day, lats, lons):
    """p90/p95/p99 rain3 climatology for `day` from the doy stack (or None)."""
    p = CLIM_DIR / f"doy_{day.timetuple().tm_yday}.tif"
    if not p.exists():
        return None
    return {k: to_grid(p, i + 1, lats, lons)
            for i, k in enumerate(("p90", "p95", "p99"))}


def terrain(lats, lons):
    from live.tpi import terrain_flood, terrain_slide
    slope = to_grid(TERRAIN_DIR / "slope_deg.tif", 1, lats, lons)
    hand = to_grid(TERRAIN_DIR / "hand_m.tif", 1, lats, lons)
    return terrain_slide(slope), terrain_flood(hand, slope)


def smap_for(day, lats, lons):
    """Latest SPL4SMGP rootzone wetness on `day`, regridded; npy-cached."""
    cache = SMAP_CACHE / f"sm_{day:%Y%m%d}.npy"
    if cache.exists():
        return (np.load(cache), np.load(cache.with_suffix(".lats.npy")),
                np.load(cache.with_suffix(".lons.npy")))
    from pipeline.fetch import smap
    gs = smap.search_granules(BOUNDS, day + dt.timedelta(days=1), days_back=2)
    gs = [g for g in gs if smap.product_date(g, day) == day]
    if not gs:
        return None
    files = smap.download_granules([gs[-1]], SMAP_CACHE / "h5")
    glats, glons, vals = smap.open_rootzone(files[-1], BOUNDS)
    sm_lats, sm_lons, sm = smap.grid_to_regular(glats, glons, vals, BOUNDS,
                                              res=0.1)
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.save(cache.with_suffix(".lats.npy"), sm_lats)
    np.save(cache.with_suffix(".lons.npy"), sm_lons)
    np.save(cache, sm)
    return sm, sm_lats, sm_lons


def _align_sm(sm, sm_lats, sm_lons, lats, lons):
    """grid_to_regular emits a regular 0.1-degree grid over `bounds`
    (lats ascending) — reindex onto the IMERG lat/lon axes by rounding."""
    ilat = {round(float(v), 6): i for i, v in enumerate(sm_lats)}
    ilon = {round(float(v), 6): i for i, v in enumerate(sm_lons)}
    out = np.full((len(lats), len(lons)), np.nan)
    for i, la in enumerate(lats):
        j = ilat.get(round(float(la), 6))
        if j is None:
            continue
        for k, lo in enumerate(lons):
            m = ilon.get(round(float(lo), 6))
            if m is not None:
                out[i, k] = sm[j, m]
    return out


def write_day_cog(path, slide_cls, flood_cls, lats, lons):
    from rasterio.crs import CRS
    path.parent.mkdir(parents=True, exist_ok=True)
    north_up = lats[0] > lats[-1]
    tr = grid_transform(lats, lons)
    bands = [slide_cls if north_up else slide_cls[::-1],
             flood_cls if north_up else flood_cls[::-1]]
    with rasterio.open(
            path, "w", driver="GTiff", height=len(lats), width=len(lons),
            count=2, dtype="uint8", crs=CRS.from_epsg(4326), transform=tr,
            tiled=True, compress="deflate") as dst:
        dst.write(bands[0], 1)
        dst.write(bands[1], 2)
        dst.set_band_description(1, "tpi_slide class 0-3")
        dst.set_band_description(2, "tpi_flood class 0-3")


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-smap", action="store_true",
                    help="emit frames with s=NaN (unscored sm) — testing")
    args = ap.parse_args()

    from live.tpi import classify, rain_score, soil_score, tpi
    from tp.bundle import _class_png_3857

    daily, times, lats, lons = imerg_daily()
    lats, lons = np.asarray(lats), np.asarray(lons)
    print(f"IMERG: {times[0]} -> {times[-1]} ({len(times)} days, "
          f"grid {len(lats)}x{len(lons)})", flush=True)
    t_slide, t_flood = terrain(lats, lons)
    print("terrain ready", flush=True)

    idx_of = {d: i for i, d in enumerate(times)}
    png_dir = CASE_DIR / "png"
    cog_dir = CASE_DIR / "cog"
    png_dir.mkdir(parents=True, exist_ok=True)
    cog_dir.mkdir(parents=True, exist_ok=True)
    index_path = png_dir / "_index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else []
    have = {(e.get("id"), e.get("date")) for e in index}
    wb = [float(BOUNDS[0]), float(BOUNDS[1]), float(BOUNDS[2]), float(BOUNDS[3])]
    series = []

    day = WINDOW_LO
    while day <= WINDOW_HI:
        i = idx_of.get(day)
        if i is None or i < 2:
            day += dt.timedelta(days=1)
            continue
        rain3 = np.nansum(daily[i - 2:i + 1], axis=0)
        clim = clim_for(day, lats, lons)
        if clim is None:
            # no doy climatology (local stack covers DOY 284-341 only) ->
            # no honest rain_score -> skip the day rather than emit an
            # empty frame
            series.append({"date": day.isoformat(), "scored": False,
                           "reason": "no doy climatology "
                                     "(local stack covers DOY 284-341)",
                           "imerg_mm": round(float(np.nanmean(daily[i])), 3)})
            day += dt.timedelta(days=1)
            continue
        sm = None if args.skip_smap else smap_for(day, lats, lons)
        sm_g = _align_sm(*sm, lats, lons) if sm is not None else \
            np.full(rain3.shape, np.nan)
        s = soil_score(sm_g)
        r = rain_score(rain3, clim["p90"], clim["p95"], clim["p99"])
        valid = np.isfinite(rain3)
        wr, ws, wt = 1 / 3, 1 / 3, 1 / 3
        tpi_s = np.where(np.isfinite(r) & np.isfinite(s),
                         r ** wr * s ** ws * t_slide ** wt, np.nan)
        tpi_f = np.where(np.isfinite(r) & np.isfinite(s),
                         r ** wr * s ** ws * t_flood ** wt, np.nan)
        cs, cf = classify(np.where(valid, tpi_s, np.nan)), \
            classify(np.where(valid, tpi_f, np.nan))
        north_up = lats[0] > lats[-1]
        for haz, cls in (("slide", cs), ("flood", cf)):
            png_name = f"tpi_{haz}_{day:%Y%m%d}.png"
            arr = cls if north_up else cls[::-1]
            png, _pwb = _class_png_3857(
                arr, np.isfinite(arr.astype(float)),
                grid_transform(lats, lons),
                rasterio.crs.CRS.from_epsg(4326))
            (png_dir / png_name).write_bytes(png)
            cog_rel = f"{CASE}/cog/{CASE}_tpi_{day:%Y%m%d}.tif"
            if (f"tpi_{haz}", day.isoformat()) not in have:
                index.append({"id": f"tpi_{haz}", "date": day.isoformat(),
                              "png": f"{CASE}/png/{png_name}", "cog": cog_rel,
                              "bounds": wb, "forecast": False})
        write_day_cog(cog_dir / f"{CASE}_tpi_{day:%Y%m%d}.tif",
                      cs, cf, lats, lons)
        series.append({
            "date": day.isoformat(),
            "imerg_mm": round(float(np.nanmean(daily[i])), 3),
            "rain3_mm": round(float(np.nanmean(rain3)), 3),
            "smap": (round(float(np.nanmean(sm_g)), 4)
                     if np.isfinite(sm_g).any() else None),
            "scored": True,
            "r_mean": round(float(np.nanmean(r)), 3),
            "s_mean": round(float(np.nanmean(s)), 3)
            if np.isfinite(sm_g).any() else None,
            "cls_slide_pct": round(float((cs > 0).mean() * 100), 2),
            "cls_flood_pct": round(float((cf > 0).mean() * 100), 2),
        })
        print(day, "scored" if clim else "imerg-only",
              f"rain3={np.nanmean(rain3):.1f}mm "
              f"slide%={(cs > 0).mean() * 100:.1f} "
              f"flood%={(cf > 0).mean() * 100:.1f}", flush=True)
        day += dt.timedelta(days=1)

    index_path.write_text(json.dumps(index, indent=2) + "\n")
    sdir = CASE_DIR / "series"
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "imerg_smap_daily.json").write_text(
        json.dumps({"case": CASE, "bounds": list(BOUNDS),
                    "window": [WINDOW_LO.isoformat(), WINDOW_HI.isoformat()],
                    "data_basis": "IMERG Early daily (GPM_3IMERGDE V07); "
                                  "SMAP SPL4SMGP rootzone; south_brazil doy "
                                  "climatology (DOY 284-341 only)",
                    "days": series}, indent=2) + "\n")
    print(f"index +{len(series) * 2} entries; series -> "
          f"{sdir / 'imerg_smap_daily.json'}", flush=True)


if __name__ == "__main__":
    main()
