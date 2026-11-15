"""One-off (A5): as-available lead-time TPI series for rs_2024.

Rebuilds the daily TPI series over the case window 2024-04-22..2024-05-07
using ONLY inputs that would have existed at each day D:

* R: 3-day accumulated IMERG *Early* (GPM_3IMERGHHE half-hourly, ~4 h
  latency; daily GPM_3IMERGDE fills older days) via
  pipeline/fetch/imerg.py fetch_daily, normalized by the same p90/p95/p99
  3-day-rain climatology basis as the published series (GEE clim asset
  rs_2024_clim_rain3_2001_2023 — climatology is a static historic basis,
  available at every D).
* S: SMAP L4 SPL4SMGP with latency awareness — at day D the series uses
  granules valid on D-3 (GEE NASA/SMAP/SPL4SMGP/008 mean over
  filterDate(D-3, D-2)), approximating the last granule published by
  D given the ~2-3 day L4 latency.
* T: static — the existing 90 m T_slide/T_flood COGs, reused as-is.

R/S are resampled UP to the 90 m NASADEM grid and combined with the same
_combine_case_tpi geomean as the published series (doc rule 3).

Outputs:
  data/rs_2024/cog/rs_2024_tpi_asavail_20240502.tif        (TPI_slide, bands YYYYMMDD)
  data/rs_2024/cog/rs_2024_tpi_asavail_flood_20240502.tif  (TPI_flood, bands YYYYMMDD)
  data/rs_2024/series/asavail_tpi_daily.json
  data/rs_2024/lead_time_asavail.json                      (same flat keys as lead_time_metrics.json)

    PYTHONPATH=.:pipeline .venv/bin/python .herdr/asavail_run.py
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

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "pipeline"))

import ee  # noqa: E402
from gee import common  # noqa: E402
from gee import tpi as gee_tpi  # noqa: E402
from ml.lead_time import (cluster_lead_times, damage_time,  # noqa: E402
                          first_crossing_frame, frame_times_from_bands,
                          read_tpi_series)
from pipeline.fetch import imerg  # noqa: E402
from tp.config import thresholds  # noqa: E402
from tp.fallback import _download, _write_cog  # noqa: E402

CASE = "rs_2024"
BOUNDS = (-52.5, -30.5, -50.5, -29.0)
DATES = [(dt.date(2024, 4, 22) + dt.timedelta(days=i)).isoformat()
         for i in range(16)]                      # 2024-04-22 .. 2024-05-07
SMAP_LAG_DAYS = 3        # L4 ~2-3 d latency -> at day D use granules valid D-3
CRITICAL = 0.6
WORK = REPO / "intermediate" / "asavail"
COG_DIR = REPO / "data/rs_2024/cog"
SLIDE_COG = COG_DIR / "rs_2024_tpi_asavail_20240502.tif"
FLOOD_COG = COG_DIR / "rs_2024_tpi_asavail_flood_20240502.tif"
MAX_BYTES = 32 * 1024 * 1024


def rain_score_np(rain3, p90, p95, p99):
    """numpy port of gee.tpi.rain_score — later .where() wins."""
    r = np.zeros_like(rain3)
    r = np.where(rain3 >= p90, (rain3 - p90) / (p95 - p90) * 0.5, r)
    r = np.where(rain3 >= p95, (rain3 - p95) / (p99 - p95) * 0.5 + 0.5, r)
    r = np.where(rain3 >= p99, 1.0, r)
    return np.where(np.isfinite(rain3), np.clip(r, 0, 1), np.nan)


def grid_transform(lats, lons):
    """North-up affine for a regular grid given cell-center coords."""
    lats = np.asarray(lats, dtype=np.float64)
    lons = np.asarray(lons, dtype=np.float64)
    yres = float(abs(np.median(np.diff(lats))))
    xres = float(abs(np.median(np.diff(lons))))
    return (from_origin(float(lons[0] - xres / 2),
                        float(max(lats[0], lats[-1]) + yres / 2), xres, yres),
            xres, yres)


def write_series_cog(path, cube, lats, lons, names):
    """Multiband float32 north-up EPSG:4326 COG with YYYYMMDD band names."""
    arr = np.asarray(cube, dtype=np.float32)
    lats = np.asarray(lats, dtype=np.float64)
    lons = np.asarray(lons, dtype=np.float64)
    if lats[0] < lats[-1]:
        arr, lats = arr[:, ::-1, :], lats[::-1]
    transform, xres, yres = grid_transform(lats, lons)
    profile = {"driver": "GTiff", "height": arr.shape[1],
               "width": arr.shape[2], "count": arr.shape[0],
               "dtype": "float32", "crs": "EPSG:4326",
               "transform": transform, "nodata": np.nan,
               "tiled": True, "compress": "deflate", "predictor": 3}
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", **profile) as dst:
        for i in range(arr.shape[0]):
            dst.write_band(i + 1, arr[i])
            dst.set_band_description(i + 1, names[i])
    return path


def fetch_r():
    """IMERG Early daily/3-day rain -> R score cube on the 0.1° grid."""
    daily, rain3, steps = imerg.fetch_daily(
        BOUNDS, dt.date(2024, 5, 7), 17, WORK)   # 2024-04-20..05-07
    times = pd.DatetimeIndex(pd.to_datetime(rain3["time"].values)).date
    lats, lons = rain3["lat"].values, rain3["lon"].values
    wanted = [dt.date.fromisoformat(d) for d in DATES]
    missing = [d for d in wanted if d not in set(times)]
    if missing:
        raise RuntimeError(f"IMERG Early missing rain3 days: {missing}")
    idx = [list(times).index(d) for d in wanted]
    rain3_np = np.asarray(rain3.values, dtype=np.float64)[idx]

    proj = common.init()
    aoi = common.aoi_geom(CASE)
    clim = gee_tpi.climatology(CASE, proj, assets=False).clip(aoi).toFloat()
    clim_path = WORK / "clim_rain3.tif"
    if not _write_cog(clim, aoi, clim_path, fetch=_download,
                      max_bytes=MAX_BYTES, scale=gee_tpi.CLIM_SCALE_M):
        raise RuntimeError("climatology download exceeds size limit")
    with rasterio.open(clim_path) as src:
        desc = list(src.descriptions)
        order = sorted(range(src.count),
                       key=lambda i: desc[i] if desc[i] else f"p{i}")
        raw = src.read().astype(np.float64)
        names = [d.lower() if d else "" for d in desc]
        band_of = {}
        for i, n in enumerate(names):
            for p in ("90", "95", "99"):
                if n.endswith(f"p{p}"):
                    band_of[p] = raw[i]
        if len(band_of) != 3:
            # percentile reducer emits p90/p95/p99 in config order
            band_of = dict(zip(("90", "95", "99"), raw[order]))
        transform, _, _ = grid_transform(lats, lons)
        north_up = lats[0] > lats[-1]
        h, w = (len(lats), len(lons))
        clim_on = {}
        for p, arr in band_of.items():
            out = np.full((h, w), np.nan)
            reproject(np.asarray(arr, dtype=np.float64), out,
                      src_transform=src.transform, src_crs=src.crs,
                      src_nodata=np.nan, dst_transform=transform,
                      dst_crs="EPSG:4326", dst_nodata=np.nan,
                      resampling=Resampling.bilinear)
            clim_on[p] = out if north_up else out[::-1]
    r = rain_score_np(rain3_np, clim_on["90"], clim_on["95"], clim_on["99"])
    names = [d.replace("-", "") for d in DATES]
    return write_series_cog(WORK / "r_asavail.tif", r, lats, lons, names)


def fetch_s():
    """SMAP L4 with 3-day latency shift -> S score cube on a ~9 km grid."""
    proj = common.init()
    aoi = common.aoi_geom(CASE)
    col = ee.ImageCollection(common.COL_SMAP).select(common.BAND_SMAP)
    imgs = []
    for d in DATES:
        t = dt.date.fromisoformat(d) - dt.timedelta(days=SMAP_LAG_DAYS)
        imgs.append(col.filterDate(str(t), str(t + dt.timedelta(days=1)))
                    .mean().rename(f"d{d.replace('-', '')}"))
    img = ee.Image.cat(imgs).toFloat().clip(aoi)
    raw_path = WORK / "smap_raw.tif"
    if not _write_cog(img, aoi, raw_path, fetch=_download,
                      max_bytes=MAX_BYTES, scale=9000):
        raise RuntimeError("SMAP download exceeds size limit")
    lo, hi = thresholds()["soil_wetness_range"]
    with rasterio.open(raw_path) as src:
        arr = src.read().astype(np.float64)
        profile = dict(src.profile)
    s = np.clip((arr - lo) / (hi - lo), 0, 1)
    s[~np.isfinite(arr)] = np.nan
    names = [d.replace("-", "") for d in DATES]
    s_path = WORK / "s_asavail.tif"
    profile.update(dtype="float32", nodata=np.nan, compress="deflate",
                   predictor=3, tiled=True)
    with rasterio.open(s_path, "w", **profile) as dst:
        for i in range(s.shape[0]):
            dst.write_band(i + 1, s[i].astype(np.float32))
            dst.set_band_description(i + 1, names[i])
    return s_path


def combine(r_path, s_path):
    paths = {
        "T_slide": COG_DIR / "rs_2024_t_slide_20240502.tif",
        "T_flood": COG_DIR / "rs_2024_t_flood_20240502.tif",
        "R": r_path, "S": s_path,
        "TPI_slide": SLIDE_COG, "TPI_flood": FLOOD_COG,
    }
    names = [d.replace("-", "") for d in DATES]
    gee_tpi._combine_case_tpi(paths, names,
                              thresholds().get("tpi_weights"))
    return names


def series_json():
    slide = read_tpi_series(SLIDE_COG)
    flood = read_tpi_series(FLOOD_COG)
    days = []
    for i, d in enumerate(DATES):
        days.append({
            "date": d,
            "tpi_slide_max": (float(np.nanmax(slide[i]))
                              if np.isfinite(slide[i]).any() else None),
            "tpi_flood_max": (float(np.nanmax(flood[i]))
                              if np.isfinite(flood[i]).any() else None),
            "first_critical_bool": bool(
                np.nanmax(np.maximum(slide[i], flood[i])) >= CRITICAL
                if np.isfinite(np.maximum(slide[i], flood[i])).any()
                else False),
        })
    out = {
        "case": CASE,
        "basis": ("as-available (IMERG Early ~4h + SMAP L4 shifted 3d); "
                  "climatology = static GEE asset basis, T = static 90m"),
        "days": days,
    }
    path = REPO / "data/rs_2024/series/asavail_tpi_daily.json"
    path.write_text(json.dumps(out, indent=2) + "\n")
    return out


def _hist_flat(leads, bin_h=6):
    """summarize_flat/hist_array from .herdr/lead_time_run.py, inlined —
    that script runs both cases at import time so it cannot be imported."""
    a = np.asarray(leads, dtype=float)
    lo = float(np.floor(a.min() / bin_h) * bin_h)
    hi = float(np.floor(a.max() / bin_h) * bin_h + bin_h)
    counts, _ = np.histogram(a, bins=np.arange(lo, hi + 1, bin_h))
    return {
        "lead_time_median_h": float(np.median(a)),
        "lead_time_iqr_h": np.percentile(a, [25, 75]).tolist(),
        "lead_time_n": int(len(a)),
        "warned_before_pct": float(100 * (a > 0).mean()),
        "warned_24h_pct": float(100 * (a >= 24).mean()),
        "lead_time_hist": counts.tolist(),
        "lead_time_hist_bin_h": 6,
        "lead_time_hist_from_h": lo,
    }


def lead_time_json():
    """Cluster-lead vs damage_anchor on the as-available slide cube —
    .herdr/lead_time_run.py rs_2024() logic, same flat keys."""
    event = json.loads((REPO / "events/rs-2024-05.json").read_text())
    tz = "America/Sao_Paulo"
    cube = read_tpi_series(SLIDE_COG)
    times = frame_times_from_bands(SLIDE_COG)
    raw_anchor = event.get("damage_anchor") or {}
    anchor, _ = damage_time(
        raw_anchor.get("time_utc") or raw_anchor.get("at")
        or raw_anchor.get("date"), tz)
    reasons, flat = {}, {}
    clusters = cluster_lead_times(cube, times, CRITICAL, anchor,
                                min_cluster_px=4)
    if clusters.empty:
        for k in ("lead_time_median_h", "lead_time_iqr_h", "lead_time_n",
                  "warned_before_pct", "warned_24h_pct", "lead_time_hist"):
            flat[k] = None
            reasons[k] = ("No >=4px cluster crossed TPI >= 0.6 "
                          "before/around the anchor")
    else:
        flat.update(_hist_flat(clusters["lead_hours"]))
    flat["never_critical_pct"] = None
    reasons["never_critical_pct"] = (
        "Cluster mode has no never-crossing denominator: clusters are "
        "defined by ever crossing Critical; the landslide catalog has "
        "16,027 points but none are dated")
    flat["mode"] = "cluster_lead_vs_damage_anchor"
    flat["n_clusters"] = int(len(clusters))
    flat["threshold_tpi"] = CRITICAL
    first = first_crossing_frame(cube, CRITICAL)
    ever = first >= 0
    crossing = times[first[ever].min()] if ever.any() else None
    flat["first_critical_time"] = str(crossing) if crossing is not None else None
    flat["event_lead_h"] = (
        (anchor - crossing).total_seconds() / 3600
        if crossing is not None else None)
    w = event.get("official_warning") or {}
    w_at = w.get("at") or w.get("time_utc")
    w_delta = None
    if w_at and crossing is not None:
        t, kind = damage_time(w_at, tz)
        if kind == "hour":
            w_delta = (t - crossing).total_seconds() / 3600
    flat["official_warning_delta_h"] = w_delta
    flat["damage_anchor"] = {
        "at": raw_anchor.get("time_utc") or raw_anchor.get("date"),
        "source": raw_anchor.get("source"),
    }
    flat["n_landslide_points"] = 0
    reasons["point_lead_times"] = (
        "data/rs_2024/geojson/landslide_catalog.geojson has 16,027 points but "
        "none are dated: point-level lead times are undefined; "
        "fell back to cluster-lead vs damage_anchor")
    out = {"case": CASE, "basis": "as-available", "metrics": flat,
           "_null_reasons": reasons}
    path = REPO / "data/rs_2024/lead_time_asavail.json"
    path.write_text(json.dumps(out, indent=2, default=str) + "\n")
    return out


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    r_path = fetch_r()
    print(f"R: {r_path}")
    s_path = fetch_s()
    print(f"S: {s_path}")
    combine(r_path, s_path)
    print(f"TPI: {SLIDE_COG} / {FLOOD_COG}")
    print(json.dumps(series_json(), indent=2))
    print(json.dumps(lead_time_json(), indent=2, default=str))


if __name__ == "__main__":
    main()
