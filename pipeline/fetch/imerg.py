"""IMERG Early precip -> daily + rolling-3-day COGs for Modo Agora.

Days older than RECENT_HALFHOURS come from the IMERG *Early* daily product
GPM_3IMERGDE v07 (CMR-verified: entry GPM_3IMERGDE_07, "GPM IMERG Early
Precipitation L3 1 day 0.1 degree V07") — one granule per day instead of 48.
The trailing RECENT_HALFHOURS window — plus any older day the daily product
has not posted yet — uses half-hourly GPM_3IMERGHHE v07 (the IMERG *Early*
half-hourly run, ~4 h latency; same short name as live/fetch.py. Late and
Final half-hourly are GPM_3IMERGHHL and GPM_3IMERGHH — not used here).
Granules are streamed — download, open, subset to --bounds, delete the HDF5
— with at most MAX_WORKERS files in flight, so peak disk use and open
datasets are bounded by concurrency, not the granule count. Increments
(mm/h * 0.5 half-hourly, day totals for the daily product) aggregate to
daily mm/day over the --days lookback, roll a 3-day accumulation, clip to
the now AOI and stage
each raster as an image asset in the GEE image collection
projects/<GEE_PROJECT>/assets/tipping-point/now/imerg_early.

    uv run python -m pipeline.fetch.imerg [--date YYYY-MM-DD] \
        [--bounds W S E N] [--days N] [--dry-run] [--no-download]

Auth split: the CMR search is anonymous; only the download step needs
Earthdata credentials — EARTHDATA_TOKEN, or EDL_USER/EDL_PASS mapped onto
earthaccess's EARTHDATA_USERNAME/PASSWORD env names.

Ruling L10: no GCS bucket and no bucket-free raster upload exists — there
is NO raster-ingest path. COGs stay local and the planned asset ids are
recorded in data/now/assets.json with status 'local-only' via
append_manifest from the ingest contract (unit/ingest), imported
best-effort below — --dry-run and --no-download work without it. Runs are
idempotent: asset ids already present in assets.json are skipped unless
--force.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

LEAF = "imerg_early"


def collection_path(case: str = "now") -> str:
    """GEE collection path for a case: tipping-point/<case>/imerg_early."""
    return f"tipping-point/{case}/{LEAF}"


COLLECTION = collection_path()
SHORT_NAME = "GPM_3IMERGHHE"
DAILY_SHORT_NAME = "GPM_3IMERGDE"        # IMERG Early daily, CMR-verified V07
VERSION = "07"
MAX_WORKERS = 4          # download/open/delete concurrency bound
RECENT_HALFHOURS = 48    # trailing hours served by half-hourly granules

REPO_ROOT = Path(__file__).resolve().parents[2]
EVENTS_DIR = REPO_ROOT / "events"
NOW_DIR = REPO_ROOT / "data" / "now"
DEFAULT_COG_DIR = NOW_DIR / "cog" / LEAF
ASSETS_JSON = NOW_DIR / "assets.json"
DEFAULT_DAYS = 7                        # same lookback as live/run_now.py


def now_bounds() -> tuple:
    """Operational now AOI (west, south, east, north).

    Canonical source: config.yaml `domains.now` via tp.config.now_bounds()
    (ruling L9). An explicit --bounds always overrides.
    """
    from tp.config import now_bounds as _cfg_now_bounds
    return tuple(_cfg_now_bounds())


# The ingest contract lands on unit/ingest; try the plausible homes so this
# module works whichever package it settles into. None of these existing is
# fine — --dry-run and --no-download never touch ingest.
_INGEST_CANDIDATES = (
    "tp.ingest",
    "tools.ingest",
    "pipeline.tp.ingest",
    "pipeline.tools.ingest",
    "fetch.ingest",
    "pipeline.fetch.ingest",
)


def _load_ingest():
    for name in _INGEST_CANDIDATES:
        try:
            mod = importlib.import_module(name)
        except ImportError:
            continue
        if callable(getattr(mod, "append_manifest", None)):
            return mod
    return None


try:
    _ing = _load_ingest()
    append_manifest = _ing.append_manifest if _ing else None
    ensure_collection = getattr(_ing, "ensure_collection", None) if _ing else None
except Exception:  # noqa: BLE001 — a broken ingest must never break fetch/dry-run
    append_manifest = ensure_collection = None


# --------------------------------------------------------------------------- #
# auth + earthaccess (lazy: importing this module needs no creds/network)
# --------------------------------------------------------------------------- #
def _edl_login():
    """Download-side Earthdata auth — call ONLY before earthaccess.download.

    CMR search is anonymous. EARTHDATA_TOKEN bearer first; then the
    EDL_USER/EDL_PASS pair — mapped onto EARTHDATA_USERNAME/PASSWORD only
    when BOTH are set (partial credentials are rejected, same rule as
    fetch/edl.py); then a ~/.netrc entry.
    """
    import earthaccess

    msg = (f"{SHORT_NAME} download needs EARTHDATA_TOKEN or the "
           "EDL_USER+EDL_PASS pair (the CMR search itself is anonymous)")
    try:
        if os.environ.get("EARTHDATA_TOKEN"):
            return earthaccess.login(strategy="environment")
        edl_user = os.environ.get("EDL_USER")
        edl_pass = os.environ.get("EDL_PASS")
        if edl_user and edl_pass:
            os.environ.setdefault("EARTHDATA_USERNAME", edl_user)
            os.environ.setdefault("EARTHDATA_PASSWORD", edl_pass)
        if (os.environ.get("EARTHDATA_USERNAME")
                and os.environ.get("EARTHDATA_PASSWORD")):
            return earthaccess.login(strategy="environment")
        if (Path.home() / ".netrc").exists():
            return earthaccess.login(strategy="netrc")
    except Exception as e:
        raise RuntimeError(msg) from e
    raise RuntimeError(msg)


def search_granules(bounds, end: dt.date, days: int = DEFAULT_DAYS) -> list:
    """Anonymous CMR search for IMERG Early granules in the lookback window."""
    import earthaccess

    return earthaccess.search_data(
        short_name=SHORT_NAME, version=VERSION,
        temporal=(str(end - dt.timedelta(days=days)),
                  str(end + dt.timedelta(days=1))),
        bounding_box=tuple(bounds),
    )


def search_granules_range(bounds, start: dt.date, end: dt.date,
                          short_name: str = SHORT_NAME) -> list:
    """Anonymous CMR search for every IMERG Early granule in start..end."""
    import earthaccess

    return earthaccess.search_data(
        short_name=short_name, version=VERSION,
        temporal=(str(start), str(end + dt.timedelta(days=1))),
        bounding_box=tuple(bounds),
    )


# --------------------------------------------------------------------------- #
# case-window hindcast (--case): bounds/window from events/<case>.json
# --------------------------------------------------------------------------- #
def load_event(case: str) -> dict | None:
    """events/<case>.json, tolerant of '-' vs '_' in the file name."""
    names = dict.fromkeys(
        (case, case.replace("-", "_"), case.replace("_", "-")))
    for name in names:
        p = EVENTS_DIR / f"{name}.json"
        if p.is_file():
            try:
                return json.loads(p.read_text())
            except ValueError:
                return None
    return None


def case_bounds(case: str) -> tuple | None:
    """AOI (w, s, e, n) from events/<case>.json: aoi.bbox then gee.aoi_bbox."""
    ev = load_event(case) or {}
    bbox = (ev.get("aoi") or {}).get("bbox") \
        or (ev.get("gee") or {}).get("aoi_bbox")
    return tuple(bbox) if bbox else None


def case_window(case: str) -> tuple | None:
    """(start, end) dates from events/<case>.json window, else gee.window."""
    ev = load_event(case) or {}
    for src in (ev.get("window"), (ev.get("gee") or {}).get("window")):
        if isinstance(src, dict) and src.get("start") and src.get("end"):
            return (dt.date.fromisoformat(src["start"]),
                    dt.date.fromisoformat(src["end"]))
    return None


# --------------------------------------------------------------------------- #
# granule start-time tags: per-granule asset ids are YYYYMMDDThhmm
# --------------------------------------------------------------------------- #
_IMERG_NAME_RE = re.compile(r"\.(\d{8})-S(\d{2})(\d{2})\d{2}")
_ISO_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})")


def _strings(obj, depth: int = 0):
    """Yield every string under dicts/lists, depth-capped."""
    if depth > 4 or obj is None:
        return
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v, depth + 1)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _strings(v, depth + 1)


def _granule_payload(g):
    """String-bearing view of a granule: dicts as-is, else common attrs."""
    if isinstance(g, dict):
        return g
    payload = {}
    for attr in ("umm", "meta", "producer_granule_id", "native_id"):
        try:
            payload[attr] = getattr(g, attr)
        except Exception:  # noqa: BLE001 — odd granule objects must not break
            payload[attr] = None
    try:
        payload["data_links"] = g.data_links()
    except Exception:  # noqa: BLE001
        payload["data_links"] = None
    return payload


def _granule_name(g) -> str | None:
    """Best-effort granule file name for the manifest 'source' field."""
    strings = list(_strings(_granule_payload(g)))
    for t in strings:
        if t.lower().endswith((".hdf5", ".h5", ".he5", ".nc")):
            return t
    return strings[0] if strings else None


def granule_start_tag(g) -> str:
    """Granule start time as YYYYMMDDThhmm (the per-granule asset id leaf).

    IMERG v07 filenames carry it as '.YYYYMMDD-SHHMMSS.'; ISO timestamps in
    the granule metadata are the fallback.
    """
    texts = list(_strings(_granule_payload(g)))
    for t in texts:
        m = _IMERG_NAME_RE.search(t)
        if m:
            return f"{m.group(1)}T{m.group(2)}{m.group(3)}"
    for t in texts:
        m = _ISO_RE.search(t)
        if m:
            return (f"{m.group(1)}{m.group(2)}{m.group(3)}"
                    f"T{m.group(4)}{m.group(5)}")
    raise ValueError("no granule start time found")


def download_granules(granules, dest_dir: Path) -> list[Path]:
    """Earthdata-authenticated download of granule HDF5s into dest_dir."""
    _edl_login()
    import earthaccess

    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    files = earthaccess.download(granules, str(dest_dir)) or []
    return [Path(f) for f in files]


def _subset(da, bounds):
    """xarray .sel() clip to (w, s, e, n); tolerant of descending lat axes."""
    w, s, e, n = bounds
    lat_name = next(d for d in da.dims if "lat" in d.lower())
    lon_name = next(d for d in da.dims if "lon" in d.lower())
    lats = da[lat_name]
    lat_sl = slice(s, n) if lats[0] < lats[-1] else slice(n, s)
    return da.sel({lat_name: lat_sl, lon_name: slice(w, e)})


def _decode_time(da):
    """Decode IMERG's 'seconds since 1980-01-06' julian time by hand — xarray
    cannot decode that calendar without cftime (decode_times=False)."""
    if "time" in da.dims and not np.issubdtype(
            da["time"].dtype, np.datetime64):
        units = str(da["time"].attrs.get("units", ""))
        # half-hourly: 'seconds since 1980-01-06'; daily: 'days since ...'
        m = re.search(r"(seconds|minutes|hours|days) since "
                      r"(\d{4}-\d{2}-\d{2})", units)
        unit = {"seconds": "s", "minutes": "m",
                "hours": "h", "days": "D"}[m.group(1)] if m else "s"
        epoch = np.datetime64(m.group(2) if m else "1980-01-06")
        vals = np.asarray(da["time"].values)
        da = da.assign_coords(
            time=epoch + vals.astype(f"timedelta64[{unit}]"))
    return da


def open_granule(path, bounds, daily=False):
    """One granule -> bounds-subset DataArray; the dataset is closed before
    returning, so the open-dataset count stays bounded by MAX_WORKERS.

    v07 keeps the precip fields under the 'Grid' HDF5 group; Early run
    names the rate field 'precipitation' (older files: 'precipitationCal').
    daily=True is for GPM_3IMERGDE granules: 'precipitation' there is the
    day's accumulated mm (a single time step), kept as a one-timestamp
    array so resample(time="1D").sum() treats it as the day total.
    """
    import xarray as xr

    try:
        ds = xr.open_dataset(path, group="Grid",
                             engine="h5netcdf", decode_times=False)
    except (OSError, KeyError, ValueError):
        ds = xr.open_dataset(path, engine="h5netcdf",
                             decode_times=False)
    try:
        var = "precipitation" if "precipitation" in ds else "precipitationCal"
        da = _subset(_decode_time(ds[var]), bounds)
        if daily:
            if "time" in da.dims:
                da = da.isel(time=slice(0, 1))
            else:
                m = _IMERG_NAME_RE.search(Path(getattr(
                    path, 'name', str(path))).name)
                ymd = m.group(1) if m else "19700101"
                da = da.expand_dims(
                    time=[np.datetime64(
                        f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:]}")])
        da = da.load()
        # Normalize float32 lat/lon to rounded coords so granule subsets
        # from different products compare exactly equal at concat time —
        # a join='outer' on mismatched coords union-joins the grids and
        # NaN-pads the result (was a >7 GB blowup on the runner).
        lat_name = next(d for d in da.dims if "lat" in d.lower())
        lon_name = next(d for d in da.dims if "lon" in d.lower())
        return da.assign_coords(
            {lat_name: np.round(da[lat_name].values.astype(np.float64), 4),
             lon_name: np.round(da[lon_name].values.astype(np.float64), 4)})
    finally:
        ds.close()


def open_halfhourly(paths, bounds):
    """Open IMERG HDF5s as one (time, lat, lon) mm/h DataArray over `bounds`.

    Files are opened one at a time (open_mfdataset needs a dask chunk
    manager, which is not a dep) and closed after subsetting — see
    open_granule for the per-file details.
    """
    import xarray as xr

    arrays = [open_granule(p, bounds) for p in paths]
    return arrays[0] if len(arrays) == 1 else xr.concat(
        arrays, dim="time", join="exact").sortby("time")


def _accumulate(increments):
    """Per-step mm increments -> (daily mm/day, 3-day rolling mm, steps/day).

    `steps` counts increments per day — 48 for a full half-hourly day, 1 for
    a day served by a single GPM_3IMERGDE granule.
    """
    daily = increments.resample(time="1D").sum()
    steps = increments.resample(time="1D").count().max(
        dim=[d for d in increments.dims if d != "time"])
    rain3 = daily.rolling(time=3, min_periods=3).sum()
    return daily, rain3, steps


def daily_and_3day(halfhourly):
    """mm/h half-hourly -> (daily mm/day, 3-day rolling mm), plus steps/day.

    IMERG rates are mm/h over 30-min windows, so daily = sum(rate) * 0.5.
    rain3[i] covers days i-2..i; the first two days carry NaN.
    """
    return _accumulate(halfhourly * 0.5)


def _daterange(start: dt.date, end: dt.date):
    d = start
    while d < end:
        yield d
        d += dt.timedelta(days=1)


def _stream_granules(granules, bounds, dest_dir, daily=False):
    """download -> open -> subset -> delete, <=MAX_WORKERS files in flight.

    Returns (granule, DataArray) pairs in granule order. Each raw HDF5 is
    deleted as soon as its subset is read, so peak disk use and open
    datasets are bounded by MAX_WORKERS, not the granule count.
    """
    if not granules:
        return []
    _edl_login()
    import earthaccess

    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    def work(g):
        # earthaccess.open streams the granule over HTTP range requests —
        # only the chunks covering the AOI are fetched, nothing hits disk
        out = []
        for f in earthaccess.open([g]) or []:
            out.append((g, open_granule(f, bounds, daily=daily)))
        return out

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        return [pair for part in ex.map(work, granules) for pair in part]


def fetch_increments(bounds, end: dt.date, days: int, work_dir: Path):
    """Streamed per-granule precip increments (mm) over the lookback window.

    Days older than RECENT_HALFHOURS come from the daily product
    GPM_3IMERGDE when CMR has coverage for them; the trailing window — and
    any older day the daily product missed — comes from half-hourly
    GPM_3IMERGHHE (rate * 0.5 mm per step).
    """
    window_start = end - dt.timedelta(days=days)
    hh_start = end - dt.timedelta(hours=RECENT_HALFHOURS)
    daily_gs = []
    if window_start < hh_start:
        daily_gs = search_granules_range(
            bounds, window_start, hh_start - dt.timedelta(days=1),
            short_name=DAILY_SHORT_NAME) or []
    covered = {granule_start_tag(g)[:8] for g in daily_gs}
    uncovered = [d for d in _daterange(window_start, hh_start)
                 if d.strftime("%Y%m%d") not in covered]
    hh_from = min(uncovered, default=hh_start)
    hh_gs = search_granules_range(bounds, hh_from, end) or []
    if uncovered:
        need = {d.strftime("%Y%m%d") for d in uncovered}
        recent = hh_start.strftime("%Y%m%d")
        hh_gs = [g for g in hh_gs
                 if granule_start_tag(g)[:8] >= recent
                 or granule_start_tag(g)[:8] in need]
    if not daily_gs and not hh_gs:
        raise RuntimeError(
            f"no {SHORT_NAME}/{DAILY_SHORT_NAME} granules in window "
            f"ending {end}")
    raw = Path(work_dir) / "raw"
    incs = [da * 0.5 for _, da in _stream_granules(hh_gs, bounds, raw)]
    incs += [da for _, da in _stream_granules(
        daily_gs, bounds, raw, daily=True)]
    if not incs:
        raise RuntimeError("earthaccess.download returned no files")
    return incs


def fetch_daily(bounds, end: dt.date, days: int, work_dir: Path):
    """Search -> stream -> aggregate. Returns (daily, rain3, steps)."""
    import xarray as xr

    incs = fetch_increments(bounds, end, days, work_dir)
    inc = incs[0] if len(incs) == 1 else xr.concat(
        incs, dim="time", join="exact").sortby("time")
    return _accumulate(inc)


# --------------------------------------------------------------------------- #
# COG + asset plumbing
# --------------------------------------------------------------------------- #
def write_cog(path: Path, values, lats, lons) -> Path:
    """Write a north-up EPSG:4326 float32 COG (tiled deflate GTiff fallback).

    `values` may be 2-D (one band) or 3-D (band, lat, lon). NaN is written as
    nodata — same convention as live/render.cog_bytes.
    """
    import rasterio
    from rasterio.crs import CRS
    from rasterio.transform import from_origin

    arr = np.asarray(values, dtype=np.float32)
    lats = np.asarray(lats, dtype=np.float64)
    lons = np.asarray(lons, dtype=np.float64)
    if arr.ndim == 2:
        arr = arr[None]
    if lats[0] < lats[-1]:                 # south-up source -> north-up file
        arr, lats = arr[:, ::-1, :], lats[::-1]
    yres = float(abs(np.median(np.diff(lats))))
    xres = float(abs(np.median(np.diff(lons))))
    profile = {
        "driver": "COG", "height": arr.shape[1], "width": arr.shape[2],
        "count": arr.shape[0], "dtype": "float32",
        "crs": CRS.from_user_input("EPSG:4326"),
        "transform": from_origin(float(lons[0] - xres / 2),
                                 float(lats[0] + yres / 2), xres, yres),
        "nodata": np.nan,
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with rasterio.Env() as env:
            cog_ok = "COG" in env.drivers()
    except Exception:  # noqa: BLE001 — fake rasterio in tests has no Env
        cog_ok = False
    if not cog_ok:
        profile.update(driver="GTiff", tiled=True, compress="deflate")
    with rasterio.open(path, "w", **profile) as dst:
        for i in range(arr.shape[0]):
            dst.write(arr[i], i + 1)
    return path


def asset_id(project: str, tag: str, case: str = "now") -> str:
    return f"projects/{project}/assets/{collection_path(case)}/{tag}"


def manifest_asset_ids(path: Path = ASSETS_JSON) -> set[str]:
    """Asset ids already recorded by append_manifest — schema-tolerant scan.

    assets.json belongs to unit/ingest; rather than assume its shape, walk
    the JSON and collect every string stored under an id-ish key.
    """
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return set()
    ids: set[str] = set()
    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for k, v in node.items():
                if k in ("asset_id", "asset", "id") and isinstance(v, str):
                    ids.add(v)
                stack.append(v)
        elif isinstance(node, list):
            stack.extend(node)
    return ids


def scan_cogs(cog_dir: Path) -> list[dict]:
    """Rebuild the asset list from <LEAF>_<tag>.tif files (--no-download)."""
    out = []
    for p in sorted(Path(cog_dir).glob(f"{LEAF}_*.tif")):
        tag = p.stem[len(LEAF) + 1:]
        out.append({"tag": tag, "cog": p,
                    "product": ("3day" if tag.endswith("_3day")
                                else "halfhourly" if "T" in tag else "daily")})
    return out


def stage_and_ingest(assets: list[dict], *, project: str,
                     force: bool = False, assets_json: Path = ASSETS_JSON,
                     ingest: bool = True, case: str = "now") -> list[dict]:
    """Record each COG's planned asset id in assets.json; idempotent.

    L10: no raster-ingest path exists — with ingest=True the assets are
    marked 'local-only' (a one-line warning is printed), with ingest=False
    they stay 'planned'.
    """
    if ingest:
        print(f"warning: raster ingest is not supported without a GCS "
              f"bucket (L10) — {LEAF} assets stay local-only",
              file=sys.stderr)
    done = manifest_asset_ids(assets_json)
    entries = []
    for a in assets:
        aid = asset_id(project, a["tag"], case)
        a["asset_id"] = aid
        if not force and aid in done:
            a["status"] = "skipped_existing"
            continue
        if not Path(a["cog"]).exists():
            a["status"] = "missing_cog"
            continue
        if not ingest:
            a["status"] = "planned"
            continue
        entry = {
            "asset_id": aid,
            "collection": f"projects/{project}/assets/{collection_path(case)}",
            "kind": "image",
            "input": LEAF,
            "tag": a["tag"],
            "date": a.get("date"),
            "product": a.get("product"),
            "cog": str(a["cog"]),
            "source": a.get("source"),
            "status": "local-only",
            "ingested_at": dt.datetime.now(dt.UTC).isoformat()
            .replace("+00:00", "Z"),
        }
        if append_manifest is not None:
            append_manifest(case, entry)
        a["status"] = "local-only"
        entries.append(entry)
    return entries


def _resolve_project(project: str | None) -> str:
    p = project or os.environ.get("GEE_PROJECT")
    if not p:
        raise RuntimeError("set GEE_PROJECT or pass --project")
    return p


def _time_label(da, i: int) -> str:
    return np.datetime_as_string(da.time.values[i], unit="D")


def run(date: dt.date, bounds=None, days: int = DEFAULT_DAYS, *,
        out_dir: Path, project=None, download: bool = True,
        force: bool = False, assets_json: Path = ASSETS_JSON,
        ingest: bool = True) -> list[dict]:
    """Fetch + aggregate + write + record; returns the asset list+status."""
    bounds = tuple(bounds) if bounds is not None else now_bounds()
    project = _resolve_project(project)
    assets: list[dict] = []
    if download:
        daily, rain3, steps = fetch_daily(bounds, date, days, Path(out_dir))
        lat_name = next(d for d in daily.dims if "lat" in d.lower())
        lon_name = next(d for d in daily.dims if "lon" in d.lower())
        lats, lons = daily[lat_name].values, daily[lon_name].values
        for i in range(daily.sizes["time"]):
            iso = _time_label(daily, i)
            ymd = iso.replace("-", "")
            cog = Path(out_dir) / f"{LEAF}_{ymd}.tif"
            write_cog(cog, daily.isel(time=i).values, lats, lons)
            steps_i = steps.values[i]
            meta = {"hh_steps": int(steps_i) if np.isfinite(steps_i)
                    else 0}
            assets.append({"tag": ymd, "date": iso, "cog": cog,
                           "product": "daily", "source": meta})
            if i >= 2:
                cog3 = Path(out_dir) / f"{LEAF}_{ymd}_3day.tif"
                write_cog(cog3, rain3.isel(time=i).values, lats, lons)
                assets.append({"tag": f"{ymd}_3day", "date": iso, "cog": cog3,
                               "product": "3day", "source": meta})
    else:
        assets = scan_cogs(out_dir)
    stage_and_ingest(assets, project=project, force=force,
                     assets_json=assets_json, ingest=ingest)
    return assets


def run_case(case: str, start: dt.date, end: dt.date, bounds=None, *,
             out_dir: Path, project=None, download: bool = True,
             force: bool = False, assets_json: Path = ASSETS_JSON,
             ingest: bool = True) -> list[dict]:
    """Case-window hindcast: one COG + one image asset per half-hourly
    granule under projects/<proj>/assets/tipping-point/<case>/imerg_early.

    Bounds come from events/<case>.json unless given; the parent
    IMAGE_COLLECTION container is created once via ensure_collection before
    the children are recorded.
    """
    bounds = tuple(bounds) if bounds is not None else case_bounds(case)
    if bounds is None:
        raise RuntimeError(
            f"no aoi in events file for case {case!r}; pass --bounds")
    project = _resolve_project(project)
    assets: list[dict] = []
    if download:
        granules = search_granules_range(bounds, start, end)
        if not granules:
            raise RuntimeError(
                f"no {SHORT_NAME} granules in {start}..{end} for {case}")
        raw = Path(out_dir) / "raw"
        _edl_login()
        import earthaccess
        raw.mkdir(parents=True, exist_ok=True)

        def work(g):
            das = []
            for f in earthaccess.download([g], str(raw)) or []:
                path = Path(f)
                try:
                    da = open_halfhourly([path], bounds)
                    if "time" in da.dims:
                        da = da.isel(time=0)
                    das.append(da)
                finally:
                    path.unlink(missing_ok=True)
            return das

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
            results = list(ex.map(work, granules))
        for g, das in zip(granules, results):
            tag = granule_start_tag(g)
            for da in das:
                lat_name = next(d for d in da.dims if "lat" in d.lower())
                lon_name = next(d for d in da.dims if "lon" in d.lower())
                cog = Path(out_dir) / f"{LEAF}_{tag}.tif"
                write_cog(cog, da.values, da[lat_name].values,
                          da[lon_name].values)
                assets.append({"tag": tag, "cog": cog,
                               "product": "halfhourly",
                               "date": f"{tag[:4]}-{tag[4:6]}-{tag[6:8]}",
                               "source": {"granule": _granule_name(g)}})
    else:
        assets = scan_cogs(out_dir)
    if ingest and assets and ensure_collection is not None:
        ensure_collection(
            f"projects/{project}/assets/{collection_path(case)}")
    stage_and_ingest(assets, project=project, force=force,
                     assets_json=assets_json, ingest=ingest, case=case)
    return assets


def plan(date: dt.date, bounds=None, days: int = DEFAULT_DAYS, *,
         out_dir: Path, project=None) -> dict:
    """The --dry-run document: projected assets, no network or credentials."""
    bounds = tuple(bounds) if bounds is not None else now_bounds()
    proj = project or os.environ.get("GEE_PROJECT") or "<GEE_PROJECT>"
    assets = []
    for k in range(days):
        ymd = (date - dt.timedelta(days=k)).strftime("%Y%m%d")
        assets.append({"tag": ymd, "product": "daily",
                       "asset_id": asset_id(proj, ymd),
                       "cog": str(Path(out_dir) / f"{LEAF}_{ymd}.tif")})
        if k <= days - 3:
            assets.append({"tag": f"{ymd}_3day", "product": "3day",
                           "asset_id": asset_id(proj, f"{ymd}_3day"),
                           "cog": str(Path(out_dir)
                                      / f"{LEAF}_{ymd}_3day.tif")})
    return {
        "input": LEAF,
        "collection": f"projects/{proj}/assets/{COLLECTION}",
        "short_name": f"{SHORT_NAME}.{VERSION}",
        "date": date.isoformat(),
        "bounds": list(bounds),
        "days": days,
        "assets_json": str(ASSETS_JSON),
        "auth": "search anonymous; download needs EARTHDATA_TOKEN or "
                "EDL_USER/EDL_PASS",
        "assets": assets,
    }


def plan_case(case: str, start: dt.date, end: dt.date, bounds=None, *,
              out_dir: Path, project=None) -> dict:
    """The --case --dry-run document: projected per-granule assets."""
    bounds = tuple(bounds) if bounds is not None else case_bounds(case)
    proj = project or os.environ.get("GEE_PROJECT") or "<GEE_PROJECT>"
    parent = f"projects/{proj}/assets/{collection_path(case)}"
    return {
        "input": LEAF,
        "mode": "case",
        "case": case,
        "collection": parent,
        "short_name": f"{SHORT_NAME}.{VERSION}",
        "window": [start.isoformat(), end.isoformat()],
        "bounds": list(bounds) if bounds else None,
        "assets_json": str(ASSETS_JSON),
        "granule_asset": f"{parent}/<yyyymmddThhmm>",
        "auth": "search anonymous; download needs EARTHDATA_TOKEN or "
                "EDL_USER/EDL_PASS",
    }


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="python -m pipeline.fetch.imerg",
        description=__doc__.splitlines()[0])
    p.add_argument("--date", type=dt.date.fromisoformat,
                   default=dt.datetime.now(dt.UTC).date(),
                   help="window end date, UTC (default: today)")
    p.add_argument("--bounds", type=float, nargs=4, default=None,
                   metavar=("W", "S", "E", "N"),
                   help="default: config.yaml domains.now via "
                        "tp.config.now_bounds()")
    p.add_argument("--days", type=int, default=DEFAULT_DAYS,
                   help="lookback days for daily precip (default: 7)")
    p.add_argument("--out-dir", type=Path, default=DEFAULT_COG_DIR)
    p.add_argument("--assets-json", type=Path, default=ASSETS_JSON)
    p.add_argument("--project", default=None,
                   help="GEE project (default: $GEE_PROJECT)")
    p.add_argument("--dry-run", action="store_true",
                   help="print the plan JSON; no network, creds or writes")
    p.add_argument("--no-download", action="store_true",
                   help="skip fetch; record COGs already in --out-dir")
    p.add_argument("--no-ingest", action="store_true",
                   help="fetch + write COGs but skip the assets.json "
                        "record (raster ingest itself is unsupported, L10)")
    p.add_argument("--force", action="store_true",
                   help="re-ingest assets already in assets.json")
    p.add_argument("--case", default=None, metavar="CASE_ID",
                   help="hindcast mode: per-granule image assets under "
                        "tipping-point/<case>/imerg_early; aoi/window from "
                        "events/<case>.json unless --bounds/--start/--end")
    p.add_argument("--start", type=dt.date.fromisoformat, default=None,
                   help="case-window start date (default: events window)")
    p.add_argument("--end", type=dt.date.fromisoformat, default=None,
                   help="case-window end date (default: events window)")
    return p.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)
    if args.case:
        window = case_window(args.case)
        start = args.start or (window[0] if window else None)
        end = args.end or (window[1] if window else None)
        if start is None or end is None:
            raise SystemExit(
                "--case needs --start/--end or a window in "
                f"events/{args.case}.json")
        bounds = (tuple(args.bounds) if args.bounds is not None
                  else case_bounds(args.case))
        if args.dry_run:
            print(json.dumps(
                plan_case(args.case, start, end, bounds,
                          out_dir=args.out_dir,
                          project=args.project), indent=2))
            return
        assets = run_case(args.case, start, end, bounds,
                          out_dir=args.out_dir,
                          project=args.project,
                          download=not args.no_download, force=args.force,
                          assets_json=args.assets_json,
                          ingest=not args.no_ingest)
        counts = {}
        for a in assets:
            counts[a["status"]] = counts.get(a["status"], 0) + 1
        print(f"{LEAF}: {counts} -> {collection_path(args.case)}")
        return
    if args.start is not None or args.end is not None:
        raise SystemExit("--start/--end apply to --case mode")
    bounds = tuple(args.bounds) if args.bounds is not None else now_bounds()
    if args.dry_run:
        print(json.dumps(plan(args.date, bounds, args.days,
                              out_dir=args.out_dir, project=args.project),
                         indent=2))
        return
    assets = run(args.date, bounds, args.days,
                 out_dir=args.out_dir,
                 project=args.project, download=not args.no_download,
                 force=args.force, assets_json=args.assets_json,
                 ingest=not args.no_ingest)
    counts = {}
    for a in assets:
        counts[a["status"]] = counts.get(a["status"], 0) + 1
    print(f"{LEAF}: {counts} -> {COLLECTION}")


if __name__ == "__main__":
    main()
