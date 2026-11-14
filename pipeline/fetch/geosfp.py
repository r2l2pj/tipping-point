"""GEOS-FP precip forecast D+1..D+N -> per-lead-day COGs for Modo Agora.

Reads the GMAO GrADS-OPeNDAP fcast collection tavg1_2d_flx_Nx over its plain
'.ascii' interface (public HTTP, no Earthdata/GDS auth), picks the newest run
whose coverage reaches the end of D+N, sums the hourly PRECTOT flux
(kg m-2 s-1 == mm/s, time-averaged NOT mm/h -> * 3600) per UTC day, clips to
the now AOI, and stages one image asset per lead day in
projects/<GEE_PROJECT>/assets/tipping-point/now/geosfp — asset tag is the
forecast VALID date.

    uv run python -m pipeline.fetch.geosfp [--date YYYY-MM-DD] \
        [--bounds W S E N] [--days N] [--dry-run] [--no-download]

The .ascii parsing is adapted (condensed) from live/fetch.py — kept local so
this entry point also works where the top-level live/ package is not
importable. No credentials are needed anywhere in this module.

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

LEAF = "geosfp"
COLLECTION = f"tipping-point/now/{LEAF}"

REPO_ROOT = Path(__file__).resolve().parents[2]
NOW_DIR = REPO_ROOT / "data" / "now"
DEFAULT_COG_DIR = NOW_DIR / "cog" / LEAF
ASSETS_JSON = NOW_DIR / "assets.json"
DEFAULT_LEAD_DAYS = 3                   # D+1..D+3, like live/run_now FORECAST_DAYS

_GDS_BASE = ("https://opendap.nccs.nasa.gov/dods/GEOS-5/fp/0.25_deg/fcast/"
             "tavg1_2d_flx_Nx")
_GDS_RUN_RE = re.compile(r"tavg1_2d_flx_Nx\.(\d{8}_\d{2})")
_GDS_FILL = 1.0e15                      # _FillValue/missing_value for PRECTOT
_GDS_TIMEOUT = 60
_GDS_TRIES = 8                          # candidate runs, newest first


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
# GMAO GDS '.ascii' client (adapted from live/fetch.py — no auth needed)
# --------------------------------------------------------------------------- #
def _gds_get(url: str) -> str:
    import requests

    r = requests.get(url, timeout=_GDS_TIMEOUT)
    r.raise_for_status()
    return r.text


def _geosfp_runs(html: str) -> list[str]:
    """Dataset names in the collection dir listing, newest first."""
    return [f"tavg1_2d_flx_Nx.{t}"
            for t in sorted(set(_GDS_RUN_RE.findall(html)), reverse=True)]


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
    ordinal+1+fraction (live/fetch.py verified: run 20261113_00 starts
    2026-11-13 00:30, i.e. int(v)-1 is the proleptic ordinal)."""
    out = []
    for v in vals:
        day, frac = divmod(float(v), 1.0)
        base = dt.datetime.combine(dt.date.fromordinal(int(day) - 1), dt.time())
        out.append(base + dt.timedelta(seconds=round(frac * 86400)))
    return out


def _pick_run(issued: dt.date, need_by: dt.date):
    """Newest run whose coverage reaches end-of-`need_by` (last step >= 23:00
    so the final day is complete). Returns (run_name, [timesteps])."""
    full_last = dt.datetime.combine(need_by, dt.time(23))
    runs = _geosfp_runs(_gds_get(f"{_GDS_BASE}/"))
    if not runs:
        raise RuntimeError("no GEOS-FP runs in the collection listing")
    seen = []
    for cand in runs[:_GDS_TRIES]:
        vec = _gds_parse_vectors(
            _gds_get(f"{_GDS_BASE}/{cand}.ascii?prectot.time"))
        ts = _gds_times(vec["time"])
        seen.append(f"{cand} covers {ts[0]}..{ts[-1]}" if ts
                    else f"{cand} has no time axis")
        if ts and ts[-1] >= full_last:
            return cand, ts
    raise RuntimeError(
        f"no GEOS-FP run reaches end of {need_by} ({'; '.join(seen)})")


