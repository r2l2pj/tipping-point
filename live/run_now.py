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


def write_imerg_cogs(daily, out_dir: Path) -> list[Path]:
    """One north-up lat-first COG per IMERG day (imerg_early_YYYYMMDD.tif):
    R1's rain-context rule reads them. Additive — never fails the run."""
    written = []
    try:
        from pipeline.fetch.imerg import write_cog

        lat = next(d for d in daily.dims if "lat" in d.lower())
        lon = next(d for d in daily.dims if "lon" in d.lower())
        da = daily.transpose("time", lat, lon)
        for i in range(da.sizes["time"]):
            day = np.datetime_as_string(da.time.values[i], unit="D")
            path = out_dir / f"imerg_early_{day.replace('-', '')}.tif"
            path.parent.mkdir(parents=True, exist_ok=True)
            written.append(write_cog(path, da.isel(time=i).values,
                                     da[lat].values, da[lon].values))
    except Exception as exc:
        print(f"[now] imerg daily COGs failed: {exc!r}", file=sys.stderr)
    return written


def _sample_points(daily) -> list[tuple[float, float]]:
    """>= 3 test points at SOURCE cell centres (never on a cell edge, where
    nearest-cell picks are ambiguous): the wettest cell of the last day +
    two interior cells at 1/3 and 2/3 of the grid."""
    lat = next(d for d in daily.dims if "lat" in d.lower())
    lon = next(d for d in daily.dims if "lon" in d.lower())
    last = daily.isel(time=-1).transpose(lat, lon)
    la, lo = last[lat].values, last[lon].values
    i, j = np.unravel_index(int(np.nanargmax(np.nan_to_num(last.values))),
                            last.shape)
    idx = [(i, j), (la.size // 3, lo.size // 3),
           (2 * la.size // 3, 2 * lo.size // 3)]
    return [(float(lo[c]), float(la[r])) for r, c in idx]


def imerg_cogs_match_source(daily, paths: list[Path], tol_mm: float = 0.01) -> bool:
    """Read each written daily COG back at >= 3 points and compare with the
    source IMERG values (nearest cell, by coordinate) — catches transposed
    or flipped writes like the legacy 42f5eb6 files."""
    if not paths:
        return False
    try:
        import rasterio
        lat = next(d for d in daily.dims if "lat" in d.lower())
        lon = next(d for d in daily.dims if "lon" in d.lower())
        pts = _sample_points(daily)
        for i, path in enumerate(paths):
            src = daily.isel(time=i)
            with rasterio.open(path) as ds:
                got = [float(v[0]) for v in ds.sample(pts)]
            want = [float(src.sel({lon: x, lat: y}, method="nearest").values)
                    for x, y in pts]
            if not np.allclose(np.nan_to_num(got), np.nan_to_num(want),
                               atol=tol_mm):
                print(f"[now] IMERG COG mismatch {path.name}: {got} vs {want}",
                      file=sys.stderr)
                return False
        return True
    except Exception as exc:
        print(f"[now] IMERG COG check failed: {exc!r}", file=sys.stderr)
        return False


def class_areas_km2(cls: np.ndarray, valid: np.ndarray,
                    area_km2: np.ndarray) -> dict[str, float]:
    """km² per TPI class label over valid pixels."""
    return {LABELS[c]: float((area_km2 * ((cls == c) & valid)).sum())
            for c in range(4)}


def critical_from_classes(areas: dict[str, float]) -> float:
    return float(areas[LABELS[2]] + areas[LABELS[3]])


def run_checks(terrain: dict | None, r_zero_violations: int,
               imerg_cog_ok: bool | None = None) -> dict:
    """Run-level sanity gates; alerts are published only when all pass.
    terrain: static_fetch.terrain_checks() (None on the legacy npz path)."""
    checks = dict(terrain or {"terrain_checked": False})
    checks["r_zero_tpi_zero"] = r_zero_violations == 0
    checks["r_zero_violations"] = r_zero_violations
    if imerg_cog_ok is not None:
        checks["imerg_cogs_match_source"] = imerg_cog_ok
    checks["passed"] = all(v for k, v in checks.items()
                           if isinstance(v, bool) and k != "passed")
    return checks


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
    imerg_cogs = write_imerg_cogs(imerg_daily, args.out_dir / "cog" / "imerg_early")
    imerg_cog_ok = imerg_cogs_match_source(imerg_daily, imerg_cogs)
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
    r_zero_violations = 0   # pixels with R == 0 but TPI > 0 (must stay 0)
    for k in range(FORECAST_DAYS + 1):
        day = today + dt.timedelta(days=k)
        rain3 = rain3_today if k == 0 else tpi.projected_rain3(obs_stack[-3:], fc_stack, k)
        out = tpi.tpi(rain3, sm, _clim_for_day(static, day), t_slide, t_flood)
        r0 = out["r"] == 0
        r_zero_violations += int(np.count_nonzero(
            r0 & ((np.nan_to_num(out["tpi_slide"]) > 0)
                  | (np.nan_to_num(out["tpi_flood"]) > 0))))
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
    # ONE computation: per-hazard class areas for today; critical_km2 is
    # derived from them (Alto + Crítico), so the two can never disagree.
    classes_km2 = {
        "landslide": class_areas_km2(slides[0], valid_s0, area_km2),
        "flood": class_areas_km2(floods[0], valid_f0, area_km2)}
    today_mask = ((slides[0] >= 2) & valid_s0) | ((floods[0] >= 2) & valid_f0)
    summary = {
        "generated_at": dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z"),
        "critical_km2": {
            "slide": critical_from_classes(classes_km2["landslide"]),
            "flood": critical_from_classes(classes_km2["flood"]),
            "any": float((area_km2 * today_mask).sum()),
            "any_today_or_d1": float((area_km2 * crit_mask).sum())},
        "critical_km2_def": "today, class Alto or Crítico (TPI >= 0.4); "
                            "any = slide OR flood; any_today_or_d1 adds D+1",
        "nodata_km2": {"slide": float((area_km2 * ~valid_s0).sum()),
                        "flood": float((area_km2 * ~valid_f0).sum())},
        "people_exposed": people_critical,
        "people_critical": people_critical,   # v4 metrics key (same value)
        "forecast_source": "noaa-gfs",        # v4 metrics key (D+1..D+3)
        "imerg_source": imerg_source,         # honest stand-in labeling
        "classes_km2": classes_km2,
        "data_dates": {"imerg": imerg_date, "smap": smap_date,
                        "gfs_issued": gfs_issued,
                        "clim_doy_used": static.get("clim_doy_used")},
    }
    files["summary.json"] = summary

    prev_path = args.out_dir / "prev_classes.npz"
    prev = None
    if prev_path.exists():
        with np.load(prev_path) as z:
            # per-hazard keys since L27; older snapshots only have "worst"
            prev = {h: z[h] if h in z.files else z["worst"]
                    for h in ("landslide", "flood") if h in z.files or "worst" in z.files}
    terrain = static.get("checks")
    if terrain is None and "slope_deg" in static:   # legacy npz path
        from live.static_fetch import terrain_checks
        terrain = terrain_checks(np.asarray(static["slope_deg"]), t_slide)
    checks = run_checks(terrain, r_zero_violations, imerg_cog_ok)
    # every alert must name its municipality: boundaries must be loaded
    n_muni = now_geojson._n_features(gj_dir / "municipal_boundaries.geojson")
    checks["municipalities_loaded"] = n_muni > 0
    checks["municipalities"] = n_muni
    checks["passed"] = checks["passed"] and n_muni > 0
    alerts_paused = not checks["passed"]
    payload = alerts.emit_hazard_alerts(
        prev, {"landslide": (slides[0], slides[1]),
               "flood": (floods[0], floods[1])},
        dt.datetime.now(dt.UTC), transform)
    where = now_geojson.municipality_lookup(gj_dir)
    for c in payload["candidates"]:
        c.update(where(*c["centroid"]) or {"municipality": None, "uf": None})
        c["kind"] = "tpi"
    payload["paused"] = alerts_paused
    if alerts_paused:
        payload["candidates"] = []
        payload["paused_reason"] = "run checks failed: " + ", ".join(
            k for k, v in checks.items() if v is False)
    files["alerts.json"] = payload
    # the alert baseline advances only when alerts were actually evaluated:
    # a paused run must not swallow the upgrades it suppressed
    if not alerts_paused:
        files["prev_classes.npz"] = render.npz_bytes(
            worst=np.maximum(np.maximum(slides[0], floods[0]),
                             np.maximum(slides[1], floods[1])),
            landslide=np.maximum(slides[0], slides[1]),
            flood=np.maximum(floods[0], floods[1]))

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
    manifest["checks"] = checks
    manifest["alerts_paused"] = alerts_paused
    manifest["verified"] = checks["passed"]   # app gates /alerts on this
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
