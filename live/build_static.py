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
    NOW_RES_DEG,
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
NOW_PAD_DAYS = 35
TILES = 3
EVENTS_DIR = Path(__file__).resolve().parents[1] / "events"


def _ee_init():
    try:
        from gee.common import ee_init
    except ImportError:
        pass
    else:
        ee_init()
        import ee
        return ee

    import ee

    key = json.loads(__import__("os").environ["GCP_SA_KEY"])
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(key, f)
        key_file = f.name
    creds = ee.ServiceAccountCredentials(key["client_email"], key_file)
    ee.Initialize(creds, project=__import__("os").environ.get("GEE_PROJECT"))
    return ee


def _download_tiles(scale_m):
    west, south, east, north = NOW_BOUNDS
    scale_deg = NOW_RES_DEG * scale_m / 1000
    width = round((east - west) / scale_deg)
    height = round((north - south) / scale_deg)
    xs = [round(col * width / TILES) for col in range(TILES + 1)]
    ys = [round(row * height / TILES) for row in range(TILES + 1)]
    for row in range(TILES):
        for col in range(TILES):
            bounds = [west + xs[col] * scale_deg,
                      north - ys[row + 1] * scale_deg,
                      west + xs[col + 1] * scale_deg,
                      north - ys[row] * scale_deg]
            yield (row, col, bounds, xs[col + 1] - xs[col],
                   ys[row + 1] - ys[row], scale_deg)


