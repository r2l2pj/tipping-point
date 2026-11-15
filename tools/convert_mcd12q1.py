"""One-off: MCD12Q1 2025 HDF4 tiles -> one LC_Type1 (IGBP) COG for the Now domain.

The venv's GDAL (rasterio wheel) has no HDF4 driver, so this runs in a
throwaway env with pyhdf — NOT a project dependency:

  uv run --no-project --with pyhdf --with rasterio --with numpy --with pyyaml \
      python tools/convert_mcd12q1.py \
      [--src ~/code/r2l2pj/tipping-point-data/data/now/static/mcd12q1] \
      [--out data/now/static/mcd12q1/lc_type1_2025.tif]

(`uvx --with pyhdf --with rasterio --with numpy --with pyyaml python tools/convert_mcd12q1.py`
is equivalent.)

Output: EPSG:4326, 0.005 deg, nearest, clipped to config.yaml domains.now.bbox
(read via tp.config.now_bounds), uint8, nodata 255, deflate COG. IGBP LC_Type1 classes:
11 permanent wetlands, 12 croplands, 13 urban, 14 cropland/natural mosaic,
17 water bodies (255 = fill/unclassified).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import numpy as np
import rasterio
from pyhdf.SD import SD, SDC
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
from tp.config import now_bounds

RES = 0.005
# MODIS sinusoidal grid (500 m products: 2400 x 2400 px per tile)
SINU = "+proj=sinu +lon_0=0 +x_0=0 +y_0=0 +R=6371007.181 +units=m +no_defs"
TILE_M = 1111950.5197665233
X0, Y0 = -20015109.354, 10007554.677
NPX = 2400
TILE_RE = re.compile(r"\.h(\d{2})v(\d{2})\.")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path,
                    default=Path.home() / "code/r2l2pj/tipping-point-data/data/now/static/mcd12q1")
    ap.add_argument("--out", type=Path, default=Path("data/now/static/mcd12q1/lc_type1_2025.tif"))
    args = ap.parse_args()

    files = sorted(args.src.glob("MCD12Q1.A2025001.*.hdf"))
    if not files:
        raise SystemExit(f"no MCD12Q1 2025 HDFs in {args.src}")
    hv = [tuple(int(x) for x in TILE_RE.search(f.name).groups()) for f in files]
    h0, h1 = min(h for h, _ in hv), max(h for h, _ in hv)
    v0, v1 = min(v for _, v in hv), max(v for _, v in hv)
    mosaic = np.full(((v1 - v0 + 1) * NPX, (h1 - h0 + 1) * NPX), 255, np.uint8)
    for f, (h, v) in zip(files, hv, strict=True):
        sd = SD(str(f), SDC.READ)
        lc = sd.select("LC_Type1").get().astype(np.uint8)
        sd.end()
        r, c = (v - v0) * NPX, (h - h0) * NPX
        mosaic[r:r + NPX, c:c + NPX] = lc
        print(f"{f.name}: h{h:02d}v{v:02d} classes {np.unique(lc).tolist()}")
    src_tf = from_origin(X0 + h0 * TILE_M, Y0 - v0 * TILE_M, TILE_M / NPX, TILE_M / NPX)

    w, s, e, n = now_bounds()
    shape = (round((n - s) / RES), round((e - w) / RES))
    dst_tf = from_origin(w, n, RES, RES)
    out = np.full(shape, 255, np.uint8)
    reproject(mosaic, out, src_transform=src_tf, src_crs=SINU, src_nodata=255,
              dst_transform=dst_tf, dst_crs="EPSG:4326", dst_nodata=255,
              resampling=Resampling.nearest)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(args.out, "w", driver="COG", height=shape[0], width=shape[1], count=1,
                       dtype="uint8", crs="EPSG:4326", transform=dst_tf, nodata=255,
                       compress="DEFLATE") as dst:
        dst.write(out, 1)
        dst.update_tags(SOURCE="MCD12Q1.061 2025 LC_Type1 (IGBP)", FILES=",".join(f.name for f in files),
                        DOI="10.5067/MODIS/MCD12Q1.061")
    print(f"wrote {args.out} {shape} {args.out.stat().st_size} bytes")


if __name__ == "__main__":
    main()
