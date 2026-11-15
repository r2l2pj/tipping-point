"""Per-run static inputs for the local TPI path (L20/L25).

L25: no GEE at runtime. Climatology and terrain are read as COGs —
first from the public GitHub 'static-v1' Release, then the local
data/static/ tree, then the legacy Neon object-storage copy:

  data/static/south_brazil/clim/clim_imerg_0p1.tif  (L30-clim, preferred)
    whole-year p90/p95/p99 at IMERG native 0.1 deg, 1098 bands
    (doy-1)*3+{p90,p95,p99}, built by live/build_clim.py. Source order:
    local file -> downloaded whole from the static-v1 Release into that
    path (CI keeps it in actions/cache) -> legacy per-doy COGs below.
    Only the 3 bands of each needed doy are read, then interpolated to the
    dst grid exactly like the IMERG obs (live.grid.regrid_da, linear).
    NOW_CLIM_SOURCE=legacy forces the legacy COGs (verification only).
  data/static/south_brazil/clim/doy_NNN.tif   legacy 3-band float32:
    p90/p95/p99 (mm, 3-day IMERG accumulation percentiles) on the
    NOW_BOUNDS 1150x1000 EPSG:4326 dst grid; doys 284..341 only.
  terrain/{slope_deg,hand_m}.tif  float32 on the same grid; T_slide and
    T_flood are DERIVED here (live/tpi.py formulas) and sanity-checked
    (terrain_checks). Precomputed t_*.tif are never read.

Doys are on the leap-year calendar (date(2000,1,1)+doy-1 = month/day),
the calendar both climatology products were built on.

The COGs are exported once from the GEE assets (allowed one-off under
L25) and republished by lead-analysis; the Now job only ever reads them.
"""
from __future__ import annotations

import datetime as dt
import os
import shutil
import sys
import urllib.request
from pathlib import Path

import numpy as np

from live.config import NOW_BOUNDS, REPO_ROOT

LOCAL_STATIC = REPO_ROOT / "data" / "static"
NEON_PREFIX = "static/south_brazil"
# L26-static: the canonical host is the GitHub Release (Neon hit quota);
# assets are flat basenames (doy_318.tif, slope_deg.tif, gpw_pop.tif).
STATIC_RELEASE_BASE = os.environ.get(
    "STATIC_RELEASE_BASE",
    "https://github.com/r2l2pj/tipping-point/releases/download/static-v1")


def _public_base() -> str:
    endpoint = os.environ.get("NEON_S3_ENDPOINT", "").rstrip("/")
    bucket = os.environ.get("NEON_S3_BUCKET", "")
    return f"{endpoint}/{bucket}/data"


def _read_cog(rel: str) -> np.ndarray | None:
    """band-stack array from a static COG: GitHub Release asset first
    (canonical), then the local file, then the legacy Neon URL — GDAL
    range-reads only the bytes needed."""
    import rasterio

    local = LOCAL_STATIC / "south_brazil" / rel
    candidates = [f"{STATIC_RELEASE_BASE}/{Path(rel).name}"]
    if local.exists():
        candidates.append(local)
    if os.environ.get("NEON_S3_ENDPOINT"):
        candidates.append(f"{_public_base()}/{NEON_PREFIX}/{rel}")
    for src in candidates:
        try:
            with rasterio.open(str(src)) as ds:
                return ds.read().astype(np.float64)
        except (rasterio.errors.RasterioIOError, OSError, RuntimeError):
            continue
    return None


CLIM_FILE_NAME = "clim_imerg_0p1.tif"
CLIM_FILE = LOCAL_STATIC / "south_brazil" / "clim" / CLIM_FILE_NAME
PCT_NAMES = ("p90", "p95", "p99")
# static["clim_source"] values; run_now.CLIM_SOURCE_OK must equal CLIM_SOURCE_NEW
CLIM_SOURCE_NEW = "imerg-final-0p1"
CLIM_SOURCE_LEGACY = "legacy-doy-cogs"


def leap_doy(day: dt.date) -> int:
    """Calendar (month, day) -> 1..366 on the leap-year calendar."""
    return (dt.date(2000, day.month, day.day) - dt.date(2000, 1, 1)).days + 1


def _clim_file() -> Path | None:
    """The whole-year 0.1 deg climatology: local copy, else downloaded once
    from the static-v1 Release into the local path. None -> legacy COGs."""
    if os.environ.get("NOW_CLIM_SOURCE", "").lower() == "legacy":
        return None
    if CLIM_FILE.exists():
        return CLIM_FILE
    url = f"{STATIC_RELEASE_BASE}/{CLIM_FILE_NAME}"
    tmp = CLIM_FILE.with_suffix(".part")
    try:
        CLIM_FILE.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=300) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
        tmp.replace(CLIM_FILE)
        print(f"[static_fetch] downloaded {url} -> {CLIM_FILE}", file=sys.stderr)
        return CLIM_FILE
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        print(f"[static_fetch] {CLIM_FILE_NAME} unavailable ({exc}); "
              f"falling back to per-doy COGs", file=sys.stderr)
        return None


