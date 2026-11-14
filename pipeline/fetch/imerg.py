"""IMERG Early half-hourly -> daily + rolling-3-day precip COGs for Modo Agora.

Fetches GPM_3IMERGHHE v07 (the IMERG *Early* half-hourly run, ~4 h latency;
same short name as live/fetch.py. Late and Final half-hourly are
GPM_3IMERGHHL and GPM_3IMERGHH — not used here. CMR names not re-checked
online), aggregates mm/h * 0.5 to daily mm/day over the
--days lookback, rolls a 3-day accumulation, clips to the now AOI and stages
each raster as an image asset in the GEE image collection
projects/<GEE_PROJECT>/assets/tipping-point/now/imerg_early.

    uv run python -m pipeline.fetch.imerg [--date YYYY-MM-DD] \
        [--bounds W S E N] [--days N] [--dry-run] [--no-download]

Auth split: the CMR search is anonymous; only the download step needs
Earthdata credentials — EARTHDATA_TOKEN, or EDL_USER/EDL_PASS mapped onto
earthaccess's EARTHDATA_USERNAME/PASSWORD env names.

GCS staging + GEE ingest + the data/now/assets.json manifest come from the
ingest contract (unit/ingest): stage_to_gcs(path, bucket=, case='now', name=),
ingest_asset(uri, asset_id=, kind='image', force=), append_manifest('now',
entry). Imported best-effort below — --dry-run and --no-download work without
it. Runs are idempotent: asset ids already present in assets.json are skipped
unless --force.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib
import json
import os
import sys
from pathlib import Path

import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

LEAF = "imerg_early"
COLLECTION = f"tipping-point/now/{LEAF}"
SHORT_NAME = "GPM_3IMERGHHE"
VERSION = "07"

# Fallback "now" AOI (west, south, east, north) — mirrors live/config.NOW_BOUNDS,
# the v4 manifest "now".bounds: Sul do Brasil (RS, SC, PR) at ~1 km. Ruling L9:
# the canonical default is config.yaml `domains.now` read through
# tp.config.now_bounds() — see now_bounds() below; --bounds always wins.
NOW_BOUNDS = (-58.0, -34.0, -48.0, -22.5)

REPO_ROOT = Path(__file__).resolve().parents[2]
NOW_DIR = REPO_ROOT / "data" / "now"
DEFAULT_COG_DIR = NOW_DIR / "cog" / LEAF
ASSETS_JSON = NOW_DIR / "assets.json"
DEFAULT_DAYS = 7                        # same lookback as live/run_now.py

DEFAULT_BUCKET = "tipping-point-data"   # live/config.GCS_BUCKET; NOW_GCS_BUCKET wins


def now_bounds() -> tuple:
    """Operational now AOI (west, south, east, north).

    Canonical source: config.yaml `domains.now` via tp.config.now_bounds()
    (ruling L9 — landing via lead-analysis). Falls back to the bundled
    NOW_BOUNDS while that helper doesn't exist on this branch. An explicit
    --bounds always overrides.
    """
    try:
        from tp.config import now_bounds as _cfg_now_bounds
    except ImportError:
        return tuple(NOW_BOUNDS)
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
        if all(callable(getattr(mod, f, None))
               for f in ("stage_to_gcs", "ingest_asset", "append_manifest")):
            return mod
    return None


try:
    _ing = _load_ingest()
    stage_to_gcs = _ing.stage_to_gcs if _ing else None
    ingest_asset = _ing.ingest_asset if _ing else None
    append_manifest = _ing.append_manifest if _ing else None
except Exception:  # noqa: BLE001 — a broken ingest must never break fetch/dry-run
    stage_to_gcs = ingest_asset = append_manifest = None


# --------------------------------------------------------------------------- #
# auth + earthaccess (lazy: importing this module needs no creds/network)
# --------------------------------------------------------------------------- #
def _edl_login():
    """Download-side Earthdata auth — call ONLY before earthaccess.download.

    CMR search is anonymous. EARTHDATA_TOKEN bearer first; then the
    EDL_USER/EDL_PASS pair — mapped onto EARTHDATA_USERNAME/PASSWORD only
    when BOTH are set (partial credentials are rejected, same rule as
    fetch/opera.py); then a ~/.netrc entry.
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


def open_halfhourly(paths, bounds):
    """Open IMERG HDF5s as one (time, lat, lon) mm/h DataArray over `bounds`.

    v07 keeps the half-hourly fields under the 'Grid' HDF5 group; Early run
    names the rate field 'precipitation' (older files: 'precipitationCal').
    """
    import xarray as xr

    files = [str(p) for p in paths]
    try:
        ds = xr.open_mfdataset(files, group="Grid", combine="by_coords",
                               engine="h5netcdf")
    except (OSError, KeyError, ValueError):
        ds = xr.open_mfdataset(files, combine="by_coords", engine="h5netcdf")
    var = "precipitation" if "precipitation" in ds else "precipitationCal"
    return _subset(ds[var], bounds)


def daily_and_3day(halfhourly):
    """mm/h half-hourly -> (daily mm/day, 3-day rolling mm), plus steps/day.

    IMERG rates are mm/h over 30-min windows, so daily = sum(rate) * 0.5.
    rain3[i] covers days i-2..i; the first two days carry NaN.
    """
    daily = (halfhourly * 0.5).resample(time="1D").sum()
    steps = halfhourly.resample(time="1D").count().max(
        dim=[d for d in halfhourly.dims if d != "time"])
    rain3 = daily.rolling(time=3, min_periods=3).sum()
    return daily, rain3, steps


