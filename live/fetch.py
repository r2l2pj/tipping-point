"""Live data fetchers for Modo Agora — all credentials come from env at runtime.

IMERG Early Run (GPM_3IMERGHHE) and SMAP L4 (SPL4SMGP) via earthaccess
(EDL_USER/EDL_PASS); GEOS-FP forecast via the GMAO OPeNDAP portal (no auth);
NISAR pass listing via asf_search (metadata only, no auth needed).

Nothing here runs at import time — heavy deps are imported inside functions so
`run_now.py --help` and the test suite work without credentials or network.
"""
from __future__ import annotations

import datetime as dt
import os
import re
import sys

import numpy as np


def _ensure_gdal_headers():
    """Hand GDAL /vsicurl the same bearer token earthaccess is using."""
    token = os.environ.get("EARTHDATA_TOKEN")
    if token:
        os.environ.setdefault("GDAL_HTTP_HEADERS",
                              f"Authorization: Bearer {token}")


def _edl_login():
    if not os.environ.get("EARTHDATA_TOKEN"):
        os.environ.setdefault("EARTHDATA_USERNAME", os.environ.get("EDL_USER", ""))
        os.environ.setdefault("EARTHDATA_PASSWORD", os.environ.get("EDL_PASS", ""))
    import earthaccess

    auth = earthaccess.login(strategy="environment")
    _ensure_gdal_headers()
    return auth


def check_earthdata_token():
    """Workflow pre-step: exit loudly if EARTHDATA_TOKEN is expired/rejected."""
    try:
        import earthaccess

        earthaccess.login(strategy="environment")
        earthaccess.search_data(
            short_name="GPM_3IMERGHHE", version="07", count=1)
    except Exception:  # noqa: BLE001 — workflow gate must fail on any error
        sys.exit(
            "EARTHDATA_TOKEN expired or rejected — regenerate at "
            "https://urs.earthdata.nasa.gov/user_tokens and update the "
            "GitHub secret EARTHDATA_TOKEN")


def _subset(da, bounds):
    w, s, e, n = bounds
    lat_name = next(d for d in da.dims if "lat" in d.lower())
    lon_name = next(d for d in da.dims if "lon" in d.lower())
    lats = da[lat_name]
    lat_sl = slice(s, n) if lats[0] < lats[-1] else slice(n, s)
    return da.sel({lat_name: lat_sl, lon_name: slice(w, e)})


def fetch_imerg_daily(bounds, end: dt.date, days: int = 7):
    """IMERG Early daily accumulations (mm/day) for `days` ending at `end`.

    Returns (daily_xarray(time,lat,lon), last_product_iso). 3IMERGHHE files are
    half-hourly mm/h over a 'Grid' HDF5 group; sum * 0.5 gives mm.
    """
    import earthaccess
    import xarray as xr

    _edl_login()
    results = earthaccess.search_data(
        short_name="GPM_3IMERGHHE", version="07",
        temporal=(str(end - dt.timedelta(days=days)), str(end + dt.timedelta(days=1))),
        bounding_box=tuple(bounds),
    )
    if not results:
        raise RuntimeError("no GPM_3IMERGHHE granules in window")
    files = earthaccess.open(results)
    try:
        ds = xr.open_mfdataset(files, group="Grid", combine="by_coords", engine="h5netcdf")
    except (OSError, KeyError, ValueError):
        ds = xr.open_mfdataset(files, combine="by_coords", engine="h5netcdf")
    var = "precipitation" if "precipitation" in ds else "precipitationCal"
    daily = (_subset(ds[var], bounds) * 0.5).resample(time="1D").sum()
    last_iso = np.datetime_as_string(daily.time.values[-1], unit="h")
    return daily.compute(), f"{last_iso}Z"


def _download_grid_geotiff(url: str):
    """Fetch one EE getDownloadURL GeoTIFF -> (arr2d, lats, lons).

    Masked cells become NaN; lat/lon are cell-centre coords read off the
    GeoTIFF transform so the result feeds regrid_da like the other grids.
    """
    import io
    import zipfile

    import rasterio
    import requests

    data = requests.get(url, timeout=600).content
    if data[:4] == b"PK\x03\x04":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            data = z.read(next(n for n in z.namelist() if n.endswith(".tif")))
    with rasterio.open(io.BytesIO(data)) as src:
        arr = np.asarray(src.read(1, masked=True).filled(np.nan), dtype=np.float64)
        t = src.transform
        lons = t.c + (np.arange(arr.shape[1]) + 0.5) * t.a
        lats = t.f + (np.arange(arr.shape[0]) + 0.5) * t.e
    return arr, lats, lons


