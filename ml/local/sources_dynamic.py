"""Dynamic + climate data sources for the local (no-GEE) ML pipeline.

Sources (lead-ml verified 2026-11-15):

* IMERG Early daily ``GPM_3IMERGDE.07`` and monthly ``GPM_3IMERGM.07``
  via GES DISC OPeNDAP (``gpm1.gesdisc.eosdis.nasa.gov``) — one
  ``.dap.nc4`` hyperslab per day/month, ``EARTHDATA_TOKEN`` bearer.
* SMAP L4 ``SPL4SMGP.008`` geophysical fields and ``SPL4SMLM.008``
  land-model constants via cloud OPeNDAP (``opendap.earthdata.nasa.gov``)
  on the 9 km EASE-2 grid (1624 x 3856; rows = constant lat, cols =
  constant lon; fill -9999 -> NaN).
* NASA POWER ``IMERG_PRECTOT`` per-point daily series (2001..today,
  ~575 KB/point) — point backend for the global 3b training table.
* GFS pgrb2 APCP from the NOAA AWS bucket ``noaa-gfs-bdp-pds`` via
  ``live.fetch`` helpers (``_gfs_pick_run``, ``_gfs_apcp_grid``,
  ``_gfs_day_label``); the ``atmos/`` key layout starts 2021-03-23
  (``GFS_AWS_START``) — older issue dates yield NaN.

Two modes: ``*_daily`` fetchers build cached regional cubes (3a/serving),
``fetch_power_points`` / ``fetch_smap_points`` build per-point caches for
arbitrary lon/lat (3b global GLC). All fetchers are idempotent/resumable
(skip what's cached), retry with backoff, and log progress every ~100
items. Cache root: ``settings.DATA_DIR`` (``ML_DATA_DIR`` env override).

CLI: ``python -m ml.local.sources_dynamic <imerg|clim|monthly|smap|
constants|soilmean|gfs|power|smappoints> --bbox W S E N [--start --end
--points "lon lat;lon lat"]``
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import logging
import math
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import h5py
import numpy as np
import pandas as pd
import requests
from affine import Affine

from ml.local import settings
from ml.local import sources_static as ss

log = logging.getLogger(__name__)

# --- endpoints / constants ----------------------------------------------------
GESDISC = "https://gpm2.gesdisc.eosdis.nasa.gov/opendap"
IMERG_DAILY_COLLECTION = "GPM_L3/GPM_3IMERGDE.07"     # URL path segment
IMERG_MONTHLY_COLLECTION = "GPM_L3/GPM_3IMERGM.07"
IMERG_MONTHLY_CLOUD = "C2723754851-GES_DISC"        # cloud OPeNDAP concept id
IMERG_NX, IMERG_NY = 3600, 1800                     # global 0.1 deg cells
IMERG_LON0, IMERG_LAT0 = -180.0, -90.0              # grid edge (cell-centre -.05)

OPENDAP = "https://opendap.earthdata.nasa.gov/collections"
SMAP_GPH_COLLECTION = "C3480440870-NSIDC_CPRD"      # SPL4SMGP 008
SMAP_LMC_COLLECTION = "C3480441264-NSIDC_CPRD"      # SPL4SMLM 008
SMAP_LMC_UR = "SMAP_L4_SM_lmc_00000000T000000_Vv8012_001.h5_sdu93G0W"
# any SPL4SMGP granule works to read the fixed EASE-2 grid axes
SMAP_AXES_UR = "SMAP_L4_SM_gph_20240501T103000_Vv8012_001.h5_u9loH5HD"
SMAP_NY, SMAP_NX = 1624, 3856
SMAP_FILL = -9999.0
SMAP_VARS = tuple(settings.SOIL_DYN) + ("sm_rootzone_wetness",)
# Land-Model-Constants_Data leaf names (verified via .dmr.xml); porosity
# prefers the CLSM soil-column porosity over the MWRTM look-alike.
SMAP_LMC_VARS = {"clay": ("mwrtm_clay",), "sand": ("mwrtm_sand",),
                 "porosity": ("clsm_poros", "mwrtm_poros")}

CMR = "https://cmr.earthdata.nasa.gov/search/granules.json"

POWER_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
POWER_PARAM = "IMERG_PRECTOT"
POWER_FILL = -999.0
POWER_START = "20010101"      # IMERG starts 2001-01-01
POWER_MIN_INTERVAL = 1.5      # per-request spacing across workers (0.35 s hit HTTP 429)
POWER_COOLDOWN_S = 90.0       # on 429 every worker pauses this long (x attempt)
POWER_TRIES_429 = 8

GFS_BUCKET_START = dt.date(2021, 3, 23)   # gfs.YYYYMMDD/HH/atmos/ keys begin


# --- small utilities -----------------------------------------------------------
def _key(bbox: tuple[float, ...]) -> str:
    return "_".join(f"{v:g}" for v in bbox)


def _daterange(start: dt.date, end: dt.date, step: int = 1):
    d = start
    while d <= end:
        yield d
        d += dt.timedelta(days=step)


def _as_dates(dates) -> np.ndarray:
    """array-like of date/np.datetime64 -> datetime64[D] 1-D."""
    arr = np.asarray(dates)
    if not np.issubdtype(arr.dtype, np.datetime64):
        arr = np.array([np.datetime64(str(d)) for d in arr.ravel()])
    return np.atleast_1d(arr.astype("datetime64[D]"))


def _year(d64: np.ndarray) -> np.ndarray:
    return d64.astype("datetime64[Y]").astype(int) + 1970


def _doy(d64: np.ndarray) -> np.ndarray:
    return (d64 - d64.astype("datetime64[Y]")).astype("timedelta64[D]").astype(int) + 1


def _nearest(axis: np.ndarray, vals) -> np.ndarray:
    """Index of nearest element of 1-D ``axis`` for each of ``vals``."""
    v = np.asarray(vals, dtype=np.float64)
    return np.argmin(np.abs(axis[None, :] - v[:, None]), axis=1)


def _circ_doy_dist(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Circular distance between day-of-year values (period 366)."""
    d = np.abs(np.asarray(a, dtype=int) - np.asarray(b, dtype=int))
    return np.minimum(d, 366 - d)


def _write_json(path: Path, obj) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_text(json.dumps(obj, indent=1) + "\n")
    tmp.rename(path)
    return path


def _save_npy(path: Path, arr: np.ndarray) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part.npy")
    np.save(tmp, arr)
    tmp.rename(path)
    return path


def _save_npz(path: Path, **arrays) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part.npz")
    np.savez(tmp, **arrays)
    tmp.rename(path)
    return path


class _RateLimit:
    """Global min-spacing between request starts (all workers share it)."""

    def __init__(self, interval: float):
        self._dt = interval
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            gap = self._dt - (time.monotonic() - self._last)
            if gap > 0:
                time.sleep(gap)
            self._last = time.monotonic()

    def cooldown(self, seconds: float) -> None:
        """Push the next allowed start for EVERY worker ``seconds`` ahead."""
        with self._lock:
            self._last = max(self._last, time.monotonic() + seconds - self._dt)


_POWER_RL = _RateLimit(POWER_MIN_INTERVAL)


def _power_get(params: dict) -> dict:
    """Anonymous GET against NASA POWER — no Earthdata token attached."""
    last: Exception | None = None
    n429 = 0
    i = 0
    while i < 4 and n429 < POWER_TRIES_429:
        _POWER_RL.wait()
        try:
            r = requests.get(POWER_URL, params=params, timeout=120)
            if r.status_code == 429:          # rate limited: global pause, not a failure
                n429 += 1
                last = RuntimeError("POWER HTTP 429")
                log.warning("power: HTTP 429 -> all workers pause %.0f s", POWER_COOLDOWN_S * n429)
                _POWER_RL.cooldown(POWER_COOLDOWN_S * n429)
                continue
            if r.status_code >= 500:
                last = RuntimeError(f"POWER HTTP {r.status_code}")
                i += 1
                time.sleep(2 ** i)
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            last = e
            i += 1
            time.sleep(2 ** i)
    raise RuntimeError(f"POWER GET failed after retries: {params}") from last


