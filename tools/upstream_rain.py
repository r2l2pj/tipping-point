"""L35-U2: gridded daily rain cubes over the UPSTREAM boxes of the replay cases.

Two products per case, written under intermediate/upstream/<case>/
(gitignored) as npz cubes:

(A) imerg_daily.npz — observed NASA IMERG V07 daily *Final*
    (GPM_3IMERGDF, 0.1 deg) fetched as GES DISC OPeNDAP ascii subsets with
    the EARTHDATA_TOKEN bearer (same approach as tools/imerg_vs_gauge.py /
    the clim_ref.py scratchpad: one .npy cache file per day, 8 threads,
    array comes back [lon, lat] and is stored transposed).
      keys: dates (int32 YYYYMMDD, rain day = UTC 00-24), lat, lon
            (float64 cell centres, ascending), mm float32 [day, lat, lon],
            NaN for missing; missing_dates int32.
    Days: (i) the replay window and 3 days before (rs 2024-04-15..05-10,
    wa 2025-11-24..12-23); (ii) climatology: every year 2001-2023, every day
    from (window_start - 36 d) to (window_end + 30 d) in month/day terms —
    a superset of the days the pipeline climatology needs (3-day sums over
    [end-3d, end) for ends D +/- 30 d step 3, D any day in the window;
    years 2001-2023). The wa range wraps the year boundary, so its January
    days belong to year y+1.

(B) gfs_issued.npz — issued NOAA GFS 0.25 deg 00Z runs from the AWS bucket
    noaa-gfs-bdp-pds, decoded by the existing GDAL GRIB path
    (live/fetch.py _gfs_decode_message via _gfs_apcp_grid), daily totals
    per calendar day D..D+3 = sums of the four trailing-6h APCP buckets
    ending in that day (same logic as tools/regen_gfs_replay.py day_fhs:
    day D+k <- fh 24k+6..24k+24). Issue days: rs 2024-04-22..05-07,
    wa 2025-11-30..12-20 (the replay windows).
      keys: issue (int32 YYYYMMDD), k (int32 0..3 = day D+k), lat, lon
            (float64 cell centres ascending), mm float32 [issue,k,lat,lon];
            missing_issues int32.

Verification: --verify reads data/<case>/forecast_rain/<case>_gfs_<issue>.tif
for one issue per case and compares every overlapping cell/band with the
stored cube (both are the same native 0.25 grid values — max abs diff is
reported, expected 0).

    PYTHONPATH=.:pipeline uv run --no-sync python tools/upstream_rain.py \
        [--case rs_2024|wa_2025] [--obs-only|--gfs-only|--verify-only] \
        [--workers 8]

Needs EARTHDATA_TOKEN for part (A). Part (B) is anonymous (public bucket).

GES DISC throttled to ~4 days/min on 2026-11-15, so the per-day cache was
filled from Earth Engine NASA/GPM_L3/IMERG_V07 instead
(tools/upstream_rain_gee.py, same grid, same .npy layout); on the 537
days fetched from both sources the max abs difference was 0.000 mm.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
for _p in (str(REPO), str(REPO / "pipeline")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from live import fetch as lf
from tools import imerg_vs_gauge as ivg
from tools import regen_gfs_replay as rg

OUT_ROOT = REPO / "intermediate" / "upstream"

BOXES = {
    "rs_2024": (-54.8, -31.0, -49.8, -27.8),    # w, s, e, n — generous, covers the upstream basin
    "wa_2025": (-123.0, 46.6, -120.4, 49.8),
}
OBS_DAYS = {                                    # (i) replay window + 3 days before
    "rs_2024": (dt.date(2024, 4, 15), dt.date(2024, 5, 10)),
    "wa_2025": (dt.date(2025, 11, 24), dt.date(2025, 12, 23)),
}
ISSUE_DAYS = {                                  # replay windows (00Z issue days)
    "rs_2024": (dt.date(2024, 4, 22), dt.date(2024, 5, 7)),
    "wa_2025": (dt.date(2025, 11, 30), dt.date(2025, 12, 20)),
}
YEARS = range(2001, 2024)
OFFSETS = range(-30, 31, 3)
WIN_DAYS = 3
N_K = 4                                          # k = 0..3 -> day D+k
IMERG_RES = 0.1


def _daterange(a: dt.date, b: dt.date):
    d = a
    while d <= b:
        yield d
        d += dt.timedelta(days=1)


def _from_ymd(y: int, m: int, d: int) -> dt.date:
    """ee.Date.fromYMD semantics: Feb 29 of a non-leap year -> Mar 1."""
    try:
        return dt.date(y, m, d)
    except ValueError:
        return dt.date(y, m, 1) + dt.timedelta(days=d - 1)


def replay_window(case: str) -> tuple[dt.date, dt.date]:
    """Replay window = the obs range minus its leading 3 days."""
    a, b = OBS_DAYS[case]
    return a + dt.timedelta(days=3), b


# --------------------------------------------------------------------------- #
# (A) IMERG observed daily
# --------------------------------------------------------------------------- #
def imerg_index_window(box: tuple[float, float, float, float]
                       ) -> tuple[int, int, int, int]:
    """Global IMERG 0.1 deg index ranges (inclusive) whose cells cover `box`.

    Cell i centres at -179.95 + i*0.1 (lon), j at -89.95 + j*0.1 (lat); the
    window is every cell whose footprint intersects the box — for these
    0.1-aligned boxes the centres sit just inside the edges.
    """
    w, s, e, n = box
    i0 = int(np.floor(round((w + 180.0) / IMERG_RES, 6)))
    i1 = int(np.ceil(round((e + 180.0) / IMERG_RES, 6))) - 1
    j0 = int(np.floor(round((s + 90.0) / IMERG_RES, 6)))
    j1 = int(np.ceil(round((n + 90.0) / IMERG_RES, 6))) - 1
    return i0, i1, j0, j1


def imerg_centers(i0: int, i1: int, j0: int, j1: int):
    """(lats ascending, lons ascending) cell centres of the index window."""
    lons = np.round(-179.95 + np.arange(i0, i1 + 1) * IMERG_RES, 4)
    lats = np.round(-89.95 + np.arange(j0, j1 + 1) * IMERG_RES, 4)
    return lats, lons


def clim_days(case: str) -> list[dt.date]:
    """(ii) climatology day list: the literal spec range unioned with the
    exact days the climatology sums need, for every year 2001-2023.

    Literal: every day from (window_start - 36 d) to (window_end + 30 d) in
    month/day terms (window = replay window; Jan tail of the wa range lands
    in year y+1). Exact: for every target day D in the window and offset o
    in -30..30 step 3, the 3-day sum over [end-3d, end) with
    end = date(y, D.month, D.day) + o needs days end-3..end-1.
    """
    ws, we = replay_window(case)
    lo = ws - dt.timedelta(days=36)
    hi = we + dt.timedelta(days=30)
    days: set[dt.date] = set()
    for y in YEARS:
        start = _from_ymd(y, lo.month, lo.day)
        end = _from_ymd(y, hi.month, hi.day)
        if end <= start:                       # range wraps the year boundary
            end = _from_ymd(y + 1, hi.month, hi.day)
        days.update(_daterange(start, end))
        for k in range((we - ws).days + 1):    # exact climatology need
            d = ws + dt.timedelta(days=k)
            ref = _from_ymd(y, d.month, d.day)
            for o in OFFSETS:
                days.update(_daterange(
                    ref + dt.timedelta(days=o - WIN_DAYS),
                    ref + dt.timedelta(days=o - 1)))
    return sorted(days)


def needed_days(case: str) -> list[dt.date]:
    """All IMERG days to fetch: obs window (i) union climatology (ii)."""
    a, b = OBS_DAYS[case]
    return sorted(set(_daterange(a, b)) | set(clim_days(case)))


def fetch_day_fast(day: dt.date, i0: int, i1: int, j0: int, j1: int,
                   cache: Path, token: str, retries: int = 3,
                   delay: float = 1.0) -> np.ndarray:
    """ivg.fetch_imerg_day with short retries — under GES DISC throttling
    (random 503s) it is cheaper to fail fast and let the next resume pass
    re-try than to sit through long sleeps. Same OPeNDAP ascii subset, same
    per-day .npy cache, same V07B->V07C->V07A revision fallback on 404."""
    f = cache / f"{day:%Y%m%d}.npy"
    if f.exists():
        return np.load(f)
    import requests

    err = None
    for rev in ivg.FILE_REVS:
        url = ivg._ascii_url(day, rev, i0, i1, j0, j1)
        r = None
        for _attempt in range(retries):
            try:
                r = requests.get(url, timeout=60, headers={
                    "Authorization": f"Bearer {token}"})
                if r.status_code == 200 and "precipitation" in r.text:
                    arr = ivg.parse_ascii(r.text, i1 - i0 + 1, j1 - j0 + 1)
                    cache.mkdir(parents=True, exist_ok=True)
                    np.save(f, arr)
                    return arr
                err = f"HTTP {r.status_code}"
            except (requests.RequestException, ValueError) as e:
                err = str(e)
            time.sleep(delay)
        if r is not None and r.status_code == 404:  # try the next revision
            continue
        break
    raise RuntimeError(f"IMERG Final {day}: fetch failed ({err})")


def fetch_imerg_case(case: str, cache_dir: Path, token: str,
                     workers: int = 8, fetch_day=fetch_day_fast,
                     passes: int = 10):
    """-> (mm float32 [day, lat, lon], lats, lons, fetched, missing).

    fetch_day(day, i0, i1, j0, j1, cache, token) -> [lon, lat] array; one
    .npy cache file per day under cache_dir, days fetched in parallel.
    Failures are NaN rows and land in `missing` (the cube keeps every day).
    """
    i0, i1, j0, j1 = imerg_index_window(BOXES[case])
    days = needed_days(case)
    arrs: dict[dt.date, np.ndarray] = {}
    t0 = time.time()

    def store(d, arr):
        a = np.asarray(arr, dtype=np.float32).T       # [lon,lat]->[lat,lon]
        a[(a < 0) | ~np.isfinite(a)] = np.nan
        arrs[d] = a

    for p in range(passes):
        todo = [d for d in days if d not in arrs]
        if not todo:
            break
        print(f"[{case}] imerg pass {p + 1}: {len(arrs)}/{len(days)} cached, "
              f"{len(todo)} to fetch ({workers} workers)", flush=True)

        def work(d):
            try:
                return d, fetch_day(d, i0, i1, j0, j1, cache_dir, token)
            except Exception as exc:  # noqa: BLE001 - next pass retries
                return d, exc  # noqa: B023

        n = 0
        with cf.ThreadPoolExecutor(workers) as ex:
            for d, result in ex.map(work, todo):
                n += 1
                if isinstance(result, Exception):
                    exc = result
                    if p == passes - 1 or n % 50 == 0:
                        print(f"[{case}] imerg FAIL {d}: "
                              f"{type(exc).__name__}: {exc}", flush=True)
                    continue
                store(d, result)
                if n % 200 == 0 or n == len(todo):
                    rate = n / max(time.time() - t0, 1e-6)
                    print(f"[{case}] imerg {len(arrs)}/{len(days)} "
                          f"({rate:.2f}/s)", flush=True)
    missing = [d for d in days if d not in arrs]
    lats, lons = imerg_centers(i0, i1, j0, j1)
    mm = np.stack([arrs.get(d, np.full((len(lats), len(lons)), np.nan,
                                       np.float32)) for d in days])
    return mm, lats, lons, [d for d in days if d not in missing], missing


def write_imerg_npz(case: str, out_dir: Path, mm, lats, lons,
                    missing: list[dt.date]) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "imerg_daily.npz"
    days = needed_days(case)
    np.savez_compressed(
        path,
        dates=np.array([int(f"{d:%Y%m%d}") for d in days], np.int32),
        lat=lats.astype(np.float64),
        lon=lons.astype(np.float64),
        mm=mm.astype(np.float32),
        missing_dates=np.array([int(f"{d:%Y%m%d}") for d in missing],
                               np.int32),
    )
    return path


# --------------------------------------------------------------------------- #
# (B) GFS issued forecast
# --------------------------------------------------------------------------- #
def gfs_day_fhs(k: int) -> list[int]:
    """00Z-run forecast hours whose trailing-6h APCP buckets cover calendar
    day D+k: fh 24k+6, 24k+12, 24k+18, 24k+24 (== regen day_fhs(k+1))."""
    return rg.day_fhs(k + 1)


def fetch_gfs_issue(issue: dt.date, box, workers: int = 8,
                    apcp_grid=lf._gfs_apcp_grid, head_ok=lf._gfs_head_ok):
    """One 00Z run -> (cube[N_K, lat, lon], lats, lons, missing_ks).

    `missing_ks` lists day indices k whose buckets were unavailable; those
    slabs are NaN. Raises RuntimeError when the whole run is absent.
    """
    last = gfs_day_fhs(N_K - 1)[-1]
    if not head_ok(f"{lf._GFS_BASE}/"
                   f"{lf._gfs_key(issue, 0, last)}.idx"):
        raise RuntimeError(f"{issue} 00Z run lacks f{last:03d}.idx")
    fhs = [fh for k in range(N_K) for fh in gfs_day_fhs(k)]
    grids: dict[int, np.ndarray] = {}
    lats = lons = None
    failed: set[int] = set()

    def work(fh):
        for _try in range(3):
            try:
                got = apcp_grid(issue, 0, fh)
                if got is not None:
                    return fh, got
            except Exception:  # noqa: BLE001 - retry below
                time.sleep(2)
            time.sleep(1)
        return fh, None

    with cf.ThreadPoolExecutor(workers) as ex:
        for fh, got in ex.map(work, fhs):
            if got is None:
                failed.add(fh)
                continue
            arr, la, lo = got
            if lats is None:
                lats, lons = la, lo
            elif not (np.array_equal(la, lats) and np.array_equal(lo, lons)):
                raise RuntimeError(f"{issue} f{fh:03d}: grid changed")
            grids[fh] = arr
    w, s, e, n = box
    mlat = (lats >= s - 1e-6) & (lats <= n + 1e-6)
    mlon = (lons >= w - 1e-6) & (lons <= e + 1e-6)
    sub_lats, sub_lons = lats[mlat], lons[mlon]
    cube = np.full((N_K, len(sub_lats), len(sub_lons)), np.nan, np.float32)
    missing_ks = []
    for k in range(N_K):
        acc = None
        ok = True
        for fh in gfs_day_fhs(k):
            if fh in failed or fh not in grids:
                ok = False
                break
            sub = grids[fh][np.ix_(mlat, mlon)]
            acc = sub if acc is None else acc + sub
        if ok and acc is not None:
            cube[k] = acc
        else:
            missing_ks.append(k)
    return cube, sub_lats, sub_lons, missing_ks


def fetch_gfs_case(case: str, workers: int = 8,
                   apcp_grid=lf._gfs_apcp_grid, head_ok=lf._gfs_head_ok):
    """-> (mm [issue, k, lat, lon], issues, lats, lons, missing issues)."""
    a, b = ISSUE_DAYS[case]
    issues = list(_daterange(a, b))
    mm = lats = lons = None
    missing, partial = [], {}
    for issue in issues:
        try:
            cube, la, lo, ks = fetch_gfs_issue(
                issue, BOXES[case], workers=workers,
                apcp_grid=apcp_grid, head_ok=head_ok)
        except Exception as exc:  # noqa: BLE001 - whole run absent
            missing.append(issue)
            print(f"[{case}] gfs FAIL {issue}: "
                  f"{type(exc).__name__}: {exc}", flush=True)
            continue
        if lats is None:
            lats, lons = la, lo
            mm = np.full((len(issues), N_K, len(lats), len(lons)),
                         np.nan, np.float32)
        if ks:
            partial[issue] = ks
            print(f"[{case}] gfs {issue}: missing day(s) {ks}", flush=True)
        mm[issues.index(issue)] = cube
    if mm is None:                                   # every issue missing
        lats = lons = np.empty(0)
        mm = np.full((len(issues), N_K, 0, 0), np.nan, np.float32)
    return mm, issues, lats, lons, sorted(missing), partial


def write_gfs_npz(case: str, out_dir: Path, mm, issues, lats, lons,
                  missing: list[dt.date]) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "gfs_issued.npz"
    np.savez_compressed(
        path,
        issue=np.array([int(f"{d:%Y%m%d}") for d in issues], np.int32),
        k=np.arange(N_K, dtype=np.int32),
        lat=lats.astype(np.float64),
        lon=lons.astype(np.float64),
        mm=mm.astype(np.float32),
        missing_issues=np.array([int(f"{d:%Y%m%d}") for d in missing],
                                np.int32),
    )
    return path


# --------------------------------------------------------------------------- #
# verify vs the existing case COG (same native 0.25 grid, same messages)
# --------------------------------------------------------------------------- #
def verify_gfs_cog(tif_path: Path, issue: dt.date,
                   mm, issues, lats, lons) -> dict:
    """Compare cube[issue, k] with COG band d(k+1) over overlapping cells."""
    import rasterio

    ii = issues.index(issue)
    with rasterio.open(tif_path) as ds:
        bands = ds.read()                                # (4, y, x) north-up
        t = ds.transform
        w_lons = t.c + (np.arange(ds.width) + 0.5) * t.a
        w_lats = t.f + (np.arange(ds.height) + 0.5) * t.e  # descending
    lon_idx = {round(float(x), 6): i for i, x in enumerate(lons)}
    lat_idx = {round(float(x), 6): i for i, x in enumerate(lats)}
    per_band, worst = {}, 0.0
    for k in range(N_K):
        diffs, n_cmp, n_miss = [], 0, 0
        for r, la in enumerate(w_lats):
            qi = lat_idx.get(round(float(la), 6))
            if qi is None:
                continue
            for c, lo_ in enumerate(w_lons):
                qj = lon_idx.get(round(float(lo_), 6))
                if qj is None:
                    continue
                cog_v = float(bands[k, r, c])
                my_v = float(mm[ii, k, qi, qj])
                if np.isnan(cog_v) != np.isnan(my_v):
                    n_miss += 1
                    continue
                if np.isnan(cog_v):
                    continue
                n_cmp += 1
                diffs.append(abs(cog_v - my_v))
        per_band[f"k{k}"] = {
            "cells": n_cmp, "nan_mismatches": n_miss,
            "max_abs_diff": max(diffs) if diffs else None,
        }
        worst = max(worst, max(diffs) if diffs else 0.0)
    return {"case_tif": str(tif_path), "issue": issue.isoformat(),
            "max_abs_diff": worst, "bands": per_band}


def verify_case(case: str, out_dir: Path,
                issue: dt.date | None = None) -> dict:
    z = np.load(out_dir / "gfs_issued.npz")
    issues = [dt.datetime.strptime(str(v), "%Y%m%d").date()
              for v in z["issue"]]
    issue = issue or issues[0]
    tif = REPO / "data" / case / "forecast_rain" / \
        f"{case}_gfs_{issue:%Y%m%d}.tif"
    return verify_gfs_cog(tif, issue, z["mm"], issues,
                          z["lat"], z["lon"])


# --------------------------------------------------------------------------- #
def run_case(case: str, workers: int, token: str | None,
             obs: bool, gfs: bool, passes: int = 10) -> dict:
    out_dir = OUT_ROOT / case
    report: dict = {"box": BOXES[case]}
    if obs:
        if not token:
            raise SystemExit("EARTHDATA_TOKEN not set (IMERG bearer)")
        mm, lats, lons, fetched, missing = fetch_imerg_case(
            case, out_dir / "imerg_cache", token, workers, passes=passes)
        path = write_imerg_npz(case, out_dir, mm, lats, lons, missing)
        report["imerg"] = {
            "npz": str(path.relative_to(REPO)),
            "shape": list(mm.shape),
            "n_days": mm.shape[0],
            "days_fetched": len(fetched), "days_missing": len(missing),
            "missing": [d.isoformat() for d in missing],
            "lat": [float(lats[0]), float(lats[-1]), len(lats)],
            "lon": [float(lons[0]), float(lons[-1]), len(lons)],
        }
        print(f"[{case}] wrote {path} shape={mm.shape} "
              f"fetched={len(fetched)} missing={len(missing)}", flush=True)
    if gfs:
        mm, issues, lats, lons, missing, partial = fetch_gfs_case(
            case, workers)
        path = write_gfs_npz(case, out_dir, mm, issues, lats, lons, missing)
        report["gfs"] = {
            "npz": str(path.relative_to(REPO)),
            "shape": list(mm.shape),
            "n_issues": len(issues),
            "issues_fetched": len(issues) - len(missing),
            "issues_missing": len(missing),
            "missing": [d.isoformat() for d in missing],
            "partial_days": {d.isoformat(): ks for d, ks in partial.items()},
            "lat": [float(lats[0]), float(lats[-1]), len(lats)]
                   if len(lats) else [],
            "lon": [float(lons[0]), float(lons[-1]), len(lons)]
                   if len(lons) else [],
        }
        print(f"[{case}] wrote {path} shape={mm.shape} "
              f"fetched={len(issues) - len(missing)} "
              f"missing={len(missing)}", flush=True)
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--case", choices=sorted(BOXES))
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--passes", type=int, default=10,
                    help="IMERG resume sweeps over still-missing days")
    ap.add_argument("--obs-only", action="store_true")
    ap.add_argument("--gfs-only", action="store_true")
    ap.add_argument("--verify-only", action="store_true",
                    help="only run the COG-overlap check per case")
    ap.add_argument("--verify-issue", type=dt.date.fromisoformat,
                    default=None, help="issue day to check (default: first)")
    args = ap.parse_args(argv)
    cases = [args.case] if args.case else sorted(BOXES)
    report = {}
    if args.verify_only:
        for case in cases:
            report[case] = verify_case(
                case, OUT_ROOT / case, args.verify_issue)
            print(json.dumps(report[case], indent=1))
    else:
        token = os.environ.get("EARTHDATA_TOKEN")
        obs = not args.gfs_only
        gfs = not args.obs_only
        for case in cases:
            report[case] = run_case(case, args.workers, token, obs, gfs,
                                    args.passes)
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    rp = OUT_ROOT / "report.json"
    prev = json.loads(rp.read_text()) if rp.exists() else {}
    prev.update(report)
    rp.write_text(json.dumps(prev, indent=2) + "\n")
    print(f"wrote {rp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