def fetch_imerg_gee_standin(bounds, days: int = 7):
    """GEE NASA/GPM_L3/IMERG_V07 stand-in for fetch_imerg_daily.

    Exists ONLY while the GES DISC token account has not accepted the
    GPM_3IMERGHHE EULA (fetch_imerg_daily gets 403). IMERG_V07 is the
    ~1.5-day-latency half-hourly collection — 'precipitation' is mm/h per
    30-min step, so the daily .sum() is multiplied by 0.5 (same factor as
    build_static._climatology.accum) to get mm/day.

    The 0.1 deg (~10 km) single-region getDownloadURL keeps each request
    far below the EE 'user memory limit' — the local TPI grid regrids it
    anyway. Returns (daily_xarray(time,lat,lon), product_id); product_id
    carries a 'gee:' prefix so the stand-in is visible in every artifact.
    """
    import xarray as xr

    from gee.common import ee_init
    ee_init()  # returns the project string; the ee module itself is used below
    import ee

    col = ee.ImageCollection("NASA/GPM_L3/IMERG_V07").select("precipitation")
    latest_ms = col.aggregate_max("system:time_start").getInfo()
    latest_dt = dt.datetime.fromtimestamp(latest_ms / 1000, dt.UTC)
    last_day = latest_dt.date()
    region = ee.Geometry.Rectangle(list(bounds))

    slabs, lats, lons, dates = [], None, None, []
    for i in range(days):
        day = last_day - dt.timedelta(days=days - 1 - i)
        img = (col.filterDate(day.isoformat(),
                              (day + dt.timedelta(days=1)).isoformat())
               .sum().multiply(0.5))
        url = img.getDownloadURL({
            "scale": 10_000, "format": "GEO_TIFF", "crs": "EPSG:4326",
            "region": region})
        arr, lats, lons = _download_grid_geotiff(url)
        slabs.append(arr)
        dates.append(np.datetime64(day.isoformat()))

    daily = xr.DataArray(
        np.stack(slabs), dims=("time", "lat", "lon"),
        coords={"time": np.array(dates), "lat": lats, "lon": lons},
        name="precip",
    )
    pid = (f"gee:NASA/GPM_L3/IMERG_V07 "
           f"latest={latest_dt.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    return daily, pid


def fetch_smap_l4(bounds, days_back: int = 10):
    """Most recent SMAP L4 sm_rootzone_wetness as scattered lat/lon points.

    Returns (lats2d, lons2d, values2d, product_date_iso). SPL4SMGP is on the
    9 km EASE-2 grid (2-D cell_lat/cell_lon), so callers regrid with
    grid.regrid_points rather than xarray.interp.
    """
    import earthaccess
    import xarray as xr

    _edl_login()
    end = dt.date.today()
    results = earthaccess.search_data(
        short_name="SPL4SMGP", version="008",
        temporal=(str(end - dt.timedelta(days=days_back)), str(end)),
        bounding_box=tuple(bounds),
    )
    if not results:
        raise RuntimeError(f"no SPL4SMGP granules in the last {days_back} days")
    latest = results[-1]
    f = earthaccess.open([latest])[0]
    ds = xr.open_dataset(f, group="Geophysical_Data", engine="h5netcdf", phony_dims="sort")
    sm = ds["sm_rootzone_wetness"].load()
    geo = xr.open_dataset(f, engine="h5netcdf", phony_dims="sort")
    lats, lons = geo["cell_lat"].values, geo["cell_lon"].values
    w, s, e, n = bounds
    m = (lats >= s - 0.5) & (lats <= n + 0.5) & (lons >= w - 0.5) & (lons <= e + 0.5)
    if m.ndim == 2:
        rows, cols = np.flatnonzero(m.any(axis=1)), np.flatnonzero(m.any(axis=0))
        sl = np.s_[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]
        return lats[sl], lons[sl], sm.values[sl], str(latest["meta"]["native-id"])
    return lats[m], lons[m], sm.values[m], str(latest["meta"]["native-id"])


_GEOSFP_BASE = ("https://opendap.nccs.nasa.gov/dods/GEOS-5/fp/0.25_deg/fcast/"
                "tavg1_2d_flx_Nx")
_GEOSFP_RUN_RE = re.compile(r"tavg1_2d_flx_Nx\.(\d{8}_\d{2})")
_GEOSFP_FILL = 1.0e15          # _FillValue/missing_value for PRECTOT
_GEOSFP_TIMEOUT = 60
_GEOSFP_TRIES = 8              # candidate runs, newest first


def _gds_get(url: str) -> str:
    import time

    import requests

    tries = 3
    for i in range(tries):
        try:
            r = requests.get(url, timeout=_GEOSFP_TIMEOUT)
            r.raise_for_status()
            return r.text
        except (requests.exceptions.ReadTimeout,
                requests.exceptions.ConnectionError):
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))


