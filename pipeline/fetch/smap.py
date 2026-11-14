"""SMAP L4 sm_rootzone_wetness -> daily COG for Modo Agora.

Fetches the latest SPL4SMGP v008 granule (SMAP L4 global geophysical product,
9 km EASE-2 grid, 3-hourly files — the daily asset uses the most recent one),
reads sm_rootzone_wetness [0..1], grids the 2-D EASE lat/lon onto a regular
~0.09° EPSG:4326 grid clipped to the now AOI, and stages it as an image asset
in projects/<GEE_PROJECT>/assets/tipping-point/now/smap_l4.

    uv run python -m pipeline.fetch.smap [--date YYYY-MM-DD] \
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
from pathlib import Path

import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

LEAF = "smap_l4"
COLLECTION = f"tipping-point/now/{LEAF}"
SHORT_NAME = "SPL4SMGP"
VERSION = "008"

REPO_ROOT = Path(__file__).resolve().parents[2]
NOW_DIR = REPO_ROOT / "data" / "now"
DEFAULT_COG_DIR = NOW_DIR / "cog" / LEAF
ASSETS_JSON = NOW_DIR / "assets.json"
DEFAULT_DAYS_BACK = 10                  # granule search window ending --date
GRID_RES_DEG = 0.09                     # ~9 km, the native SMAP L4 posting
CLIP_PAD_DEG = 0.5                      # EASE cells kept beyond the bbox edge

_NATIVE_ID_DATE_RE = re.compile(r"(\d{8})T\d{6}")


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
except Exception:  # noqa: BLE001 — a broken ingest must never break fetch/dry-run
    append_manifest = None


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


def search_granules(bounds, end: dt.date, days_back: int = DEFAULT_DAYS_BACK) -> list:
    """Anonymous CMR search for recent SPL4SMGP granules over the AOI."""
    import earthaccess

    return earthaccess.search_data(
        short_name=SHORT_NAME, version=VERSION,
        temporal=(str(end - dt.timedelta(days=days_back)), str(end)),
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


def product_date(granule, fallback: dt.date) -> dt.date:
    """YYYYMMDD inside the granule native-id (e.g. SMAP_L4_SM_gph_20261113T013000_...)."""
    try:
        native_id = str(granule["meta"]["native-id"])
    except (TypeError, KeyError):
        native_id = str(granule)
    m = _NATIVE_ID_DATE_RE.search(native_id)
    if m:
        return dt.date.fromisoformat(
            f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]}")
    return fallback


def open_rootzone(path, bounds):
    """Read sm_rootzone_wetness + 2-D EASE cell lat/lon, clipped to the bbox.

    SPL4SMGP keeps fields under 'Geophysical_Data'; cell_lat/cell_lon sit
    there or under 'Metadata' depending on the product revision.
    """
    import xarray as xr

    # Real SPL4SMGP v008: sm_rootzone_wetness under Geophysical_Data,
    # cell_lat/cell_lon at file root (no dimension labels, so h5netcdf
    # needs phony_dims). Same layout live/fetch.py reads.
    ds = xr.open_dataset(str(path), group="Geophysical_Data",
                         engine="h5netcdf", phony_dims="sort")
    sm = ds["sm_rootzone_wetness"].load()
    geo = xr.open_dataset(str(path), engine="h5netcdf", phony_dims="sort")
    lats, lons = geo["cell_lat"].values, geo["cell_lon"].values
    w, s, e, n = bounds
    m = ((lats >= s - CLIP_PAD_DEG) & (lats <= n + CLIP_PAD_DEG)
         & (lons >= w - CLIP_PAD_DEG) & (lons <= e + CLIP_PAD_DEG))
    return lats[m], lons[m], np.asarray(sm.values, dtype=np.float64)[m]


def grid_to_regular(lats, lons, vals, bounds, res: float = GRID_RES_DEG):
    """Scatter EASE-grid points -> regular (lat, lon) grid over `bounds`.

    Linear interpolation, nearest-fill of interior gaps, NaN where no source
    cell is nearby (same coverage rule as live/run_now.py).
    """
    from scipy.interpolate import griddata

    w, s, e, n = bounds
    glat = np.arange(s, n + res / 2, res)
    glon = np.arange(w, e + res / 2, res)
    glon2d, glat2d = np.meshgrid(glon, glat)
    valid = np.isfinite(vals)
    pts = (lats[valid], lons[valid])
    out = griddata(pts, vals[valid], (glat2d, glon2d), method="linear")
    near = griddata(pts, vals[valid], (glat2d, glon2d), method="nearest")
    out = np.where(np.isnan(out), near, out)
    # coverage is gridded over ALL source cells (gap cells carry 0) — a dst
    # pixel whose nearest source is a hole stays NaN, like live/run_now.py
    cover = griddata((lats, lons), valid.astype(float), (glat2d, glon2d),
                     method="nearest")
    out[cover < 0.5] = np.nan
    return glat, glon, out


def fetch_latest(bounds, end: dt.date, days_back: int, work_dir: Path):
    """Latest granule -> (glat, glon, wetness2d, {product_date, native_id})."""
    granules = search_granules(bounds, end, days_back)
    if not granules:
        raise RuntimeError(
            f"no {SHORT_NAME} granules in the {days_back} days before {end}")
    latest = granules[-1]
    try:
        native_id = str(latest["meta"]["native-id"])
    except (TypeError, KeyError):
        native_id = str(latest)
    paths = download_granules([latest], Path(work_dir) / "raw")
    if not paths:
        raise RuntimeError("earthaccess.download returned no files")
    lats, lons, vals = open_rootzone(paths[0], bounds)
    glat, glon, grid = grid_to_regular(lats, lons, vals, bounds)
    meta = {"product_date": product_date(latest, end).isoformat(),
            "native_id": native_id}
    return glat, glon, grid, meta


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
        out.append({"tag": tag, "cog": p, "product": "daily"})
    return out


def stage_and_ingest(assets: list[dict], *, project: str,
                     force: bool = False, assets_json: Path = ASSETS_JSON,
                     ingest: bool = True) -> list[dict]:
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
        entry = {
            "asset_id": aid,
            "collection": f"projects/{project}/assets/{COLLECTION}",
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
            append_manifest("now", entry)
        a["status"] = "local-only"
        entries.append(entry)
    return entries


def _resolve_project(project: str | None) -> str:
    p = project or os.environ.get("GEE_PROJECT")
    if not p:
        raise RuntimeError("set GEE_PROJECT or pass --project")
    return p


def run(date: dt.date, bounds=None, days_back: int = DEFAULT_DAYS_BACK, *,
        out_dir: Path, project=None, download: bool = True,
        force: bool = False, assets_json: Path = ASSETS_JSON,
        ingest: bool = True) -> list[dict]:
    """Fetch + grid + write + record assets; returns the list with status."""
    bounds = tuple(bounds) if bounds is not None else now_bounds()
    project = _resolve_project(project)
    if download:
        glat, glon, grid, meta = fetch_latest(bounds, date, days_back,
                                            Path(out_dir))
        ymd = meta["product_date"].replace("-", "")
        cog = Path(out_dir) / f"{LEAF}_{ymd}.tif"
        write_cog(cog, grid, glat, glon)
        assets = [{"tag": ymd, "date": meta["product_date"], "cog": cog,
                   "product": "daily", "source": meta}]
    else:
        assets = scan_cogs(out_dir)
    stage_and_ingest(assets, project=project, force=force,
                     assets_json=assets_json, ingest=ingest)
    return assets


def plan(date: dt.date, bounds=None, days_back: int = DEFAULT_DAYS_BACK, *,
         out_dir: Path, project=None) -> dict:
    """The --dry-run document: projected asset, no network or credentials."""
    bounds = tuple(bounds) if bounds is not None else now_bounds()
    proj = project or os.environ.get("GEE_PROJECT") or "<GEE_PROJECT>"
    ymd = date.strftime("%Y%m%d")
    return {
        "input": LEAF,
        "collection": f"projects/{proj}/assets/{COLLECTION}",
        "short_name": f"{SHORT_NAME}.{VERSION}",
        "date": date.isoformat(),
        "bounds": list(bounds),
        "days_back": days_back,
        "assets_json": str(ASSETS_JSON),
        "auth": "search anonymous; download needs EARTHDATA_TOKEN or "
                "EDL_USER/EDL_PASS",
        "assets": [{"tag": ymd, "product": "daily",
                    "asset_id": asset_id(proj, ymd),
                    "cog": str(Path(out_dir) / f"{LEAF}_{ymd}.tif")}],
    }


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="python -m pipeline.fetch.smap",
        description=__doc__.splitlines()[0])
    p.add_argument("--date", type=dt.date.fromisoformat,
                   default=dt.datetime.now(dt.UTC).date(),
                   help="search end date, UTC (default: today)")
    p.add_argument("--bounds", type=float, nargs=4, default=None,
                   metavar=("W", "S", "E", "N"),
                   help="default: config.yaml domains.now via "
                        "tp.config.now_bounds()")
    p.add_argument("--days", type=int, default=DEFAULT_DAYS_BACK,
                   help="granule search window in days (default: 10)")
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
    return p.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)
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
