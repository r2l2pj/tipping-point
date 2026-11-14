"""Slope GeoTIFF (float32, degrees) from a DEM — Horn's method, geodesic-aware.

Usage: uv run python pipeline/scripts/00_terrain.py --dem data/raw/aux/dem.tif \
    [--out data/raw/aux/slope.tif] [--event rs-2024-05]

--dem accepts a local path or any GDAL-readable URL (https://, /vsicurl/...).
--event/--aoi clips the output to the event's AOI bbox (events/<id>.json),
reading one extra pixel of context so edge slopes stay honest.
data/raw/ is gitignored — the COG is written, never committed.
"""
import argparse
from pathlib import Path

from tp.terrain import slope_deg


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dem", required=True, help="DEM path or rasterio-readable URL")
    ap.add_argument("--out", type=Path, default=Path("data/raw/aux/slope.tif"))
    ap.add_argument("--event", "--aoi", dest="event", default=None,
                    help="event id; clip output to its AOI bbox (events/<id>.json)")
    args = ap.parse_args()

    import numpy as np
    import rasterio
    from rasterio.io import MemoryFile
    from rasterio.windows import Window, from_bounds

    with rasterio.open(args.dem) as ds:
        if args.event:
            from tp.events import load_event

            bbox = load_event(args.event)["aoi"]["bbox"]
            win = from_bounds(*bbox, transform=ds.transform)
            win = win.round_offsets().round_lengths()
            # one pixel of Horn context around the AOI, clamped to the raster
            col0 = max(int(win.col_off) - 1, 0)
            row0 = max(int(win.row_off) - 1, 0)
            ctx = Window(
                col_off=col0, row_off=row0,
                width=min(int(win.col_off + win.width) + 1, ds.width) - col0,
                height=min(int(win.row_off + win.height) + 1, ds.height) - row0,
            )
            profile = dict(ds.profile)
            profile.update(height=int(ctx.height), width=int(ctx.width),
                           transform=ds.window_transform(ctx))
            with MemoryFile() as mf:
                with mf.open(**profile) as w:
                    w.write(ds.read(1, window=ctx), 1)
                with mf.open() as mem:
                    slope, out_profile = slope_deg(mem)
            r0, c0 = int(win.row_off) - row0, int(win.col_off) - col0
            slope = slope[r0 : r0 + int(win.height), c0 : c0 + int(win.width)]
            out_profile.update(height=int(win.height), width=int(win.width),
                               transform=ds.window_transform(win))
        else:
            slope, out_profile = slope_deg(ds)

    out_profile.update(driver="COG", dtype="float32", nodata=np.nan, count=1)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(args.out, "w", **out_profile) as dst:
        dst.write(slope.astype(np.float32), 1)
    print(f"wrote {args.out} {slope.shape[0]}x{slope.shape[1]} float32 degrees, "
          f"max {float(np.nanmax(slope)):.1f} deg")


if __name__ == "__main__":
    main()