def _download_band(ee, image, band: str, scale_m: int = 1000) -> np.ndarray:
    import rasterio
    import requests

    rows = [[] for _ in range(TILES)]
    selected = image.select(band)
    for row, col, bounds, width, height, scale_deg in _download_tiles(scale_m):
        url = selected.getDownloadURL({
            "scale": scale_m, "format": "GEO_TIFF",
            "crs": "EPSG:4326", "dimensions": [width, height],
            "crs_transform": [scale_deg, 0, bounds[0], 0, -scale_deg, bounds[3]],
        })
        response = requests.get(url, timeout=600)
        data = response.content
        is_zip = data[:4] == b"PK\x03\x04"
        is_tiff = data[:4] in (b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")
        if getattr(response, "status_code", 200) >= 400 or not (is_zip or is_tiff):
            body = data[:200].decode("utf-8", errors="replace")
            raise RuntimeError(f"Download {band} tile ({row}, {col}) failed: {body!r}")
        if is_zip:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                name = next(n for n in z.namelist() if n.endswith(".tif"))
                tif = io.BytesIO(z.read(name))
        else:
            tif = io.BytesIO(data)
        with rasterio.open(tif) as src:
            rows[row].append(src.read(1).astype(np.float64))
    return np.concatenate([np.concatenate(row, axis=1) for row in rows], axis=0)


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


def _doy(d: dt.date) -> int:
    """Calendar (month, day) -> 1..366 on the leap-year climatology calendar."""
    return (dt.date(2000, d.month, d.day) - dt.date(2000, 1, 1)).days + 1


def _doys_around(d: dt.date, pad: int) -> set[int]:
    """doy +-pad on the leap-year calendar, wrapping across the year boundary."""
    return {((_doy(d) - 1 + i) % 366) + 1 for i in range(-pad, pad + 1)}


def _doys_between(start: dt.date, end: dt.date) -> list[int]:
    """doy(start)..doy(end) inclusive on the leap-year calendar, wrapping
    across the year boundary (e.g. a Nov..Jan event window)."""
    a, b = _doy(start), _doy(end)
    if a <= b:
        return list(range(a, b + 1))
    return list(range(a, 367)) + list(range(1, b + 1))


def _case_id(event_id: str) -> str:
    """Canonical case id ('rs_2024') from an events/*.json id ('rs-2024-05')."""
    try:
        from tp.config import case_id as resolve
        return resolve(event_id)
    except Exception:
        return "_".join(event_id.split("-")[:2])


def _clim_cases(events_dir: Path = EVENTS_DIR) -> list[dict]:
    """Per-case clim_rain specs from events/*.json: id, aoi.bbox and the
    doy range of window.start..window.end inclusive (window used as-is)."""
    cases = []
    for path in sorted(events_dir.glob("*.json")):
        case = json.loads(path.read_text())
        cases.append({
            "id": _case_id(case["id"]),
            "bbox": case["aoi"]["bbox"],
            "doys": _doys_between(
                dt.date.fromisoformat(case["window"]["start"]),
                dt.date.fromisoformat(case["window"]["end"])),
        })
    return cases


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


def _ensure_parents(ee, path: str) -> None:
    """Create any missing FOLDER ancestors of an asset id."""
    parts = path.split("/")
    for i in range(4, len(parts)):
        folder = "/".join(parts[:i])
        try:
            ee.data.getAsset(folder)
        except ee.EEException:
            ee.data.createAsset({"type": "FOLDER"}, folder)


def _ensure_collection(ee, path: str) -> None:
    try:
        info = ee.data.getAsset(path)
    except ee.EEException:
        info = None
    if not info:
        _ensure_parents(ee, path)
        ee.data.createAsset({"type": "ImageCollection"}, path)


def build_assets(ee, dry_run: bool = False) -> list:
    """Export the static clim_rain ImageCollections + terrain assets (L8;
    lead-analysis reads them):

      - static/<case_id>/clim_rain for each events/*.json case, region =
        the case aoi.bbox, one image per doy of window.start..window.end
      - static/<STATIC_DOMAIN>/clim_rain over NOW_BOUNDS, the rolling now
        window (today's doy +-35d)

    Each clim image: 'doy' property, p90/p95/p99 bands over +-30d windows
    around that month/day, stored int16 = mm*10 with image property
    scale=10 (readers divide by 10; now_tpi uses config.climatology.scale),
    ~10 km. slope (~30 m, degrees) and hand (~30 m, meters) stay float.
    Population stays npz-only — it is not in the analysis asset list.
    Returns the export tasks started."""
    region = ee.Geometry.Rectangle(list(NOW_BOUNDS))
    tasks = []

    def _export(image, asset_id, export_region, scale, description):
        task = ee.batch.Export.image.toAsset(
            image, description=description, assetId=asset_id,
            region=export_region, scale=scale, crs="EPSG:4326",
            maxPixels=1e13)
        if dry_run:
            print(f"[assets] (dry-run) {description} -> {asset_id}")
        else:
            task.start()
            print(f"[assets] started {description} -> {asset_id}")
        tasks.append(task)
        return task

    def _export_clim(doy, coll, clim_region, domain):
        img = (_climatology_for_doy(ee, doy)
               .multiply(10).round().toInt16()
               .set("doy", doy).set("scale", 10))
        _export(img, f"{coll}/doy_{doy:03d}", clim_region,
                ASSET_CLIM_SCALE_M, f"{domain}_clim_rain_doy_{doy:03d}")

    for spec in _clim_cases():
        coll = f"{ASSET_ROOT}/static/{spec['id']}/clim_rain"
        if not dry_run:
            _ensure_collection(ee, coll)
        case_region = ee.Geometry.Rectangle(list(spec["bbox"]))
        for doy in spec["doys"]:
            _export_clim(doy, coll, case_region, spec["id"])

    coll = _asset_path("clim_rain")
    if not dry_run:
        _ensure_collection(ee, coll)
    for doy in sorted(_doys_around(dt.date.today(), NOW_PAD_DAYS)):
        _export_clim(doy, coll, region, STATIC_DOMAIN)

    _, _, slope, hand, _pop = _terrain(ee)
    _export(slope, _asset_path("slope"), region,
            ASSET_TERRAIN_SCALE_M, "slope")
    _export(hand, _asset_path("hand"), region,
            ASSET_TERRAIN_SCALE_M, "hand")
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