def _clim_from_file(path: Path, doy: int, bounds) -> dict[str, np.ndarray] | None:
    """Read the 3 bands of `doy` and interpolate them to the dst grid the
    same way run_now regrids IMERG obs (regrid_da, linear on centers)."""
    import rasterio
    import xarray as xr

    from live.grid import dst_lats_lons, regrid_da

    first = (doy - 1) * 3 + 1
    with rasterio.open(path) as ds:
        if ds.count < first + 2:
            return None
        arr = ds.read([first, first + 1, first + 2]).astype(np.float64)
        scale = ds.scales[first - 1] if ds.scales else 1.0
        t = ds.transform
    if scale not in (None, 1.0):
        arr = arr * scale
    lats = t.f + (np.arange(arr.shape[1]) + 0.5) * t.e
    lons = t.c + (np.arange(arr.shape[2]) + 0.5) * t.a
    dlats, dlons = dst_lats_lons(tuple(bounds))
    out = {}
    for k, name in enumerate(PCT_NAMES):
        da = xr.DataArray(arr[k][::-1], dims=("lat", "lon"),
                          coords={"lat": lats[::-1], "lon": lons})
        out[name] = regrid_da(da, dlats, dlons)
    return out


def _clim_for_doy(doy: int) -> dict[str, np.ndarray] | None:
    arr = _read_cog(f"clim/doy_{doy:03d}.tif")
    if arr is None or arr.shape[0] < 3:
        return None
    return {"p90": arr[0], "p95": arr[1], "p99": arr[2]}


# terrain sanity on the 0.01 deg NOW grid (static-v1 slope: p99 ~20.8 deg).
# The stale pre-L26 rasters had p99 7.6 deg -> T_slide ~0 everywhere.
SLOPE_P99_RANGE = (12.0, 45.0)
T_SLIDE_MIN_FRAC = 0.01


def terrain_checks(slope: np.ndarray, t_slide: np.ndarray) -> dict:
    p99 = float(np.nanpercentile(slope, 99))
    frac = float(np.mean(t_slide > 0))
    return {
        "slope_p99_deg": round(p99, 2),
        "slope_p99_ok": SLOPE_P99_RANGE[0] <= p99 <= SLOPE_P99_RANGE[1],
        "t_slide_nonzero_frac": round(frac, 4),
        "t_slide_ok": frac >= T_SLIDE_MIN_FRAC,
    }


HAND_90M = LOCAL_STATIC / "south_brazil" / "terrain" / "hand_90m.tif"


def hand_block_mean(path: Path, bounds: tuple[float, ...] = NOW_BOUNDS,
                    res_deg: float = 0.01) -> np.ndarray | None:
    """3 arc-sec HAND proxy (z - focalMin(z, 1500 m), NASADEM) -> mean per
    0.01 deg dst cell. The 90 m grid nests exactly (12 x 12 px per cell),
    so this is a chunked block mean, no warping. None if the file does not
    match the NOW grid."""
    import rasterio
    from rasterio.windows import Window

    w, s, e, n = bounds
    with rasterio.open(path) as ds:
        k = res_deg / ds.res[0]
        if (abs(k - round(k)) > 1e-6 or abs(ds.bounds.left - w) > 1e-6
                or abs(ds.bounds.top - n) > 1e-6):
            return None
        k = int(round(k))
        H, W = ds.height // k, ds.width // k
        out = np.full((H, W), np.nan)
        for r0 in range(0, H, 50):
            r1 = min(H, r0 + 50)
            blk = ds.read(1, window=Window(0, r0 * k, W * k, (r1 - r0) * k),
                          masked=True).astype(np.float64).filled(np.nan)
            with np.errstate(invalid="ignore"), __import__("warnings").catch_warnings():
                __import__("warnings").simplefilter("ignore", RuntimeWarning)
                out[r0:r1] = np.nanmean(
                    blk.reshape(r1 - r0, k, W, k), axis=(1, 3))
    return out


def _terrain() -> tuple[np.ndarray, np.ndarray, dict] | None:
    """T_slide/T_flood DERIVED from the static-v1 slope_deg + hand_m COGs
    (same formulas as live/tpi.py). Precomputed t_*.tif files are never
    read: the committed ones were stale and silently zeroed landslide TPI."""
    from live.tpi import terrain_flood, terrain_slide

    slope = _read_cog("terrain/slope_deg.tif")
    hand = _read_cog("terrain/hand_m.tif")
    if slope is None or hand is None:
        return None
    slope, hand = slope[0], hand[0]
    hand_source = "static-v1 hand_m.tif (0.01 deg)"
    if HAND_90M.exists():          # fetched + cached by now.yml (R2 uses it too)
        h90 = hand_block_mean(HAND_90M)
        if h90 is not None and h90.shape == hand.shape:
            hand = np.where(np.isfinite(h90), h90, hand)
            hand_source = "static-v1 hand_90m.tif, 12x12 block mean"
    t_slide = terrain_slide(slope)
    t_flood = terrain_flood(hand, slope)
    checks = terrain_checks(slope, t_slide)
    checks["hand_source"] = hand_source
    return t_slide, t_flood, checks