def _cmr_filemap(short_name: str, version: str, start: dt.date,
                 end: dt.date, granule_ur: str | None = None
                 ) -> dict[dt.date, str]:
    """{date: file/granule name} from CMR granules.json over start..end.

    ``granule_ur`` takes a CMR pattern (e.g. ``*T103000*`` to pin SMAP's
    daily T103000 granule). The returned name is the GranuleUR (CMR
    ``title``): the hash-suffixed UR SMAP OPeNDAP needs; for IMERG it is
    ``<collection>:<filename>`` — we strip the prefix.
    """
    params: dict = {
        "short_name": short_name, "version": version,
        "temporal": f"{start}T00:00:00Z,{end + dt.timedelta(days=1)}T00:00:00Z",
        "page_size": 2000,
    }
    if granule_ur:
        params["granule_ur"] = granule_ur
        params["options[granule_ur][pattern]"] = "true"
    entries: list[dict] = []
    page = 1
    while True:
        params["page_num"] = page
        feed = ss._http_get(CMR, params=params).json()["feed"]["entry"]
        entries.extend(feed)
        if len(feed) < params["page_size"]:
            break
        page += 1
    out: dict[dt.date, str] = {}
    for e in entries:
        # GranuleUR (CMR 'title'); SMAP OPeNDAP needs the hash-suffixed
        # UR, IMERG title carries a '<collection>:' prefix we strip.
        name = e["title"].rsplit(":", 1)[-1]
        m = re.search(r"[.\-_](\d{8})[-T]", name)
        if m:
            out[dt.date(int(m.group(1)[:4]), int(m.group(1)[4:6]),
                        int(m.group(1)[6:8]))] = name
    return out


def _open_nc4(content: bytes) -> h5py.File:
    return h5py.File(io.BytesIO(content), "r")


def _dap_get(base: str, ce: str, timeout: int = 300,
             tries: int = 5) -> bytes:
    """One .dap.nc4 request -> raw nc4 bytes (brackets encoded by requests).

    Retries non-200 with backoff: GES DISC OPeNDAP returns transient
    404/400s under load that _http_get's 429/5xx handling won't retry.
    """
    url = f"{base}.dap.nc4"
    last: Exception | None = None
    for i in range(tries):
        try:
            r = ss._http_get(url, params={"dap4.ce": ce}, timeout=timeout)
        except RuntimeError as e:
            last = e
            time.sleep(2 ** i)
            continue
        if r.status_code == 200:
            return r.content
        last = RuntimeError(
            f"OPeNDAP {r.status_code} for {base} ce={ce[:120]}")
        time.sleep(2 ** i)
    raise last


# --- IMERG grid ---------------------------------------------------------------
def _imerg_window(bbox: tuple[float, ...]) -> tuple[int, int, int, int]:
    """(lon0, lon1, lat0, lat1) inclusive cell indices covering bbox.

    Cell i spans [LON0 + 0.1*i, LON0 + 0.1*(i+1)), centre +0.05 — the
    window is every cell touching the bbox.
    """
    w, s, e, n = bbox
    lon0 = max(0, math.floor((w - IMERG_LON0) * 10.0 + 1e-9))
    lon1 = min(IMERG_NX - 1, math.floor((e - IMERG_LON0) * 10.0 + 1e-9))
    lat0 = max(0, math.floor((s - IMERG_LAT0) * 10.0 + 1e-9))
    lat1 = min(IMERG_NY - 1, math.floor((n - IMERG_LAT0) * 10.0 + 1e-9))
    return lon0, lon1, lat0, lat1


def _imerg_axes(lon0: int, lon1: int, lat0: int, lat1: int):
    lons = IMERG_LON0 + 0.05 + np.arange(lon0, lon1 + 1) * 0.1
    lats = IMERG_LAT0 + 0.05 + np.arange(lat0, lat1 + 1) * 0.1
    return lats, lons


def _imerg_snap(lon, lat, meta: dict) -> tuple[np.ndarray, np.ndarray]:
    """bbox-relative (iy, ix) cell indices; out-of-window -> -1."""
    ix = np.clip(np.floor((np.asarray(lon, float) - IMERG_LON0) * 10.0
                          + 1e-9).astype(np.int64), 0, IMERG_NX - 1)
    iy = np.clip(np.floor((np.asarray(lat, float) - IMERG_LAT0) * 10.0
                          + 1e-9).astype(np.int64), 0, IMERG_NY - 1)
    ix, iy = ix - meta["lon0"], iy - meta["lat0"]
    bad = (ix < 0) | (ix > meta["lon1"] - meta["lon0"]) | (iy < 0) | (
        iy > meta["lat1"] - meta["lat0"])
    ix[bad], iy[bad] = -1, -1
    return iy, ix


def _imerg_grid_dir(bbox: tuple[float, ...]) -> Path:
    return settings.path("imerg_daily", _key(bbox))


def _imerg_meta(bbox: tuple[float, ...]) -> dict:
    lon0, lon1, lat0, lat1 = _imerg_window(bbox)
    return {"bbox": list(bbox), "lon0": lon0, "lon1": lon1,
            "lat0": lat0, "lat1": lat1}


def _read_imerg_nc4(content: bytes, meta: dict,
                    var: str = "precipitation") -> np.ndarray:
    """precipitation slab -> (ny, nx) float32, lat ascending, fills->NaN.

    Daily nc4 keeps the var at root; monthly HDF5 nests it under /Grid/.
    """
    nx = meta["lon1"] - meta["lon0"] + 1
    ny = meta["lat1"] - meta["lat0"] + 1
    with _open_nc4(content) as f:
        arr = np.asarray(f[var][0], dtype=np.float32)
    if arr.shape == (nx, ny):           # documented dim order (time,lon,lat)
        arr = arr.T
    elif arr.shape != (ny, nx):
        raise ValueError(f"unexpected slab shape {arr.shape}, want {ny}x{nx}")
    arr[~np.isfinite(arr) | (arr < 0)] = np.nan
    return arr


def _imerg_daily_url(day: dt.date, fname: str) -> str:
    return (f"{GESDISC}/{IMERG_DAILY_COLLECTION}/{day:%Y}/{day:%m}/"
            f"{fname}.dap.nc4")


def _imerg_monthly_url(day: dt.date, fname: str) -> str:
    # GES DISC on-prem OPeNDAP 404s for GPM_3IMERGM (verified 2026-11-15); the
    # cloud OPeNDAP serves the same HDF5 with the same Grid/precipitation layout.
    return (f"{OPENDAP}/{IMERG_MONTHLY_CLOUD}/granules/"
            f"GPM_3IMERGM.07%3A{fname}")  # _dap_get appends .dap.nc4


def _imerg_ce(meta: dict, var: str = "/precipitation") -> str:
    return (f"{var}[0:0][{meta['lon0']}:{meta['lon1']}]"
            f"[{meta['lat0']}:{meta['lat1']}]")


def fetch_imerg_daily(start: dt.date, end: dt.date,
                      bbox: tuple[float, ...] = settings.REGION_TRAIN,
                      workers: int = 8) -> Path:
    """IMERG Early daily mm/day slabs -> per-day .npy + memmap cube.

    CMR resolves the exact granule name per day (V07A/V07B suffixes vary);
    days with no granule are logged and absent from the cube. Existing day
    files are skipped (resumable).
    """
    gdir = _imerg_grid_dir(bbox)
    meta = _imerg_meta(bbox)
    _write_json(gdir / "meta.json", meta)
    days_dir = gdir / "days"
    filemap = _cmr_filemap("GPM_3IMERGDE", "07", start, end)
    todo = [d for d in _daterange(start, end)
            if d in filemap
            and not (days_dir / f"{d:%Y%m%d}.npy").exists()]
    missing_cmr = [str(d) for d in _daterange(start, end)
                   if d not in filemap]
    if missing_cmr:
        log.info("imerg_daily: %d days have no CMR granule (%s ...)",
                 len(missing_cmr), missing_cmr[:3])
    ce = _imerg_ce(meta)

    def work(day: dt.date) -> str:
        url = _imerg_daily_url(day, filemap[day])
        arr = _read_imerg_nc4(_dap_get(url, ce), meta)
        _save_npy(days_dir / f"{day:%Y%m%d}.npy", arr)
        return "ok"

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for _ in ex.map(work, todo):
            done += 1
            if done % 100 == 0:
                log.info("imerg_daily: %d/%d days", done, len(todo))
    cube = _build_imerg_cube(bbox)
    log.info("imerg_daily: %d new days, cube %s", done, cube)
    return cube


