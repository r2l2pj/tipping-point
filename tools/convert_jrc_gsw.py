"""One-off: JRC Global Surface Water v1.4 (1984-2021) tiles -> Now-domain COGs.

Downloads (no GEE, no auth) the 10x10 deg occurrence + seasonality tiles
covering config.yaml domains.now.bbox from the JRC GSW download bucket, then
aggregates the 30 m pixels onto the R1 DSWx-S1 analysis grid (EPSG:4326,
0.0025 deg, north-up, origin = bbox west/north) with an AREA AVERAGE that
ignores no-data (255). A cell's value is therefore the mean occurrence (%) /
mean seasonality (months of water in the reference year) of its ~80 30 m
pixels, so a single 30 m river pixel cannot flag a ~280 m cell.

  PYTHONPATH=.:pipeline .venv/bin/python tools/convert_jrc_gsw.py \
      --raw <empty dir for the downloaded tiles> \
      [--out data/now/static/jrc_gsw]

Output (uint8, nodata 255, deflate COG): gsw_occurrence_2021.tif (0..100 %),
gsw_seasonality_2021.tif (0..12 months), plus manifest.json. The COGs are
gitignored and served from the static-v1 GitHub Release.

Citation: Pekel, J.-F., Cottam, A., Gorelick, N., Belward, A. S. (2016).
High-resolution mapping of global surface water and its long-term changes.
Nature 540, 418-422. doi:10.1038/nature20584. Data: EC JRC/Google, v1.4 (2021).
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import sys
import urllib.request
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "pipeline"))
from tp.config import now_bounds  # noqa: E402

BASE = "https://storage.googleapis.com/global-surface-water/downloads2021"
VARS = {"occurrence": "gsw_occurrence_2021.tif", "seasonality": "gsw_seasonality_2021.tif"}
RES = 0.0025
NODATA = 255


def tile_names(w, s, e, n) -> list[str]:
    """JRC tile ids ('60W_20S' = lon -60..-50, lat -20..-30) covering the bbox."""
    out = []
    for lon0 in range(int(math.floor(w / 10) * 10), int(math.ceil(e / 10) * 10), 10):
        for lat0 in range(int(math.ceil(n / 10) * 10), int(math.floor(s / 10) * 10), -10):
            lon = f"{abs(lon0)}{'W' if lon0 < 0 else 'E'}"
            lat = f"{abs(lat0)}{'S' if lat0 < 0 else 'N'}"
            out.append(f"{lon}_{lat}")
    return out


def fetch(var: str, tile: str, raw: Path) -> Path:
    name = f"{var}_{tile}v1_4_2021.tif"
    path = raw / name
    if not path.exists():
        urllib.request.urlretrieve(f"{BASE}/{var}/{name}", path)  # noqa: S310 - fixed https URL
    return path


def aggregate(tiles: list[Path], w, s, e, n) -> np.ndarray:
    h, wd = round((n - s) / RES), round((e - w) / RES)
    dst_t = from_origin(w, n, RES, RES)
    acc = np.zeros((h, wd), np.float64)
    wsum = np.zeros((h, wd), np.float64)
    for path in tiles:
        with rasterio.open(path) as src:
            val = np.full((h, wd), np.nan, np.float32)
            # average over valid pixels only (255 = no data)
            reproject(rasterio.band(src, 1), val, src_nodata=NODATA, dst_nodata=np.nan,
                      dst_transform=dst_t, dst_crs="EPSG:4326", resampling=Resampling.average)
        ok = np.isfinite(val)
        acc[ok] += val[ok]          # tiles do not overlap: each cell gets one tile
        wsum[ok] += 1
    out = np.full((h, wd), NODATA, np.uint8)
    good = wsum > 0
    out[good] = np.clip(np.rint(acc[good] / wsum[good]), 0, 254).astype(np.uint8)
    return out, dst_t


def write(path: Path, arr: np.ndarray, transform, desc: str) -> None:
    profile = {"driver": "COG", "dtype": "uint8", "count": 1, "height": arr.shape[0],
               "width": arr.shape[1], "crs": "EPSG:4326", "transform": transform,
               "nodata": NODATA, "compress": "deflate"}
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(arr, 1)
        dst.set_band_description(1, desc)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--raw", type=Path, required=True, help="directory for the downloaded tiles")
    ap.add_argument("--out", type=Path, default=REPO / "data/now/static/jrc_gsw")
    args = ap.parse_args(argv)
    w, s, e, n = now_bounds()
    args.raw.mkdir(parents=True, exist_ok=True)
    args.out.mkdir(parents=True, exist_ok=True)
    tiles = tile_names(w, s, e, n)
    files = {}
    for var, out_name in VARS.items():
        paths = [fetch(var, t, args.raw) for t in tiles]
        arr, transform = aggregate(paths, w, s, e, n)
        out = args.out / out_name
        write(out, arr, transform, f"JRC GSW v1.4 {var} (cell mean of 30 m pixels)")
        files[out_name] = {"sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
                           "valid_frac": round(float((arr != NODATA).mean()), 4),
                           "tiles": [p.name for p in paths]}
        print(f"{out_name}: {arr.shape}, valid {files[out_name]['valid_frac']}")
    manifest = {
        "product": "JRC Global Surface Water v1.4 (1984-2021): occurrence + seasonality",
        "source_url": f"{BASE}/<var>/<var>_<tile>v1_4_2021.tif",
        "bbox": [w, s, e, n],
        "grid": f"EPSG:4326, {RES} deg, origin bbox west/north (same as R1 DSWx-S1 grid)",
        "aggregation": "area average of valid 30 m pixels (255 = no data excluded)",
        "units": {"gsw_occurrence_2021.tif": "percent of valid observations with water, 0-100",
                  "gsw_seasonality_2021.tif": "months of water in the 2021 reference year, 0-12"},
        "files": files,
        "citation": "Pekel et al. (2016) Nature 540:418-422, doi:10.1038/nature20584; EC JRC/Google",
        "license": "CC-BY-4.0 (Copernicus Programme)",
        "created_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
    }
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