CLIM_REFERENCE = Path(__file__).with_name("clim_reference.json")


def clim_checks(path: Path | None, bounds: tuple[float, ...] = NOW_BOUNDS,
                reference: Path = CLIM_REFERENCE) -> dict:
    """Geolocation gates for the climatology file (conductor 2026-11-15):
    - lat axis strictly monotonic (north-up) and covering the whole domain;
    - p90/p95/p99 at >= 3 reference points within tolerance of an
      INDEPENDENT IMERG-Final percentile computed by separate code
      (live/clim_reference.json). The scrambled legacy COGs miss by up to
      19.6 mm; the 0.1 deg file matches within 0.1 mm."""
    import json

    import rasterio

    out = {"clim_lat_monotonic": False, "clim_covers_domain": False,
           "clim_ref_ok": False}
    if path is None:
        return out
    ref = json.loads(Path(reference).read_text())
    with rasterio.open(path) as ds:
        t = ds.transform
        lats = t.f + (np.arange(ds.height) + 0.5) * t.e
        out["clim_lat_monotonic"] = bool(t.e < 0 and np.all(np.diff(lats) < 0))
        w, s, e, n = bounds
        out["clim_covers_domain"] = bool(ds.bounds.left <= w and ds.bounds.right >= e
                                         and ds.bounds.bottom <= s and ds.bounds.top >= n)
        first = (int(ref["doy"]) - 1) * 3 + 1
        worst = 0.0
        for name, pt in ref["points"].items():
            got = [float(v) for v in next(ds.sample(
                [(pt["lon"], pt["lat"])], indexes=[first, first + 1, first + 2]))]
            for g, pname in zip(got, ("p90", "p95", "p99")):
                want = float(pt[pname])
                tol = max(float(ref["tolerance_mm"]), float(ref["tolerance_rel"]) * want)
                worst = max(worst, abs(g - want) / tol)
    out["clim_ref_points"] = len(ref["points"])
    out["clim_ref_worst_tol_ratio"] = round(worst, 3)
    out["clim_ref_ok"] = bool(worst <= 1.0 and len(ref["points"]) >= 3)
    return out


def load_static(bounds: tuple[float, ...] = NOW_BOUNDS,
                days: list[dt.date] | None = None,
                cache_dir: Path | None = None) -> dict:
    """static inputs for the local TPI loop.

    days: the run's today + forecast days; their leap-calendar doys select
    which climatology bands/COGs are read. Missing doys fall back to the
    nearest available one (clim_for_day).
    """
    terr = _terrain()
    if terr is None:
        sys.exit("no terrain COGs on Neon/local (data/static/south_brazil"
                 "/terrain/) — export the static assets first")
    days = days or [dt.datetime.now(dt.UTC).date()]
    doys = sorted({leap_doy(d) for d in days})
    clim, missing = {}, []
    path = _clim_file()
    source = CLIM_SOURCE_NEW if path else CLIM_SOURCE_LEGACY
    for doy in doys:
        c = _clim_from_file(path, doy, bounds) if path else None
        if c is None:
            c = _clim_for_doy(doy)
            if c is not None:
                source = CLIM_SOURCE_LEGACY   # any legacy doy taints the run
        (clim.__setitem__(doy, c) if c is not None else missing.append(doy))
    if not clim:
        sys.exit(f"no climatology COGs for doys {doys}")
    if missing:
        print(f"[static_fetch] {missing} doy(s) missing — nearest "
              f"available will be used", file=sys.stderr)
    checks = dict(terr[2])
    checks.update(clim_checks(path if source == CLIM_SOURCE_NEW else None,
                              tuple(bounds)))
    return {"t_slide": terr[0], "t_flood": terr[1], "clim": clim,
            "checks": checks, "clim_source": source}


def clim_for_day(static: dict, day: dt.date) -> dict[str, np.ndarray]:
    """p90/p95/p99 for `day`; falls back to the nearest fetched doy.
    The requested->used mapping is recorded in static["clim_doy_used"] so
    the run manifest can publish it — a substituted climatology day must
    never be silent."""
    doy = leap_doy(day)
    clim = static["clim"]
    used = doy if doy in clim else min(
        clim, key=lambda d: min(abs(d - doy), 366 - abs(d - doy)))
    static.setdefault("clim_doy_used", {})[doy] = used
    if used != doy:
        print(f"[static_fetch] CLIM SUBSTITUTION: doy {doy} missing — "
              f"using nearest doy {used} for {day.isoformat()}",
              file=sys.stderr)
    return clim[used]
