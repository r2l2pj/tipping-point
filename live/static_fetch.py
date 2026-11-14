"""Per-run static inputs for the local TPI path (L20).

Instead of the monolithic static/sul_static.npz (a ~12 h GEE tile build),
each run downloads only what it needs from the exported project assets:

  climatology — projects/<GEE_PROJECT>/assets/tipping-point/static/
    south_brazil/clim_rain/doy_NNN: per-day-of-year image, bands
    p90/p95/p99, int16 = mm*10 (3-day IMERG accumulation percentiles).
  terrain — static/south_brazil/slope (deg) and hand (m), from which
    t_slide/t_flood are derived with the same formulas build_static
    applies in EE (slope.unitScale(15,30), hand.unitScale(5,15),
    t_flood masked where slope > 5 deg).

Everything is cached under static/cache/ as .npy: climatology entries are
per-doy (a run needs today + D+1..D+3 = 4 doys), terrain is one-off.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import numpy as np

from live.build_static import _download_band, _ee_init
from live.config import NOW_BOUNDS, STATIC_DIR

CACHE_DIR = STATIC_DIR / "cache"
CLIM_PARENT = "static/south_brazil/clim_rain"
TERRAIN = ("slope", "hand")


def _asset_root(ee) -> str:
    import os
    proj = os.environ.get("GEE_PROJECT", "").strip()
    return f"projects/{proj}/assets/tipping-point"


def _cache(cache_dir: Path, name: str) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{name}.npy"


def _band_cached(ee, source, band: str, name: str,
                 cache_dir: Path, scale_m: int = 1000) -> np.ndarray:
    path = _cache(cache_dir, name)
    if path.exists():
        return np.load(path)
    image = source if isinstance(source, ee.Image) else ee.Image(source)
    arr = _download_band(ee, image, band, scale_m)
    np.save(path, arr)
    return arr


def _terrain(ee, cache_dir: Path) -> tuple[np.ndarray, np.ndarray]:
    """t_slide/t_flood derived from slope+hand, per build_static formulas."""
    t_slide_p, t_flood_p = (_cache(cache_dir, "t_slide"),
                            _cache(cache_dir, "t_flood"))
    if t_slide_p.exists() and t_flood_p.exists():
        return np.load(t_slide_p), np.load(t_flood_p)
    root = _asset_root(ee)
    try:
        slope = _band_cached(ee, f"{root}/static/south_brazil/slope",
                             "slope", "slope_deg", cache_dir)
        hand = _band_cached(ee, f"{root}/static/south_brazil/hand",
                            "hand", "hand_m", cache_dir)
    except (RuntimeError, OSError, ValueError,
            ee.ee_exception.EEException):
        # exported slope/hand assets not written yet — compute the same
        # rasters interactively from the NASADEM catalog (identical to
        # build_static._terrain: slope = Terrain.slope(dem),
        # hand = dem - focalMin(HAND_PROXY_RADIUS_M))
        from live.config import HAND_PROXY_RADIUS_M
        dem = ee.Image("NASA/NASADEM_HGT/001").select("elevation")
        slope_img = ee.Terrain.slope(dem).rename("slope_deg")
        hand_img = dem.subtract(
            dem.focalMin(radius=HAND_PROXY_RADIUS_M, units="meters")
        ).rename("hand_m")
        slope = _band_cached(ee, slope_img, "slope_deg", "slope_deg",
                             cache_dir)
        hand = _band_cached(ee, hand_img, "hand_m", "hand_m", cache_dir)
    t_slide = np.clip((slope - 15.0) / (30.0 - 15.0), 0.0, 1.0)
    t_flood = np.clip(1.0 - (hand - 5.0) / (15.0 - 5.0), 0.0, 1.0)
    t_flood[slope > 5.0] = 0.0
    np.save(t_slide_p, t_slide)
    np.save(t_flood_p, t_flood)
    return t_slide, t_flood


def _clim_for_doy(ee, doy: int, cache_dir: Path,
                  root: str) -> dict[str, np.ndarray]:
    """p90/p95/p99 mm arrays for one doy (int16 mm*10 -> float mm)."""
    out = {}
    for band in ("p90", "p95", "p99"):
        raw = _band_cached(
            ee, f"{root}/{CLIM_PARENT}/doy_{doy:03d}", band,
            f"doy_{doy:03d}_{band}", cache_dir)
        out[band] = raw.astype(np.float64) / 10.0
    return out


def load_static(bounds: tuple[float, ...] = NOW_BOUNDS,
                days: list[dt.date] | None = None,
                cache_dir: Path = CACHE_DIR) -> dict:
    """static inputs for the local TPI loop.

    days: the run's today + forecast days; their day-of-year values select
    which climatology assets are fetched (each is cached, so a 3-hourly run
    only downloads a doy once).
    """
    ee = _ee_init()
    t_slide, t_flood = _terrain(ee, cache_dir)
    days = days or [dt.datetime.now(dt.UTC).date()]
    doys = sorted({d.timetuple().tm_yday for d in days})
    root = _asset_root(ee)
    clim = {}
    missing = []
    for doy in doys:
        try:
            clim[doy] = _clim_for_doy(ee, doy, cache_dir, root)
        except (RuntimeError, OSError, ValueError,
                ee.ee_exception.EEException) as exc:
            missing.append((doy, exc))
    if not clim:
        sys.exit(f"no climatology assets for doys {doys}: {missing[-1][1]}")
    if missing:
        print(f"[static_fetch] {len(missing)} doy(s) missing "
              f"({[d for d, _ in missing]}) — nearest available will be used",
              file=sys.stderr)
    return {"t_slide": t_slide, "t_flood": t_flood, "clim": clim}


def clim_for_day(static: dict, day: dt.date) -> dict[str, np.ndarray]:
    """p90/p95/p99 for `day`; falls back to the nearest fetched doy."""
    doy = day.timetuple().tm_yday
    clim = static["clim"]
    if doy in clim:
        return clim[doy]
    nearest = min(clim, key=lambda d: min(abs(d - doy), 366 - abs(d - doy)))
    return clim[nearest]
