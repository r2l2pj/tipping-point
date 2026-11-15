"""Modo Agora — live pipeline entry point (doc steps 2-6).

L8: the default run delegates to the GEE orchestration job (`live.jobs now`)
— fetch into `now/` asset collections, derived TPI assets, fallback export,
manifest last. `--local` runs the local numpy pipeline below instead
(fallback + tests):

  1. download IMERG Early (last 7 days), latest SMAP L4, today's GEOS-FP forecast
  2. compute R, S, TPI today and the D+1..D+3 projection with static/ climatology
     and terrain — same thresholds as config.yaml / gee/01_tpi_index.js
  3. list recent NISAR passes over the area (metadata only)
  4. write PNGs, critical-areas GeoJSON, summary.json, alerts.json into data/now/
  5. write manifest.json LAST (the app never reads a half-updated set)

The --local path requires static/sul_static.npz — build once with
live/build_static.py. Credentials come from env (EARTHDATA_TOKEN canonical,
EDL_USER/EDL_PASS local fallback, GCP_SA_KEY); nothing is stored.

`--imer-source gee` stands in for IMERG Early with the GEE collection
NASA/GPM_L3/IMERG_V07 (honestly labeled: 'gee:' product_id and
summary.json imerg_source="gee-imer-v07-standin"). It exists only until
the GES DISC EULA is accepted on the token account — 'gesdisc' stays the
default so the daily workflow is unaffected.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from live import alerts, fetch, publish, render, tpi
from live.now_manifest import OPTIONAL_PRODUCTS, prune_products
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
    p.add_argument("--imer-source", choices=("gesdisc", "gee"), default="gesdisc",
                   help="--local IMERG source: 'gesdisc' = IMERG Early via GES "
                        "DISC (default; requires the accepted EULA), 'gee' = "
                        "temporary NASA/GPM_L3/IMERG_V07 stand-in via Earth "
                        "Engine until the GES DISC EULA is accepted")
    p.add_argument("--no-nisar", action="store_true", help="skip the NISAR pass listing")
    p.add_argument("--local", action="store_true",
                   help="run the local numpy pipeline instead of the GEE job "
                        "(fallback path; the options above only apply here)")
    p.add_argument("--dry-run", action="store_true",
                   help="GEE mode: print the job's subprocess steps without "
                        "running them")
    return p.parse_args(argv)


def _load_static(static_dir: Path, days: list[dt.date] | None = None):
    """Static inputs: full sul_static.npz when present (legacy), else L20 —
    fetch only the needed climatology doys + terrain from the GEE assets,
    cached under static/cache/."""
    path = static_dir / STATIC_NPZ
    if path.exists():
        return dict(np.load(path))
    from live import static_fetch
    return static_fetch.load_static(days=days)


def _clim_for_month(static: dict[str, np.ndarray], month: int) -> dict[str, np.ndarray]:
    return {k: static[k][month - 1] for k in ("p90", "p95", "p99")}


def build_manifest(bounds, updated_at, data_dates, layers, nisar_passes) -> dict:
    """data/now/manifest.json — the `now` node of the v4 manifest schema
    (data/SCHEMA.md): {mode: "live", bounds, data_dates, layers:[...]}.
    `products`/`nisar_passes` are additive keys the side panel reads."""
    return {
        "schema": "tipping-point/now@2",
        "mode": "live",
        "updated_at": updated_at,
        "bounds": list(bounds),
        "data_dates": data_dates,
        "layers": layers,
        "products": {
            "critical_areas": "geojson/critical_areas.geojson",
            **OPTIONAL_PRODUCTS,
            "summary": "summary.json",
            "alerts": "alerts.json",
        },
        "nisar_passes": nisar_passes,
    }


def _clim_for_day(static: dict, day: dt.date) -> dict[str, np.ndarray]:
    if "clim" in static:
        from live import static_fetch
        return static_fetch.clim_for_day(static, day)
    return _clim_for_month(static, day.month)


def run(args) -> dict:
    static = _load_static(
        args.static_dir,
        days=[args.date + dt.timedelta(days=k)
              for k in range(FORECAST_DAYS + 1)])
    lats, lons = dst_lats_lons(tuple(args.bounds))
    transform = dst_transform(tuple(args.bounds))
    today = args.date

    # ---- fetch ------------------------------------------------------------
    if args.imer_source == "gee":
        imerg_daily, imerg_date = fetch.fetch_imerg_gee_standin(
            tuple(args.bounds), days=args.imerg_days)
        imerg_source = "gee-imer-v07-standin"
    else:
        try:
            imerg_daily, imerg_date = fetch.fetch_imerg_daily(
                tuple(args.bounds), today, args.imerg_days)
        except Exception as exc:
            if "403" in str(exc) or "eula" in str(exc).lower():
                print("hint: GES DISC IMERG Early was rejected (403/EULA?) — "
                      "rerun with `--imer-source gee` to stand in with GEE "
                      "NASA/GPM_L3/IMERG_V07 until the EULA is accepted",
                      file=sys.stderr)
            raise
        imerg_source = "gesdisc-early"
    sm_lats, sm_lons, sm_vals, smap_date = fetch.fetch_smap_l4(tuple(args.bounds))
    fc_daily_da, gfs_issued = fetch.fetch_gfs_daily(tuple(args.bounds), today)
    nisar = [] if args.no_nisar else fetch.nisar_recent_passes(
        tuple(args.bounds), NISAR_LOOKBACK_DAYS)

    # ---- regrid to the ~1 km static grid ----------------------------------
    obs_stack = np.stack(
        [regrid_da(imerg_daily.isel(time=i), lats, lons)
         for i in range(imerg_daily.sizes["time"])])
    sm = regrid_points(sm_lats, sm_lons, sm_vals, lats, lons)
    # regrid_points nearest-fills what linear leaves NaN, so a source hole
    # would silently become data — mark dst pixels whose nearest SMAP source
    # is a gap as real nodata (interior gaps still interpolate).
    sm_cover = regrid_points(
        sm_lats, sm_lons, np.isfinite(sm_vals).astype(float), lats, lons,
        method="nearest")
    sm[sm_cover < 0.5] = np.nan
    fc_stack = np.stack(
        [regrid_da(fc_daily_da.isel(time=i), lats, lons)
         for i in range(fc_daily_da.sizes["time"])])[:FORECAST_DAYS]

    # ---- TPI today + D+1..D+3 ----------------------------------------------
    rain3_today = tpi.accumulate3(obs_stack)[-1]
    t_slide, t_flood = static["t_slide"], static["t_flood"]

    days, slides, floods, slide_tpi, flood_tpi = [], [], [], [], []
    for k in range(FORECAST_DAYS + 1):
        day = today + dt.timedelta(days=k)
        rain3 = rain3_today if k == 0 else tpi.projected_rain3(obs_stack[-3:], fc_stack, k)
        out = tpi.tpi(rain3, sm, _clim_for_day(static, day), t_slide, t_flood)
        days.append(day)
        slide_tpi.append(out["tpi_slide"])
        flood_tpi.append(out["tpi_flood"])
        slides.append(tpi.classify(out["tpi_slide"]))
        floods.append(tpi.classify(out["tpi_flood"]))

    # ---- artifacts ----------------------------------------------------------
    files: dict[str, bytes | dict] = {}
    layers = []
    # SMAP gaps stay NaN through the TPI product; `valid` keeps them out of
    # every artifact (transparent PNG pixels, nodata km², honest classes).
    for kind, arrs, tpis in (("tpi_slide", slides, slide_tpi),
                            ("tpi_flood", floods, flood_tpi)):
        for k, cls in enumerate(arrs):
            valid_k = np.isfinite(tpis[k])
            rel = f"png/{kind}_d{k}.png"
            png, png_bounds = render.layer_png_3857(cls, transform, valid=valid_k)
            files[rel] = png
            cog_rel = None
            if k <= 1:  # float TPI COGs only for today + D+1 (alert-bot contract)
                cog_rel = f"cog/{kind}_{'today' if k == 0 else 'd1'}.tif"
                files[cog_rel] = render.cog_bytes(tpis[k], transform)
            layers.append({  # v4 layer shape {id, date, png, cog, legend} + bounds
                "id": f"{kind}_d{k}", "date": days[k].isoformat(),
                "png": rel, "cog": cog_rel, "legend": LABELS,
                "bounds": png_bounds, "forecast": k > 0,
            })

    feats = []
    for kind, arrs in (("slide", slides[:2]), ("flood", floods[:2])):
        for k, cls in enumerate(arrs):
            feats += render.critical_features(cls, transform, kind, days[k].isoformat())
    files["geojson/critical_areas.geojson"] = render.feature_collection(feats)

    # reference layers for the Command Center Now map (cached on disk;
    # priority zones come from R3, run after this job — see now_manifest)
    gj_dir = args.out_dir / "geojson"
    from live import now_geojson
    for ensure in (now_geojson.ensure_municipal_boundaries,
                   now_geojson.ensure_permanent_water):
        try:
            ensure(gj_dir, tuple(args.bounds))
        except Exception as exc:  # reference layers are additive
            print(f"[now] {ensure.__name__} failed: {exc!r}",
                  file=sys.stderr)

    area_km2 = pixel_area_km2(lats)
    valid_s0 = np.isfinite(slide_tpi[0])
    valid_f0 = np.isfinite(flood_tpi[0])
    crit_mask = (((slides[0] >= 2) & valid_s0) | ((slides[1] >= 2) & np.isfinite(slide_tpi[1]))
                 | ((floods[0] >= 2) & valid_f0) | ((floods[1] >= 2) & np.isfinite(flood_tpi[1])))
    pop = static.get("pop")
    people_critical = None if pop is None else float(np.nansum(pop * crit_mask))
    summary = {
        "generated_at": dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z"),
        "critical_km2": {"slide": float((area_km2 * ((slides[0] >= 2) & valid_s0)).sum()),
                          "flood": float((area_km2 * ((floods[0] >= 2) & valid_f0)).sum()),
                          "any": float((area_km2 * crit_mask).sum())},
        "nodata_km2": {"slide": float((area_km2 * ~valid_s0).sum()),
                        "flood": float((area_km2 * ~valid_f0).sum())},
        "people_exposed": people_critical,
        "people_critical": people_critical,   # v4 metrics key (same value)
        "forecast_source": "noaa-gfs",        # v4 metrics key (D+1..D+3)
        "imerg_source": imerg_source,         # honest stand-in labeling
        "classes_km2": {
            LABELS[c]: float((area_km2 * ((slides[0] == c) & valid_s0)).sum())
            for c in range(4)},
        "data_dates": {"imerg": imerg_date, "smap": smap_date,
                        "gfs_issued": gfs_issued,
                        "clim_doy_used": static.get("clim_doy_used")},
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
        {"imerg": imerg_date, "smap": smap_date, "gfs_issued": gfs_issued,
         "clim_doy_used": static.get("clim_doy_used")},
        layers, nisar)
    # never advertise a 404: drop optional products not on disk; files/
    # entries are written just below, so they count as present
    present = {k: v for k, v in manifest["products"].items() if v in files}
    prune_products(manifest, args.out_dir)
    manifest["products"] = {**manifest["products"], **present}
    written = publish.write_outputs(args.out_dir, files, manifest)
    assert written[-1].name == "manifest.json"
    return manifest


def main(argv=None) -> None:
    args = parse_args(argv)
    if not args.local:
        from live import jobs
        jargv = ["now"] + (["--dry-run"] if args.dry_run else [])
        return jobs.main(jargv)
    manifest = run(args)
    print(f"data/now/ updated_at={manifest['updated_at']} "
          f"layers={len(manifest['layers'])} nisar={len(manifest['nisar_passes'])}")


if __name__ == "__main__":
    main()
