"""Now-job rain climatology at IMERG native 0.1° (L30-clim), no GEE.

One file for the whole year on the leap-year calendar (doy 1..366):

  data/static/south_brazil/clim/clim_imerg_0p1.tif
    EPSG:4326, the 0.1° IMERG grid over NOW_BOUNDS padded by one cell,
    north-up. 1098 bands, band index (doy-1)*3 + {0: p90, 1: p95, 2: p99}
    (1-based GDAL band = that + 1), descriptions 'doy001_p90' ...

Semantics match live/build_static.py `_climatology` (gee/01_tpi_index.js):
for doy N -> (month, day) = date(2000,1,1)+N-1; for each year 2001..2023
and offset in range(-30, 31, 3): end = date(y, month, day) + offset
(Feb 29 in a non-leap year rolls to Mar 1, like ee.Date.fromYMD); sample =
sum of daily rain over [end-3d, end). 483 samples -> numpy percentile
(linear) 90/95/99 per pixel, mm.

Source: IMERG Final daily V07 (GPM_3IMERGDF.07) via GES DISC OPeNDAP with
the EARTHDATA_TOKEN bearer; each day's subset is cached under
data/raw/imerg_final_daily/YYYY/YYYYMMDD.npy (resumable, gitignored).

    uv run python live/build_clim.py [--workers 16] [--download-only]
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import os
import re
import struct
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from live.config import NOW_BOUNDS, REPO_ROOT

YEARS = range(2001, 2024)
OFFSETS = range(-30, 31, 3)
WINDOW_DAYS = 3
PCTS = (90, 95, 99)
PCT_NAMES = ("p90", "p95", "p99")
NDOY = 366
RES = 0.1
COLLECTION = "GPM_3IMERGDF.07"
OPENDAP = "https://gpm1.gesdisc.eosdis.nasa.gov/opendap/GPM_L3/GPM_3IMERGDF.07"
CACHE_DIR = REPO_ROOT / "data" / "raw" / "imerg_final_daily"
CLIM_REL = "clim/clim_imerg_0p1.tif"
OUT_PATH = REPO_ROOT / "data" / "static" / "south_brazil" / CLIM_REL


# --- grid -----------------------------------------------------------------

def native_index_window(bounds=NOW_BOUNDS, pad: int = 1):
    """IMERG global 0.1° index ranges (inclusive) covering bounds + pad cells.
    lon centers -179.95 + i*0.1, lat centers -89.95 + j*0.1 (ascending)."""
    w, s, e, n = bounds
    def q(x):
        return round((x) / RES, 6)
    i0 = int(np.floor(q(w + 180.0))) - pad
    i1 = int(np.ceil(q(e + 180.0))) - 1 + pad
    j0 = int(np.floor(q(s + 90.0))) - pad
    j1 = int(np.ceil(q(n + 90.0))) - 1 + pad
    return i0, i1, j0, j1


def native_centers(bounds=NOW_BOUNDS, pad: int = 1):
    """(lats descending north-first, lons ascending) of the padded grid."""
    i0, i1, j0, j1 = native_index_window(bounds, pad)
    lons = np.round(-179.95 + np.arange(i0, i1 + 1) * RES, 4)
    lats = np.round(-89.95 + np.arange(j0, j1 + 1) * RES, 4)[::-1]
    return lats, lons


def native_transform(lats: np.ndarray, lons: np.ndarray):
    from rasterio.transform import from_origin
    return from_origin(float(lons[0]) - RES / 2, float(lats[0]) + RES / 2,
                       RES, RES)


# --- date logic -------------------------------------------------------------

def doy_month_day(doy: int) -> tuple[int, int]:
    d = dt.date(2000, 1, 1) + dt.timedelta(days=doy - 1)
    return d.month, d.day


def leap_doy(d: dt.date) -> int:
    """Calendar (month, day) -> 1..366 on the leap-year calendar."""
    return (dt.date(2000, d.month, d.day) - dt.date(2000, 1, 1)).days + 1


def from_ymd(y: int, m: int, d: int) -> dt.date:
    """ee.Date.fromYMD semantics: Feb 29 of a non-leap year -> Mar 1."""
    try:
        return dt.date(y, m, d)
    except ValueError:
        return dt.date(y, m, 1) + dt.timedelta(days=d - 1)


def window_ends(doy: int) -> list[dt.date]:
    m, d = doy_month_day(doy)
    return [from_ymd(y, m, d) + dt.timedelta(days=o)
            for y in YEARS for o in OFFSETS]


def sample_days(end: dt.date) -> list[dt.date]:
    """The WINDOW_DAYS days before `end` (end exclusive)."""
    return [end - dt.timedelta(days=k) for k in range(WINDOW_DAYS, 0, -1)]


def needed_days() -> list[dt.date]:
    days = set()
    for doy in range(1, NDOY + 1):
        for end in window_ends(doy):
            days.update(sample_days(end))
    return sorted(days)


def band_index(doy: int, pct: int | str) -> int:
    """0-based band index; GDAL band number is this + 1."""
    k = PCT_NAMES.index(pct) if isinstance(pct, str) else PCTS.index(pct)
    return (doy - 1) * 3 + k


# --- download ---------------------------------------------------------------

def _token() -> str:
    tok = os.environ.get("EARTHDATA_TOKEN")
    if not tok:
        sys.exit("EARTHDATA_TOKEN not set")
    return tok


def parse_dods_first_array(raw: bytes, shape: tuple[int, ...]) -> np.ndarray:
    """First Float32 array of a DAP2 .dods response (XDR big-endian)."""
    k = raw.index(b"\nData:\n") + len(b"\nData:\n")
    n1, n2 = struct.unpack(">II", raw[k:k + 8])
    n = int(np.prod(shape))
    if n1 != n or n2 != n:
        raise ValueError(f"dods array length {n1}/{n2} != {n}")
    return np.frombuffer(raw, dtype=">f4", count=n, offset=k + 8) \
        .astype(np.float32).reshape(shape)


_name_cache: dict[str, dict[str, str]] = {}
_name_lock = threading.Lock()


def _get(url: str, timeout: float = 120) -> bytes:
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {_token()}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _file_name(day: dt.date, version: str = "V07B") -> str:
    return (f"3B-DAY.MS.MRG.3IMERG.{day:%Y%m%d}-S000000-E235959."
            f"{version}.nc4")


def _listing_name(day: dt.date) -> str | None:
    """Actual granule name from the month directory listing (version
    suffix differs between reprocessings)."""
    key = f"{day:%Y/%m}"
    with _name_lock:
        names = _name_cache.get(key)
    if names is None:
        html = _get(f"{OPENDAP}/{key}/contents.html").decode("utf-8", "replace")
        names = {}
        for m in re.finditer(
                r"3B-DAY\.MS\.MRG\.3IMERG\.(\d{8})-S000000-E235959\.(V07\w*)\.nc4",
                html):
            names[m.group(1)] = m.group(0)
        with _name_lock:
            _name_cache[key] = names
    return names.get(f"{day:%Y%m%d}")


def cache_path(day: dt.date, cache_dir: Path = CACHE_DIR) -> Path:
    return cache_dir / f"{day:%Y}" / f"{day:%Y%m%d}.npy"


def fetch_day(day: dt.date, cache_dir: Path = CACHE_DIR,
              retries: int = 8) -> Path:
    """Download one day's padded subset to the cache as (lat desc, lon)
    float32 mm/day (NaN = missing)."""
    out = cache_path(day, cache_dir)
    if out.exists():
        return out
    i0, i1, j0, j1 = native_index_window()
    shape = (1, i1 - i0 + 1, j1 - j0 + 1)
    name = _file_name(day)
    delay = 5.0
    for attempt in range(retries):
        url = (f"{OPENDAP}/{day:%Y/%m}/{name}.dods?"
               f"precipitation[0:0][{i0}:{i1}][{j0}:{j1}]")
        try:
            arr = parse_dods_first_array(_get(url), shape)
            break
        except urllib.error.HTTPError as e:
            if e.code == 404 and name == _file_name(day):
                alt = _listing_name(day)
                if alt and alt != name:
                    name = alt
                    continue
                raise
            if e.code in (401, 403):
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError,
                ValueError, OSError):
            pass
        if attempt == retries - 1:
            raise RuntimeError(f"giving up on {day}")
        time.sleep(delay)
        delay = min(delay * 2, 120)
    a = arr[0].T[::-1].copy()                     # (lat desc, lon)
    a[(a < 0) | ~np.isfinite(a)] = np.nan
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp.npy")
    np.save(tmp, a)
    tmp.replace(out)
    return out


def download(days: list[dt.date], workers: int = 16,
             cache_dir: Path = CACHE_DIR, reverse: bool = False) -> list[dt.date]:
    todo = [d for d in days if not cache_path(d, cache_dir).exists()]
    if reverse:
        todo.reverse()
    print(f"[build_clim] {len(days)} days needed, {len(todo)} to fetch "
          f"({workers} workers)", flush=True)
    failed, t0 = [], time.time()
    with cf.ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(fetch_day, d, cache_dir): d for d in todo}
        for k, f in enumerate(cf.as_completed(futs), 1):
            try:
                f.result()
            except Exception as e:  # noqa: BLE001 - report and continue
                failed.append(futs[f])
                print(f"[build_clim] FAIL {futs[f]}: {type(e).__name__}: {e}",
                      flush=True)
            if k % 100 == 0 or k == len(todo):
                rate = k / max(time.time() - t0, 1e-6)
                print(f"[build_clim] {k}/{len(todo)} "
                      f"({rate:.2f}/s, eta {(len(todo) - k) / rate / 60:.0f} min)",
                      flush=True)
    return failed


# --- percentiles --------------------------------------------------------------

def load_series(days: list[dt.date], cache_dir: Path = CACHE_DIR):
    """(index dict day->row, stack (T, H, W) float32)."""
    arrs = [np.load(cache_path(d, cache_dir)) for d in days]
    return {d: k for k, d in enumerate(days)}, np.stack(arrs)


def compute_climatology(index: dict[dt.date, int], stack: np.ndarray,
                        doys=range(1, NDOY + 1)) -> np.ndarray:
    """(len(doys)*3, H, W) float32 p90/p95/p99 per doy, mm."""
    _, h, w = stack.shape
    out = np.empty((len(doys) * 3, h, w), np.float32)
    for k, doy in enumerate(doys):
        rows = np.array([[index[d] for d in sample_days(e)]
                         for e in window_ends(doy)])          # (483, 3)
        samples = stack[rows].sum(axis=1, dtype=np.float64)   # (483, H, W)
        p = np.nanpercentile(samples, PCTS, axis=0)
        out[k * 3:(k + 1) * 3] = p.astype(np.float32)
    return out


# --- write --------------------------------------------------------------------

def write_tif(path: Path, data: np.ndarray, lats: np.ndarray, lons: np.ndarray,
              doys=range(1, NDOY + 1), years=YEARS) -> Path:
    import rasterio

    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.round(data, 1).astype(np.float32)
    profile = {
        "driver": "GTiff", "width": len(lons), "height": len(lats),
        "count": data.shape[0], "dtype": "float32", "crs": "EPSG:4326",
        "transform": native_transform(lats, lons), "nodata": np.nan,
        "compress": "DEFLATE", "predictor": 3, "zlevel": 9,
        "interleave": "band", "tiled": True,
        "blockxsize": 128, "blockysize": 128}
    tmp = path.with_suffix(".tmp.tif")
    with rasterio.open(tmp, "w", **profile) as ds:
        ds.write(data)
        for doy in doys:
            for k, nm in enumerate(PCT_NAMES):
                ds.set_band_description(
                    (doy - doys[0]) * 3 + k + 1, f"doy{doy:03d}_{nm}")
        ds.update_tags(
            source="NASA GPM IMERG Final daily V07 (GES DISC OPeNDAP)",
            collection=COLLECTION,
            years=f"{years[0]}-{years[-1]}",
            window=f"{WINDOW_DAYS}-day sum over [end-{WINDOW_DAYS}d, end); "
                   f"end = date(y, month, day) + offset, offsets "
                   f"{OFFSETS.start}..{OFFSETS.stop - 1} step {OFFSETS.step}",
            method="numpy percentile (linear) 90/95/99 per pixel over "
                   f"{len(years) * len(OFFSETS)} samples; leap-year doy "
                   "calendar; band (doy-1)*3+{0:p90,1:p95,2:p99}",
            units="mm", grid="IMERG native 0.1 deg, NOW_BOUNDS padded 1 cell")
    tmp.replace(path)
    return path


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="build_clim",
                                description=__doc__.splitlines()[0])
    p.add_argument("--workers", type=int, default=16)
    p.add_argument("--download-only", action="store_true")
    p.add_argument("--reverse", action="store_true",
                   help="fetch newest days first (run a second downloader)")
    p.add_argument("--out", type=Path, default=OUT_PATH)
    a = p.parse_args(argv)

    days = needed_days()
    failed = download(days, a.workers, reverse=a.reverse)
    if failed:
        print(f"[build_clim] {len(failed)} day(s) failed; rerun to resume",
              file=sys.stderr)
        return 1
    if a.download_only:
        return 0
    index, stack = load_series(days)
    print(f"[build_clim] stack {stack.shape}, NaN frac "
          f"{np.isnan(stack).mean():.4f}", flush=True)
    data = compute_climatology(index, stack)
    lats, lons = native_centers()
    out = write_tif(a.out, data, lats, lons)
    print(f"[build_clim] wrote {out} ({out.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
