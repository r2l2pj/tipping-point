"""Per-run static inputs for the local TPI path (L20/L25).

L25: no GEE at runtime. Climatology and terrain are read as COGs —
first from the public GitHub 'static-v1' Release, then the local
data/static/ tree, then the legacy Neon object-storage copy:

  data/static/south_brazil/clim/doy_NNN.tif   3-band float32: p90/p95/p99
    (mm, 3-day IMERG accumulation percentiles) on the NOW_BOUNDS
    1150x1000 EPSG:4326 dst grid.
  terrain/{slope_deg,hand_m}.tif  float32 on the same grid; T_slide and
    T_flood are DERIVED here (live/tpi.py formulas) and sanity-checked
    (terrain_checks). Precomputed t_*.tif are never read.

The COGs are exported once from the GEE assets (allowed one-off under
L25) and republished by lead-analysis; the Now job only ever reads them.
"""
from __future__ import annotations

import datetime as dt
import os
import sys
from pathlib import Path

import numpy as np

from live.config import NOW_BOUNDS, REPO_ROOT

LOCAL_STATIC = REPO_ROOT / "data" / "static"
NEON_PREFIX = "static/south_brazil"
# L26-static: the canonical host is the GitHub Release (Neon hit quota);
# assets are flat basenames (doy_318.tif, slope_deg.tif, gpw_pop.tif).
STATIC_RELEASE_BASE = os.environ.get(
    "STATIC_RELEASE_BASE",
    "https://github.com/r2l2pj/tipping-point-data/releases/download/static-v1")


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
    t_slide = terrain_slide(slope)
    t_flood = terrain_flood(hand, slope)
    return t_slide, t_flood, terrain_checks(slope, t_slide)


def load_static(bounds: tuple[float, ...] = NOW_BOUNDS,
                days: list[dt.date] | None = None,
                cache_dir: Path | None = None) -> dict:
    """static inputs for the local TPI loop.

    days: the run's today + forecast days; their day-of-year values select
    which climatology COGs are read. Missing doys fall back to the
    nearest available one (clim_for_day).
    """
    terr = _terrain()
    if terr is None:
        sys.exit("no terrain COGs on Neon/local (data/static/south_brazil"
                 "/terrain/) — export the static assets first")
    days = days or [dt.datetime.now(dt.UTC).date()]
    doys = sorted({d.timetuple().tm_yday for d in days})
    clim, missing = {}, []
    for doy in doys:
        c = _clim_for_doy(doy)
        (clim.__setitem__(doy, c) if c is not None else missing.append(doy))
    if not clim:
        sys.exit(f"no climatology COGs for doys {doys}")
    if missing:
        print(f"[static_fetch] {missing} doy(s) missing — nearest "
              f"available will be used", file=sys.stderr)
    return {"t_slide": terr[0], "t_flood": terr[1], "clim": clim,
            "checks": terr[2]}


def clim_for_day(static: dict, day: dt.date) -> dict[str, np.ndarray]:
    """p90/p95/p99 for `day`; falls back to the nearest fetched doy.
    The requested->used mapping is recorded in static["clim_doy_used"] so
    the run manifest can publish it — a substituted climatology day must
    never be silent."""
    doy = day.timetuple().tm_yday
    clim = static["clim"]
    used = doy if doy in clim else min(
        clim, key=lambda d: min(abs(d - doy), 366 - abs(d - doy)))
    static.setdefault("clim_doy_used", {})[doy] = used
    if used != doy:
        print(f"[static_fetch] CLIM SUBSTITUTION: doy {doy} missing — "
              f"using nearest doy {used} for {day.isoformat()}",
              file=sys.stderr)
    return clim[used]
