"""L22: REAL GFS-forecast TPI replay frames for rs_2024 + wa_2025.

For each issue day D in the case window, horizons k=1..3, hazards
slide/flood:

    TPI_fcst(D+k) = geomean(R_fc, S_held(D), T, weights=tpi_weights)

* R_fc(D+k): rain_score of the 3-day accumulation ending D+k built from
  OBSERVED IMERG daily mm for days <= D (via pipeline.fetch.imerg
  .fetch_daily — the same basis as .herdr/asavail_run.py) plus the GFS
  forecast-rain COG bands d1..dk issued at D (data/<case>/forecast_rain/),
  scored against the case rain3 climatology (p90/p95/p99 — the asavail
  clim COG for rs_2024, a GEE download for wa_2025).
* S_held: the published s_series band for day D, held for all 3 horizons.
* T: the static 90 m t_slide/t_flood COGs; R_fc/S are resampled UP to
  that grid exactly like gee.tpi._combine_case_tpi.

Outputs per issue day D:
  data/<case>/cog/<case>_tpi_gfs_<YYYYMMDD>.tif      (bands d1_slide..d3_flood)
  data/<case>/png/tpi_{slide,flood}_gfs_d<k>_<D+k>.png
  appended forecast layer entries in data/<case>/png/_index.json
  data/<case>/forecast_lead_time.json

    set -a; . ./.env; set +a
    PYTHONPATH=.:pipeline .venv/bin/python .herdr/gfs_replay_run.py
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from rasterio.warp import Resampling, reproject

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "pipeline"))

from live.tpi import classify, projected_rain3, rain_score  # noqa: E402
from ml.lead_time import damage_time  # noqa: E402
from pipeline.fetch import imerg  # noqa: E402
from tp.bundle import _class_png_3857  # noqa: E402
from tp.config import thresholds  # noqa: E402

CRITICAL = 0.6
WORK = REPO / "intermediate" / "forecast_replay"

CASES = {
    "rs_2024": {
        "event": "rs-2024-05",
        "bounds": (-52.5, -30.5, -50.5, -29.0),
        "window": (dt.date(2024, 4, 22), dt.date(2024, 5, 7)),
        "tag": "20240502",
        "tz": "America/Sao_Paulo",
        "clim": REPO / "intermediate/asavail/clim_rain3.tif",
    },
    "wa_2025": {
        "event": "wa-2025-12",
        "bounds": (-122.6, 47.0, -121.5, 48.5),
        "window": (dt.date(2025, 11, 30), dt.date(2025, 12, 20)),
        "tag": "20251210",
        "tz": "America/Los_Angeles",
        "clim": WORK / "wa_2025_clim_rain3.tif",
    },
}


def window_dates(case):
    lo, hi = CASES[case]["window"]
    return [lo + dt.timedelta(days=i) for i in range((hi - lo).days + 1)]


def load_daily(case):
    """IMERG daily mm over [window_start-2, window_end] on the 0.1° grid."""
    cfg = CASES[case]
    lo, hi = cfg["window"]
    days = (hi - lo).days + 3          # cover D-2 for the first issue day
    daily, _rain3, _steps = imerg.fetch_daily(
        cfg["bounds"], hi, days, WORK / case / "imerg")
    times = list(pd.DatetimeIndex(
        pd.to_datetime(daily["time"].values)).date)
    return daily.values.astype(np.float64), times, \
        daily["lat"].values, daily["lon"].values


def clim_on_grid(case, lats, lons):
    """p90/p95/p99 reprojected onto the IMERG daily grid (asavail logic)."""
    cfg = CASES[case]
    clim_path = cfg["clim"]
    if not clim_path.exists():
        # wa_2025: no local clim COG yet — pull the case climatology via GEE
        # (static asset when present, built + exported once otherwise).
        from gee import common
        from gee import tpi as gee_tpi
        from tp.fallback import _download, _write_cog
        proj = common.init()
        aoi = common.aoi_geom(case)
        img = gee_tpi.climatology(case, proj, assets=False) \
            .clip(aoi).toFloat()
        clim_path.parent.mkdir(parents=True, exist_ok=True)
        if not _write_cog(img, aoi, clim_path, fetch=_download,
                          max_bytes=32 * 1024 * 1024,
                          scale=gee_tpi.CLIM_SCALE_M):
            raise RuntimeError(f"{case}: climatology download too large")
    from rasterio.transform import from_origin
    lats = np.asarray(lats, dtype=np.float64)
    lons = np.asarray(lons, dtype=np.float64)
    yres = float(abs(np.median(np.diff(lats))))
    xres = float(abs(np.median(np.diff(lons))))
    transform = from_origin(float(lons[0] - xres / 2),
                            float(max(lats[0], lats[-1]) + yres / 2),
                            xres, yres)
    h, w = len(lats), len(lons)
    north_up = lats[0] > lats[-1]
    out = {}
    with rasterio.open(clim_path) as src:
        for i, p in enumerate(("p90", "p95", "p99")):
            # percentile reducer emits bands in RAIN_PERCENTILES order
            band = src.read(i + 1).astype(np.float64)
            dst = np.full((h, w), np.nan)
            reproject(band, dst, src_transform=src.transform,
                      src_crs=src.crs, src_nodata=np.nan,
                      dst_transform=transform, dst_crs="EPSG:4326",
                      dst_nodata=np.nan, resampling=Resampling.bilinear)
            out[p] = dst if north_up else dst[::-1]
    return out


def gfs_cube(case, issue, lats, lons):
    """GFS d1..d3 mm reprojected onto the IMERG daily grid."""
    cfg = CASES[case]
    path = (REPO / "data" / case / "forecast_rain"
            / f"{case}_gfs_{issue:%Y%m%d}.tif")
    if not path.exists():
        raise FileNotFoundError(path)
    from rasterio.transform import from_origin
    lats = np.asarray(lats, dtype=np.float64)
    lons = np.asarray(lons, dtype=np.float64)
    yres = float(abs(np.median(np.diff(lats))))
    xres = float(abs(np.median(np.diff(lons))))
    transform = from_origin(float(lons[0] - xres / 2),
                            float(max(lats[0], lats[-1]) + yres / 2),
                            xres, yres)
    h, w = len(lats), len(lons)
    north_up = lats[0] > lats[-1]
    cube = np.full((3, h, w), np.nan)
    with rasterio.open(path) as src:
        for b in range(min(3, src.count)):
            dst = np.full((h, w), np.nan)
            reproject(src.read(b + 1).astype(np.float64), dst,
                      src_transform=src.transform, src_crs=src.crs,
                      src_nodata=np.nan, dst_transform=transform,
                      dst_crs="EPSG:4326", dst_nodata=np.nan,
                      resampling=Resampling.bilinear)
            cube[b] = dst if north_up else dst[::-1]
    return cube


def band_index(path, day: dt.date) -> int:
    with rasterio.open(path) as src:
        for i, d in enumerate(src.descriptions, 1):
            if d == day.strftime("%Y%m%d"):
                return i
    raise KeyError(f"{path.name}: no band for {day}")


def combine_90m(case, r_cube, lats, lons, s_band_idx):
    """R_fc (3 x daily-grid) + S band -> 6-band TPI on the 90 m T grid."""
    cfg = CASES[case]
    tag = cfg["tag"]
    cog_dir = REPO / "data" / case / "cog"
    weights = thresholds().get("tpi_weights", [1 / 3, 1 / 3, 1 / 3])
    from rasterio.transform import from_origin
    lats = np.asarray(lats, dtype=np.float64)
    lons = np.asarray(lons, dtype=np.float64)
    yres = float(abs(np.median(np.diff(lats))))
    xres = float(abs(np.median(np.diff(lons))))
    src_transform = from_origin(float(lons[0] - xres / 2),
                                float(max(lats[0], lats[-1]) + yres / 2),
                                xres, yres)
    north_up = lats[0] > lats[-1]

    def to_tgrid(arr):
        if not north_up:
            arr = arr[::-1]
        out = np.full((h_t, w_t), np.nan)
        reproject(np.asarray(arr, dtype=np.float64), out,
                  src_transform=src_transform, src_crs="EPSG:4326",
                  src_nodata=np.nan, dst_transform=t_transform,
                  dst_crs=t_crs, dst_nodata=np.nan,
                  resampling=Resampling.bilinear)
        return out

    with rasterio.open(cog_dir / f"{case}_t_slide_{tag}.tif") as src:
        t_transform, t_crs = src.transform, src.crs
        h_t, w_t = src.height, src.width
        t_slide = src.read(1).astype(np.float64)
        profile = dict(src.profile)
    with rasterio.open(cog_dir / f"{case}_t_flood_{tag}.tif") as src:
        t_flood = src.read(1).astype(np.float64)
    s_path = cog_dir / f"{case}_s_series_{tag}.tif"
    with rasterio.open(s_path) as src:
        # s_series is ~1 km — resample UP to the 90 m T grid like
        # _combine_case_tpi does for every coarse input
        s_raw = src.read(s_band_idx).astype(np.float64)
        if src.nodata is not None and np.isfinite(src.nodata):
            s_raw = np.where(s_raw == src.nodata, np.nan, s_raw)
        s_held = np.full((h_t, w_t), np.nan)
        reproject(s_raw, s_held, src_transform=src.transform,
                  src_crs=src.crs, src_nodata=np.nan,
                  dst_transform=t_transform, dst_crs=t_crs,
                  dst_nodata=np.nan, resampling=Resampling.bilinear)

    r90 = np.stack([to_tgrid(r_cube[k]) for k in range(3)])
    s90 = np.where(np.isfinite(s_held), s_held, np.nan)
    t_mask = np.isfinite(t_slide) & np.isfinite(t_flood)
    bands = {}
    for k in range(3):
        valid = np.isfinite(r90[k]) & np.isfinite(s90) & t_mask
        for haz, t in (("slide", t_slide), ("flood", t_flood)):
            tpi = np.where(valid, np.power(np.clip(r90[k], 0, None), weights[0])
                           * np.power(np.clip(s90, 0, None), weights[1])
                           * np.power(np.clip(t, 0, None), weights[2]),
                           np.nan).astype(np.float32)
            bands[f"d{k + 1}_{haz}"] = tpi
    return bands, profile


def issue_forecast(case, issue, daily, times, lats, lons, clim, s_idx):
    """One issue day -> 6-band COG + PNGs + index entries."""
    cfg = CASES[case]
    idx = {t: i for i, t in enumerate(times)}
    obs_days = [issue - dt.timedelta(days=o) for o in (2, 1, 0)]
    missing = [d for d in obs_days if d not in idx]
    if missing:
        raise RuntimeError(f"{case} {issue}: IMERG daily missing {missing}")
    obs_recent = np.stack([daily[idx[d]] for d in obs_days])
    fc = gfs_cube(case, issue, lats, lons)
    r_cube = np.stack([
        rain_score(projected_rain3(obs_recent, fc, k),
                   clim["p90"], clim["p95"], clim["p99"])
        for k in (1, 2, 3)])
    bands, profile = combine_90m(case, r_cube, lats, lons, s_idx)

    cog_dir = REPO / "data" / case / "cog"
    png_dir = REPO / "data" / case / "png"
    cog_path = cog_dir / f"{case}_tpi_gfs_{issue:%Y%m%d}.tif"
    order = [f"d{k}_{haz}" for k in (1, 2, 3) for haz in ("slide", "flood")]
    prof = dict(profile, count=6, dtype="float32", nodata=np.nan,
                compress="deflate", predictor=3, tiled=True)
    cog_dir.mkdir(parents=True, exist_ok=True)
    with rasterio.open(cog_path, "w", **prof) as dst:
        for i, name in enumerate(order, 1):
            dst.write_band(i, bands[name])
            dst.set_band_description(i, name)

    entries = []
    with rasterio.open(cog_path) as src:
        band_of = {src.descriptions[i - 1]: i for i in range(1, 7)}
        transform, crs = src.transform, src.crs
        for k in (1, 2, 3):
            day = issue + dt.timedelta(days=k)
            for haz in ("slide", "flood"):
                arr = src.read(band_of[f"d{k}_{haz}"]).astype(np.float64)
                valid = np.isfinite(arr)
                png, bounds = _class_png_3857(
                    classify(np.where(valid, arr, np.nan)), valid,
                    transform, crs)
                png_name = f"tpi_{haz}_gfs_d{k}_{day:%Y%m%d}.png"
                (png_dir / png_name).write_bytes(png)
                entries.append({
                    "id": f"tpi_{haz}_gfs_d{k}",
                    "date": day.isoformat(),
                    "issued": issue.isoformat(),
                    "png": f"{case}/png/{png_name}",
                    "cog": f"{case}/cog/{cog_path.name}",
                    "bounds": bounds,
                    "forecast": True,
                    "forecast_source": "noaa-gfs",
                    "kind": "forecast",
                })
    return entries, bands


def lead_time(case, issued_bands):
    """Earliest forecast-critical issue vs the damage anchor.

    An issue day 'warns' when some horizon's AOI max (slide or flood)
    crosses CRITICAL for a depicted date <= the anchor date. Lead_h per
    warning issue = anchor - issue run time (00Z per the GFS run JSONs).
    """
    cfg = CASES[case]
    event = json.loads((REPO / "events" / f"{cfg['event']}.json").read_text())
    raw_anchor = event.get("damage_anchor") or {}
    anchor, _kind = damage_time(
        raw_anchor.get("time_utc") or raw_anchor.get("date"), cfg["tz"])
    anchor_date = anchor.date()
    issue_days = []
    leads = []
    for issue in window_dates(case):
        day_bands = issued_bands[issue]
        first_k = None
        first_date = None
        for k in (1, 2, 3):
            day = issue + dt.timedelta(days=k)
            if day > anchor_date:
                continue
            mx = max(
                float(np.nanmax(day_bands[f"d{k}_slide"]))
                if np.isfinite(day_bands[f"d{k}_slide"]).any() else np.nan,
                float(np.nanmax(day_bands[f"d{k}_flood"]))
                if np.isfinite(day_bands[f"d{k}_flood"]).any() else np.nan)
            if np.isfinite(mx) and mx > CRITICAL:
                first_k, first_date = k, day
                break
        warned = first_k is not None
        issue_at = pd.Timestamp(f"{issue.isoformat()}T00:00:00Z")
        lead_h = (anchor - issue_at).total_seconds() / 3600 if warned else None
        if warned:
            leads.append(lead_h)
        issue_days.append({
            "issue": issue.isoformat(),
            "earliest_critical_k": first_k,
            "first_critical_date": first_date.isoformat() if first_date else None,
            "warned": warned,
            "lead_h": lead_h,
        })
    earliest = next((d for d in issue_days if d["warned"]), None)
    out = {
        "case": case,
        "basis": ("GFS-forecast replay TPI: R_fc = score(obs IMERG daily "
                  "<=D + GFS d1..dk) vs case climatology, S held at D, "
                  "T static; critical = AOI max over slide/flood > 0.6 "
                  "for depicted date <= anchor"),
        "anchor_utc": str(anchor),
        "issue_days": issue_days,
        "earliest_critical_issue": earliest["issue"] if earliest else None,
        "would_have_warned_h": (
            (anchor - pd.Timestamp(f"{earliest['issue']}T00:00:00Z"))
            .total_seconds() / 3600 if earliest else None),
        "lead_h_median": float(np.median(leads)) if leads else None,
        "lead_h_iqr": np.percentile(leads, [25, 75]).tolist() if leads else None,
        "n_warned": len(leads),
        "n_issue_days": len(issue_days),
    }
    path = REPO / "data" / case / "forecast_lead_time.json"
    path.write_text(json.dumps(out, indent=2) + "\n")
    return out


def run_case(case):
    cfg = CASES[case]
    print(f"== {case}: fetching IMERG daily ==", flush=True)
    daily, times, lats, lons = load_daily(case)
    print(f"   daily {daily.shape} {times[0]}..{times[-1]}")
    clim = clim_on_grid(case, lats, lons)
    s_path = REPO / "data" / case / "cog" / f"{case}_s_series_{cfg['tag']}.tif"
    index_path = REPO / "data" / case / "png" / "_index.json"
    index = json.loads(index_path.read_text())
    issued_bands = {}
    n_entries = 0
    for issue in window_dates(case):
        s_idx = band_index(s_path, issue)
        entries, bands = issue_forecast(
            case, issue, daily, times, lats, lons, clim, s_idx)
        index["layers"].extend(entries)
        issued_bands[issue] = bands
        n_entries += len(entries)
        print(f"   {issue}: COG + {len(entries)} PNGs", flush=True)
    index_path.write_text(json.dumps(index, indent=2,
                                     ensure_ascii=False) + "\n")
    metrics = lead_time(case, issued_bands)
    print(f"   index +{n_entries} layers; "
          f"would_have_warned_h={metrics['would_have_warned_h']}")
    return metrics


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    for case in CASES:
        run_case(case)


if __name__ == "__main__":
    main()