def fetch_daily(bounds, end: dt.date, days: int, work_dir: Path):
    """Search -> download -> open -> aggregate. Returns (daily, rain3, steps)."""
    granules = search_granules(bounds, end, days)
    if not granules:
        raise RuntimeError(f"no {SHORT_NAME} granules in window ending {end}")
    paths = download_granules(granules, Path(work_dir) / "raw")
    if not paths:
        raise RuntimeError("earthaccess.download returned no files")
    return daily_and_3day(open_halfhourly(paths, bounds))


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


def asset_id(project: str, tag: str) -> str:
    return f"projects/{project}/assets/{COLLECTION}/{tag}"


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
                    "product": "3day" if tag.endswith("_3day") else "daily"})
    return out


def stage_and_ingest(assets: list[dict], *, bucket: str, project: str,
                     force: bool = False, assets_json: Path = ASSETS_JSON,
                     ingest: bool = True) -> list[dict]:
    """GCS-stage + GEE-ingest each COG; idempotent via assets.json."""
    done = manifest_asset_ids(assets_json)
    entries = []
    for a in assets:
        aid = asset_id(project, a["tag"])
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
        if stage_to_gcs is None or ingest_asset is None:
            a["status"] = "ingest_unavailable"
            continue
        uri = stage_to_gcs(Path(a["cog"]), bucket=bucket, case="now",
                           name=f"cog/{LEAF}/{Path(a['cog']).name}")
        ingest_asset(uri, asset_id=aid, kind="image", force=force)
        entry = {
            "asset_id": aid,
            "collection": f"projects/{project}/assets/{COLLECTION}",
            "kind": "image",
            "input": LEAF,
            "tag": a["tag"],
            "date": a.get("date"),
            "product": a.get("product"),
            "gcs_uri": uri,
            "cog": str(a["cog"]),
            "source": a.get("source"),
            "ingested_at": dt.datetime.now(dt.UTC).isoformat()
            .replace("+00:00", "Z"),
        }
        if append_manifest is not None:
            append_manifest("now", entry)
        a["status"] = "ingested"
        entries.append(entry)
    return entries


def _resolve_project(project: str | None) -> str:
    p = project or os.environ.get("GEE_PROJECT")
    if not p:
        raise RuntimeError("set GEE_PROJECT or pass --project")
    return p


def _resolve_bucket(bucket: str | None) -> str:
    return bucket or os.environ.get("NOW_GCS_BUCKET") or DEFAULT_BUCKET


def _time_label(da, i: int) -> str:
    return np.datetime_as_string(da.time.values[i], unit="D")


def run(date: dt.date, bounds=None, days: int = DEFAULT_DAYS, *,
        out_dir: Path, bucket=None, project=None, download: bool = True,
        force: bool = False, assets_json: Path = ASSETS_JSON,
        ingest: bool = True) -> list[dict]:
    """Fetch + aggregate + write + ingest; returns the asset list with status."""
    bounds = tuple(bounds) if bounds is not None else now_bounds()
    project = _resolve_project(project)
    bucket = _resolve_bucket(bucket)
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
            meta = {"hh_steps": int(steps.values[i])}
            assets.append({"tag": ymd, "date": iso, "cog": cog,
                           "product": "daily", "source": meta})
            if i >= 2:
                cog3 = Path(out_dir) / f"{LEAF}_{ymd}_3day.tif"
                write_cog(cog3, rain3.isel(time=i).values, lats, lons)
                assets.append({"tag": f"{ymd}_3day", "date": iso, "cog": cog3,
                               "product": "3day", "source": meta})
    else:
        assets = scan_cogs(out_dir)
    stage_and_ingest(assets, bucket=bucket, project=project, force=force,
                     assets_json=assets_json, ingest=ingest)
    return assets


def plan(date: dt.date, bounds=None, days: int = DEFAULT_DAYS, *,
         out_dir: Path, bucket=None, project=None) -> dict:
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
        "bucket": _resolve_bucket(bucket),
        "assets_json": str(ASSETS_JSON),
        "auth": "search anonymous; download needs EARTHDATA_TOKEN or "
                "EDL_USER/EDL_PASS",
        "assets": assets,
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
    p.add_argument("--bucket", default=None,
                   help="GCS staging bucket (default: $NOW_GCS_BUCKET)")
    p.add_argument("--project", default=None,
                   help="GEE project (default: $GEE_PROJECT)")
    p.add_argument("--dry-run", action="store_true",
                   help="print the plan JSON; no network, creds or writes")
    p.add_argument("--no-download", action="store_true",
                   help="skip fetch; stage+ingest COGs already in --out-dir")
    p.add_argument("--no-ingest", action="store_true",
                   help="fetch + write COGs but skip GCS/GEE staging")
    p.add_argument("--force", action="store_true",
                   help="re-ingest assets already in assets.json")
    return p.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)
    bounds = tuple(args.bounds) if args.bounds is not None else now_bounds()
    if args.dry_run:
        print(json.dumps(plan(args.date, bounds, args.days,
                              out_dir=args.out_dir, bucket=args.bucket,
                              project=args.project),
                         indent=2))
        return
    assets = run(args.date, bounds, args.days,
                 out_dir=args.out_dir, bucket=args.bucket,
                 project=args.project, download=not args.no_download,
                 force=args.force, assets_json=args.assets_json,
                 ingest=not args.no_ingest)
    counts = {}
    for a in assets:
        counts[a["status"]] = counts.get(a["status"], 0) + 1
    print(f"{LEAF}: {counts} -> {COLLECTION}")


if __name__ == "__main__":
    main()
