"""L35-U2 (A) via Earth Engine: fill the IMERG per-day cache that
tools/upstream_rain.py reads, from NASA/GPM_L3/IMERG_V07 (Final, half-hourly).

Why: GES DISC OPeNDAP throttled to ~4 days/min (3,700 days left); GEE serves
the same IMERG V07 Final record. Daily mm = sum of the 48 half-hourly
'precipitation' (mm/h) steps x 0.5 — the factor build_static / the case
climatology use. Pixels are read on the exact IMERG 0.1 deg grid (edges at
multiples of 0.1 from -180/-90, so no resampling) for the same index window
as upstream_rain.imerg_index_window, and saved in the same layout as
imerg_vs_gauge.parse_ascii: float32 [i=lon asc, j=lat asc], one
<YYYYMMDD>.npy per day.

    PYTHONPATH=.:pipeline python tools/upstream_rain_gee.py --case rs_2024 \
        [--cache-name imerg_cache_gee] [--chunk 40] [--compare-dir imerg_cache]

--compare-dir reports max/mean abs diff vs an existing GES DISC cache on the
overlapping days (sanity check that both sources agree).
Needs GEE_PROJECT + GCP_SA_KEY / GOOGLE_APPLICATION_CREDENTIALS.
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
for _p in (str(REPO), str(REPO / "pipeline")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tools import upstream_rain as ur

COL = "NASA/GPM_L3/IMERG_V07"


def day_stack(ee, days: list[dt.date]):
    col = ee.ImageCollection(COL).select("precipitation")
    imgs = []
    for d in days:
        a = d.isoformat()
        b = (d + dt.timedelta(days=1)).isoformat()
        imgs.append(col.filterDate(a, b).sum().multiply(0.5)
                    .rename(f"d{d:%Y%m%d}").toFloat())
    return ee.Image.cat(imgs)


def fetch_chunk(ee, days, i0, i1, j0, j1, retries=4):
    w, h = i1 - i0 + 1, j1 - j0 + 1
    lon0 = -180.0 + i0 * 0.1
    lat_top = -90.0 + (j1 + 1) * 0.1
    req = {
        "expression": day_stack(ee, days),
        "fileFormat": "NPY",
        "grid": {
            "dimensions": {"width": w, "height": h},
            "affineTransform": {"scaleX": 0.1, "shearX": 0, "translateX": lon0,
                                "shearY": 0, "scaleY": -0.1, "translateY": lat_top},
            "crsCode": "EPSG:4326",
        },
    }
    err = None
    for k in range(retries):
        try:
            raw = ee.data.computePixels(req)
            arr = np.load(io.BytesIO(raw)) if isinstance(raw, (bytes, bytearray)) else raw
            return arr  # structured [h, w] with one field per band
        except Exception as e:  # noqa: BLE001
            err = e
            time.sleep(2 * (k + 1))
    raise RuntimeError(f"computePixels failed: {err}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--case", required=True, choices=sorted(ur.BOXES))
    ap.add_argument("--cache-name", default="imerg_cache_gee")
    ap.add_argument("--chunk", type=int, default=40)
    ap.add_argument("--compare-dir", default=None)
    args = ap.parse_args(argv)

    from gee.common import ee_init
    ee_init()
    import ee

    i0, i1, j0, j1 = ur.imerg_index_window(ur.BOXES[args.case])
    cache = ur.OUT_ROOT / args.case / args.cache_name
    cache.mkdir(parents=True, exist_ok=True)
    days = [d for d in ur.needed_days(args.case)
            if not (cache / f"{d:%Y%m%d}.npy").exists()]
    print(f"[{args.case}] {len(days)} days to fetch, grid {i1 - i0 + 1}x{j1 - j0 + 1}",
          flush=True)
    t0 = time.time()
    for s in range(0, len(days), args.chunk):
        chunk = days[s:s + args.chunk]
        arr = fetch_chunk(ee, chunk, i0, i1, j0, j1)
        for d in chunk:
            a = np.asarray(arr[f"d{d:%Y%m%d}"], dtype=np.float32)  # [lat desc, lon asc]
            np.save(cache / f"{d:%Y%m%d}.npy", a[::-1, :].T)       # -> [lon asc, lat asc]
        print(f"  {s + len(chunk)}/{len(days)}  {time.time() - t0:.0f}s", flush=True)

    if args.compare_dir:
        ref = ur.OUT_ROOT / args.case / args.compare_dir
        diffs = []
        for f in sorted(ref.glob("*.npy")):
            g = cache / f.name
            if g.exists():
                a, b = np.load(f), np.load(g)
                m = np.isfinite(a) & np.isfinite(b) & (a >= 0) & (b >= 0)
                diffs.append((f.stem, float(np.abs(a - b)[m].max()),
                              float(np.abs(a - b)[m].mean()), float(a[m].mean()),
                              float(b[m].mean())))
        if diffs:
            mx = max(d[1] for d in diffs)
            mean = float(np.mean([d[2] for d in diffs]))
            ma = float(np.mean([d[3] for d in diffs]))
            mb = float(np.mean([d[4] for d in diffs]))
            print(f"compare vs {args.compare_dir}: {len(diffs)} days, max abs diff "
                  f"{mx:.3f} mm, mean abs diff {mean:.4f} mm, mean gesdisc {ma:.3f} "
                  f"vs gee {mb:.3f} mm/day", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
