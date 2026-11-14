"""Replay NOAA GFS forecast precip from GEE (NOAA/GFS0P25) -> per-issue COGs.

For a replay case and an issue day D this module replays what an operational
forecaster could have seen: the GFS run issued on D (00Z cycle preferred;
the latest run with creation_time <= D end-of-day otherwise), summed into
daily forecast precipitation for days D+k (k = 1..3 by default).

GEE's NOAA/GFS0P25 collection carries one image per (run, forecast hour);
``total_precipitation_surface`` is a *trailing* accumulation in kg/m^2 (= mm)
over the 1-6 h interval ending at that forecast's valid time, RESETTING
each 6-h boundary — so summing every hourly image double-counts (~3.3x
measured); daily mm = sum over the forecast_hours % 6 == 0 images valid
in (day_start, day_end]. Forecast hours extend 0-384, and 4 runs/day post to GEE
(00/06/12/18Z) covering 2015 -> now.

Output (lead-analysis layout): one COG per issue day at
``data/<case>/forecast_rain/<case>_gfs_<YYYYMMDD>.tif`` — 3 float32 bands
``d1``..``d3``, EPSG:4326 north-up at the ~25 km GFS native scale on the
case AOI — plus a ``<case>_gfs_<YYYYMMDD>.json`` sidecar recording the
issued run's creation_time, the day buckets, and the honesty label
``'forecast: NOAA GFS (issued <date>)'``.

    uv run python -m pipeline.fetch.gfs --case rs_2024 --issue 2024-04-27 \
        [--days 1,2,3] [--out-dir DIR] [--dry-run]
    uv run python -m pipeline.fetch.gfs --case rs_2024 --list-runs \
        --start 2024-04-20 --end 2024-05-07

Earth Engine is read lazily: importing this module needs no ee and no
credentials; real runs call ``gee.common.ee_init()`` which reads
GEE_PROJECT plus the service-account env (GCP_SA_KEY /
GOOGLE_APPLICATION_CREDENTIALS) from the environment. Tests inject a fake
``ee`` module via ``sys.modules`` and a stub ImageCollection honoring
filterDate/filter/select/sum.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib
import io
import json
import sys
import zipfile
from pathlib import Path

import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

COLLECTION = "NOAA/GFS0P25"
BAND = "total_precipitation_surface"   # kg/m^2 == mm, trailing 1-6h accumulation
SCALE_M = 25_000                       # GFS 0.25 deg native ~25 km
DEFAULT_DAYS = (1, 2, 3)
SOURCE = "NOAA GFS via GEE"
HOUR_MS = 3_600_000
MAX_DOWNLOAD_BYTES = 32 * 1024 * 1024

REPO_ROOT = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- #
# lazy plumbing — nothing below imports ee or tp/gee modules at module level
# --------------------------------------------------------------------------- #
def _ee():
    """The ee module (importlib so tests can install a sys.modules fake)."""
    return importlib.import_module("ee")


def _init_ee():
    """gee.common.ee_init() -> resolved GEE project (env GEE_PROJECT)."""
    repo = str(REPO_ROOT)
    if repo not in sys.path:
        sys.path.insert(0, repo)
    from gee import common

    return common.ee_init()


def _case_cfg(case: str) -> tuple[str, list[float]]:
    """(canonical case id, aoi [w, s, e, n]) — accepts case or event id."""
    from tp.config import case as _case
    from tp.config import case_id

    cid = case_id(case)
    return cid, list(_case(cid)["aoi"])


def _ms(d: dt.date) -> int:
    """Epoch ms of 00:00Z on date d."""
    return int(dt.datetime(d.year, d.month, d.day,
                           tzinfo=dt.UTC).timestamp() * 1000)


def _iso(ms: int) -> str:
    return dt.datetime.fromtimestamp(
        ms / 1000, dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def out_dir(case_id: str, root: Path | None = None) -> Path:
    return Path(root or REPO_ROOT) / "data" / case_id / "forecast_rain"


def cog_path(case_id: str, issue: dt.date, root: Path | None = None) -> Path:
    return out_dir(case_id, root) / f"{case_id}_gfs_{issue:%Y%m%d}.tif"


# --------------------------------------------------------------------------- #
# run selection + day buckets (pure ee-expression builders — offline testable)
# --------------------------------------------------------------------------- #
def _distinct_times(col, ee, prop: str = "creation_time") -> list[int]:
    vals = ee.List(col.aggregate_array(prop)).distinct().getInfo()
    return sorted(int(v) for v in vals)


def select_run(col, ee, issue: dt.date) -> int:
    """creation_time (ms) of the replay run for issue day D.

    Prefer the 00Z cycle (creation_time == D 00:00Z); fall back to the run
    with the latest creation_time <= D end-of-day.
    """
    day = col.filterDate(issue.isoformat(),
                         (issue + dt.timedelta(days=1)).isoformat())
    times = _distinct_times(day, ee)
    if not times:
        raise RuntimeError(f"no GFS run in {COLLECTION} on {issue}")
    zero_z = _ms(issue)
    return zero_z if zero_z in times else times[-1]


def day_sum(col, ee, aoi, run_ms: int, day: dt.date):
    """Daily mm image for [day, day+1): sum of the trailing-accumulation
    band over 6-hourly bucket images valid in (day, day+1].

    The band resets every 6 forecast hours — fh 6,12,18,… each carry the
    accumulation since the last 6-h boundary, while intermediate hours
    carry partial 1-6h amounts. Summing every hourly image double-counts
    (~3.3x measured), so only forecast_hours % 6 == 0 are kept. That also
    works past fh 120 where outputs go 3-hourly (multiples of 6 still land
    on bucket ends). `lo` must sit on a bucket edge: the day bounds are
    00Z..00Z and GFS cycles are 00/06/12/18Z, so (day_ms - run_ms) is
    always a multiple of 6 for a valid run pick."""
    lo = (_ms(day) - run_ms) / HOUR_MS
    hi = lo + 24
    if lo % 6:
        raise ValueError(
            f"day edge not on a 6h forecast bucket for run_ms={run_ms} "
            f"(lo={lo}) — pick a 00/06/12/18Z run")
    fhs = [f for f in range(int(lo) + 1, int(hi) + 1) if f % 6 == 0]
    if not fhs:
        raise ValueError(f"no 6h forecast bucket in ({lo}, {hi}]")
    return (col.filter(ee.Filter.eq("creation_time", run_ms))
               .filter(ee.Filter.gt("forecast_hours", 0))
               .filter(ee.Filter.inList("forecast_hours", fhs))
               .select(BAND).sum().clip(aoi))


def forecast_image(col, ee, aoi, run_ms: int, issue: dt.date,
                   days=DEFAULT_DAYS):
    """3-band float image: band d<k> = forecast daily mm for [D+k-1, D+k)."""
    bands = [day_sum(col, ee, aoi, run_ms, issue + dt.timedelta(days=k - 1))
             for k in days]
    return ee.Image.cat(bands).rename([f"d{k}" for k in days]).toFloat()


# --------------------------------------------------------------------------- #
# download -> COG
# --------------------------------------------------------------------------- #
def _download(url: str, limit: int = MAX_DOWNLOAD_BYTES) -> bytes | None:
    from tp.fallback import _download as fetch

    return fetch(url, limit=limit)


def _download_url(image, aoi, stem: str) -> str:
    return image.getDownloadURL({
        "name": stem, "region": aoi, "scale": SCALE_M,
        "crs": "EPSG:4326", "format": "GEO_TIFF", "filePerBand": False})


def write_cog(path: Path, data: bytes, band_names: list[str],
              label: str) -> Path:
    """Persist a GEE GeoTIFF payload as a north-up EPSG:4326 float32 COG."""
    import rasterio

    if zipfile.is_zipfile(io.BytesIO(data)):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            member = next(n for n in z.namelist() if n.endswith(".tif"))
            data = z.read(member)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.MemoryFile(data) as mem, mem.open() as src:
        arr = src.read().astype(np.float32)
        profile = dict(src.profile)
    profile.update(driver="COG", dtype="float32", crs="EPSG:4326",
                   nodata=np.nan)
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(arr)
        for i, name in enumerate(band_names, 1):
            dst.set_band_description(i, name)
        dst.update_tags(source=SOURCE, label=label)
    return path


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #
def list_runs(case: str, start: dt.date, end: dt.date, *,
              ee_module=None, col=None) -> list[str]:
    """ISO creation_times of every GFS run on days start..end (4/day).

    For lead-analysis's per-issue-day TPI loop. `case` scopes the collection
    to days that matter for the replay; run availability is global so the
    case only documents intent.
    """
    ee = ee_module or _ee()
    if col is None:
        _init_ee()
        col = ee.ImageCollection(COLLECTION)
    window = col.filterDate(start.isoformat(),
                            (end + dt.timedelta(days=1)).isoformat())
    return [_iso(ms) for ms in _distinct_times(window, ee)]


def fetch(case: str, issue: dt.date, days=DEFAULT_DAYS, *,
          out_root: Path | None = None, ee_module=None, col=None,
          fetch=None, aoi=None) -> dict:
    """Replay one issue day: select the run, sum day buckets, write COG+json.

    Returns the sidecar manifest dict.
    """
    ee = ee_module or _ee()
    case_id, bbox = _case_cfg(case)
    if col is None:
        _init_ee()
        col = ee.ImageCollection(COLLECTION)
    aoi = aoi if aoi is not None else ee.Geometry.Rectangle(bbox)
    run_ms = select_run(col, ee, issue)
    image = forecast_image(col, ee, aoi, run_ms, issue, days)
    names = [f"d{k}" for k in days]
    label = f"forecast: NOAA GFS (issued {issue.isoformat()})"
    path = cog_path(case_id, issue, out_root)
    if fetch is None:
        fetch = _download
    data = fetch(_download_url(image, aoi, path.stem),
                 limit=MAX_DOWNLOAD_BYTES)
    if data is None:
        raise RuntimeError(
            f"GFS download exceeds {MAX_DOWNLOAD_BYTES}-byte limit: {path}")
    write_cog(path, data, names, label)
    manifest = {
        "case": case_id,
        "issue_date": issue.isoformat(),
        "creation_time": _iso(run_ms),
        "days": {f"d{k}": [
            (issue + dt.timedelta(days=k - 1)).isoformat(),
            (issue + dt.timedelta(days=k)).isoformat()] for k in days},
        "bands": names,
        "label": label,
        "source": SOURCE,
        "collection": COLLECTION,
        "band": BAND,
        "scale_m": SCALE_M,
        "native_resolution": "~25 km (0.25 deg); day bucket = sum of "
                             "trailing 1-6h accumulations valid in the day",
        "cog": str(path),
    }
    sidecar = path.with_suffix(".json")
    sidecar.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def plan(case: str, issue: dt.date, days=DEFAULT_DAYS, *,
         out_root: Path | None = None) -> dict:
    """The --dry-run document: projected paths/filters, no ee, no network."""
    case_id, bbox = _case_cfg(case)
    path = cog_path(case_id, issue, out_root)
    return {
        "input": "gfs",
        "collection": COLLECTION,
        "band": BAND,
        "case": case_id,
        "aoi": bbox,
        "issue_date": issue.isoformat(),
        "run_selection": "00Z cycle on issue day; else latest "
                         "creation_time <= issue-day end-of-day",
        "days": {f"d{k}": [
            (issue + dt.timedelta(days=k - 1)).isoformat(),
            (issue + dt.timedelta(days=k)).isoformat()] for k in days},
        "scale_m": SCALE_M,
        "cog": str(path),
        "sidecar": str(path.with_suffix(".json")),
        "label": f"forecast: NOAA GFS (issued {issue.isoformat()})",
    }


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="python -m pipeline.fetch.gfs",
        description=__doc__.splitlines()[0])
    p.add_argument("--case", required=True,
                   help="case id (rs_2024) or event id (rs-2024-05)")
    p.add_argument("--issue", type=dt.date.fromisoformat, default=None,
                   help="issue date YYYY-MM-DD (the forecast's day D)")
    p.add_argument("--days", default="1,2,3",
                   help="comma-separated forecast day offsets (default 1,2,3)")
    p.add_argument("--out-dir", type=Path, default=None,
                   help="data root override (default: repo data/<case>/)")
    p.add_argument("--list-runs", action="store_true",
                   help="print the GFS run creation_times in --start..--end")
    p.add_argument("--start", type=dt.date.fromisoformat, default=None)
    p.add_argument("--end", type=dt.date.fromisoformat, default=None)
    p.add_argument("--dry-run", action="store_true",
                   help="print the plan JSON; no ee, network or writes")
    return p.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)
    days = tuple(int(d) for d in args.days.split(","))
    if args.list_runs:
        if args.start is None or args.end is None:
            raise SystemExit("--list-runs needs --start and --end")
        for ts in list_runs(args.case, args.start, args.end):
            print(ts)
        return
    if args.issue is None:
        raise SystemExit("--issue YYYY-MM-DD is required")
    if args.dry_run:
        print(json.dumps(plan(args.case, args.issue, days,
                              out_root=args.out_dir), indent=2))
        return
    manifest = fetch(args.case, args.issue, days, out_root=args.out_dir)
    print(f"gfs: {manifest['creation_time']} -> {manifest['cog']}")


if __name__ == "__main__":
    main()
