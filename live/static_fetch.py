"""Per-run static inputs for the local TPI path (L20/L25).

L25: no GEE at runtime. Climatology and terrain are read as COGs —
first from the local data/static/ tree when present, else from the
public Neon object-storage copy:

  data/static/south_brazil/clim/doy_NNN.tif   3-band float32: p90/p95/p99
    (mm, 3-day IMERG accumulation percentiles) on the NOW_BOUNDS
    1150x1000 EPSG:4326 dst grid.
  data/static/south_brazil/terrain/{slope_deg,hand_m,t_slide,t_flood}.tif
    float32 on the same grid; t_* are precomputed products, slope/hand
    the raw rasters they derive from.

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


def _public_base() -> str:
    endpoint = os.environ.get("NEON_S3_ENDPOINT", "").rstrip("/")
    bucket = os.environ.get("NEON_S3_BUCKET", "")
    return f"{endpoint}/{bucket}/data"


def _read_cog(rel: str) -> np.ndarray | None:
    """band-stack array from a static COG: local file first, Neon URL
    fallback (GDAL range-reads only the bytes needed)."""
    import rasterio

    local = LOCAL_STATIC / "south_brazil" / rel
    candidates = ([local] if local.exists() else []) + [
        f"{_public_base()}/{NEON_PREFIX}/{rel}"]
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


def _terrain() -> tuple[np.ndarray, np.ndarray] | None:
    ts = _read_cog("terrain/t_slide.tif")
    tf = _read_cog("terrain/t_flood.tif")
    if ts is not None and tf is not None:
        return ts[0], tf[0]
    # derive from raw rasters (same formulas as the EE export)
    slope = _read_cog("terrain/slope_deg.tif")
    hand = _read_cog("terrain/hand_m.tif")
    if slope is None or hand is None:
        return None
    slope, hand = slope[0], hand[0]
    t_slide = np.clip((slope - 15.0) / (30.0 - 15.0), 0.0, 1.0)
    t_flood = np.clip(1.0 - (hand - 5.0) / (15.0 - 5.0), 0.0, 1.0)
    t_flood[slope > 5.0] = 0.0
    return t_slide, t_flood


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
    return {"t_slide": terr[0], "t_flood": terr[1], "clim": clim}


def clim_for_day(static: dict, day: dt.date) -> dict[str, np.ndarray]:
    """p90/p95/p99 for `day`; falls back to the nearest fetched doy."""
    doy = day.timetuple().tm_yday
    clim = static["clim"]
    if doy in clim:
        return clim[doy]
    nearest = min(clim, key=lambda d: min(abs(d - doy), 366 - abs(d - doy)))
    return clim[nearest]