def _build_imerg_cube(bbox: tuple[float, ...]) -> Path:
    """Assemble every cached day slab into cube.npy (days, ny, nx) f32."""
    gdir = _imerg_grid_dir(bbox)
    meta = _imerg_meta(bbox)
    days_dir = gdir / "days"
    files = sorted(days_dir.glob("*.npy")) if days_dir.exists() else []
    dates = np.array([np.datetime64(f.stem[:4] + "-" + f.stem[4:6] + "-"
                                    + f.stem[6:8]) for f in files],
                     dtype="datetime64[D]")
    ny = meta["lat1"] - meta["lat0"] + 1
    nx = meta["lon1"] - meta["lon0"] + 1
    cube = np.lib.format.open_memmap(
        gdir / "cube.npy", mode="w+", dtype="float32",
        shape=(len(files), ny, nx))
    for i, f in enumerate(files):
        cube[i] = np.load(f)
    cube.flush()
    _write_json(gdir / "cube.json",
                {"dates": [str(d) for d in dates],
                 "ny": ny, "nx": nx})
    return gdir / "cube.npy"


class ImergDaily:
    """Memmapped IMERG daily cube for one bbox: (days, ny, nx) f32 mm/day."""

    def __init__(self, cube: np.ndarray, dates: np.ndarray, meta: dict):
        self._cube = cube
        self._dates = dates                       # datetime64[D]
        self._meta = meta
        self.bbox = tuple(meta["bbox"])
        self._pos = {d: i for i, d in enumerate(dates)}

    @classmethod
    def load(cls, bbox: tuple[float, ...] = settings.REGION_TRAIN
             ) -> ImergDaily:
        gdir = _imerg_grid_dir(bbox)
        meta_p = gdir / "meta.json"
        meta = (json.loads(meta_p.read_text()) if meta_p.exists()
                else _imerg_meta(bbox))
        cube_p, dates_p = gdir / "cube.npy", gdir / "cube.json"
        if cube_p.exists() and dates_p.exists():
            dates = np.array(json.loads(dates_p.read_text())["dates"],
                             dtype="datetime64[D]")
            cube = np.load(cube_p, mmap_mode="r")
        else:
            ny = meta["lat1"] - meta["lat0"] + 1
            nx = meta["lon1"] - meta["lon0"] + 1
            cube = np.zeros((0, ny, nx), np.float32)
            dates = np.zeros(0, "datetime64[D]")
        return cls(cube, dates, meta)

    @property
    def dates(self) -> np.ndarray:
        return self._dates

    def _grid_axes(self):
        m = self._meta
        return _imerg_axes(m["lon0"], m["lon1"], m["lat0"], m["lat1"])

    def _sel(self, lon, lat, dates):
        """(iy, ix, pos) index arrays; -1 where outside grid/no such day."""
        iy, ix = _imerg_snap(lon, lat, self._meta)
        d64 = _as_dates(dates)
        pos = np.array([self._pos.get(d, -1) for d in d64])
        return iy, ix, pos

    def daily(self, lon, lat, dates) -> np.ndarray:
        """mm/day at each (lon, lat, date); NaN outside grid or missing day."""
        iy, ix, pos = self._sel(lon, lat, dates)
        out = np.full(len(pos), np.nan)
        ok = (iy >= 0) & (ix >= 0) & (pos >= 0)
        out[ok] = self._cube[pos[ok], iy[ok], ix[ok]]
        return out

    def window_sum(self, lon, lat, end_dates, ndays: int) -> np.ndarray:
        """Sum of daily rain over days end-ndays+1..end inclusive.

        NaN if any of the ndays days is missing from the cube or the cell
        is NaN on any of them.
        """
        ends = _as_dates(end_dates)
        spans = ends[:, None] + np.arange(-ndays + 1, 1)[None, :]
        iy0, ix0 = _imerg_snap(lon, lat, self._meta)
        iy = np.repeat(iy0, ndays)
        ix = np.repeat(ix0, ndays)
        pos = np.array([self._pos.get(d, -1) for d in spans.ravel()])
        vals = np.full(spans.size, np.nan)
        ok = (iy >= 0) & (ix >= 0) & (pos >= 0)
        vals[ok] = self._cube[pos[ok], iy[ok], ix[ok]]
        return vals.reshape(spans.shape).sum(axis=1)

    def grid(self, day) -> tuple[np.ndarray, Affine]:
        """(2-D mm/day field for one day, Affine transform).

        The array is lat-ascending; the Affine maps (col, row) ->
        (lon, lat) cell *edges* (e = +0.1, south-up origin).
        """
        lats, lons = self._grid_axes()
        res = settings.IMERG_RES_DEG
        tr = Affine(res, 0.0, float(lons[0] - res / 2),
                    0.0, res, float(lats[0] - res / 2))
        pos = self._pos.get(np.datetime64(str(day)), -1)
        arr = (self._cube[pos].copy() if pos >= 0 else
               np.full((len(lats), len(lons)), np.nan, np.float32))
        return arr, tr


# --- rain climatology -----------------------------------------------------------
def _rolling3(cube: np.ndarray, dates: np.ndarray) -> np.ndarray:
    """3-day sums ending at index i (i-2..i); NaN unless days consecutive."""
    r3 = np.full(cube.shape, np.nan, np.float32)
    if len(cube) < 3:
        return r3
    r3[2:] = cube[:-2] + cube[1:-1] + cube[2:]
    gaps = (dates[2:] - dates[:-2]) != np.timedelta64(2, "D")
    r3[2:][gaps] = np.nan
    return r3


def build_rain_climatology(years: tuple[int, int] = settings.CLIM_YEARS,
                           window_days: int = 15,
                           bbox: tuple[float, ...] = settings.REGION_TRAIN
                           ) -> Path:
    """Per-pixel rain climatology -> rain_clim/clim_<key>.npz.

    p99_daily: p99 of daily mm/day over the years. p99_3day: p99 of
    consecutive-3-day sums. doy_p90/95/99: per-day-of-year percentiles of
    3-day sums pooled over ends within +-window_days of each doy.
    """
    imerg = ImergDaily.load(bbox)
    keep = (_year(imerg.dates) >= years[0]) & (_year(imerg.dates) <= years[1])
    dates = imerg.dates[keep]
    cube = np.asarray(imerg._cube[keep], dtype=np.float32)
    lats, lons = imerg._grid_axes()
    ny, nx = len(lats), len(lons)
    if len(cube) == 0:
        empty = np.full((ny, nx), np.nan, np.float32)
        p99d = p993 = empty
        doy = {q: np.full((366, ny, nx), np.nan, np.float32)
               for q in (90, 95, 99)}
    else:
        p99d = np.nanpercentile(cube, 99, axis=0).astype(np.float32)
        r3 = _rolling3(cube, dates)
        ends = _doy(dates[2:]) if len(dates) >= 3 else np.zeros(0, int)
        p993 = np.nanpercentile(r3, 99, axis=0).astype(np.float32)
        doy = {}
        for q in (90, 95, 99):
            grid = np.full((366, ny, nx), np.nan, np.float32)
            for d in range(1, 367):
                pool = r3[2:][(_circ_doy_dist(ends, d) <= window_days)]
                if len(pool):
                    grid[d - 1] = np.nanpercentile(pool, q, axis=0)
            doy[q] = grid
    out = settings.path("rain_clim", f"clim_{_key(bbox)}.npz")
    _save_npz(out, p99_daily=p99d, p99_3day=p993,
              doy_p90=doy[90], doy_p95=doy[95], doy_p99=doy[99],
              lats=lats.astype(np.float32), lons=lons.astype(np.float32))
    _write_json(out.with_suffix(".json"),
                {"bbox": list(bbox), "years": list(years),
                 "window_days": window_days, "ndays": len(cube)})
    return out


