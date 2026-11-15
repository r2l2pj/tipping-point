"""One-off static download for the Modo Agora domain (no GEE at runtime).

bbox: [-58.0, -34.0, -48.0, -22.5]  (S Brazil, domains.now)

Datasets:
  1. NASADEM_HGT.001 tiles  -> data/now/static/nasadem/
  2. MCD12Q1.061 (latest year) -> data/now/static/mcd12q1/
  3. GPW v4.11 pop count 2020, 30 arc-sec -> data/now/static/gpw/

Auth: EARTHDATA_TOKEN bearer from .env. SEDAC's own host is unreachable from
this network; the CMR granule record exposes a cloud mirror under
data.earthdata.nasa.gov which works with the same token.
"""
import datetime as dt
import json
import os
import sys
import zipfile
from pathlib import Path

import numpy as np
import rasterio
import requests
from rasterio.crs import CRS
from rasterio.io import MemoryFile
from rasterio.merge import merge
from rasterio.warp import Resampling, reproject, transform_bounds

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data/now/static"
BBOX = (-58.0, -34.0, -48.0, -22.5)  # W, S, E, N
NOW = dt.datetime.now(dt.timezone.utc).isoformat()

TOKEN = None
for line in open(ROOT / ".env"):
    if line.startswith("EARTHDATA_TOKEN="):
        TOKEN = line.split("=", 1)[1].strip().strip("\"'")
assert TOKEN, "EARTHDATA_TOKEN missing in .env"
os.environ["EARTHDATA_TOKEN"] = TOKEN
HDR = {"Authorization": f"Bearer {TOKEN}"}


def dl(url: str, dest: Path) -> None:
    if dest.exists() and dest.stat().st_size > 0:
        return
    with requests.get(url, headers=HDR, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)


def write_manifest(d: Path, m: dict) -> None:
    m["downloaded_at"] = NOW
    (d / "manifest.json").write_text(json.dumps(m, indent=2) + "\n")
    print("manifest:", d / "manifest.json")


# ---------------------------------------------------------------- NASADEM
def nasadem() -> None:
    import earthaccess

    earthaccess.login(strategy="environment")
    d = OUT / "nasadem"
    res = earthaccess.search_data(
        short_name="NASADEM_HGT", bounding_box=BBOX, count=300)
    print("nasadem granules:", len(res))
    links = sorted({g.data_links()[0] for g in res})
    zdir = d / "zip"
    zdir.mkdir(parents=True, exist_ok=True)
    names = []
    for u in links:
        z = zdir / u.rsplit("/", 1)[-1]
        dl(u, z)
        names.append(z.stem.replace("NASADEM_HGT_", ""))
    # extract .hgt
    hdir = d / "hgt"
    hdir.mkdir(exist_ok=True)
    hgts = []
    for z in sorted(zdir.glob("*.zip")):
        with zipfile.ZipFile(z) as zf:
            for n in zf.namelist():
                if n.endswith(".hgt"):
                    tgt = hdir / Path(n).name
                    if not tgt.exists():
                        tgt.write_bytes(zf.read(n))
                    hgts.append(tgt)
    print("hgt tiles:", len(hgts))
    # mosaic -> COG cropped to bbox
    srcs = [rasterio.open(str(p)) for p in hgts]
    arr, tr = merge(srcs, bounds=BBOX, nodata=-32768)
    profile = srcs[0].profile.copy()
    for s in srcs:
        s.close()
    profile.update(driver="GTiff", height=arr.shape[1], width=arr.shape[2],
                   transform=tr, nodata=-32768, compress="deflate",
                   tiled=True, blockxsize=512, blockysize=512, BIGTIFF="YES")
    cog = d / "nasadem.tif"
    with rasterio.open(cog, "w", **profile) as dst:
        dst.write(arr)
    # coverage check: every 1-deg cell overlapping bbox must have a tile
    lons = range(int(np.floor(BBOX[0])), int(np.ceil(BBOX[2])))
    lats = range(int(np.floor(BBOX[1])), int(np.ceil(BBOX[3])))
    have = set()
    for n in names:
        hem = n[0]; lat = int(n[1:3]); lw = n[3]; lon = int(n[4:7])
        have.add((-lon if lw == "w" else lon, -lat if hem == "s" else lat))
    missing = [(lo, la) for la in lats for lo in lons if (lo, la) not in have]
    print("missing 1deg cells:", missing)
    write_manifest(d, {
        "product": "NASADEM_HGT.001",
        "source": "LP DAAC (earthaccess, EARTHDATA_TOKEN bearer)",
        "granule_pattern": "https://data.lpdaac.earthdatacloud.nasa.gov/lp-prod-protected/NASADEM_HGT.001/<tile>/<tile>.zip",
        "tiles": names,
        "missing_1deg_cells_in_bbox": missing,
        "resolution": "1 arc-second (~30 m), 1x1-degree .hgt tiles",
        "bbox": BBOX,
        "mosaic": "nasadem.tif (COG, cropped to bbox, nodata=-32768)",
        "citation": "NASA JPL (2020). NASADEM Merged DEM Global 1 arc second V001. NASA EOSDIS LP DAAC. https://doi.org/10.5067/MEaSUREs/NASADEM/NASADEM_HGT.001",
    })


