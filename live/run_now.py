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
from live.now_manifest import OPTIONAL_PRODUCTS, prune_products


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


def _load_static(static_dir: Path, days: list[dt.date] | None = None,
                 bounds: tuple[float, ...] = NOW_BOUNDS):
    """Static inputs: full sul_static.npz when present (legacy), else L20 —
    fetch only the needed climatology doys + terrain from the GEE assets,
    cached under static/cache/."""
    path = static_dir / STATIC_NPZ
    if path.exists():
        return dict(np.load(path))
    from live import static_fetch
    return static_fetch.load_static(bounds=bounds, days=days)


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


CLIM_SOURCE_OK = "imerg-final-0p1"


SMAP_FILL_KM = 9.0


GSW_OCCURRENCE = (Path(__file__).resolve().parents[1] / "data" / "now" / "static"
                  / "jrc_gsw" / "gsw_occurrence_2021.tif")
PERMANENT_WATER_OCC_PCT = 50       # external review 2026-11-15: no TPI over lakes/lagoons


def now_water_mask(bounds, height: int, width: int,
                   occ_path: Path = GSW_OCCURRENCE,
                   occ_min: float = PERMANENT_WATER_OCC_PCT) -> tuple[np.ndarray | None, str]:
    """Permanent water on the dst grid: JRC GSW occurrence (cell mean) >= occ_min %
    OR MCD12Q1 IGBP 17. TPI is undefined there (nodata). (None, reason) when
    neither input is available — then nothing is masked."""
    import rasterio
    from rasterio.warp import Resampling, reproject

    tr = dst_transform(tuple(bounds))
    water = np.zeros((height, width), bool)
    used = []
    try:
        if Path(occ_path).exists():
            occ = np.full((height, width), np.nan, np.float32)
            with rasterio.open(occ_path) as ds:
                src = ds.read(1).astype(np.float32)
                if ds.nodata is not None:
                    src[src == ds.nodata] = np.nan
                reproject(src, occ, src_transform=ds.transform, src_crs=ds.crs,
                          src_nodata=np.nan, dst_transform=tr, dst_crs="EPSG:4326",
                          dst_nodata=np.nan, resampling=Resampling.average)
            water |= np.nan_to_num(occ, nan=0.0) >= occ_min
            used.append(f"JRC GSW occurrence >= {occ_min:g} %")
        from live.now_geojson import IGBP_WATER, LC_TYPE1
        if LC_TYPE1.exists():
            lc = np.zeros((height, width), dtype="uint8")
            with rasterio.open(LC_TYPE1) as ds:
                reproject(rasterio.band(ds, 1), lc, dst_transform=tr,
                          dst_crs="EPSG:4326", resampling=Resampling.mode)
            water |= lc == IGBP_WATER
            used.append("MCD12Q1 IGBP 17")
    except Exception as exc:  # noqa: BLE001 - masking is additive, never fatal
        print(f"[now] water mask failed: {exc!r}", file=sys.stderr)
        return None, f"water mask failed: {exc!r}"
    if not used:
        return None, "no JRC GSW / MCD12Q1 raster available — TPI not water-masked"
    return water, " OR ".join(used)


def now_land_mask(bounds, height: int, width: int) -> np.ndarray | None:
    """Land on the dst grid: inside the IBGE Brazil outline and NOT MCD12Q1
    IGBP 17 (permanent water: ocean, lagoons). None if either input is
    missing — then nothing is gap-filled."""
    try:
        import json as _json

        import rasterio
        import rasterio.features
        from rasterio.warp import Resampling, reproject
        from shapely.geometry import shape

        from live.now_geojson import BRAZIL_OUTLINE, IGBP_WATER, LC_TYPE1
        if not (BRAZIL_OUTLINE.exists() and LC_TYPE1.exists()):
            return None
        tr = dst_transform(tuple(bounds))
        geoms = [shape(f["geometry"]) for f in
                 _json.loads(BRAZIL_OUTLINE.read_text())["features"]]
        inside = rasterio.features.rasterize(
            geoms, out_shape=(height, width), transform=tr, fill=0,
            default_value=1, dtype="uint8").astype(bool)
        lc = np.zeros((height, width), dtype="uint8")
        with rasterio.open(LC_TYPE1) as ds:
            reproject(rasterio.band(ds, 1), lc, dst_transform=tr,
                      dst_crs="EPSG:4326", resampling=Resampling.mode)
        return inside & (lc != IGBP_WATER)
    except Exception as exc:  # no mask -> no fill (never fill the sea)
        print(f"[now] land mask failed: {exc!r}", file=sys.stderr)
        return None