class RainClim:
    """Per-pixel rain climatology (npz from build_rain_climatology)."""

    def __init__(self, z: dict[str, np.ndarray]):
        self._p99d = z["p99_daily"]
        self._p993 = z["p99_3day"]
        self._doy = {90: z["doy_p90"], 95: z["doy_p95"], 99: z["doy_p99"]}
        self._lats = z["lats"]
        self._lons = z["lons"]

    @classmethod
    def load(cls, bbox: tuple[float, ...] = settings.REGION_TRAIN
             ) -> RainClim:
        p = settings.path("rain_clim", f"clim_{_key(bbox)}.npz")
        z = dict(np.load(p)) if p.exists() else {}
        if not z:
            w = _imerg_meta(bbox)
            lats, lons = _imerg_axes(w["lon0"], w["lon1"],
                                     w["lat0"], w["lat1"])
            ny, nx = len(lats), len(lons)
            z = {"p99_daily": np.full((ny, nx), np.nan, np.float32),
                 "p99_3day": np.full((ny, nx), np.nan, np.float32),
                 "doy_p90": np.full((366, ny, nx), np.nan, np.float32),
                 "doy_p95": np.full((366, ny, nx), np.nan, np.float32),
                 "doy_p99": np.full((366, ny, nx), np.nan, np.float32),
                 "lats": lats.astype(np.float32),
                 "lons": lons.astype(np.float32)}
        return cls(z)

    def _ij(self, lon, lat):
        iy = _nearest(self._lats, lat)
        ix = _nearest(self._lons, lon)
        bad = ((np.asarray(lat) < self._lats[0] - 0.05)
               | (np.asarray(lat) > self._lats[-1] + 0.05)
               | (np.asarray(lon) < self._lons[0] - 0.05)
               | (np.asarray(lon) > self._lons[-1] + 0.05))
        return iy, ix, bad

    def p99_daily(self, lon, lat) -> np.ndarray:
        iy, ix, bad = self._ij(lon, lat)
        return np.where(bad, np.nan, self._p99d[iy, ix])

    def p99_3day(self, lon, lat) -> np.ndarray:
        iy, ix, bad = self._ij(lon, lat)
        return np.where(bad, np.nan, self._p993[iy, ix])

    def pctl_3day(self, lon, lat, doy
                  ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(p90, p95, p99) of 3-day sums pooled +-15 d around ``doy``."""
        iy, ix, bad = self._ij(lon, lat)
        di = np.clip(np.asarray(doy, dtype=int) - 1, 0, 365)
        out = tuple(np.where(bad, np.nan, self._doy[q][di, iy, ix])
                    for q in (90, 95, 99))
        return out


def rain_features(lon, lat, dates, imerg=None, clim=None) -> pd.DataFrame:
    """settings.RAIN columns for each (lon, lat, date) row.

    rain_24h = day t; rain_72h = t-2..t; rain_7d = t-6..t; *_rel =
    normalised by the pixel's climatological p99. ``imerg`` is an
    ImergDaily (cube) or PowerRain (points); ``clim`` a RainClim or the
    same PowerRain.
    """
    if imerg is None:
        imerg = ImergDaily.load()
    if clim is None:
        clim = (imerg if isinstance(imerg, PowerRain)
                else RainClim.load(bbox=imerg.bbox))
    r24 = np.asarray(imerg.daily(lon, lat, dates), float)
    r72 = np.asarray(imerg.window_sum(lon, lat, dates, 3), float)
    r7d = np.asarray(imerg.window_sum(lon, lat, dates, 7), float)
    p99d = np.asarray(clim.p99_daily(lon, lat), float)
    p993 = np.asarray(clim.p99_3day(lon, lat), float)
    rel24 = np.where(np.isfinite(p99d) & (p99d > 0), r24 / p99d, np.nan)
    rel72 = np.where(np.isfinite(p993) & (p993 > 0), r72 / p993, np.nan)
    return pd.DataFrame({"rain_24h": r24, "rain_72h": r72,
                         "rain_7d": r7d, "rain_24h_rel": rel24,
                         "rain_72h_rel": rel72})[settings.RAIN]


# --- IMERG monthly -> rain_mean --------------------------------------------------
def fetch_imerg_monthly(years: tuple[int, int] = settings.CLIM_YEARS,
                        bbox: tuple[float, ...] = settings.REGION_TRAIN,
                        workers: int = 8) -> Path:
    """IMERG monthly mean rate -> rain_mean (mm/yr) grid npz.

    GPM_3IMERGM ``precipitation`` is the monthly-mean rain rate (mm/hr);
    rain_mean = mean over months * 24 * 365.25.
    """
    gdir = settings.path("imerg_monthly", _key(bbox))
    meta = _imerg_meta(bbox)
    months = [dt.date(y, m, 1)
              for y in range(years[0], years[1] + 1) for m in range(1, 13)]
    filemap = _cmr_filemap("GPM_3IMERGM", "07", months[0], months[-1])
    slabs_dir = gdir / "months"
    ce = _imerg_ce(meta, var="/Grid/precipitation")
    todo = [d for d in months if d in filemap
            and not (slabs_dir / f"{d:%Y%m}.npy").exists()]

    def work(day: dt.date) -> None:
        url = _imerg_monthly_url(day, filemap[day])
        arr = _read_imerg_nc4(_dap_get(url, ce), meta,
                              var="Grid/precipitation")
        _save_npy(slabs_dir / f"{day:%Y%m}.npy", arr)

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for _ in ex.map(work, todo):
            done += 1
            if done % 100 == 0:
                log.info("imerg_monthly: %d/%d months", done, len(todo))
    files = sorted(slabs_dir.glob("*.npy")) if slabs_dir.exists() else []
    lats, lons = _imerg_axes(meta["lon0"], meta["lon1"],
                             meta["lat0"], meta["lat1"])
    ny, nx = len(lats), len(lons)
    if files:
        stack = np.stack([np.load(f) for f in files])
        rate = np.where(np.isfinite(stack).any(axis=0),
                        np.nanmean(stack, axis=0), np.nan)
        rain_mean = (rate * 24.0 * 365.25).astype(np.float32)
    else:
        rain_mean = np.full((ny, nx), np.nan, np.float32)
    out = gdir / "rain_mean.npz"
    _save_npz(out, rain_mean=rain_mean, lats=lats.astype(np.float32),
              lons=lons.astype(np.float32),
              months=np.array([f.stem for f in files]))
    log.info("imerg_monthly: %d new months, %d total -> %s",
             done, len(files), out)
    return out


def _rain_mean_grid(bbox: tuple[float, ...]):
    p = settings.path("imerg_monthly", _key(bbox)) / "rain_mean.npz"
    if p.exists():
        z = dict(np.load(p))
        return z["rain_mean"], z["lats"], z["lons"]
    return None, None, None


# --- SMAP grid axes + windows -----------------------------------------------------
def _smap_axes() -> tuple[np.ndarray, np.ndarray]:
    """(row_lats (1624, descending), col_lons (3856)) — cached once.

    Read as two 1-D slices (a column of cell_lat, a row of cell_lon):
    differently-shaped slabs cannot share one dap4.ce (the server's
    response dims collide).
    """
    p = settings.path("smap", "axes.npz")
    if p.exists():
        z = dict(np.load(p))
        return z["row_lats"], z["col_lons"]
    base = f"{OPENDAP}/{SMAP_GPH_COLLECTION}/granules/{SMAP_AXES_UR}"
    with _open_nc4(_dap_get(base, "/cell_lat[0:1623][0:0]")) as f:
        row_lats = np.asarray(f["cell_lat"][:, 0], dtype=np.float64)
    with _open_nc4(_dap_get(base, "/cell_lon[0:0][0:3855]")) as f:
        col_lons = np.asarray(f["cell_lon"][0, :], dtype=np.float64)
    _save_npz(p, row_lats=row_lats, col_lons=col_lons)
    return row_lats, col_lons


def _smap_window(bbox: tuple[float, ...], pad: int = 1
                 ) -> tuple[int, int, int, int]:
    """(r0, r1, c0, c1) inclusive EASE-2 indices covering bbox (+pad)."""
    row_lats, col_lons = _smap_axes()
    w, s, e, n = bbox
    rows = np.flatnonzero((row_lats >= s) & (row_lats <= n))
    cols = np.flatnonzero((col_lons >= w) & (col_lons <= e))
    if not len(rows) or not len(cols):
        raise ValueError(f"bbox {bbox} outside SMAP grid coverage")
    return (max(0, int(rows[0]) - pad), min(SMAP_NY - 1, int(rows[-1]) + pad),
            max(0, int(cols[0]) - pad), min(SMAP_NX - 1, int(cols[-1]) + pad))


def _smap_rc(lon, lat) -> tuple[np.ndarray, np.ndarray]:
    """Global (row, col) cell indices nearest each point."""
    row_lats, col_lons = _smap_axes()
    return _nearest(row_lats, lat), _nearest(col_lons, lon)


def _smap_dap_vars(base: str, var_fq: dict[str, str],
                   r0: int, r1: int, c0: int, c1: int,
                   coords: bool = True) -> dict[str, np.ndarray]:
    """One .dap.nc4 request for vars (+cell_lat/lon) over a window.

    All slices share the same [r0:r1][c0:c1] slab — required (see
    _smap_axes). Fill -9999 -> NaN.
    """
    slab = f"[{r0}:{r1}][{c0}:{c1}]"
    wants = [f"{fq}{slab}" for fq in var_fq.values()]
    if coords:
        wants += [f"/cell_lat{slab}", f"/cell_lon{slab}"]
    with _open_nc4(_dap_get(base, ";".join(wants))) as f:
        out = {}
        for leaf, fq in var_fq.items():
            arr = np.asarray(f[fq.lstrip("/")][:], dtype=np.float32)
            arr[arr == SMAP_FILL] = np.nan
            out[leaf] = arr
        if coords:
            out["cell_lat"] = np.asarray(f["cell_lat"][:])
            out["cell_lon"] = np.asarray(f["cell_lon"][:])
    return out


def _smap_daily_dir(bbox: tuple[float, ...]) -> Path:
    return settings.path("smap_daily", _key(bbox))


def fetch_smap_daily(dates, bbox: tuple[float, ...] = settings.REGION_TRAIN,
                     hour: int = settings.SMAP_HOUR_UTC,
                     workers: int = 8) -> Path:
    """SPL4SMGP 008 per-day npz (4 soil vars + window axes) -> dir Path.

    One granule per day at T{hour:02d}3000 (CMR ``granule_ur`` pattern);
    one combined OPeNDAP request per day.
    """
    days = sorted(set(_as_dates(dates)))
    ddir = _smap_daily_dir(bbox)
    r0, r1, c0, c1 = _smap_window(bbox)
    urs = (_cmr_filemap("SPL4SMGP", "008", days[0].tolist(),
                        days[-1].tolist(),
                        granule_ur=f"*T{hour:02d}3000*")
           if days else {})
    var_fq = {v: f"/Geophysical_Data/{v}" for v in SMAP_VARS}
    missing = [str(d) for d in days if d.tolist() not in urs]
    if missing:
        log.info("smap_daily: %d days have no T%02d3000 granule (%s ...)",
                 len(missing), hour, missing[:3])
    todo = [d for d in days
            if not (ddir / f"{str(d).replace('-', '')}.npz").exists()
            and d.tolist() in urs]

    def work(d) -> str:
        ur = urs[d.tolist()]
        base = f"{OPENDAP}/{SMAP_GPH_COLLECTION}/granules/{ur}"
        v = _smap_dap_vars(base, var_fq, r0, r1, c0, c1)
        _save_npz(ddir / f"{str(d).replace('-', '')}.npz",
                  **{k: v[k] for k in SMAP_VARS},
                  lats=v["cell_lat"][:, 0].astype(np.float32),
                  lons=v["cell_lon"][0, :].astype(np.float32),
                  rc=np.array([r0, c0], np.int32))
        return "ok"

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for _ in ex.map(work, todo):
            done += 1
            if done % 100 == 0:
                log.info("smap_daily: %d/%d days", done, len(todo))
    log.info("smap_daily: %d new days -> %s", done, ddir)
    return ddir


class SmapDaily:
    """Cached SPL4SMGP daily slabs for one bbox."""

    def __init__(self, bbox: tuple[float, ...]):
        self.bbox = tuple(bbox)
        self._dir = _smap_daily_dir(bbox)
        self._days: dict[str, dict] = {}

    @classmethod
    def load(cls, bbox: tuple[float, ...] = settings.REGION_TRAIN
             ) -> SmapDaily:
        return cls(bbox)

    def _day(self, d64: np.datetime64) -> dict | None:
        key = str(d64).replace("-", "")
        if key not in self._days:
            p = self._dir / f"{key}.npz"
            self._days[key] = dict(np.load(p)) if p.exists() else None
        return self._days[key]

    def sample(self, lon, lat, dates) -> pd.DataFrame:
        """SOIL_DYN + sm_rootzone_wetness per (lon, lat, date)."""
        d64 = _as_dates(dates)
        rows = {v: np.full(len(d64), np.nan) for v in SMAP_VARS}
        for i, d in enumerate(d64):
            z = self._day(d)
            if z is None:
                continue
            iy = int(_nearest(z["lats"], [lat[i]])[0])
            ix = int(_nearest(z["lons"], [lon[i]])[0])
            for v in SMAP_VARS:
                rows[v][i] = z[v][iy, ix]
        return pd.DataFrame(rows)[list(SMAP_VARS)]


# --- SMAP land-model constants ----------------------------------------------------
def _smap_lmc_fq(base: str) -> dict[str, str]:
    """{clay|sand|porosity: FQ DAP4 path} resolved from the granule DMR."""
    dmr = ss._http_get(f"{base}.dmr.xml", timeout=120).text
    paths = ss._dmr_paths(dmr)
    out: dict[str, str] = {}
    for field, leaves in SMAP_LMC_VARS.items():
        for leaf in leaves:
            cands = [p for p in paths.get(leaf, [])
                     if p.startswith("/Land-Model-Constants_Data/")]
            if cands:
                out[field] = cands[0]
                break
    missing = set(SMAP_LMC_VARS) - set(out)
    if missing:
        raise KeyError(f"SMAP LMC vars {missing} not in DMR of {base}")
    return out


def fetch_smap_constants(bbox: tuple[float, ...] = settings.REGION_TRAIN
                         ) -> Path:
    """SPL4SMLM clay/sand/porosity (fractions) -> constants.npz."""
    base = f"{OPENDAP}/{SMAP_LMC_COLLECTION}/granules/{SMAP_LMC_UR}"
    out = settings.path("smap_constants", _key(bbox)) / "constants.npz"
    if out.exists():
        return out
    r0, r1, c0, c1 = _smap_window(bbox)
    v = _smap_dap_vars(base, _smap_lmc_fq(base), r0, r1, c0, c1)
    _save_npz(out, clay=v["clay"], sand=v["sand"], porosity=v["porosity"],
              lats=v["cell_lat"][:, 0].astype(np.float32),
              lons=v["cell_lon"][0, :].astype(np.float32),
              rc=np.array([r0, c0], np.int32))
    return out


def _constants_grid(bbox: tuple[float, ...]):
    p = settings.path("smap_constants", _key(bbox)) / "constants.npz"
    return dict(np.load(p)) if p.exists() else None


def soil_texture(lon, lat,
                 bbox: tuple[float, ...] = settings.REGION_TRAIN
                 ) -> pd.DataFrame:
    """clay, sand, porosity fractions at each point (nearest SMAP cell)."""
    z = _constants_grid(bbox)
    cols = settings.SOIL_TEXTURE
    if z is None:
        return pd.DataFrame({c: np.full(len(np.atleast_1d(lon)), np.nan)
                             for c in cols})
    iy = _nearest(z["lats"], lat)
    ix = _nearest(z["lons"], lon)
    return pd.DataFrame({c: z[c][iy, ix] for c in cols})[cols]


# --- soil_mean + climate -----------------------------------------------------------
def build_soil_mean(years: tuple[int, int] = settings.SOIL_MEAN_YEARS,
                    every: int = settings.SOIL_MEAN_EVERY_DAYS,
                    bbox: tuple[float, ...] = settings.REGION_TRAIN,
                    workers: int = 8) -> Path:
    """Mean SMAP sm_rootzone (m3/m3) per pixel -> soil_mean.npz.

    Fetches one SPL4SMGP day every ``every`` days across ``years`` itself.
    """
    start = max(dt.date(years[0], 1, 1), settings.SMAP_START)
    days = list(_daterange(start, dt.date(years[1], 12, 31), every))
    fetch_smap_daily(days, bbox=bbox, workers=workers)
    ddir = _smap_daily_dir(bbox)
    files = sorted(ddir.glob("*.npz"))
    if files:
        stacks, lats, lons = [], None, None
        for f in files:
            z = dict(np.load(f))
            stacks.append(z["sm_rootzone"])
            lats, lons = z["lats"], z["lons"]
        sm = np.stack(stacks)
        soil_mean = np.where(np.isfinite(sm).any(axis=0),
                             np.nanmean(sm, axis=0), np.nan)
    else:
        r0, r1, c0, c1 = _smap_window(bbox)
        row_lats, col_lons = _smap_axes()
        lats, lons = row_lats[r0:r1 + 1], col_lons[c0:c1 + 1]
        soil_mean = np.full((len(lats), len(lons)), np.nan, np.float32)
    out = settings.path("soil_mean", _key(bbox)) / "soil_mean.npz"
    _save_npz(out, soil_mean=soil_mean.astype(np.float32),
              lats=np.asarray(lats, np.float32),
              lons=np.asarray(lons, np.float32),
              ndays=np.int64(len(files)))
    return out


def _soil_mean_grid(bbox: tuple[float, ...]):
    p = settings.path("soil_mean", _key(bbox)) / "soil_mean.npz"
    if p.exists():
        z = dict(np.load(p))
        return z["soil_mean"], z["lats"], z["lons"]
    return None, None, None


def climate(lon, lat,
            bbox: tuple[float, ...] = settings.REGION_TRAIN) -> pd.DataFrame:
    """rain_mean (mm/yr) + soil_mean (m3/m3) per point."""
    lon, lat = np.atleast_1d(np.asarray(lon, float)), np.atleast_1d(
        np.asarray(lat, float))
    rain, rlats, rlons = _rain_mean_grid(bbox)
    soil, slats, slons = _soil_mean_grid(bbox)
    rain_v = (np.full(len(lon), np.nan) if rain is None
              else rain[_nearest(rlats, lat), _nearest(rlons, lon)])
    soil_v = (np.full(len(lon), np.nan) if soil is None
              else soil[_nearest(slats, lat), _nearest(slons, lon)])
    return pd.DataFrame({"rain_mean": rain_v, "soil_mean": soil_v})[
        settings.CLIMATE]


def climate_on_grid(transform, shape: tuple[int, int],
                    bbox: tuple[float, ...] = settings.REGION_TRAIN
                    ) -> dict[str, np.ndarray]:
    """rain_mean/soil_mean/clay/sand/porosity nearest-resampled onto an
    EPSG:4326 grid described by an Affine ``transform`` + ``shape``
    (north-up convention: row 0 = transform.f)."""
    ny, nx = shape
    tlons = transform.c + (np.arange(nx) + 0.5) * transform.a
    tlats = transform.f + (np.arange(ny) + 0.5) * transform.e
    out: dict[str, np.ndarray] = {}
    rain, rlats, rlons = _rain_mean_grid(bbox)
    soil, slats, slons = _soil_mean_grid(bbox)
    consts = _constants_grid(bbox)
    srcs: list[tuple[str, np.ndarray | None, object, object]] = [
        ("rain_mean", rain, rlats, rlons),
        ("soil_mean", soil, slats, slons)]
    for c in settings.SOIL_TEXTURE:
        srcs.append((c, consts[c] if consts else None,
                     consts["lats"] if consts else None,
                     consts["lons"] if consts else None))
    for name, grid, ax_lat, ax_lon in srcs:
        if grid is None:
            out[name] = np.full((ny, nx), np.nan, np.float32)
            continue
        iy = _nearest(np.asarray(ax_lat, float), tlats)
        ix = _nearest(np.asarray(ax_lon, float), tlons)
        out[name] = grid[np.ix_(iy, ix)].astype(np.float32)
    return out


# --- NASA POWER per-point rain ------------------------------------------------------
def _power_cell(lon, lat) -> tuple[np.ndarray, np.ndarray]:
    """0.1-deg IMERG cell centre containing each point (lon wrapped to [-180, 180))."""
    lon = (np.asarray(lon, float) + 180.0) % 360.0 - 180.0
    lonc = np.floor((np.asarray(lon, float) + 180.0) * 10.0
                    + 1e-9) / 10.0 - 180.0 + 0.05
    latc = np.floor((np.asarray(lat, float) + 90.0) * 10.0
                    + 1e-9) / 10.0 - 90.0 + 0.05
    return lonc, latc


def _power_path(lonc: float, latc: float) -> Path:
    return settings.path("power", f"{latc:.2f}_{lonc:.2f}.json")


def fetch_power_points(lon, lat, workers: int = 2) -> Path:
    """Full IMERG daily series (2001..today) per distinct 0.1-deg cell.

    Cached at DATA_DIR/power/<lat>_<lon>.json; existing files only fetch
    the missing tail (resumable).
    """
    lonc, latc = _power_cell(lon, lat)
    cells = sorted(set(zip(np.round(latc, 4), np.round(lonc, 4))))
    today = dt.date.today()

    def work(cell) -> str:
        # one dead cell (bad coordinate, POWER outage) must not kill a global run
        try:
            return _work(cell)
        except Exception as exc:  # noqa: BLE001
            log.warning("power: cell %s failed (%s) -> NaN", cell, exc)
            return "failed"

    def _work(cell) -> str:
        la, lo = cell
        p = _power_path(lo, la)
        start = POWER_START
        daily: dict = {}
        if p.exists():
            try:
                daily = json.loads(p.read_text()).get("daily", {})
            except ValueError:
                daily = {}
            if daily:
                last = max(daily)
                if dt.date.fromisoformat(f"{last[:4]}-{last[4:6]}-"
                                         f"{last[6:]}") >= today - dt.timedelta(days=3):
                    return "fresh"
                start = (dt.date.fromisoformat(
                    f"{last[:4]}-{last[4:6]}-{last[6:]}")
                    + dt.timedelta(days=1)).strftime("%Y%m%d")
        r = _power_get({"parameters": POWER_PARAM, "community": "AG",
                        "longitude": lo, "latitude": la,
                        "start": start, "end": today.strftime("%Y%m%d"),
                        "format": "JSON", "time-standard": "UTC"})
        got = r.get("properties", {}).get("parameter", {}).get(
            POWER_PARAM, {})
        daily.update({k: float(v) for k, v in got.items()})
        _write_json(p, {"lat": la, "lon": lo, "param": POWER_PARAM,
                        "daily": daily})
        return "ok"

    done = 0
    failed = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for cell, st in zip(cells, ex.map(work, cells), strict=True):
            done += 1
            if st == "failed":
                failed.append(cell)
            if done % 100 == 0:
                log.info("power: %d/%d cells (%d failed)", done, len(cells), len(failed))
    if failed:   # one slow serial retry pass after a pause
        log.warning("power: retrying %d failed cells after %.0f s", len(failed), POWER_COOLDOWN_S)
        time.sleep(POWER_COOLDOWN_S)
        failed = [c for c in failed if work(c) == "failed"]
    log.info("power: %d cells, %d still failed (rows there get NaN rain; rerun fetches them)",
             len(cells), len(failed))
    return settings.path("power")


class PowerRain:
    """Per-point IMERG daily series + climatology (POWER cache).

    Same sampling API as ImergDaily + RainClim: daily / window_sum /
    p99_daily / p99_3day / pctl_3day — so ``rain_features`` works for
    point mode with ``imerg=clim=PowerRain.load()``.
    """

    def __init__(self):
        self._cells: dict[tuple[float, float],
                          tuple[np.ndarray, np.ndarray] | None] = {}

    @classmethod
    def load(cls) -> PowerRain:
        return cls()

    def _series(self, lon: float, lat: float):
        cell = (round(float(_power_cell(lon, lat)[1]), 4),
                round(float(_power_cell(lon, lat)[0]), 4))
        if cell not in self._cells:
            p = _power_path(cell[1], cell[0])
            if p.exists():
                d = json.loads(p.read_text()).get("daily", {})
                keys = sorted(d)
                dates = np.array([np.datetime64(f"{k[:4]}-{k[4:6]}-"
                                                f"{k[6:8]}") for k in keys])
                vals = np.array([d[k] for k in keys], np.float64)
                vals[vals == POWER_FILL] = np.nan
                self._cells[cell] = (dates, vals)
            else:
                self._cells[cell] = None
        return self._cells[cell]

    def _value(self, lon, lat, d: np.datetime64) -> float:
        s = self._series(lon, lat)
        if s is None:
            return np.nan
        i = np.searchsorted(s[0], d)
        return float(s[1][i]) if i < len(s[0]) and s[0][i] == d else np.nan

    def daily(self, lon, lat, dates) -> np.ndarray:
        d64 = _as_dates(dates)
        return np.array([self._value(l, a, d)
                         for l, a, d in zip(lon, lat, d64)])

    def window_sum(self, lon, lat, end_dates, ndays: int) -> np.ndarray:
        ends = _as_dates(end_dates)
        out = np.full(len(ends), np.nan)
        for i, e in enumerate(ends):
            s = self._series(lon[i], lat[i])
            if s is None:
                continue
            span = e + np.arange(-ndays + 1, 1)
            i0 = np.searchsorted(s[0], span[0])
            if i0 + ndays > len(s[0]):
                continue
            if not (s[0][i0:i0 + ndays] == span).all():
                continue                    # a gap inside the window
            v = s[1][i0:i0 + ndays]
            out[i] = v.sum() if np.isfinite(v).all() else np.nan
        return out

    def _r3(self, lon, lat):
        """(end_dates, 3-day sums) for one point's series."""
        s = self._series(lon, lat)
        if s is None or len(s[0]) < 3:
            return None, None
        d, v = s
        r3 = np.full(len(v), np.nan)
        r3[2:] = v[:-2] + v[1:-1] + v[2:]
        gaps = (d[2:] - d[:-2]) != np.timedelta64(2, "D")
        r3[2:][gaps] = np.nan
        return d, r3

    def p99_daily(self, lon, lat) -> np.ndarray:
        out = np.full(len(np.atleast_1d(lon)), np.nan)
        for i, (l, a) in enumerate(zip(np.atleast_1d(lon),
                                       np.atleast_1d(lat))):
            s = self._series(l, a)
            if s is not None:
                out[i] = np.nanpercentile(s[1], 99)
        return out

    def p99_3day(self, lon, lat) -> np.ndarray:
        out = np.full(len(np.atleast_1d(lon)), np.nan)
        for i, (l, a) in enumerate(zip(np.atleast_1d(lon),
                                       np.atleast_1d(lat))):
            d, r3 = self._r3(l, a)
            if d is not None:
                out[i] = np.nanpercentile(r3[2:], 99)
        return out

    def pctl_3day(self, lon, lat, doy,
                  window_days: int = 15
                  ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(p90, p95, p99) of 3-day sums pooled +-window_days of doy."""
        n = len(np.atleast_1d(lon))
        out = [np.full(n, np.nan) for _ in range(3)]
        for i, (l, a, dy) in enumerate(zip(np.atleast_1d(lon),
                                           np.atleast_1d(lat), doy)):
            d, r3 = self._r3(l, a)
            if d is None:
                continue
            ends = _doy(d[2:])
            pool = r3[2:][_circ_doy_dist(ends, int(dy)) <= window_days]
            if len(pool):
                for j, q in enumerate((90, 95, 99)):
                    out[j][i] = np.nanpercentile(pool, q)
        return tuple(out)


# --- SMAP at points -------------------------------------------------------------
_SMAP_POINT_BLOCK = 22     # ~2 deg per clustered window (22 * ~0.09 deg)


def fetch_smap_points(lon, lat, dates, workers: int = 8) -> Path:
    """Per-date npz of the 4 soil vars at each point's SMAP cell.

    For each date the requested points are deduplicated onto cells,
    clustered into <=~2 deg index windows (22x22 blocks) and fetched in
    one combined OPeNDAP request per window.
    """
    days = sorted(set(_as_dates(dates)))
    if not days:
        return settings.path("smap_points")
    pdir = settings.path("smap_points")
    rows, cols = _smap_rc(lon, lat)
    pixels = np.unique(np.stack([rows, cols], axis=1), axis=0)
    row_lats, col_lons = _smap_axes()
    urs = _cmr_filemap("SPL4SMGP", "008", days[0].tolist(),
                       days[-1].tolist(),
                       granule_ur=f"*T{settings.SMAP_HOUR_UTC:02d}3000*")
    var_fq = {v: f"/Geophysical_Data/{v}" for v in SMAP_VARS}

    def work(d) -> str:
        try:
            return _work(d)
        except Exception as exc:  # noqa: BLE001 - a missing day -> NaN, never fatal
            log.warning("smap_points: %s failed (%s) -> NaN", d, exc)
            return "failed"

    def _work(d) -> str:
        key = str(d).replace("-", "")
        outp = pdir / f"{key}.npz"
        if outp.exists():
            return "cached"
        if d.tolist() not in urs:
            return "no-granule"
        base = f"{OPENDAP}/{SMAP_GPH_COLLECTION}/granules/{urs[d.tolist()]}"
        blocks: dict[tuple[int, int], list[tuple[int, int]]] = {}
        for r, c in pixels:
            blocks.setdefault((int(r) // _SMAP_POINT_BLOCK,
                              int(c) // _SMAP_POINT_BLOCK), []).append(
                (int(r), int(c)))
        val = {v: {} for v in SMAP_VARS}
        for pix in blocks.values():
            pr = [p[0] for p in pix]
            pc = [p[1] for p in pix]
            r0, r1, c0, c1 = min(pr), max(pr), min(pc), max(pc)
            got = _smap_dap_vars(base, var_fq, r0, r1, c0, c1,
                                 coords=False)
            for r, c in pix:
                for v in SMAP_VARS:
                    val[v][(r, c)] = float(got[v][r - r0, c - c0])
        _save_npz(outp,
                  rows=pixels[:, 0], cols=pixels[:, 1],
                  lats=row_lats[pixels[:, 0]].astype(np.float64),
                  lons=col_lons[pixels[:, 1]].astype(np.float64),
                  **{v: np.array([val[v][tuple(p)] for p in pixels],
                                 np.float32) for v in SMAP_VARS})
        return "ok"

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for _ in ex.map(work, days):
            done += 1
            if done % 100 == 0:
                log.info("smap_points: %d/%d dates", done, len(days))
    return pdir


class SmapPoints:
    """Per-date point cache of SMAP soil vars (fetch_smap_points output)."""

    def __init__(self):
        self._dir = settings.path("smap_points")
        self._days: dict[str, dict | None] = {}

    @classmethod
    def load(cls) -> SmapPoints:
        return cls()

    def _day(self, d64: np.datetime64) -> dict | None:
        key = str(d64).replace("-", "")
        if key not in self._days:
            p = self._dir / f"{key}.npz"
            self._days[key] = dict(np.load(p)) if p.exists() else None
        return self._days[key]

    def sample(self, lon, lat, dates) -> pd.DataFrame:
        """SOIL_DYN + sm_rootzone_wetness at each (lon, lat, date)."""
        d64 = _as_dates(dates)
        rows_rc, cols_rc = _smap_rc(lon, lat)
        out = {v: np.full(len(d64), np.nan) for v in SMAP_VARS}
        for i, d in enumerate(d64):
            z = self._day(d)
            if z is None:
                continue
            m = (z["rows"] == rows_rc[i]) & (z["cols"] == cols_rc[i])
            if not m.any():
                continue
            j = int(np.flatnonzero(m)[0])
            for v in SMAP_VARS:
                out[v][i] = z[v][j]
        return pd.DataFrame(out)[list(SMAP_VARS)]


# --- GFS forecast rain (NOAA AWS via live.fetch) --------------------------------------
def _gfs_issue_dir(bbox: tuple[float, ...], issue: dt.date) -> Path:
    return settings.path("gfs", _key(bbox), f"{issue:%Y%m%d}")


def fetch_gfs(issue_dates, bbox: tuple[float, ...] = settings.REGION_TRAIN,
              lead_days: int = settings.GFS_MAX_LEAD_DAYS,
              workers: int = 4) -> Path:
    """6-h APCP bucket subsets per issue date -> gfs/<key>/<YYYYMMDD>/.

    The run is ``live.fetch._gfs_pick_run`` output — the issue date's 00Z
    cycle when its archive files exist, else the newest earlier cycle.
    Issue dates before ``GFS_BUCKET_START`` (2021-03-23, when the
    ``atmos/`` key layout begins) are recorded with no buckets -> NaN.
    """
    from live import fetch as lf

    w, s, e, n = bbox
    out_root = settings.path("gfs", _key(bbox))
    issues = sorted({d for d in _as_dates(issue_dates)})
    for d in issues:
        issue = d.tolist()
        idir = _gfs_issue_dir(bbox, issue)
        meta_p = idir / "meta.json"
        if meta_p.exists():
            continue
        meta: dict = {"issue": str(issue), "fh": [], "missing": [],
                      "lats": [], "lons": []}
        if issue < GFS_BUCKET_START:
            meta["note"] = (f"pre-archive: atmos/ keys start "
                            f"{GFS_BUCKET_START}")
            _write_json(meta_p, meta)
            continue
        try:
            cday, chh = lf._gfs_pick_run(issue)
        except RuntimeError as exc:
            meta["note"] = f"no usable cycle: {exc}"
            _write_json(meta_p, meta)
            continue
        cycle0 = dt.datetime.combine(cday, dt.time(chh))
        meta["cycle"] = f"{cday}T{chh:02d}Z"
        # L28b: _gfs_day_label = the UTC day a trailing 6-h bucket covers
        want = [fh for fh in range(6, 385, 6)
                if 0 <= (lf._gfs_day_label(cycle0 + dt.timedelta(hours=fh))
                         - issue).days < lead_days]

        def work(fh: int, cday: dt.date = cday, chh: int = chh):
            got = lf._gfs_apcp_grid(cday, chh, fh)
            if got is None:
                return fh, None, None, None
            arr, lats, lons = got
            mlat = (lats >= s - 1e-6) & (lats <= n + 1e-6)
            mlon = (lons >= w - 1e-6) & (lons <= e + 1e-6)
            return fh, arr[np.ix_(mlat, mlon)], lats[mlat], lons[mlon]

        with ThreadPoolExecutor(max_workers=workers) as ex:
            for fh, sub, la, lo in ex.map(work, want):
                if sub is None:
                    meta["missing"].append(fh)
                    continue
                _save_npy(idir / f"f{fh:03d}.npy",
                          sub.astype(np.float32))
                meta["fh"].append(fh)
                if la is not None and not meta["lats"]:
                    meta["lats"] = [float(v) for v in la]
                    meta["lons"] = [float(v) for v in lo]
        _write_json(meta_p, meta)
        log.info("gfs: %s cycle %s, %d buckets, %d missing",
                 issue, meta.get("cycle"), len(meta["fh"]),
                 len(meta["missing"]))
    return out_root


def gfs_daily(lon, lat, issue_dates, lead_days: int,
              bbox: tuple[float, ...] = settings.REGION_TRAIN
              ) -> np.ndarray:
    """(n, lead_days) daily forecast rain (mm).

    Column k covers the UTC window (issue+k 00Z, issue+k+1 00Z] — live's
    ``_gfs_day_label`` D+k (L28b) — from the picked run (issue-day 00Z when
    archived). NaN where the run or any bucket is unavailable.
    """
    from live import fetch as lf

    issues = _as_dates(issue_dates)
    out = np.full((len(issues), lead_days), np.nan)
    for i, d in enumerate(issues):
        issue = d.tolist()
        meta_p = _gfs_issue_dir(bbox, issue) / "meta.json"
        if not meta_p.exists():
            continue
        meta = json.loads(meta_p.read_text())
        if not meta.get("cycle") or not meta.get("fh"):
            continue
        cday = dt.date.fromisoformat(meta["cycle"][:10])
        chh = int(meta["cycle"][11:13])
        cycle0 = dt.datetime.combine(cday, dt.time(chh))
        lats = np.asarray(meta["lats"], float)
        lons = np.asarray(meta["lons"], float)
        iy = int(_nearest(lats, [lat[i]])[0]) if len(lats) else -1
        ix = int(_nearest(lons, [lon[i]])[0]) if len(lons) else -1
        if iy < 0 or ix < 0:
            continue
        idir = _gfs_issue_dir(bbox, issue)
        for k in range(lead_days):
            label = issue + dt.timedelta(days=k)   # L28b: label = covered day
            fhs = [fh for fh in sorted(meta["fh"])
                   if lf._gfs_day_label(cycle0 + dt.timedelta(hours=fh))
                   == label]
            if len(fhs) < 4:          # a 24 h window = 4 x 6h buckets
                continue
            vals = []
            for fh in fhs:
                p = idir / f"f{fh:03d}.npy"
                vals.append(np.load(p)[iy, ix] if p.exists() else np.nan)
            out[i, k] = np.nansum(vals) if np.isfinite(vals).all() else np.nan
    return out


# --- CLI ------------------------------------------------------------------------------
def _parse_bbox(v: list[float]) -> tuple[float, float, float, float]:
    return (v[0], v[1], v[2], v[3])


def _parse_points(s: str) -> tuple[list[float], list[float]]:
    lon, lat = [], []
    for pair in s.split(";"):
        lo, la = pair.replace(",", " ").split()
        lon.append(float(lo))
        lat.append(float(la))
    return lon, lat


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="ml.local.sources_dynamic", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_common(sp, dates=True, points=False):
        sp.add_argument("--bbox", type=float, nargs=4,
                        default=list(settings.REGION_TRAIN),
                        metavar=("W", "S", "E", "N"))
        if dates:
            sp.add_argument("--start", required=True, type=dt.date.fromisoformat)
            sp.add_argument("--end", required=True, type=dt.date.fromisoformat)
        if points:
            sp.add_argument("--points", required=True,
                            help='"lon lat;lon lat;..."')
        sp.add_argument("--workers", type=int, default=8)

    for name in ("imerg", "smap", "gfs", "smappoints"):
        add_common(sub.add_parser(name), dates=True,
                   points=name == "smappoints")
    for name in ("clim", "monthly", "constants", "soilmean"):
        sp = sub.add_parser(name)
        sp.add_argument("--bbox", type=float, nargs=4,
                        default=list(settings.REGION_TRAIN),
                        metavar=("W", "S", "E", "N"))
        sp.add_argument("--years", default=None,
                        help="YYYY,YYYY (defaults from settings)")
        sp.add_argument("--workers", type=int, default=8)
        if name == "clim":
            sp.add_argument("--window-days", type=int, default=15)
    spp = sub.add_parser("power")
    spp.add_argument("--points", required=True, help='"lon lat;lon lat;..."')
    spp.add_argument("--workers", type=int, default=4)

    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    bbox = getattr(args, "bbox", None)
    bbox = _parse_bbox(bbox) if bbox else settings.REGION_TRAIN
    if args.cmd == "imerg":
        fetch_imerg_daily(args.start, args.end, bbox=bbox,
                          workers=args.workers)
    elif args.cmd == "monthly":
        yrs = settings.CLIM_YEARS if not args.years else tuple(
            int(v) for v in args.years.split(","))
        fetch_imerg_monthly(years=yrs, bbox=bbox, workers=args.workers)
    elif args.cmd == "clim":
        yrs = settings.CLIM_YEARS if not args.years else tuple(
            int(v) for v in args.years.split(","))
        build_rain_climatology(years=yrs, window_days=args.window_days,
                               bbox=bbox)
    elif args.cmd == "smap":
        fetch_smap_daily(list(_daterange(args.start, args.end)), bbox=bbox,
                         workers=args.workers)
    elif args.cmd == "constants":
        fetch_smap_constants(bbox=bbox)
    elif args.cmd == "soilmean":
        yrs = settings.SOIL_MEAN_YEARS if not args.years else tuple(
            int(v) for v in args.years.split(","))
        build_soil_mean(years=yrs, bbox=bbox, workers=args.workers)
    elif args.cmd == "gfs":
        fetch_gfs(list(_daterange(args.start, args.end)), bbox=bbox,
                  workers=args.workers)
    elif args.cmd == "power":
        lon, lat = _parse_points(args.points)
        fetch_power_points(lon, lat, workers=args.workers)
    elif args.cmd == "smappoints":
        lon, lat = _parse_points(args.points)
        fetch_smap_points(lon, lat,
                          list(_daterange(args.start, args.end)),
                          workers=args.workers)
    return 0


if __name__ == "__main__":
    sys.exit(main())
