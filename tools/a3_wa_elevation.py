"""A3 (external review, L59): elevation of the WA Critical slide area, NASADEM, no GEE.

Local version of the review's Anexo item 4: km² of TPI_slide > 0.6 per elevation band
on each event day, plus the review's longitude-band proxy as a cross-check. A3 is
confirmed if more than half of the Critical area on 2025-12-10 lies above 1,000 m.

NASADEM_HGT.001 1-arc-second tiles (n46-n48, w122-w123) are fetched with the
EARTHDATA_TOKEN bearer via ml.local.sources_static into a cache dir outside the repo,
mosaicked and averaged onto the 90 m TPI grid.

    EARTHDATA_TOKEN=... PYTHONPATH=.:pipeline python tools/a3_wa_elevation.py \
        --hgt-dir ~/.cache/lead-ml/nasadem_wa [--out ml/models/a3_wa_elevation.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
for p in (str(REPO), str(REPO / "pipeline")):
    if p not in sys.path:
        sys.path.insert(0, p)

BANDS_M = [(0, 500), (500, 1000), (1000, 1500), (1500, 5000)]
LON_BANDS = {"puget_lowland_w_of_-122.0": (-180, -122.0), "foothills_-122.0_-121.7": (-122.0, -121.7),
             "high_cascades_e_of_-121.7": (-121.7, 180)}
DAYS = ("20251210", "20251211", "20251212")
CRIT, ALTO = 0.6, 0.4
KM_PER_DEG = 111.32


def elevation_on_grid(hgt_dir: Path, grid) -> np.ndarray:
    import rasterio
    from rasterio.merge import merge
    from rasterio.warp import Resampling, reproject

    from ml.local import sources_static as ss
    ss._hgt_dir = lambda: hgt_dir
    b = grid.bounds
    cells = [(lon, lat) for lat in range(int(np.floor(b.bottom)), int(np.floor(b.top)) + 1)
             for lon in range(int(np.floor(b.left)), int(np.floor(b.right)) + 1)]
    paths = [ss._ensure_hgt(c)[0] for c in cells]
    srcs = [rasterio.open(p) for p in paths if p]
    mosaic, mtr = merge(srcs)
    for s in srcs:
        s.close()
    src = np.where(mosaic[0] == -32768, np.nan, mosaic[0]).astype(np.float32)
    out = np.full(grid.shape, np.nan, np.float32)
    reproject(src, out, src_transform=mtr, src_crs="EPSG:4326", src_nodata=np.nan,
              dst_transform=grid.transform, dst_crs=grid.crs, dst_nodata=np.nan,
              resampling=Resampling.average)
    return out


def summarise(tpi: np.ndarray, elev: np.ndarray, area: np.ndarray, lon: np.ndarray, cut: float) -> dict:
    flag = np.isfinite(tpi) & (tpi > cut)
    tot = float(area[flag].sum())
    by_elev = {}
    for lo, hi in BANDS_M:
        m = flag & (elev >= lo) & (elev < hi)
        band = np.isfinite(elev) & (elev >= lo) & (elev < hi) & np.isfinite(tpi)
        by_elev[f"{lo}-{hi}m"] = {"km2": round(float(area[m].sum()), 1),
                                  "share_of_flagged_pct": round(100 * float(area[m].sum()) / tot, 1) if tot else None,
                                  "pct_of_band_flagged": round(100 * float(area[m].sum()) / float(area[band].sum()), 1)
                                  if area[band].sum() else None}
    by_lon = {}
    for k, (lo, hi) in LON_BANDS.items():
        m = flag & (lon >= lo) & (lon < hi)
        by_lon[k] = {"km2": round(float(area[m].sum()), 1),
                     "share_of_flagged_pct": round(100 * float(area[m].sum()) / tot, 1) if tot else None}
    above = flag & (elev >= 1000)
    return {"cut": cut, "flagged_km2": round(tot, 1),
            "above_1000m_pct": round(100 * float(area[above].sum()) / tot, 1) if tot else None,
            "median_elev_m": round(float(np.nanmedian(elev[flag])), 0) if tot else None,
            "by_elevation": by_elev, "by_longitude": by_lon}


def run(hgt_dir: Path, data_root: Path) -> dict:
    import rasterio
    path = data_root / "wa_2025" / "cog" / "wa_2025_tpi_slide_20251210.tif"
    with rasterio.open(path) as g:
        elev = elevation_on_grid(hgt_dir, g)
        rr, cc = np.indices(g.shape)
        lon, lat = g.transform * (cc + 0.5, rr + 0.5)
        area = (abs(g.transform.a) * KM_PER_DEG * np.cos(np.radians(lat))) * (abs(g.transform.e) * KM_PER_DEG)
        desc = list(g.descriptions)
        out = {"source": "NASADEM_HGT.001 (LP DAAC, local), mean onto the 90 m TPI grid",
               "grid": path.name, "elev_valid_pct": round(100 * float(np.isfinite(elev).mean()), 2), "days": {}}
        for d in DAYS:
            tpi = g.read(desc.index(d) + 1).astype(np.float64)
            out["days"][d] = {"critico": summarise(tpi, elev, area, lon, CRIT),
                              "alto_plus": summarise(tpi, elev, area, lon, ALTO)}
    c = out["days"]["20251210"]["critico"]
    out["a3_confirmed"] = bool(c["above_1000m_pct"] is not None and c["above_1000m_pct"] > 50)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hgt-dir", type=Path, default=Path.home() / ".cache/lead-ml/nasadem_wa")
    ap.add_argument("--out", type=Path, default=REPO / "ml/models/a3_wa_elevation.json")
    a = ap.parse_args(argv)
    a.hgt_dir.mkdir(parents=True, exist_ok=True)
    res = run(a.hgt_dir, REPO / "data")
    a.out.write_text(json.dumps(res, indent=1))
    for d, v in res["days"].items():
        c = v["critico"]
        print(d, "Critical km2", c["flagged_km2"], ">=1000m %", c["above_1000m_pct"], "median m", c["median_elev_m"],
              {k: x["km2"] for k, x in c["by_elevation"].items()}, {k: x["km2"] for k, x in c["by_longitude"].items()})
    print("A3 confirmed:", res["a3_confirmed"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
