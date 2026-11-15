"""One-off: NASADEM 1-arc-second mosaic -> boolean slope >= 15 deg mask (Now bbox).

Input: data/now/static/nasadem/nasadem.tif (EPSG:4326, 1", int16, gitignored,
built by tools/fetch_static_now.py). Slope is computed in metres per row
(dx = res * 111320 * cos(lat), dy = res * 111320) with central differences,
block by block with a 1-row halo, so the full 1.5 G-pixel mosaic never sits
in memory. Output: uint8 COG, 1 = slope >= threshold, 0 = flatter, 255 = no
DEM; served from the static-v1 Release (gitignored locally).

    PYTHONPATH=.:pipeline .venv/bin/python tools/make_steep_mask.py \
        [--dem data/now/static/nasadem/nasadem.tif] \
        [--out data/now/static/nasadem/steep15_1as.tif] [--deg 15]
"""
from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import Window

REPO = Path(__file__).resolve().parents[1]
M_PER_DEG = 111320.0
BLOCK = 1024


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dem", type=Path, default=REPO / "data/now/static/nasadem/nasadem.tif")
    ap.add_argument("--out", type=Path, default=REPO / "data/now/static/nasadem/steep15_1as.tif")
    ap.add_argument("--deg", type=float, default=15.0)
    args = ap.parse_args(argv)
    tan_t = math.tan(math.radians(args.deg))
    tmp = args.out.with_suffix(".tmp.tif")
    with rasterio.open(args.dem) as src:
        H, W = src.height, src.width
        t = src.transform
        nod = src.nodata
        prof = {"driver": "GTiff", "dtype": "uint8", "count": 1, "height": H, "width": W,
                "crs": src.crs, "transform": t, "nodata": 255, "tiled": True,
                "blockxsize": 512, "blockysize": 512, "compress": "deflate", "predictor": 2}
        n_steep = n_valid = 0
        with rasterio.open(tmp, "w", **prof) as dst:
            for r0 in range(0, H, BLOCK):
                r1 = min(H, r0 + BLOCK)
                a0, a1 = max(0, r0 - 1), min(H, r1 + 1)
                z = src.read(1, window=Window(0, a0, W, a1 - a0)).astype(np.float32)
                bad = (z == nod) if nod is not None else np.zeros(z.shape, bool)
                z[bad] = np.nan
                lat = t.f + (np.arange(a0, a1) + 0.5) * t.e
                dx = abs(t.a) * M_PER_DEG * np.cos(np.radians(lat))[:, None]
                dy = abs(t.e) * M_PER_DEG
                gy, gx = np.gradient(z, axis=(0, 1))
                grad = np.hypot(gx / dx, gy / dy)
                core = slice(r0 - a0, r0 - a0 + (r1 - r0))
                g = grad[core]
                out = np.where(np.isfinite(g), (g >= tan_t).astype(np.uint8), 255).astype(np.uint8)
                dst.write(out, 1, window=Window(0, r0, W, r1 - r0))
                n_valid += int((out != 255).sum())
                n_steep += int((out == 1).sum())
    # COG copy (overviews not needed: always read at full res per tile)
    with rasterio.open(tmp) as s:
        prof = s.profile | {"driver": "COG", "compress": "deflate", "predictor": 2}
        prof.pop("tiled", None), prof.pop("blockxsize", None), prof.pop("blockysize", None)
        with rasterio.open(args.out, "w", **prof) as d:
            for _, win in s.block_windows(1):
                d.write(s.read(1, window=win), 1, window=win)
    tmp.unlink()
    print(f"{args.out}: steep {n_steep / max(n_valid, 1):.4f} of {n_valid} valid px")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
