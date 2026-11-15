"""REPLAY v2 doc crops: render the same TPI band from v1 and v2 COGs around
Porto Alegre / the Guaiba lagoon (EPSG:3857 PNGs under docs/img/) so the
10 km-step removal and the permanent-water mask are visible.

    PYTHONPATH=.:pipeline .venv/bin/python tools/replay_v2_png.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import from_bounds

REPO = Path(__file__).resolve().parents[1]
for _p in (str(REPO), str(REPO / "pipeline")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from live.render import layer_png_3857  # noqa: E402
from live.tpi import classify  # noqa: E402

# Porto Alegre + Guaiba lagoon + lower Taquari mouth, EPSG:4326.
CROP = (-51.65, -30.45, -50.75, -29.75)   # w, s, e, n
CASE = "rs_2024"
REF = "20240502"
HAZARD = "flood"


def crop_png(cog_path: Path, band: int, out: Path) -> dict:
    with rasterio.open(cog_path) as src:
        win = from_bounds(*CROP, transform=src.transform)
        win = win.round_offsets().round_lengths()
        arr = src.read(band, window=win).astype(np.float64)
        tr = src.window_transform(win)
        valid = np.isfinite(arr)
    cls = classify(arr)              # 0..3, NaN -> anything then masked
    cls = np.where(valid, cls, 0).astype(np.uint8)
    png, bounds = layer_png_3857(cls, tr, valid=valid)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(png)
    return {"png": str(out), "bounds3857": bounds, "shape": list(arr.shape)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-root", type=Path, default=REPO / "data")
    ap.add_argument("--img-dir", type=Path, default=REPO / "docs/img")
    ap.add_argument("--band-date", default=REF,
                    help="YYYYMMDD band of the observed series to render")
    args = ap.parse_args(argv)
    infos = {}
    for tag in ("", "_v2"):
        p = (args.data_root / CASE / "cog"
             / f"{CASE}_tpi_{HAZARD}_{REF}{tag}.tif")
        with rasterio.open(p) as src:
            band = list(src.descriptions).index(args.band_date) + 1
        name = f"replay_v2_{CASE}_{HAZARD}_{args.band_date}{tag or '_v1'}.png"
        infos[tag or "_v1"] = crop_png(p, band, args.img_dir / name)
    for k, v in infos.items():
        print(k, v["png"], v["shape"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