def fill_smap_gaps(sm, src_lats, src_lons, src_vals, dst_lats, dst_lons,
                   max_km: float = SMAP_FILL_KM, allow: np.ndarray | None = None):
    """Fill NaN dst pixels from the nearest valid SMAP source within max_km
    (equirectangular km at the local latitude), only where `allow` (land)
    is True; allow=None fills nothing. Returns (sm, filled_mask)."""
    from scipy.spatial import cKDTree

    sm = np.array(sm, dtype=np.float64, copy=True)
    filled = np.zeros(sm.shape, dtype=bool)
    if allow is None:
        return sm, filled
    gap = ~np.isfinite(sm) & allow
    v = np.ravel(src_vals)
    ok = np.isfinite(v)
    if not gap.any() or not ok.any():
        return sm, filled
    la, lo = np.ravel(src_lats)[ok], np.ravel(src_lons)[ok]
    lat0 = np.deg2rad(np.mean(dst_lats))
    def xy(lat, lon):
        return np.column_stack([lat * 111.195, lon * 111.195 * np.cos(lat0)])
    tree = cKDTree(xy(la, lo))
    gi, gj = np.nonzero(gap)
    dist, idx = tree.query(xy(np.asarray(dst_lats)[gi], np.asarray(dst_lons)[gj]))
    near = dist <= max_km
    sm[gi[near], gj[near]] = v[ok][idx[near]]
    filled[gi[near], gj[near]] = True
    return sm, filled


def certainty_for(basis: str) -> str:
    """Alert certainty is capped by its rain basis (conductor 2026-11-15):
    'high' only when the trigger is in observed data."""
    return {"observed": "high", "observed+forecast": "medium"}.get(basis, "low")


