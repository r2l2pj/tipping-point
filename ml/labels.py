"""Inventory labels: UFRGS/Cemaden shapefiles → deduped cells → footprint → t.

Pipeline (spec §2.2, §5.4):
  1. read the point shapefiles with geopandas
  2. dedupe to one initiation point per 90 m grid cell (~10-15k, cap 10k)
  3. mapped footprint = convex hull of the inventory (N1 must stay inside it)
  4. assign each cell t = end of its max-24h IMERG window, Apr 27 – May 6 2024
  5. write a GEE-ready CSV and (optionally) upload it as an asset

The Zenodo downloads and GEE credentials are NOT in the repo: steps that need
them raise clear errors. The dedupe/footprint/timing logic runs on synthetic
inputs.
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from . import config as cfg

log = logging.getLogger(__name__)

_MISSING_FILES_MSG = (
    "inventory files not found: {files}. Download the CC-BY-4.0 inventories "
    "from Zenodo (UFRGS doi:{ufrgs}, Cemaden doi:{cemaden}) and pass the "
    "shapefile paths."
)


def require_files(paths: list[str | Path]) -> list[Path]:
    paths = [Path(p) for p in paths]
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        raise FileNotFoundError(_MISSING_FILES_MSG.format(
            files=missing, ufrgs=cfg.ZENODO_UFRGS, cemaden=cfg.ZENODO_CEMADEN))
    return paths


def load_points(paths: list[str | Path]):
    """Read and concatenate inventory point shapefiles (any mix of sources)."""
    import geopandas as gpd

    frames = []
    for p in require_files(paths):
        gdf = gpd.read_file(p)
        gdf["source"] = Path(p).stem
        frames.append(gdf[["geometry", "source"]])
    if not frames:
        raise FileNotFoundError("no inventory paths given")
    out = pd.concat(frames, ignore_index=True)
    if out.crs is None:
        raise ValueError("inventory has no CRS; cannot grid-dedupe")
    return out.to_crs("EPSG:4326")


def dedupe_to_grid(gdf, cell_m: float = cfg.CELL_SIZE_M, cap: int = cfg.POS_CAP):
    """One point per ``cell_m`` grid cell (in the inventory's UTM CRS)."""
    utm = gdf.estimate_utm_crs()
    proj = gdf.to_crs(utm)
    cx = np.floor(proj.geometry.x / cell_m).astype(np.int64)
    cy = np.floor(proj.geometry.y / cell_m).astype(np.int64)
    proj = proj.assign(_cell=cx * 10_000_000 + cy)
    deduped = proj.drop_duplicates("_cell").drop(columns="_cell").to_crs("EPSG:4326")
    if len(deduped) > cap:
        deduped = deduped.sample(cap, random_state=0).sort_index()
        log.info("capped positives at %d", cap)
    return deduped.reset_index(drop=True)


def mapped_footprint(gdf):
    """Convex hull of all inventory points — the N1 sampling domain."""
    return gdf.union_all().convex_hull


def assign_peak24h_times(
    gdf, rain, window: tuple[str, str] = cfg.EVENT_WINDOW
) -> pd.Series:
    """t = end of each cell's maximum-24h rain window inside ``window``.

    ``rain`` is an xarray DataArray of rain rate (mm/h) with dims
    (time, lat, lon) — a 30-min IMERG stack in production, anything regular
    in tests. Works entirely on local/synthetic data.
    """
    rain_w = rain.sel(time=slice(window[0], window[1]))
    times = pd.DatetimeIndex(rain_w.time.values)
    step_h = times.to_series().diff().median().total_seconds() / 3600
    win = max(1, round(24.0 / step_h))
    acc = rain_w.rolling(time=win).sum()  # 24h total per window end (scale irrelevant)

    ends = []
    for geom in gdf.geometry:
        prof = acc.sel(lon=geom.x, lat=geom.y, method="nearest").fillna(0)
        ends.append(prof.idxmax("time").values.tolist())
    return pd.Series(pd.to_datetime(ends), name="t_utc", index=gdf.index)


def to_gee_table(gdf, t_utc: pd.Series, out_csv: str | Path) -> Path:
    """GEE-ready CSV: lon, lat, assigned t, source — uploadable as a table."""
    out_csv = Path(out_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame({
        "lon": gdf.geometry.x,
        "lat": gdf.geometry.y,
        "t_utc": pd.to_datetime(t_utc).dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": gdf.get("source", "inventory"),
    })
    df.to_csv(out_csv, index=False)
    log.info("wrote %s (%d cells)", out_csv, len(df))
    return out_csv


def upload_to_gee(csv_path: str | Path, asset_name: str = cfg.ASSET_SLIDES) -> str:
    """Upload the CSV as a GEE table asset via the `earthengine` CLI.

    Guarded: needs `earthengine authenticate` and GEE_PROJECT. Raises a clear
    error instead of failing obscurely when they are absent.
    """
    import shutil
    import subprocess

    if shutil.which("earthengine") is None:
        raise RuntimeError(
            "`earthengine` CLI not found — install earthengine-api and run "
            "`earthengine authenticate` before uploading assets"
        )
    asset = cfg.asset_path(asset_name)
    cmd = ["earthengine", "upload", "table", "--asset_id", asset, str(csv_path)]
    log.info("running: %s", " ".join(cmd))
    subprocess.run(cmd, check=True)
    return asset


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--shapefiles", nargs="+", required=True,
                    help="UFRGS/Cemaden point shapefiles (Zenodo downloads)")
    ap.add_argument("--out-csv", default="data/raw/rs2024_slides.csv")
    ap.add_argument("--footprint-out", default="data/raw/rs2024_footprint.geojson")
    ap.add_argument("--via-gee", action="store_true",
                    help="assign peak-24h t per cell through GEE IMERG (needs auth)")
    ap.add_argument("--upload", action="store_true", help="also upload as GEE asset")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    import geopandas as gpd

    gdf = dedupe_to_grid(load_points(args.shapefiles))
    log.info("%d deduped positive cells", len(gdf))
    gpd.GeoDataFrame(geometry=[mapped_footprint(gdf)], crs="EPSG:4326").to_file(
        args.footprint_out, driver="GeoJSON"
    )
    if args.via_gee:
        from . import gee_features

        ee = gee_features.init_ee()
        fc = ee.FeatureCollection(
            [ee.Feature(ee.Geometry.Point([g.x, g.y])) for g in gdf.geometry]
        )
        t_utc = gee_features.assign_peak24h_gee(fc, ee=ee)["t_utc"]
        t_utc.index = gdf.index
    else:
        t_utc = pd.Series(pd.NaT, index=gdf.index)
        log.warning("t_utc left empty — rerun with --via-gee to assign peak-24h times")
    to_gee_table(gdf, t_utc, args.out_csv)
    if args.upload:
        upload_to_gee(args.out_csv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