def _geosfp_runs(html: str) -> list[str]:
    """Dataset names in the collection dir listing, newest first."""
    return [f"tavg1_2d_flx_Nx.{t}"
            for t in sorted(set(_GEOSFP_RUN_RE.findall(html)), reverse=True)]


def _gds_parse_vectors(text: str) -> dict[str, np.ndarray]:
    """Parse a GDS .ascii 1-D response: 'name, [n]' header then CSV floats."""
    out: dict[str, np.ndarray] = {}
    name: str | None = None
    vals: list[float] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"([A-Za-z_]\w*),\s*\[\d+\]\s*$", line)
        if m:
            if name is not None:
                out[name] = np.array(vals, dtype=np.float64)
            name, vals = m.group(1), []
        elif name is not None:
            vals += [float(x) for x in line.split(",") if x.strip()]
    if name is not None:
        out[name] = np.array(vals, dtype=np.float64)
    return out


def _gds_parse_grid(text: str) -> np.ndarray:
    """Parse a GDS .ascii 3-D response: 'name, [t][la][lo]' header then
    '[t][la], v, v, ...' rows (one row of nlon values per time/lat pair)."""
    lines = [ln for ln in (line.strip() for line in text.splitlines()) if ln]
    m = re.match(r"[A-Za-z_]\w*,\s*\[(\d+)\]\[(\d+)\]\[(\d+)\]", lines[0])
    if not m:
        raise ValueError(f"unexpected GDS .ascii header: {lines[0]!r}")
    shape = tuple(int(g) for g in m.groups())
    arr = np.full(shape, np.nan)
    for line in lines[1:]:
        rm = re.match(r"\[(\d+)\]\[(\d+)\]\s*,\s*(.*)", line)
        if rm:
            row = [float(x) for x in rm.group(3).split(",") if x.strip()]
            arr[int(rm.group(1)), int(rm.group(2)), : len(row)] = row
    return arr


def _gds_times(vals: np.ndarray) -> list[dt.datetime]:
    """Decode GDS 'days since 1-1-1 00:00:0.0' stamps — stored as
    ordinal+1+fraction (verified: run tavg1_2d_flx_Nx.20261113_00 starts
    2026-11-13 00:30, i.e. int(v)-1 is the proleptic ordinal)."""
    out = []
    for v in vals:
        day, frac = divmod(float(v), 1.0)
        base = dt.datetime.combine(dt.date.fromordinal(int(day) - 1), dt.time())
        out.append(base + dt.timedelta(seconds=round(frac * 86400)))
    return out


