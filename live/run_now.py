"""Modo Agora — live pipeline entry point (doc steps 2-6).

Each run (GitHub Actions, every 3 h):
  1. download IMERG Early (last 7 days), latest SMAP L4, today's GEOS-FP forecast
  2. compute R, S, TPI today and the D+1..D+3 projection with static/ climatology
     and terrain — same thresholds as config.yaml / gee/01_tpi_index.js
  3. list recent NISAR passes over the area (metadata only)
  4. write PNGs, critical-areas GeoJSON, summary.json, alerts.json into now/
  5. write manifest.json LAST (the app never reads a half-updated set)
  6. optionally upload now/ to gs://$NOW_GCS_BUCKET/now/ (manifest last)

Requires static/sul_static.npz — build once with live/build_static.py.
Credentials come from env (EDL_USER/EDL_PASS, GCP_SA_KEY); nothing is stored.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from live import alerts, fetch, publish, render, tpi
from live.config import (
    FORECAST_DAYS,
    IMERG_LOOKBACK_DAYS,
    LABELS,
    NISAR_LOOKBACK_DAYS,
    NOW_BOUNDS,
    NOW_DIR,
    STATIC_DIR,
    STATIC_NPZ,
)
from live.grid import dst_lats_lons, dst_transform, pixel_area_km2, regrid_da, regrid_points


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="run_now", description=__doc__.splitlines()[0])
    p.add_argument("--date", type=dt.date.fromisoformat,
                   default=dt.datetime.now(dt.UTC).date(),
                   help="reference date, UTC (default: today)")
    p.add_argument("--bounds", type=float, nargs=4, default=list(NOW_BOUNDS),
                   metavar=("W", "S", "E", "N"))
    p.add_argument("--out-dir", type=Path, default=NOW_DIR)
    p.add_argument("--static-dir", type=Path, default=STATIC_DIR)
    p.add_argument("--imerg-days", type=int, default=IMERG_LOOKBACK_DAYS)
    p.add_argument("--no-nisar", action="store_true", help="skip the NISAR pass listing")
    p.add_argument("--no-upload", action="store_true",
                   help="skip GCS upload even if NOW_GCS_BUCKET is set")
    return p.parse_args(argv)


def _load_static(static_dir: Path) -> dict[str, np.ndarray]:
    path = static_dir / STATIC_NPZ
    if not path.exists():
        sys.exit(f"missing {path} — run `uv run python -m live.build_static` once "
                 f"with GCP_SA_KEY/GEE_PROJECT set")
    return dict(np.load(path))


def _clim_for_month(static: dict[str, np.ndarray], month: int) -> dict[str, np.ndarray]:
    return {k: static[k][month - 1] for k in ("p90", "p95", "p99")}


def build_manifest(bounds, updated_at, data_dates, layers, nisar_passes) -> dict:
    return {
        "schema": "tipping-point/now@1",
        "mode": "live",
        "updated_at": updated_at,
        "bounds": list(bounds),
        "data_dates": data_dates,
        "layers": layers,
        "products": {
            "critical_areas": "geojson/critical_areas.geojson",
            "summary": "summary.json",
            "alerts": "alerts.json",
        },
        "nisar_passes": nisar_passes,
    }


def run(args) -> dict:
    static = _load_static(args.static_dir)
    lats, lons = dst_lats_lons(tuple(args.bounds))
    transform = dst_transform(tuple(args.bounds))
    today = args.date

    # ---- fetch ------------------------------------------------------------
    imerg_daily, imerg_date = fetch.fetch_imerg_daily(tuple(args.bounds), today, args.imerg_days)
    sm_lats, sm_lons, sm_vals, smap_date = fetch.fetch_smap_l4(tuple(args.bounds))
    fc_daily_da, geos_issued = fetch.fetch_geosfp_daily(tuple(args.bounds), today)
    nisar = [] if args.no_nisar else fetch.nisar_recent_passes(
        tuple(args.bounds), NISAR_LOOKBACK_DAYS)

    # ---- regrid to the ~1 km static grid ----------------------------------
    obs_stack = np.stack(
        [regrid_da(imerg_daily.isel(time=i), lats, lons)
         for i in range(imerg_daily.sizes["time"])])
    sm = regrid_points(sm_lats, sm_lons, sm_vals, lats, lons)
    fc_stack = np.stack(
        [regrid_da(fc_daily_da.isel(time=i), lats, lons)
         for i in range(fc_daily_da.sizes["time"])])[:FORECAST_DAYS]

    # ---- TPI today + D+1..D+3 ----------------------------------------------
    rain3_today = tpi.accumulate3(obs_stack)[-1]
    sm = np.nan_to_num(sm, nan=0.7)
    t_slide, t_flood = static["t_slide"], static["t_flood"]

    days, slides, floods = [], [], []
    for k in range(FORECAST_DAYS + 1):
        day = today + dt.timedelta(days=k)
        rain3 = rain3_today if k == 0 else tpi.projected_rain3(obs_stack[-3:], fc_stack, k)
        out = tpi.tpi(rain3, sm, _clim_for_month(static, day.month), t_slide, t_flood)
        days.append(day)
        slides.append(tpi.classify(out["tpi_slide"]))
        floods.append(tpi.classify(out["tpi_flood"]))

    # ---- artifacts ----------------------------------------------------------
    files: dict[str, bytes | dict] = {}
    layers = []
    for kind, arrs in (("tpi_slide", slides), ("tpi_flood", floods)):
        for k, cls in enumerate(arrs):
            rel = f"png/{kind}_d{k}.png"
            files[rel] = render.classes_png_bytes(cls)
            layers.append({
                "id": f"{kind}_d{k}", "type": "png", "path": rel,
                "day": days[k].isoformat(), "bounds": list(args.bounds),
                "legend": LABELS,
            })

    feats = []
    for kind, arrs in (("slide", slides[:2]), ("flood", floods[:2])):
        for k, cls in enumerate(arrs):
            feats += render.critical_features(cls, transform, kind, days[k].isoformat())
    files["geojson/critical_areas.geojson"] = render.feature_collection(feats)

    area_km2 = pixel_area_km2(lats)
    crit_mask = (slides[0] >= 2) | (slides[1] >= 2) | (floods[0] >= 2) | (floods[1] >= 2)
    pop = static.get("pop")
    summary = {
        "generated_at": dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z"),
        "critical_km2": {"slide": float((area_km2 * (slides[0] >= 2)).sum()),
                          "flood": float((area_km2 * (floods[0] >= 2)).sum()),
                          "any": float((area_km2 * crit_mask).sum())},
        "people_exposed": (None if pop is None else
                           float(np.nansum(pop * crit_mask))),
        "classes_km2": {
            LABELS[c]: float((area_km2 * (slides[0] == c)).sum()) for c in range(4)},
        "data_dates": {"imerg": imerg_date, "smap": smap_date,
                        "geos_fp_issued": geos_issued},
    }
    files["summary.json"] = summary

    prev_path = args.out_dir / "prev_classes.npz"
    prev_worst = None
    if prev_path.exists():
        prev = np.load(prev_path)
        prev_worst = prev["worst"]
    worst_today = np.maximum(slides[0], floods[0])
    worst_d1 = np.maximum(slides[1], floods[1])
    worst = np.maximum(worst_today, worst_d1)
    files["alerts.json"] = alerts.emit_alerts(
        prev_worst, worst_today, worst_d1,
        dt.datetime.now(dt.UTC), transform)
    files["prev_classes.npz"] = render.npz_bytes(worst=worst)

    # ---- write everything, manifest LAST ------------------------------------
    updated_at = dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z")
    manifest = build_manifest(
        tuple(args.bounds), updated_at,
        {"imerg": imerg_date, "smap": smap_date, "geos_fp_issued": geos_issued},
        layers, nisar)
    written = publish.write_outputs(args.out_dir, files, manifest)
    assert written[-1].name == "manifest.json"

    # ---- optional upload (env-gated; plain git commit of now/ also works) ----
    bucket = os.environ.get("NOW_GCS_BUCKET")
    if bucket and not args.no_upload:
        publish.upload_gcs(args.out_dir, bucket)
    return manifest


def main(argv=None) -> None:
    args = parse_args(argv)
    manifest = run(args)
    print(f"now/ updated_at={manifest['updated_at']} "
          f"layers={len(manifest['layers'])} nisar={len(manifest['nisar_passes'])}")


if __name__ == "__main__":
    main()
