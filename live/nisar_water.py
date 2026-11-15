"""L61: NISAR inside the flood index — observed-water mask + combined layer + agreement.

nisar_water: HH backscatter < −18 dB (smooth open water) from the published
fixed-stretch nisar_frame PNGs (gray = 1 + (dB − DB_MIN)/(DB_MAX − DB_MIN)·254,
alpha 0 = no data; decoded exactly back to dB), warped onto the TPI grid, outside
permanent water. For a map day D only passes with D − MAX_AGE_DAYS <= date <= D
are used (never a later pass); where several passes overlap, the most recent wins.

tpi_flood_nisar = max(TPI_flood, NISAR_WEIGHT · nisar_water): observed water raises the
flood index to Critical (0.8 > 0.6); elsewhere the normal TPI (no pass != zero risk).

nisar_agreement(tpi, water): share of NISAR water pixels the TPI flagged High+ (> 0.4)
and Critical (> 0.6), the domain area share flagged, and lift = hit / area share.
"""
from __future__ import annotations

import datetime as dt
import io
from pathlib import Path

import numpy as np

WATER_DB = -18.0
NISAR_WEIGHT = 0.8
MAX_AGE_DAYS = 12
HIGH, CRIT = 0.4, 0.6
LABEL = "Flood risk with NISAR-observed water (candidate)"
NOTE = ("Flood index (TPI) raised to Critical only where the latest NISAR radar pass up to 12 days "
        "before the map day saw open water (HH < −18 dB, outside permanent water) ON THE RIVER "
        "FLOODPLAIN (HAND ≤ 5 m): max(TPI, 0.8 × water on floodplain). NISAR water off the floodplain "
        "(mostly flooded rice paddies / reservoirs) is shown in the NISAR-water overlay but does not "
        "raise risk. Candidate water — never confirmed; no pass ≠ no risk.")


def gray_to_db(gray: np.ndarray, db_min: float, db_max: float) -> np.ndarray:
    """Inverse of the fixed stretch (gray 1..255 -> dB); 0 -> NaN."""
    g = np.asarray(gray, np.float64)
    return np.where(g > 0, db_min + (g - 1) / 254.0 * (db_max - db_min), np.nan)


def fixed_stretch(entry: dict) -> tuple[float, float] | None:
    sd = entry.get("stretch_db") or {}
    lg = entry.get("legend") if isinstance(entry.get("legend"), dict) else {}
    lo = sd.get("db_min", lg.get("db_min"))
    hi = sd.get("db_max", lg.get("db_max"))
    return (float(lo), float(hi)) if lo is not None and hi is not None else None


def hh_frames(entries: list[dict]) -> list[dict]:
    """HH frames rendered with a fixed stretch (others cannot be decoded to dB)."""
    out = []
    for e in entries:
        pol = e.get("polarization") or ("HV" if "_hv" in e.get("id", "") else "HH")
        if (e.get("kind") in ("nisar_frame", "observed") and pol == "HH" and e.get("date")
                and e.get("png") and e.get("bounds") and fixed_stretch(e)):
            out.append(e)
    # one entry per png (legacy aliases like nisar_pre share a png with a dated id)
    seen, uniq = set(), []
    for e in sorted(out, key=lambda e: e["date"]):
        if e["png"] not in seen:
            seen.add(e["png"])
            uniq.append(e)
    return uniq


def passes_for(frames: list[dict], day: dt.date, max_age: int = MAX_AGE_DAYS) -> list[dict]:
    lo = day - dt.timedelta(days=max_age)
    return [e for e in frames if lo <= dt.date.fromisoformat(e["date"]) <= day]


def _frame_db_on(entry: dict, png_root: Path, dst_shape, dst_transform) -> np.ndarray:
    """Frame dB (NaN = no data) on the target EPSG:4326 grid (nearest)."""
    import rasterio
    from PIL import Image
    from rasterio.transform import from_bounds
    from rasterio.warp import Resampling, reproject, transform_bounds

    im = Image.open(io.BytesIO((png_root / entry["png"]).read_bytes()))
    a = np.asarray(im.convert("LA"))
    gray = np.where(a[..., 1] > 0, a[..., 0], 0).astype(np.uint8)
    w, s, e, n = entry["bounds"]
    x0, y0, x1, y1 = transform_bounds("EPSG:4326", "EPSG:3857", w, s, e, n)
    src_tr = from_bounds(x0, y0, x1, y1, gray.shape[1], gray.shape[0])
    out = np.zeros(dst_shape, np.uint8)
    reproject(gray, out, src_transform=src_tr, src_crs="EPSG:3857", src_nodata=0,
              dst_transform=dst_transform, dst_crs="EPSG:4326", dst_nodata=0,
              resampling=Resampling.nearest)
    del rasterio
    return gray_to_db(out, *fixed_stretch(entry))


def water_mask(frames: list[dict], day: dt.date, png_root: Path, shape, transform,
               perm_water: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray, list[dict]]:
    """(water bool, observed bool, passes used) for map day `day` on the grid."""
    used = passes_for(frames, day)
    db = np.full(shape, np.nan)
    for e in sorted(used, key=lambda e: e["date"]):          # most recent wins
        f = _frame_db_on(e, png_root, shape, transform)
        db = np.where(np.isfinite(f), f, db)
    observed = np.isfinite(db)
    water = observed & (db < WATER_DB)
    if perm_water is not None:
        water &= ~perm_water
        observed &= ~perm_water
    return water, observed, used


def combine(tpi: np.ndarray, water: np.ndarray, floodplain: np.ndarray | None = None) -> np.ndarray:
    """max(TPI, 0.8) where NISAR water lies on the floodplain (HAND <= 5 m); water off
    the floodplain (rice paddies, reservoirs) never raises risk. No floodplain mask ->
    no raise (conservative)."""
    t = np.asarray(tpi, np.float32)
    if floodplain is None:
        return t.copy()
    raise_ = water & floodplain
    return np.where(raise_, np.fmax(t, NISAR_WEIGHT), t).astype(np.float32)


def agreement(tpi: np.ndarray, water: np.ndarray, km2: np.ndarray | None = None,
              floodplain: np.ndarray | None = None) -> dict:
    """Share of NISAR water the TPI flagged (High+, Critical), domain area share, lift;
    with `floodplain` (HAND <= 5 m), the share of NISAR water OUTSIDE it — single-date
    HH < -18 dB also lights flooded rice paddies / irrigation (likely, not river flood)."""
    dom = np.isfinite(tpi)
    w = water & dom
    a = np.ones(tpi.shape) if km2 is None else km2
    wa = float(a[w].sum())
    da = float(a[dom].sum())
    out = {"water_km2": round(wa, 3) if km2 is not None else None, "water_px": int(w.sum())}
    if floodplain is not None:
        out["outside_floodplain_pct"] = (round(100 * float(a[w & ~floodplain].sum()) / wa, 1)
                                         if wa else None)
        out["raised_km2"] = round(float(a[w & floodplain].sum()), 3) if km2 is not None else None
    for name, cut in (("high_plus", HIGH), ("critical", CRIT)):
        flag = dom & (tpi > cut)
        hit = float(a[w & flag].sum()) / wa if wa else None
        share = float(a[flag].sum()) / da if da else None
        out[name] = {"hit_pct": None if hit is None else round(100 * hit, 1),
                     "area_share_pct": None if share is None else round(100 * share, 2),
                     "lift": round(hit / share, 2) if hit is not None and share else None}
    return out