def fetch_geosfp_daily(bounds, issued: dt.date | None = None):
    """GEOS-FP forecast daily precipitation (mm/day) for D+1..D+3.

    Source: GMAO GrADS-OPeNDAP server, tavg1_2d_flx_Nx fcast collection (no
    auth). No DAP backend is installed, so this reads the '.ascii' interface:
    pick the NEWEST run whose coverage reaches the END of `issued + 3 days`
    (steps stamp :30, so a last step >= 23:00 means a complete D+3 — a run
    ending mid-D+3 would understate that day's total), pull the PRECTOT
    (kg m-2 s-1 = mm/s) subset, and sum the hourly steps per UTC day
    (* 3600 -> mm/day; PRECTOT is a time-averaged flux, NOT mm/h).
    Returns (daily_xarray(time,lat,lon) with 3 steps, issued_iso).
    """
    import requests
    import xarray as xr

    issued = issued or dt.date.today()
    need_by = issued + dt.timedelta(days=3)
    full_d3 = dt.datetime.combine(need_by, dt.time(23))

    runs = _geosfp_runs(_gds_get(f"{_GEOSFP_BASE}/"))
    if not runs:
        raise RuntimeError("no GEOS-FP runs in the collection listing")

    run, times, seen = None, [], []
    for cand in runs[:_GEOSFP_TRIES]:
        try:
            vec = _gds_parse_vectors(
                _gds_get(f"{_GEOSFP_BASE}/{cand}.ascii?prectot.time"))
        except requests.exceptions.RequestException as exc:
            seen.append(f"{cand} listing failed: {exc}")
            continue
        ts = _gds_times(vec["time"])
        seen.append(f"{cand} covers {ts[0]}..{ts[-1]}" if ts
                    else f"{cand} has no time axis")
        if ts and ts[-1] >= full_d3:
            run, times = cand, ts
            break
    if run is None:
        raise RuntimeError(
            f"no GEOS-FP run reaches end of {need_by} ({'; '.join(seen)})")

    vec = _gds_parse_vectors(
        _gds_get(f"{_GEOSFP_BASE}/{run}.ascii?prectot.lat,prectot.lon"))
    lats, lons = vec["lat"], vec["lon"]
    w, s, e, n = bounds
    la0 = max(0, int(np.searchsorted(lats, s)) - 1)
    la1 = min(len(lats) - 1, int(np.searchsorted(lats, n, "right")))
    lo0 = max(0, int(np.searchsorted(lons, w)) - 1)
    lo1 = min(len(lons) - 1, int(np.searchsorted(lons, e, "right")))

    t0 = next(i for i, t in enumerate(times) if t.date() > issued)
    t1 = max(i for i, t in enumerate(times) if t.date() <= need_by)
    ce = (f"prectot.prectot[{t0}:1:{t1}]"
          f"[{la0}:1:{la1}][{lo0}:1:{lo1}]")
    arr = _gds_parse_grid(_gds_get(f"{_GEOSFP_BASE}/{run}.ascii?{ce}"))
    arr = arr.astype(np.float64)
    arr[arr >= _GEOSFP_FILL] = np.nan

    sub_dates = [t.date() for t in times[t0:t1 + 1]]
    days = sorted({d for d in sub_dates if d > issued})[:3]
    if len(days) < 3:
        raise RuntimeError(
            f"GEOS-FP run {run} has fewer than 3 daily steps after {issued}")
    slabs = []
    for d in days:
        sel = arr[[i for i, dd in enumerate(sub_dates) if dd == d]]
        day = np.nansum(sel, axis=0) * 3600.0
        day[np.all(np.isnan(sel), axis=0)] = np.nan
        slabs.append(day)

    daily = xr.DataArray(
        np.stack(slabs), dims=("time", "lat", "lon"),
        coords={"time": np.array([np.datetime64(d.isoformat()) for d in days]),
                "lat": lats[la0:la1 + 1], "lon": lons[lo0:lo1 + 1]},
        name="precip",
    )
    stamp = dt.datetime.strptime(run.rsplit(".", 1)[1], "%Y%m%d_%H")
    return daily, stamp.strftime("%Y-%m-%dT%H:00:00Z")


def nisar_recent_passes(bounds, days: int = 14) -> list[dict]:
    """List recent NISAR acquisitions over the AOI — metadata only."""
    import asf_search as asf
    from shapely.geometry import box

    wkt = box(*bounds).wkt
    end = dt.datetime.now(dt.UTC)
    results = asf.geo_search(
        intersectsWith=wkt, platform=asf.PLATFORM.NISAR,
        start=end - dt.timedelta(days=days), end=end,
    )
    return [
        {
            "granule": p.properties.get("fileID") or p.properties.get("sceneName"),
            "acquired": p.properties.get("startTime"),
            "path": p.properties.get("pathNumber"),
            "direction": p.properties.get("flightDirection"),
        }
        for p in results
    ]


def _nisar_passes_cli(args) -> int:
    """Best-effort pass-list writer — the now job must never fail here."""
    import json

    try:
        passes = nisar_recent_passes(tuple(args.bounds), days=args.days)
    except Exception as exc:  # noqa: BLE001 — offline/asf_search failure -> []
        print(f"WARNING: NISAR pass list failed ({exc}) — writing []",
              file=sys.stderr)
        passes = []
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(passes, ensure_ascii=False, indent=2) + "\n")
    print(f"nisar-passes: wrote {args.out} ({len(passes)} passes)")
    if args.manifest and args.manifest.exists():
        doc = json.loads(args.manifest.read_text())
        doc["nisar_passes"] = passes
        args.manifest.write_text(
            json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
        print(f"nisar-passes: updated nisar_passes in {args.manifest}")
    return 0


def main(argv=None) -> int:
    import argparse
    from pathlib import Path

    p = argparse.ArgumentParser(
        prog="live.fetch", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    np_ = sub.add_parser(
        "nisar-passes",
        help="write the recent NISAR pass list (never fails the job)")
    np_.add_argument("--bounds", type=float, nargs=4, required=True,
                     metavar=("W", "S", "E", "N"))
    np_.add_argument("--days", type=int, default=14)
    np_.add_argument("--out", type=Path, required=True,
                     help="JSON output path (e.g. data/now/nisar_passes.json)")
    np_.add_argument("--manifest", type=Path, default=None,
                     help="also update this manifest's nisar_passes key "
                          "(e.g. data/now/manifest.json)")
    args = p.parse_args(argv)
    if args.cmd == "nisar-passes":
        return _nisar_passes_cli(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