def fetch_daily(bounds, issued: dt.date, lead_days: int = DEFAULT_LEAD_DAYS):
    """GEOS-FP daily precipitation (mm/day) for D+1..D+`lead_days`.

    Returns (lats, lons, [day2d, ...], issued_iso) — one 2-D array per lead
    day, ordered D+1 first. See the module docstring for the unit handling.
    """
    issued = issued or dt.date.today()
    need_by = issued + dt.timedelta(days=lead_days)
    run, times = _pick_run(issued, need_by)

    vec = _gds_parse_vectors(
        _gds_get(f"{_GDS_BASE}/{run}.ascii?prectot.lat,prectot.lon"))
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
    arr = _gds_parse_grid(_gds_get(f"{_GDS_BASE}/{run}.ascii?{ce}"))
    arr = arr.astype(np.float64)
    arr[arr >= _GDS_FILL] = np.nan

    sub_dates = [t.date() for t in times[t0:t1 + 1]]
    days = sorted({d for d in sub_dates if d > issued})[:lead_days]
    if len(days) < lead_days:
        raise RuntimeError(
            f"GEOS-FP run {run} has fewer than {lead_days} daily steps "
            f"after {issued}")
    slabs = []
    for d in days:
        sel = arr[[i for i, dd in enumerate(sub_dates) if dd == d]]
        day = np.nansum(sel, axis=0) * 3600.0
        day[np.all(np.isnan(sel), axis=0)] = np.nan
        slabs.append(day)

    stamp = dt.datetime.strptime(run.rsplit(".", 1)[1], "%Y%m%d_%H")
    return (lats[la0:la1 + 1], lons[lo0:lo1 + 1], slabs, days,
            stamp.strftime("%Y-%m-%dT%H:00:00Z"), run)


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
        out.append({"tag": tag, "cog": p, "product": "forecast"})
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


def run(date: dt.date, bounds=None, lead_days: int = DEFAULT_LEAD_DAYS, *,
        out_dir: Path, project=None, download: bool = True,
        force: bool = False, assets_json: Path = ASSETS_JSON,
        ingest: bool = True) -> list[dict]:
    """Fetch + write + record assets; returns the list with status."""
    bounds = tuple(bounds) if bounds is not None else now_bounds()
    project = _resolve_project(project)
    if download:
        lats, lons, slabs, days, issued_iso, run_name = fetch_daily(
            bounds, date, lead_days)
        assets = []
        for k, (slab, day) in enumerate(zip(slabs, days, strict=True)):
            ymd = day.strftime("%Y%m%d")
            cog = Path(out_dir) / f"{LEAF}_{ymd}.tif"
            write_cog(cog, slab, lats, lons)
            assets.append({"tag": ymd, "date": day.isoformat(), "cog": cog,
                           "product": "forecast",
                           "source": {"run": run_name, "issued": issued_iso,
                                      "lead": k + 1}})
    else:
        assets = scan_cogs(out_dir)
    stage_and_ingest(assets, project=project, force=force,
                     assets_json=assets_json, ingest=ingest)
    return assets


def plan(date: dt.date, bounds=None, lead_days: int = DEFAULT_LEAD_DAYS, *,
         out_dir: Path, project=None) -> dict:
    """The --dry-run document: projected assets, no network or credentials."""
    bounds = tuple(bounds) if bounds is not None else now_bounds()
    proj = project or os.environ.get("GEE_PROJECT") or "<GEE_PROJECT>"
    assets = []
    for k in range(1, lead_days + 1):
        ymd = (date + dt.timedelta(days=k)).strftime("%Y%m%d")
        assets.append({"tag": ymd, "product": "forecast", "lead": k,
                       "asset_id": asset_id(proj, ymd),
                       "cog": str(Path(out_dir) / f"{LEAF}_{ymd}.tif")})
    return {
        "input": LEAF,
        "collection": f"projects/{proj}/assets/{COLLECTION}",
        "source": "GMAO GDS tavg1_2d_flx_Nx fcast (.ascii, no auth)",
        "issued": date.isoformat(),
        "bounds": list(bounds),
        "lead_days": lead_days,
        "assets_json": str(ASSETS_JSON),
        "auth": "none — public GDS HTTP",
        "assets": assets,
    }


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="python -m pipeline.fetch.geosfp",
        description=__doc__.splitlines()[0])
    p.add_argument("--date", type=dt.date.fromisoformat,
                   default=dt.datetime.now(dt.UTC).date(),
                   help="issue/reference date, UTC; D+1..D+N follow "
                        "(default: today)")
    p.add_argument("--bounds", type=float, nargs=4, default=None,
                   metavar=("W", "S", "E", "N"),
                   help="default: config.yaml domains.now via "
                        "tp.config.now_bounds()")
    p.add_argument("--days", type=int, default=DEFAULT_LEAD_DAYS,
                   help="forecast lead days D+1..D+N (default: 3)")
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
