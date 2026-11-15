"""REPLAY v2 (L44): R on the 90 m TPI grid + permanent-water mask.

Diagnosis of v1: R was computed in Earth Engine at ~1 km from IMERG 0.1 deg
sums + percentile bands exported at 10 km with NEAREST resampling
(gee/tpi.py: no .resample('bilinear')), so the r_series COGs carry ~10 km
stair steps and the TPI inherits them. v2 computes R locally:

  P3 = 3-day IMERG sum over [D-3d, D)  (observed-band convention,
       docs/conventions-dates.md) from the upstream IMERG daily cubes
       (intermediate/upstream/<case>/imerg_daily.npz, 0.1 deg).
  p90/p95/p99 = the case climatology PER 0.1 deg cell: 2001-2023, 3-day sums
       ending at dates D +- 30 d step 3 (tools/upstream_rescore.py
       climatology(), applied cell-wise).
  Both are BILINEAR-interpolated to the 90 m TPI grid (cell centres) and
  scored with live.tpi.rain_score. S = the case s_series COG bilinear to
  90 m (identical to v1); T = the static t_slide/t_flood COGs.
  TPI = R^wR * S^wS * T^wT (config tpi_weights), NaN on permanent water:
  JRC GSW occurrence >= 50% (30 m tiles, MAX-resampled to the 90 m grid)
  OR MCD12Q1 IGBP class 17 (GEE MODIS/061/MCD12Q1, the event year).

Issued-GFS frames (L28b): the d<k> band of issue I depicts E = I+k and sums
calendar days E-2..E — observed IMERG for days < I, the GFS I 00Z run for
days >= I (gfs_issued.npz, 0.25 deg, bilinear); percentiles at target E;
S held at the s_series band dated I-1.

Outputs (side by side with v1, nothing overwritten):
  data/<case>/cog/<case>_tpi_{slide,flood}_<ref>_v2.tif   (band = YYYYMMDD)
  data/<case>/cog/<case>_tpi_gfs_<issue>_v2.tif           (bands d1..d3_*)
  plus a per-case verification dict (domain-mean R v1 vs v2, water-mask
  stats) returned by build_case().

    set -a; . ../tipping-point-data/.env; set +a
    PYTHONPATH=.:pipeline .venv/bin/python tools/replay_v2.py [--case rs_2024 ...]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.request
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject

REPO = Path(__file__).resolve().parents[1]
for _p in (str(REPO), str(REPO / "pipeline")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from live.tpi import rain_score  # noqa: E402
from tp.config import thresholds  # noqa: E402

CASE_REFS = {"rs_2024": "20240502", "wa_2025": "20251210"}
# imerg_daily.npz / gfs_issued.npz live in the l35-rain worktree (L35-U2);
# override with REPLAY_V2_UPSTREAM_ROOT.
UP_ROOT = Path(os.environ.get(
    "REPLAY_V2_UPSTREAM_ROOT",
    str(Path.home() / "code/r2l2pj/tipping-point-data-wt/l35-rain"
        "/intermediate/upstream")))
CACHE = Path(os.environ.get(
    "REPLAY_V2_CACHE",
    str(Path.home() / ".cache/lead-analysis/replay-v2")))
GSW_BASE = "https://storage.googleapis.com/global-surface-water/downloads2021"
# JRC GSW 10x10-deg tiles covering each case grid (tile named by its NW corner).
GSW_TILES = {"rs_2024": ("60W_20S", "60W_30S", "50W_20S", "50W_30S"),
             "wa_2025": ("130W_50N",)}
GSW_OCC_MIN = 50.0          # permanent-water occurrence cut (%)
MCD12Q1_YEAR = {"rs_2024": 2024, "wa_2025": 2025}
MCD12Q1_RES = 0.005         # ~500 m lon/lat request grid (convert_mcd12q1 RES)
MCD12Q1_WATER = 17          # IGBP Water bodies
CLIM_YEARS = range(2001, 2024)
CLIM_OFFSETS = range(-30, 31, 3)
CLIM_WIN = 3
CLIM_MIN_N = 300            # upstream_rescore.climatology's completeness floor
IMERG_STEP = 0.1


def _d(s) -> dt.date:
    s = str(s)
    return dt.date(int(s[:4]), int(s[4:6]), int(s[6:8]))


# ---------------------------------------------------------------- pure math
def cell_climatology(mm: np.ndarray, days: list[dt.date], target: dt.date,
                     years=CLIM_YEARS, offsets=CLIM_OFFSETS,
                     win: int = CLIM_WIN, min_n: int = CLIM_MIN_N
                     ) -> np.ndarray:
    """(3, H, W) p90/p95/p99 per cell of `win`-day sums ending at
    date(y, target.month, target.day) + o — upstream_rescore.climatology
    applied per 0.1 deg cell. Cells with < min_n complete windows -> NaN."""
    idx = {d: i for i, d in enumerate(days)}
    vals = []
    for y in years:
        try:
            ref = dt.date(y, target.month, target.day)
        except ValueError:
            continue
        for o in offsets:
            e = ref + dt.timedelta(days=o)
            inds = [idx.get(e - dt.timedelta(days=k)) for k in range(1, win + 1)]
            if any(i is None for i in inds):
                continue
            vals.append(mm[inds[0]] + mm[inds[1]] + mm[inds[2]])
    if not vals:
        return np.full((3, mm.shape[1], mm.shape[2]), np.nan)
    st = np.stack(vals).astype(np.float64)      # (N, H, W)
    n = np.isfinite(st).sum(axis=0)
    out = np.full((3, st.shape[1], st.shape[2]), np.nan)
    good = n >= min_n
    if good.any():
        p = np.nanpercentile(st[:, good], [90, 95, 99], axis=0)
        out[:, good] = p
    return out


def observed_p3(mm: np.ndarray, days: list[dt.date], end: dt.date,
                win: int = CLIM_WIN) -> np.ndarray:
    """3-day sum over [end-win, end) on the cube grid; NaN if a day is absent."""
    idx = {d: i for i, d in enumerate(days)}
    inds = [idx.get(end - dt.timedelta(days=k)) for k in range(1, win + 1)]
    if any(i is None for i in inds):
        return np.full(mm.shape[1:], np.nan, np.float64)
    out = mm[inds[0]].astype(np.float64)
    for i in inds[1:]:
        out = out + mm[i]
    return out


def frame_day_source(issue: dt.date, k: int) -> list[tuple[dt.date, str, int]]:
    """L28b window for the d<k> frame of issue I: days E-2..E (E = I+k),
    each tagged ('obs', day-index in a daily cube) or ('gfs', cube k index)."""
    if not 1 <= k <= 3:
        raise ValueError("k must be 1..3")
    e = issue + dt.timedelta(days=k)
    out = []
    for day in (e - dt.timedelta(days=j) for j in (2, 1, 0)):
        out.append((day, "obs" if day < issue else "gfs", (day - issue).days))
    return out


def combine_tpi(r: np.ndarray, s: np.ndarray, t: np.ndarray,
                water: np.ndarray | None = None,
                weights=(1 / 3, 1 / 3, 1 / 3)) -> np.ndarray:
    """TPI = R^wR*S^wS*T^wT float32; NaN where any input is non-finite or the
    cell is permanent water (same nodata propagation as gee/tpi.py
    _combine_case_tpi)."""
    valid = np.isfinite(r) & np.isfinite(s) & np.isfinite(t)
    if water is not None:
        valid &= ~water
    with np.errstate(invalid="ignore"):
        v = (np.power(np.clip(r, 0, None), weights[0])
             * np.power(np.clip(s, 0, None), weights[1])
             * np.power(np.clip(t, 0, None), weights[2]))
    return np.where(valid, v, np.nan).astype(np.float32)


# ---------------------------------------------------------------- grids
def _src_transform(lats: np.ndarray, lons: np.ndarray) -> object:
    """North-up affine for a cube whose lat/lon are ascending cell centres."""
    dy = float(np.median(np.diff(lats)))
    dx = float(np.median(np.diff(lons)))
    return from_origin(float(lons[0]) - dx / 2, float(lats[-1]) + dy / 2, dx, dy)


def cube_to_grid(arr: np.ndarray, lats: np.ndarray, lons: np.ndarray,
                 like) -> np.ndarray:
    """[lat asc, lon asc] cube slab -> the 90 m `like` grid, bilinear."""
    a = np.asarray(arr, dtype=np.float64)[::-1]
    out = np.full((like.height, like.width), np.nan)
    reproject(a, out, src_transform=_src_transform(lats, lons),
              src_crs="EPSG:4326", src_nodata=np.nan,
              dst_transform=like.transform, dst_crs=like.crs,
              dst_nodata=np.nan, resampling=Resampling.bilinear)
    return out


def cog_band_to_grid(path: Path, band: int, like) -> np.ndarray:
    """COG band -> the `like` grid, bilinear (v1's R/S upsampling recipe)."""
    with rasterio.open(path) as s:
        a = s.read(band).astype(np.float64)
        if s.nodata is not None and np.isfinite(s.nodata):
            a = np.where(a == s.nodata, np.nan, a)
        out = np.full((like.height, like.width), np.nan)
        reproject(a, out, src_transform=s.transform, src_crs=s.crs,
                  src_nodata=np.nan, dst_transform=like.transform,
                  dst_crs=like.crs, dst_nodata=np.nan,
                  resampling=Resampling.bilinear)
    return out


def cube_covers(cube_lats, cube_lons, like) -> bool:
    """Every like-grid cell centre inside the cube's cell-centre extent."""
    t = like.transform
    ys = t.f + (np.arange(like.height) + 0.5) * t.e
    xs = t.c + (np.arange(like.width) + 0.5) * t.a
    return (ys.min() >= float(cube_lats[0]) and ys.max() <= float(cube_lats[-1])
            and xs.min() >= float(cube_lons[0])
            and xs.max() <= float(cube_lons[-1]))


# ---------------------------------------------------------------- water mask
def _fetch_gsw(tile: str, raw: Path) -> Path:
    path = raw / f"occurrence_{tile}v1_4_2021.tif"
    if not path.exists():
        raw.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(  # noqa: S310 - fixed https bucket URL
            f"{GSW_BASE}/occurrence/{path.name}", path)
    return path


def gsw_water(case: str, like, raw_dir: Path) -> np.ndarray:
    """True where ANY 30 m GSW pixel in the 90 m cell has occurrence >= 50 %
    (max resampling of the occurrence band onto the TPI grid)."""
    occ = np.zeros((like.height, like.width), np.uint8)
    for tile in GSW_TILES[case]:
        with rasterio.open(_fetch_gsw(tile, raw_dir)) as src:
            dst = np.zeros((like.height, like.width), np.uint8)
            reproject(rasterio.band(src, 1), dst, src_nodata=255,
                      dst_transform=like.transform, dst_crs=like.crs,
                      dst_nodata=0, resampling=Resampling.max)
        occ = np.maximum(occ, dst)
    return occ >= GSW_OCC_MIN


def mcd12q1_water(case: str, like) -> np.ndarray:
    """True where the 90 m cell's centre falls in a MCD12Q1 IGBP-17 (water
    bodies) ~500 m cell — GEE MODIS/061/MCD12Q1 of the event year."""
    from gee.common import ee_init  # noqa: PLC0415 - lazy: GEE-only step
    ee_init()
    import ee  # noqa: PLC0415
    import io  # noqa: PLC0415

    t = like.transform
    w = t.c
    n = t.f
    e = w + like.width * t.a
    s = n + like.height * t.e
    img = ee.Image(f"MODIS/061/MCD12Q1/{MCD12Q1_YEAR[case]}_01_01") \
        .select("LC_Type1")
    h, wd = (round((n - s) / MCD12Q1_RES), round((e - w) / MCD12Q1_RES))
    req = {"expression": img, "fileFormat": "NPY",
           "grid": {"dimensions": {"width": wd, "height": h},
                    "affineTransform": {"scaleX": MCD12Q1_RES, "shearX": 0,
                                        "translateX": w, "shearY": 0,
                                        "scaleY": -MCD12Q1_RES,
                                        "translateY": n},
                    "crsCode": "EPSG:4326"}}
    raw = ee.data.computePixels(req)
    arr = np.load(io.BytesIO(raw) if isinstance(raw, (bytes, bytearray))
                  else raw)
    lc = np.asarray(arr["LC_Type1"], dtype=np.uint8)
    # warp on a {0,255} flag: a {0,1} uint8 mask + dst_nodata=0 resamples
    # pathologically (every dst cell becomes 1); 255 thresholded at >127 is
    # the verified-good recipe (max resample = any 500 m IGBP-17 cell wins).
    water = np.zeros((like.height, like.width), np.uint8)
    reproject((lc == MCD12Q1_WATER).astype(np.uint8) * 255, water,
              src_transform=from_origin(w, n, MCD12Q1_RES, MCD12Q1_RES),
              src_crs="EPSG:4326", dst_transform=like.transform,
              dst_crs=like.crs, dst_nodata=0, resampling=Resampling.max)
    return water > 127


def water_mask(case: str, like, raw_dir: Path) -> dict:
    g = gsw_water(case, like, raw_dir)
    m = mcd12q1_water(case, like)
    return {"gsw_ge50": g, "mcd12q1_17": m, "water": g | m}


# ---------------------------------------------------------------- build
def _write_multiband(path: Path, arrays: list[np.ndarray], names: list[str],
                     like_profile: dict) -> None:
    prof = dict(like_profile, count=len(arrays), dtype="float32",
                nodata=np.nan, compress="deflate", predictor=3, tiled=True)
    with rasterio.open(path, "w", **prof) as dst:
        for i, (a, n) in enumerate(zip(arrays, names, strict=True), 1):
            dst.write_band(i, a)
            dst.set_band_description(i, n)


def build_case(case: str, up_root: Path = UP_ROOT,
               data_root: Path | None = None, raw_dir: Path | None = None
               ) -> dict:
    """Write the _v2 COGs for one case; return the verification dict."""
    data_root = data_root or REPO / "data"
    raw_dir = raw_dir or CACHE / "gsw"
    ref = CASE_REFS[case]
    cog = data_root / case / "cog"
    like_path = cog / f"{case}_tpi_slide_{ref}.tif"
    weights = thresholds().get("tpi_weights", [1 / 3, 1 / 3, 1 / 3])

    with rasterio.open(like_path) as like:
        frame_days = [_d(d) for d in like.descriptions]
        with rasterio.open(cog / f"{case}_t_slide_{ref}.tif") as ts:
            t_slide = ts.read(1).astype(np.float64)
            t_slide = np.where(np.isfinite(t_slide), t_slide, np.nan)
        with rasterio.open(cog / f"{case}_t_flood_{ref}.tif") as tf:
            t_flood = tf.read(1).astype(np.float64)
            t_flood = np.where(np.isfinite(t_flood), t_flood, np.nan)

        im = np.load(up_root / case / "imerg_daily.npz")
        gf = np.load(up_root / case / "gfs_issued.npz")
        days = [_d(x) for x in im["dates"]]
        if not cube_covers(im["lat"], im["lon"], like):
            raise SystemExit(
                f"{case}: imerg_daily.npz box does not cover the case grid — "
                "extend the box in tools/upstream_rain.py BOXES and re-run "
                "tools/upstream_rain_gee.py")
        gf_issues = [_d(x) for x in gf["issue"]]
        gf_idx = {d: i for i, d in enumerate(gf_issues)}

        wm = water_mask(case, like, raw_dir)
        water = wm["water"]

        s_path = cog / f"{case}_s_series_{ref}.tif"
        r_path = cog / f"{case}_r_series_{ref}.tif"
        sdesc = list(rasterio.open(s_path).descriptions)
        rdesc = list(rasterio.open(r_path).descriptions)

        def s_on(day: dt.date) -> np.ndarray:
            return cog_band_to_grid(s_path, sdesc.index(f"{day:%Y%m%d}") + 1,
                                    like)

        clim_cache: dict[dt.date, np.ndarray] = {}

        def clim_on(target: dt.date):
            # cache the coarse percentile cube (tiny); interpolate on demand
            # so 90 m maps don't accumulate past the memory cap
            if target not in clim_cache:
                clim_cache[target] = cell_climatology(im["mm"], days, target)
            cube = clim_cache[target]
            return tuple(cube_to_grid(cube[i], im["lat"], im["lon"], like)
                         for i in range(3))

        # ---- observed series -------------------------------------------
        prof = dict(like.profile)
        out_slide, out_flood = [], []
        verify_rows = []
        for D in frame_days:
            p3 = cube_to_grid(observed_p3(im["mm"], days, D),
                              im["lat"], im["lon"], like)
            p90, p95, p99 = clim_on(D)
            r2 = rain_score(p3, p90, p95, p99)
            s90 = s_on(D)
            out_slide.append(combine_tpi(r2, s90, t_slide, water, weights))
            out_flood.append(combine_tpi(r2, s90, t_flood, water, weights))
            r1 = cog_band_to_grid(r_path, rdesc.index(f"{D:%Y%m%d}") + 1, like)
            dom = np.isfinite(r1) & np.isfinite(r2)
            verify_rows.append({
                "date": D.isoformat(),
                "r_v1_mean": round(float(r1[dom].mean()), 5) if dom.any() else None,
                "r_v2_mean": round(float(r2[dom].mean()), 5) if dom.any() else None,
                "r_v1_minus_v2_mean": round(float((r1 - r2)[dom].mean()), 5)
                if dom.any() else None,
                "n": int(dom.sum())})
        names = [f"{D:%Y%m%d}" for D in frame_days]
        _write_multiband(cog / f"{case}_tpi_slide_{ref}_v2.tif",
                         out_slide, names, prof)
        _write_multiband(cog / f"{case}_tpi_flood_{ref}_v2.tif",
                         out_flood, names, prof)

        # ---- issued-GFS frames ------------------------------------------
        gfs_written, gfs_skipped = [], []
        issues = sorted(_d(p.stem.rsplit("_", 1)[1].removesuffix("_v2"))
                        for p in cog.glob(f"{case}_tpi_gfs_*.tif")
                        if not p.stem.endswith("_v2"))
        day_idx = {d: i for i, d in enumerate(days)}

        def obs_on(day: dt.date) -> np.ndarray:
            i = day_idx.get(day)
            if i is None:
                return np.full((like.height, like.width), np.nan)
            return cube_to_grid(im["mm"][i], im["lat"], im["lon"], like)

        for I in issues:
            ii = gf_idx.get(I)
            prev = I - dt.timedelta(days=1)
            if ii is None or f"{prev:%Y%m%d}" not in sdesc:
                gfs_skipped.append(I.isoformat())
                continue
            s90 = s_on(prev)
            arrays, bnames = [], []
            for k in (1, 2, 3):
                e = I + dt.timedelta(days=k)
                p3 = np.zeros((like.height, like.width))
                for day, src, ki in frame_day_source(I, k):
                    slab = (obs_on(day) if src == "obs"
                            else cube_to_grid(gf["mm"][ii, ki],
                                              gf["lat"], gf["lon"], like))
                    p3 = p3 + slab          # NaN propagates
                p90, p95, p99 = clim_on(e)
                r2 = rain_score(p3, p90, p95, p99)
                for haz, t in (("slide", t_slide), ("flood", t_flood)):
                    arrays.append(combine_tpi(r2, s90, t, water, weights))
                    bnames.append(f"d{k}_{haz}")
            _write_multiband(cog / f"{case}_tpi_gfs_{I:%Y%m%d}_v2.tif",
                             arrays, bnames, prof)
            gfs_written.append(I.isoformat())

        finite_tpi = np.isfinite(t_slide) | np.isfinite(t_flood)
        res = {
            "case": case,
            "ref": ref,
            "grid": [like.height, like.width],
            "water_cells": int((water & finite_tpi).sum()),
            "water_cells_frac_of_finite": round(
                float((water & finite_tpi).sum()) / float(finite_tpi.sum()), 5)
                if finite_tpi.any() else None,
            "gsw_ge50_cells": int(wm["gsw_ge50"].sum()),
            "mcd12q1_17_cells": int(wm["mcd12q1_17"].sum()),
            "series": verify_rows,
            "r_v1_mean": round(float(np.nanmean(
                [r["r_v1_mean"] for r in verify_rows])), 5),
            "r_v2_mean": round(float(np.nanmean(
                [r["r_v2_mean"] for r in verify_rows])), 5),
            "gfs_v2_written": gfs_written,
            "gfs_skipped": gfs_skipped,
        }
        return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--case", action="append", choices=sorted(CASE_REFS))
    ap.add_argument("--up-root", type=Path, default=UP_ROOT)
    args = ap.parse_args(argv)
    out = {c: build_case(c, up_root=args.up_root)
           for c in (args.case or sorted(CASE_REFS))}
    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
