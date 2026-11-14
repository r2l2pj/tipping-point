"""One-time build of static/ for Modo Agora (doc step 1).

Computes over the south-Brazil grid (~1 km):
  - monthly rain climatology p90/p95/p99 of 3-day IMERG accumulation (2001-2023,
    same windows as gee/01_tpi_index.js: +-30 days around mid-month, step 3)
  - terrain scores T_slide / T_flood from NASADEM slope + HAND proxy
    (dem - focalMin(1500 m), matching the GEE script)
  - GPWv4.11 population count (NASA SEDAC/CIESIN, latest epoch), used for
    people_exposed in summary.json

Run once with credentials in env:
    GCP_SA_KEY=<service-account json> GEE_PROJECT=<project> \
        uv run python live/build_static.py

Output: static/sul_static.npz with keys p90/p95/p99 (12,H,W), t_slide,
t_flood, slope_deg, hand_m, pop, pop_year, pop_source, lats, lons.
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import sys
import tempfile
import zipfile
from pathlib import Path

import numpy as np

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from live.config import (
    ASSET_ROOT,
    HAND_PROXY_RADIUS_M,
    NOW_BOUNDS,
    STATIC_DIR,
    STATIC_DOMAIN,
    STATIC_NPZ,
)

YEARS = range(2001, 2024)
OFFSETS = range(-30, 31, 3)
POP_COLLECTION = "CIESIN/GPWv411/GPW_Population_Count"
POP_BAND = "population_count"
ASSET_TERRAIN_SCALE_M = 30
ASSET_CLIM_SCALE_M = 10_000


def _ee_init():
    try:
        from gee.common import ee_init
        return ee_init()
    except ImportError:
        pass

    import ee

    key = json.loads(__import__("os").environ["GCP_SA_KEY"])
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(key, f)
        key_file = f.name
    creds = ee.ServiceAccountCredentials(key["client_email"], key_file)
    ee.Initialize(creds, project=__import__("os").environ.get("GEE_PROJECT"))
    return ee


def _download_band(ee, image, band: str, scale_m: int = 1000) -> np.ndarray:
    import rasterio
    import requests

    rect = ee.Geometry.Rectangle(list(NOW_BOUNDS))
    url = image.select(band).getDownloadURL({
        "region": rect, "scale": scale_m, "format": "GEO_TIFF",
    })
    data = requests.get(url, timeout=600).content
    if data[:4] == b"PK\x03\x04":
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            name = next(n for n in z.namelist() if n.endswith(".tif"))
            tif = io.BytesIO(z.read(name))
    else:
        tif = io.BytesIO(data)
    with rasterio.open(tif) as src:
        return src.read(1).astype(np.float64)


def _climatology(ee, month: int, day: int):
    """p90/p95/p99 of 3-day IMERG accumulation over +-30d windows (step 3)
    around the same month/day in each year of YEARS (gee/01_tpi_index.js)."""
    imerg = ee.ImageCollection("NASA/GPM_L3/IMERG_V07").select("precipitation")

    def accum(end):
        return imerg.filterDate(end.advance(-3, "day"), end).sum().multiply(0.5)

    samples = []
    for y in YEARS:
        for o in OFFSETS:
            end = ee.Date.fromYMD(y, month, day).advance(o, "day")
            samples.append(accum(end))
    pct = ee.ImageCollection(samples).reduce(ee.Reducer.percentile([90, 95, 99]))
    return pct.rename(["p90", "p95", "p99"])


def _climatology_for_month(ee, month: int):
    return _climatology(ee, month, 15)


def _climatology_for_doy(ee, doy: int):
    d = dt.date(2000, 1, 1) + dt.timedelta(days=doy - 1)
    return _climatology(ee, d.month, d.day)


def _terrain(ee):
    dem = ee.Image("NASA/NASADEM_HGT/001").select("elevation")
    slope = ee.Terrain.slope(dem)
    hand = dem.subtract(dem.focalMin(radius=HAND_PROXY_RADIUS_M, units="meters"))
    t_slide = slope.unitScale(15, 30).clamp(0, 1)
    t_flood = ee.Image(1).subtract(hand.unitScale(5, 15)).clamp(0, 1).where(slope.gt(5), 0)
    pop_img = ee.Image(
        ee.ImageCollection(POP_COLLECTION).sort("system:time_start", False).first()
    ).select(POP_BAND)
    return t_slide.rename("t_slide"), t_flood.rename("t_flood"), slope.rename("slope_deg"), \
        hand.rename("hand_m"), pop_img.rename("pop")


def _asset_path(name: str) -> str:
    return f"{ASSET_ROOT}/static/{STATIC_DOMAIN}/{name}"


def _ensure_collection(ee, path: str) -> None:
    try:
        info = ee.data.getAsset(path)
    except ee.EEException:
        info = None
    if not info:
        ee.data.createAsset({"type": "ImageCollection"}, path)


def build_assets(ee, dry_run: bool = False) -> list:
    """Export the static/south_brazil/* GEE assets (L8; lead-analysis reads
    them): clim_rain ImageCollection (one image per day-of-year, 'doy'
    property, p90/p95/p99 bands computed over +-30d windows around that
    month/day, ~10 km), slope (~30 m, degrees) and hand (~30 m, meters).
    Population stays npz-only — it is not in the analysis asset list.
    Returns the export tasks started."""
    region = ee.Geometry.Rectangle(list(NOW_BOUNDS))
    tasks = []

    def _export(image, asset_id, scale, description):
        task = ee.batch.Export.image.toAsset(
            image, description=description, assetId=asset_id,
            region=region, scale=scale, crs="EPSG:4326", maxPixels=1e13)
        if dry_run:
            print(f"[assets] (dry-run) {description} -> {asset_id}")
        else:
            task.start()
            print(f"[assets] started {description} -> {asset_id}")
        tasks.append(task)
        return task

    coll = _asset_path("clim_rain")
    if not dry_run:
        _ensure_collection(ee, coll)
    for doy in range(1, 367):
        img = _climatology_for_doy(ee, doy)
        _export(img.set("doy", doy), f"{coll}/doy_{doy:03d}",
                ASSET_CLIM_SCALE_M, f"clim_rain_doy_{doy:03d}")

    _, _, slope, hand, _pop = _terrain(ee)
    _export(slope, _asset_path("slope"), ASSET_TERRAIN_SCALE_M, "slope")
    _export(hand, _asset_path("hand"), ASSET_TERRAIN_SCALE_M, "hand")
    return tasks


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="build_static", description=__doc__.splitlines()[0])
    p.add_argument("--assets", action="store_true",
                   help="export the static/south_brazil/* GEE assets "
                        "(clim_rain, slope, hand) instead of writing the npz")
    p.add_argument("--dry-run", action="store_true",
                   help="--assets only: build the export tasks without "
                        "starting them")
    return p.parse_args(argv)


def main(argv=None) -> None:
    from live.grid import dst_lats_lons

    args = parse_args(argv)
    ee = _ee_init()
    if args.assets:
        build_assets(ee, dry_run=args.dry_run)
        return

    out = {"lats": None, "lons": None}
    lats, lons = dst_lats_lons()
    out["lats"], out["lons"] = lats, lons

    p90 = np.empty((12, lats.size, lons.size))
    p95 = np.empty_like(p90)
    p99 = np.empty_like(p90)
    for m in range(1, 13):
        img = _climatology_for_month(ee, m)
        for i, b in enumerate(("p90", "p95", "p99")):
            arr = _download_band(ee, img, b)
            (p90, p95, p99)[i][m - 1] = arr
        print(f"climatology month {m:02d} done")
    out.update(p90=p90, p95=p95, p99=p99)

    terrain = _terrain(ee)
    for name, img in zip(
            ("t_slide", "t_flood", "slope_deg", "hand_m", "pop"), terrain):
        out[name] = _download_band(ee, img, name)
        print(f"terrain {name} done")
    pop_img = terrain[-1]
    out["pop_year"] = int(pop_img.date().get("year").getInfo())
    out["pop_source"] = POP_COLLECTION

    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    path = STATIC_DIR / STATIC_NPZ
    np.savez(path, **out)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
