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

import numpy as np


def _edl_login():
    os.environ.setdefault("EARTHDATA_USERNAME", os.environ.get("EDL_USER", ""))
    os.environ.setdefault("EARTHDATA_PASSWORD", os.environ.get("EDL_PASS", ""))
    import earthaccess

    return earthaccess.login(strategy="environment")


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
    ds = xr.open_dataset(f, group="Geophysical_Data", engine="h5netcdf")
    sm = ds["sm_rootzone_wetness"].load()
    try:
        lats, lons = ds["cell_lat"].values, ds["cell_lon"].values
    except KeyError:
        geo = xr.open_dataset(f, group="Metadata", engine="h5netcdf")
        lats, lons = geo["cell_lat"].values, geo["cell_lon"].values
    w, s, e, n = bounds
    m = (lats >= s - 0.5) & (lats <= n + 0.5) & (lons >= w - 0.5) & (lons <= e + 0.5)
    if m.ndim == 2:
        rows, cols = np.where(m.any(axis=1)), np.where(m.any(axis=0))
        sl = np.s_[rows.min():rows.max() + 1, cols.min():cols.max() + 1]
        return lats[sl], lons[sl], sm.values[sl], str(latest["meta"]["native-id"])
    return lats[m], lons[m], sm.values[m], str(latest["meta"]["native-id"])


def fetch_geosfp_daily(bounds, issued: dt.date | None = None):
    """GEOS-FP forecast daily precipitation (mm/day) for D+1..D+3.

    Source: GMAO OPeNDAP '.latest' forecast aggregation, tavg1_2d_flx_Nx
    collection, PRECTOT (kg m-2 s-1 = mm/s) summed to daily totals.
    Returns (daily_xarray(time,lat,lon) with 3 steps, issued_iso).
    """
    import xarray as xr

    url = ("https://opendap.nccs.nasa.gov/dods/GEOS-5/fp/0.25_deg/fcast/"
           "tavg1_2d_flx_Nx.latest")
    ds = xr.open_dataset(url)
    prec = _subset(ds["PRECTOT"], bounds) * 86400.0
    daily = prec.resample(time="1D").sum()
    today = np.datetime64((issued or dt.date.today()).isoformat())
    daily = daily.sel(time=daily.time > today).isel(time=slice(0, 3))
    if daily.sizes.get("time", 0) < 3:
        raise RuntimeError("GEOS-FP forecast has fewer than 3 daily steps")
    return daily.compute(), f"{today}T00:00:00Z"


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