# ---------------------------------------------------------------- MCD12Q1
def mcd12q1() -> None:
    import earthaccess

    earthaccess.login(strategy="environment")
    d = OUT / "mcd12q1"
    d.mkdir(parents=True, exist_ok=True)
    res = earthaccess.search_data(
        short_name="MCD12Q1", version="061", bounding_box=BBOX,
        temporal=("2025-01-01", "2026-12-31"), count=100)
    res = [g for g in res if g["umm"]["GranuleUR"].split(".")[1] == "A2025001"]
    print("mcd12q1 2025 tiles:", [g["umm"]["GranuleUR"] for g in res])
    files = []
    for g in res:
        for u in g.data_links():
            if u.endswith(".hdf"):
                f = d / u.rsplit("/", 1)[-1]
                dl(u, f)
                files.append(f.name)
    write_manifest(d, {
        "product": "MCD12Q1.061 (MODIS Terra+Aqua Land Cover Type, yearly L3 500 m)",
        "year": 2025,
        "source": "LP DAAC (earthaccess, EARTHDATA_TOKEN bearer)",
        "files": files,
        "resolution": "500 m, sinusoidal grid, IGBP (LC_Type1) etc.",
        "bbox": BBOX,
        "citation": "Friedl, M., Sulla-Menashe, D. (2022). MODIS/Terra+Aqua Land Cover Type Yearly L3 Global 500m SIN Grid V061. NASA EOSDIS LP DAAC. https://doi.org/10.5067/MODIS/MCD12Q1.061",
    })


# ---------------------------------------------------------------- GPW
def gpw() -> None:
    d = OUT / "gpw"
    d.mkdir(parents=True, exist_ok=True)
    url = ("https://data.earthdata.nasa.gov/nasa-earth/human-dimensions/"
           "sedac-root/downloads/data/gpw-v4/gpw-v4-population-count-rev11/"
           "gpw-v4-population-count-rev11_2020_30_sec_tif.zip")
    z = d / "gpw_2020_30sec_global.zip"
    dl(url, z)
    print("zip size MB:", z.stat().st_size / 1e6)
    with zipfile.ZipFile(z) as zf:
        tifs = [n for n in zf.namelist() if n.endswith(".tif")]
        print("tif members:", tifs)
        data = zf.read(tifs[0])
    with MemoryFile(data) as mf, mf.open() as src:
        print("native:", src.width, src.height, src.res, src.crs,
              "dtype", src.dtypes, "nodata", src.nodata)
        w, s, e, n = transform_bounds(CRS.from_epsg(4326), src.crs, *BBOX)
        win = src.window(w, s, e, n).round_offsets().round_lengths()
        tr = src.window_transform(win)
        arr = src.read(1, window=win)
        profile = src.profile.copy()
    profile.update(driver="GTiff", height=arr.shape[0], width=arr.shape[1],
                   transform=tr, compress="deflate", tiled=True,
                   blockxsize=512, blockysize=512)
    cog = d / "gpw_popcount_2020_30sec.tif"
    with rasterio.open(cog, "w", **profile) as dst:
        dst.write(arr, 1)
    valid = arr[arr != (profile.get("nodata") or -3.4e38)]
    valid = valid[np.isfinite(valid) & (valid >= 0)]
    print("total pop in bbox:", float(valid.sum()))
    z.unlink()  # keep cropped COG only
    write_manifest(d, {
        "product": "GPW v4.11 Population Count, 2020, 30 arc-second",
        "cmr_concept": "C3540918004-ESDIS (CIESIN_SEDAC_GPWv4_POPCOUNT_R11)",
        "source_url": url,
        "note": ("sedac.ciesin.columbia.edu unreachable from this network; "
                 "used the CMR-registered cloud mirror on "
                 "data.earthdata.nasa.gov with EARTHDATA_TOKEN bearer."),
        "resolution": "30 arc-second (~1 km at equator), native EPSG:4326, "
                      "units = persons per grid cell",
        "bbox": BBOX,
        "output": "gpw_popcount_2020_30sec.tif (COG, cropped to bbox)",
        "citation": ("CIESIN - Columbia University. 2018. Gridded Population "
                     "of the World, Version 4 (GPWv4): Population Count, "
                     "Revision 11. NASA SEDAC. "
                     "https://doi.org/10.7927/H4JW8BX5"),
        "license": "CC-BY-4.0 (SEDAC)",
    })


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which in ("all", "nasadem"):
        nasadem()
    if which in ("all", "mcd12q1"):
        mcd12q1()
    if which in ("all", "gpw"):
        gpw()
