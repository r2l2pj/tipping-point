"""One-time build of static/ for Modo Agora (doc step 1).

Computes over the south-Brazil grid (~1 km):
  - monthly rain climatology p90/p95/p99 of 3-day IMERG accumulation (2001-2023,
    same windows as gee/01_tpi_index.js: +-30 days around mid-month, step 3)
  - terrain scores T_slide / T_flood from NASADEM slope + HAND proxy
    (dem - focalMin(1500 m), matching the GEE script)
  - GHSL population count, used for people_exposed in summary.json

Run once with credentials in env:
    GCP_SA_KEY=<service-account json> GEE_PROJECT=<project> \
        uv run python live/build_static.py

Output: static/sul_static.npz with keys p90/p95/p99 (12,H,W), t_slide,
t_flood, slope_deg, hand_m, pop, lats, lons.
"""
from __future__ import annotations

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
    HAND_PROXY_RADIUS_M,
    NOW_BOUNDS,
    STATIC_DIR,
    STATIC_NPZ,
)

YEARS = range(2001, 2024)
OFFSETS = range(-30, 31, 3)


def _ee_init():
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
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        name = next(n for n in z.namelist() if n.endswith(".tif"))
        with rasterio.open(io.BytesIO(z.read(name))) as src:
            return src.read(1).astype(np.float64)


def _climatology_for_month(ee, month: int):
    imerg = ee.ImageCollection("NASA/GPM_L3/IMERG_V07").select("precipitation")

    def accum(end):
        return imerg.filterDate(end.advance(-3, "day"), end).sum().multiply(0.5)

    samples = []
    for y in YEARS:
        for o in OFFSETS:
            end = ee.Date.fromYMD(y, month, 15).advance(o, "day")
            samples.append(accum(end))
    pct = ee.ImageCollection(samples).reduce(ee.Reducer.percentile([90, 95, 99]))
    return pct.rename(["p90", "p95", "p99"])


def _terrain(ee):
    dem = ee.Image("NASA/NASADEM_HGT/001").select("elevation")
    slope = ee.Terrain.slope(dem)
    hand = dem.subtract(dem.focalMin(radius=HAND_PROXY_RADIUS_M, units="meters"))
    t_slide = slope.unitScale(15, 30).clamp(0, 1)
    t_flood = ee.Image(1).subtract(hand.unitScale(5, 15)).clamp(0, 1).where(slope.gt(5), 0)
    pop = ee.Image("JRC/GHSL/P2023A/GHS_POP/2020").select("population_count")
    return t_slide.rename("t_slide"), t_flood.rename("t_flood"), slope.rename("slope_deg"), \
        hand.rename("hand_m"), pop.rename("pop")


def main() -> None:
    from live.grid import dst_lats_lons

    ee = _ee_init()
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

    for name, img in zip(
            ("t_slide", "t_flood", "slope_deg", "hand_m", "pop"), _terrain(ee)):
        out[name] = _download_band(ee, img, name)
        print(f"terrain {name} done")

    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    path = STATIC_DIR / STATIC_NPZ
    np.savez(path, **out)
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
