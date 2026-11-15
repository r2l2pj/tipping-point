"""One-off: 90 m HAND proxy for the Now domain (docs/PLAN.md T enchente rule).

HAND_proxy = z - min(z within r), r = config.yaml thresholds.hand_proxy_radius_m
(1500 m), z = NASADEM 3-arc-second elevation (lead-ml's static90 tiles: 3x3
block mean of NASADEM_HGT 001 1"). Same formula as the doc's GEE reference
(dem.subtract(dem.focalMin({radius: r, units: 'meters'}))).

The circular focal minimum is the minimum over the union of K axis-aligned
rectangles inscribed in the disk (each rectangle min is separable, O(N)); with
K = 6 the union covers ~94 % of the disk and never extends outside it. The longitude radius in pixels
follows cos(lat) per 1-degree band; each band is processed with a halo of the
latitude radius, so tile seams do not bias the minimum.

    PYTHONPATH=.:pipeline .venv/bin/python tools/make_hand90.py \
        --dem ~/.cache/tipping-point-ml/static90/static90.vrt \
        [--out data/static/south_brazil/terrain/hand_90m.tif]

Output: float32 COG (m, rounded to 0.1 m, nodata NaN), EPSG:4326, 3",
bbox = config.yaml domains.now.bbox. Served from the static-v1 Release.
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin
from rasterio.windows import from_bounds
from scipy.ndimage import minimum_filter1d

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "pipeline"))
from tp.config import now_bounds  # noqa: E402

M_PER_DEG = 111320.0
K_RECT = 6


def disk_rectangles(ry: float, rx: float, k: int = K_RECT) -> list[tuple[int, int]]:
    """Half-sizes (hy, hx) in px of k rectangles whose union approximates an
    ellipse with semi-axes (ry, rx): heights at fractions of ry, widths from
    the ellipse equation."""
    out = []
    for i in range(1, k + 1):
        fy = math.cos(math.pi / 2 * i / (k + 1))         # 0.95, 0.81, 0.59, 0.31 for k=4
        hy = fy * ry
        hx = rx * math.sqrt(max(0.0, 1 - (hy / ry) ** 2))
        out.append((max(0, int(math.floor(hy))), max(0, int(math.floor(hx)))))
    return out


def focal_min_ellipse(z: np.ndarray, ry: float, rx: float) -> np.ndarray:
    """Approximate circular focal min (NaN-safe: NaN treated as +inf)."""
    zz = np.where(np.isfinite(z), z, np.inf).astype(np.float32)
    best = None
    for hy, hx in disk_rectangles(ry, rx):
        m = minimum_filter1d(zz, size=2 * hy + 1, axis=0, mode="nearest") if hy else zz
        m = minimum_filter1d(m, size=2 * hx + 1, axis=1, mode="nearest") if hx else m
        best = m if best is None else np.minimum(best, m)
    return best


def main(argv=None) -> int:
    import yaml

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dem", type=Path, required=True, help="3-arc-second DEM (lead-ml static90.vrt, band 1)")
    ap.add_argument("--out", type=Path, default=REPO / "data/static/south_brazil/terrain/hand_90m.tif")
    args = ap.parse_args(argv)
    radius = float(yaml.safe_load((REPO / "config.yaml").read_text())["thresholds"]["hand_proxy_radius_m"])
    w, s, e, n = now_bounds()
    with rasterio.open(args.dem) as src:
        res = abs(src.res[0])
        W = round((e - w) / res)
        H = round((n - s) / res)
        ry = radius / (M_PER_DEG * res)
        halo = int(math.ceil(ry)) + 1
        transform = from_origin(w, n, res, res)
        prof = {"driver": "GTiff", "dtype": "float32", "count": 1, "height": H, "width": W,
                "crs": "EPSG:4326", "transform": transform, "nodata": np.nan, "tiled": True,
                "blockxsize": 512, "blockysize": 512, "compress": "deflate", "predictor": 3}
        tmp = args.out.with_suffix(".tmp.tif")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        band_rows = round(1.0 / res)                    # 1-degree latitude bands
        stats = []
        with rasterio.open(tmp, "w", **prof) as dst:
            for r0 in range(0, H, band_rows):
                r1 = min(H, r0 + band_rows)
                a0, a1 = max(0, r0 - halo), min(H, r1 + halo)
                top, bot = n - a0 * res, n - a1 * res
                win = from_bounds(w, bot, e, top, src.transform).round_offsets().round_lengths()
                z = src.read(1, window=win, boundless=True, fill_value=np.nan).astype(np.float32)
                z = z[: a1 - a0, :W]
                lat_mid = n - (r0 + r1) / 2 * res
                rx = radius / (M_PER_DEG * math.cos(math.radians(lat_mid)) * res)
                zmin = focal_min_ellipse(z, ry, rx)
                hand = (z - zmin)[r0 - a0: r0 - a0 + (r1 - r0)]
                hand = np.where(np.isfinite(hand), np.round(hand, 1), np.nan).astype(np.float32)
                dst.write(hand, 1, window=rasterio.windows.Window(0, r0, W, r1 - r0))
                v = hand[np.isfinite(hand)]
                stats.append((round(lat_mid, 2), int(v.size), float(np.median(v)) if v.size else None))
                print(f"lat {lat_mid:7.2f}: valid {v.size}, median HAND {stats[-1][2]}", flush=True)
    with rasterio.open(tmp) as s_:
        prof = s_.profile | {"driver": "COG", "compress": "deflate", "predictor": 3, "overview_resampling": "average"}
        for k in ("tiled", "blockxsize", "blockysize"):
            prof.pop(k, None)
        with rasterio.open(args.out, "w", **prof) as d:
            for _, win in s_.block_windows(1):
                d.write(s_.read(1, window=win), 1, window=win)
            d.update_tags(method=f"HAND proxy = z - focalMin(z, r={radius:.0f} m), {K_RECT}-rectangle disk",
                          dem="NASADEM_HGT 001, 3x3 block mean (3 arc-sec)", units="m")
    tmp.unlink()
    print(f"wrote {args.out} ({W}x{H}, res {res})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