def frame_basis(day: dt.date, today: dt.date, gfs_issued: str | None) -> dict:
    """Honesty rule 2: what a 3-day frame ending on `day` is made of under
    L28b (observed IMERG strictly before `today`, NOAA GFS from `today`)."""
    window = [day - dt.timedelta(days=j) for j in (2, 1, 0)]
    obs = [d.isoformat() for d in window if d < today]
    fc = [d.isoformat() for d in window if d >= today]
    basis = "observed+forecast" if obs and fc else ("forecast" if fc else "observed")
    issued = (gfs_issued or "")[:16].replace("T", " ") + " UTC" if gfs_issued else "?"
    def _md(iso):
        d = dt.date.fromisoformat(iso)
        return d.strftime("%b ") + str(d.day)
    if basis == "observed+forecast":
        label = (f"observed to {_md(obs[-1])} + NOAA GFS forecast for "
                 f"{', '.join(_md(x) for x in fc)} (issued {issued})")
    elif basis == "forecast":
        label = f"NOAA GFS forecast (issued {issued})"
    else:
        label = f"observed (IMERG Early) to {_md(obs[-1])}"
    return {"basis": basis, "obs_days": obs, "forecast_days": fc,
            "obs_through": obs[-1] if obs else None,
            "gfs_issued": gfs_issued if fc else None, "basis_label": label}


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
              for k in range(FORECAST_DAYS + 1)],
        bounds=tuple(args.bounds))
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
    smap_fields = fetch.smap_date_fields(smap_date, dt.datetime.now(dt.UTC))
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
    # L31: a dst pixel whose nearest SMAP source is a gap is filled from the
    # nearest VALID SMAP cell if that cell is within one SMAP cell (~9 km);
    # beyond that it stays nodata. Coastal/lagoon cells were the flood
    # hotspots lost to the gap mask (lead-analysis, 2026-11-15).
    land = now_land_mask(tuple(args.bounds), lats.size, lons.size)
    sm, sm_filled = fill_smap_gaps(sm, sm_lats, sm_lons, sm_vals, lats, lons,
                                   allow=land)
    fc_stack = np.stack(
        [regrid_da(fc_daily_da.isel(time=i), lats, lons)
         for i in range(fc_daily_da.sizes["time"])])

    # ---- L28b alignment: observed IMERG through D-1 ONLY (a partial
    # current day would shrink the window), GFS forecast for D..D+3;
    # frame D+k = 3-day accumulation ending on day D+k --------------------
    def _dates(da):
        return [dt.date.fromisoformat(str(np.datetime_as_string(t, unit="D")))
                for t in da.time.values]
    series = {d: obs_stack[i] for i, d in enumerate(_dates(imerg_daily))
              if d < today}
    series.update({d: fc_stack[i] for i, d in enumerate(_dates(fc_daily_da))
                   if d >= today})
    obs_through = max((d for d in series if d < today), default=None)
    t_slide, t_flood = static["t_slide"], static["t_flood"]

    water, water_basis = now_water_mask(tuple(args.bounds), lats.size, lons.size)
    days, slides, floods, slide_tpi, flood_tpi = [], [], [], [], []
    r_zero_violations = 0   # pixels with R == 0 but TPI > 0 (must stay 0)
    for k in range(FORECAST_DAYS + 1):
        day = today + dt.timedelta(days=k)
        rain3 = tpi.frame_rain3(series, day)
        if rain3 is None:
            raise SystemExit(
                f"[now] frame {day}: missing daily input among "
                f"{[str(day - dt.timedelta(days=j)) for j in (2, 1, 0)]} "
                f"(obs through {obs_through}, fc {_dates(fc_daily_da)})")
        out = tpi.tpi(rain3, sm, _clim_for_day(static, day), t_slide, t_flood)
        if water is not None:            # permanent water: TPI undefined (nodata)
            out["tpi_slide"] = np.where(water, np.nan, out["tpi_slide"])
            out["tpi_flood"] = np.where(water, np.nan, out["tpi_flood"])
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
            fb = frame_basis(days[k], today, gfs_issued)
            layers.append({  # v4 layer shape {id, date, png, cog, legend} + bounds
                "id": f"{kind}_d{k}", "date": days[k].isoformat(),
                "png": rel, "cog": cog_rel, "legend": LABELS,
                "bounds": png_bounds,
                # L28b: D+0 already contains GFS rain for day D
                "forecast": fb["basis"] != "observed",
                "partly_forecast": fb["basis"] == "observed+forecast",
                **fb,
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
    # stats domain (audit 2026-11-15): Brazilian land only (IBGE outline,
    # MCD12Q1 water excluded) — the bbox also covers UY/AR/PY; the map still
    # shows the whole bbox. No mask -> whole bbox, stated in stats_domain.
    dom = land if land is not None else np.ones(area_km2.shape, dtype=bool)
    stats_domain = ("Brazilian land in the bbox (IBGE outline, MCD12Q1 water excluded)"
                    if land is not None else "whole bbox (Brazil land mask unavailable)")
    valid_s0 = np.isfinite(slide_tpi[0]) & dom
    valid_f0 = np.isfinite(flood_tpi[0]) & dom
    valid_s1 = np.isfinite(slide_tpi[1]) & dom
    valid_f1 = np.isfinite(flood_tpi[1]) & dom

    def _ge(c):  # today-or-D+1 mask at class >= c (2 = Alto, 3 = Crítico)
        return (((slides[0] >= c) & valid_s0) | ((slides[1] >= c) & valid_s1)
                | ((floods[0] >= c) & valid_f0) | ((floods[1] >= c) & valid_f1))
    alert_mask, crit_mask = _ge(2), _ge(3)
    pop = static.get("pop")
    people_alert = None if pop is None else float(np.nansum(pop * alert_mask))
    people_critical = None if pop is None else float(np.nansum(pop * crit_mask))
    # ONE computation: per-hazard class areas for today; critical_km2 (A8:
    # Crítico only, TPI > 0.6) and alert_km2 (Alto or Crítico) derive from them
    classes_km2 = {
        "landslide": class_areas_km2(slides[0], valid_s0, area_km2),
        "flood": class_areas_km2(floods[0], valid_f0, area_km2)}

    def _today(c):
        return ((slides[0] >= c) & valid_s0) | ((floods[0] >= c) & valid_f0)
    summary = {
        "generated_at": dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z"),
        "critical_km2": {
            "slide": classes_km2["landslide"][LABELS[3]],
            "flood": classes_km2["flood"][LABELS[3]],
            "any": float((area_km2 * _today(3)).sum()),
            "any_today_or_d1": float((area_km2 * crit_mask).sum())},
        "critical_km2_def": "today, class Crítico only (TPI > 0.6, A8); any = slide OR flood; "
                            "any_today_or_d1 adds D+1; Brazilian land only",
        "alert_km2": {
            "slide": critical_from_classes(classes_km2["landslide"]),
            "flood": critical_from_classes(classes_km2["flood"]),
            "any": float((area_km2 * _today(2)).sum()),
            "any_today_or_d1": float((area_km2 * alert_mask).sum())},
        "alert_km2_def": "today, class Alto or Crítico (TPI > 0.4) = 'High or above'; "
                         "any = slide OR flood; any_today_or_d1 adds D+1; Brazilian land only",
        "stats_domain": stats_domain,
        "water_mask": water_basis,
        "water_masked_km2": None if water is None else float((area_km2 * water).sum()),
        "stats_domain_km2": float((area_km2 * dom).sum()),
        "nodata_km2": {"slide": float((area_km2 * (~np.isfinite(slide_tpi[0]) & dom)).sum()),
                        "flood": float((area_km2 * (~np.isfinite(flood_tpi[0]) & dom)).sum())},
        "filled_km2": float((area_km2 * sm_filled).sum()),
        "soil_fill_note": ("L31: S at pixels whose nearest SMAP L4 cell is a "
                           "gap is taken from the nearest valid SMAP cell "
                           "within 9 km (one SMAP cell); farther stays nodata"),
        # people audit B3: the static pop on the TPI grid is a 0.01 deg GEE
        # export of the 30" count (~30 % low) — never published; the R3
        # response step (native-grid exact cell fractions) fills people_*
        "people_exposed": None,
        "people_alert": None,
        "people_critical": None,
        "_unpublished_people_estimates": {"people_alert": people_alert,
                                          "people_critical": people_critical,
                                          "note": "0.01 deg GEE pop, ~30 % low; diagnostic only"},
        "forecast_source": "noaa-gfs",        # v4 metrics key (D+1..D+3)
        "imerg_source": imerg_source,         # honest stand-in labeling
        "classes_km2": classes_km2,
        "data_dates": {"imerg": imerg_date, **smap_fields,
                        "gfs_issued": gfs_issued,
                        "clim_doy_used": static.get("clim_doy_used"),
                        "clim_source": static.get("clim_source")},
    }
    files["summary.json"] = summary

    prev_path = args.out_dir / "prev_classes.npz"
    prev = None
    active_state: dict = {}
    if prev_path.exists():
        with np.load(prev_path) as z:
            if "active_state" in z.files:
                import json as _json
                active_state = _json.loads(str(z["active_state"]))
            # per-hazard keys since L27; older snapshots only have "worst"
            prev = {h: z[h] if h in z.files else z["worst"]
                    for h in ("landslide", "flood") if h in z.files or "worst" in z.files}
    terrain = static.get("checks")
    if terrain is None and "slope_deg" in static:   # legacy npz path
        from live.static_fetch import terrain_checks
        terrain = terrain_checks(np.asarray(static["slope_deg"]), t_slide)
    checks = run_checks(terrain, r_zero_violations, imerg_cog_ok)
    # the legacy per-doy clim COGs are spatially scrambled (two squashed
    # domain copies, found 2026-11-15): only the IMERG-Final 0.1 deg
    # climatology may back a verified run
    checks["clim_source"] = static.get("clim_source", "legacy-doy-cogs")
    checks["clim_source_ok"] = checks["clim_source"] == CLIM_SOURCE_OK
    checks["passed"] = checks["passed"] and checks["clim_source_ok"]
    # every alert must name its municipality: boundaries must be loaded
    n_muni = now_geojson._n_features(gj_dir / "municipal_boundaries.geojson")
    checks["municipalities_loaded"] = n_muni > 0
    checks["municipalities"] = n_muni
    checks["passed"] = checks["passed"] and n_muni > 0
    alerts_paused = not checks["passed"]
    now_utc = dt.datetime.now(dt.UTC)
    now_iso = now_utc.isoformat().replace("+00:00", "Z")
    layers_hz = {"landslide": (slides[0], slides[1]),
                 "flood": (floods[0], floods[1])}
    payload = alerts.emit_hazard_alerts(prev, layers_hz, now_utc, transform)
    active, _ = alerts.active_alerts(layers_hz, now_utc, transform, None)
    where = now_geojson.municipality_lookup(gj_dir)

    def _enrich(c):
        c.update(where(*c["centroid"]) or {"municipality": None, "uf": None})
        c["kind"] = "tpi"
        day = today if c.get("when") == "today" else today + dt.timedelta(days=1)
        fb = frame_basis(day, today, gfs_issued)
        c.update(fb)
        c["certainty"] = certainty_for(fb["basis"])
        if fb["forecast_days"]:
            c["basis_note"] = (f"based on forecast rain (NOAA GFS, issued "
                               f"{fb['basis_label'].split('issued ')[-1].rstrip(')')})")

    for c in payload["candidates"] + active:
        _enrich(c)
    new_active_state = alerts.keyed_active(active, active_state, now_iso)
    # notifications = what the sending rules (upgrade, quiet hours) let out
    # this run; active = every current Alto/Crítico area for the public page
    payload["notifications"] = payload["candidates"]
    payload["active"] = sorted(active, key=lambda a: (-a["class"], -a["area_km2"]))
    payload["candidates_note"] = "deprecated alias of notifications"
    payload["paused"] = alerts_paused
    if alerts_paused:
        payload["candidates"] = payload["notifications"] = payload["active"] = []
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
            flood=np.maximum(floods[0], floods[1]),
            active_state=np.array(__import__("json").dumps(new_active_state)))

    # ---- write everything, manifest LAST ------------------------------------
    updated_at = dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z")
    manifest = build_manifest(
        tuple(args.bounds), updated_at,
        {"imerg": imerg_date, **smap_fields, "gfs_issued": gfs_issued,
         "imerg_obs_through": str(obs_through), "forecast_from": str(today),
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
