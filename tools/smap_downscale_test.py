"""SMAP soil-wetness downscaling test (HAND-weighted, mass-conserving) vs bilinear.

Question: does redistributing the 9 km SMAP L4 root-zone wetness S inside each cell
by HAND (wetter where the terrain is low) locate flooded ground better than plain
bilinear interpolation of S, once the TPI terrain term T_flood (which already uses
HAND) is included?

Reference: Copernicus EMS EMSR720 flood polygons (observedEvent + maximumFloodExtent,
images 2024-05-04..15) inside the five Taquari-valley AOIs (data/rs_2024/geojson/
flood_ems*.geojson). Grid: 90 m HAND (static-v1 hand_90m.tif) over the AOI union;
slope from the 0.01 deg static slope_deg (bilinear). S from SMAP L4 (sm_rootzone_wetness,
T10:30Z) on SMAP_DATES, cached via ml.local.sources_dynamic.

Variants of S at 90 m:
  block      nearest SMAP cell value
  bilinear   bilinear between SMAP cell centres
  hand_ds    S_cell * w / mean_cell(w), w = exp(-HAND / H0) (mass-conserving per cell), clipped [0, 1]
Scores (AOI pixels, permanent water not excluded beyond EMS): ROC-AUC and PR-AUC of
S alone and of (S * T_flood) (TPI with R = 1, monotone in S*T), and IoU of S*T > cut
at the cut matching the EMS flooded share.

    PYTHONPATH=.:pipeline python tools/smap_downscale_test.py [--out JSON]
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

RELEASE = "https://github.com/r2l2pj/tipping-point-data/releases/download/static-v1"
SMAP_DATES = ("2024-05-01", "2024-05-03")
H0 = 5.0


def grids():
    import geopandas as gpd
    import rasterio
    from rasterio.features import geometry_mask
    from rasterio.warp import Resampling, reproject
    from rasterio.windows import from_bounds

    aoi = gpd.read_file(REPO / "data/rs_2024/geojson/flood_ems_aoi.geojson").to_crs(4326)
    flood = gpd.read_file(REPO / "data/rs_2024/geojson/flood_ems.geojson").to_crs(4326)
    w, s, e, n = aoi.total_bounds
    with rasterio.open(f"/vsicurl/{RELEASE}/hand_90m.tif") as src:
        win = from_bounds(w, s, e, n, transform=src.transform).round_offsets().round_lengths()
        hand = src.read(1, window=win, masked=True).filled(np.nan).astype(float)
        tr = src.window_transform(win)
    slope = np.full(hand.shape, np.nan)
    with rasterio.open(f"/vsicurl/{RELEASE}/slope_deg.tif") as src:
        reproject(rasterio.band(src, 1), slope, dst_transform=tr, dst_crs="EPSG:4326",
                  resampling=Resampling.bilinear)
    inside = ~geometry_mask(aoi.geometry, out_shape=hand.shape, transform=tr)
    flooded = ~geometry_mask(flood.geometry, out_shape=hand.shape, transform=tr) & inside
    rows, cols = np.indices(hand.shape)
    lon, lat = tr * (cols + 0.5, rows + 0.5)
    return hand, slope, inside, flooded, lon, lat


def smap_variants(lon, lat, hand, date):
    from scipy.interpolate import RegularGridInterpolator

    from ml.local import sources_dynamic as D
    row_lats, col_lons = D._smap_axes()
    r, c = D._smap_rc(lon.ravel(), lat.ravel())
    r0, r1, c0, c1 = r.min() - 1, r.max() + 2, c.min() - 1, c.max() + 2
    rr, cc = np.meshgrid(np.arange(r0, r1), np.arange(c0, c1), indexing="ij")
    clat, clon = row_lats[rr], col_lons[cc]
    d64 = np.full(clat.size, np.datetime64(date))
    D.fetch_smap_points(clon.ravel(), clat.ravel(), d64)
    cell = D.SmapPoints.load().sample(clon.ravel(), clat.ravel(), d64)["sm_rootzone_wetness"].to_numpy()
    cell = cell.reshape(rr.shape)
    block = cell[r - r0, c - c0].reshape(lon.shape)
    # bilinear between cell centres (row lats descend -> flip to ascending)
    lats_ax, cell_f = row_lats[r0:r1], cell
    if lats_ax[0] > lats_ax[-1]:
        lats_ax, cell_f = lats_ax[::-1], cell[::-1]
    interp = RegularGridInterpolator((lats_ax, col_lons[c0:c1]), cell_f, bounds_error=False,
                                     fill_value=np.nan)
    bil = interp(np.column_stack([lat.ravel(), lon.ravel()])).reshape(lon.shape)
    # HAND-weighted, mass-conserving inside each SMAP cell
    w = np.exp(-np.nan_to_num(hand, nan=np.nanmedian(hand)) / H0)
    key = (r * 100000 + c)
    flat_w = w.ravel()
    _, inv = np.unique(key, return_inverse=True)
    mean_w = np.bincount(inv, weights=flat_w) / np.bincount(inv)
    ds = np.clip(block.ravel() * flat_w / mean_w[inv], 0, 1).reshape(lon.shape)
    return {"block": block, "bilinear": bil, "hand_ds": ds}


def scores(x, y):
    from sklearn.metrics import average_precision_score, roc_auc_score
    ok = np.isfinite(x)
    x, y = x[ok], y[ok]
    share = y.mean()
    cut = np.quantile(x, 1 - share)
    pred = x >= cut
    iou = (pred & y).sum() / max((pred | y).sum(), 1)
    return {"roc_auc": round(float(roc_auc_score(y, x)), 4), "pr_auc": round(float(average_precision_score(y, x)), 4),
            "iou_at_share": round(float(iou), 4), "n": int(ok.sum()), "flooded_share": round(float(share), 4)}


def main(argv=None) -> int:
    from live.tpi import terrain_flood

    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=REPO / "ml/models/smap_downscale_test.json")
    a = ap.parse_args(argv)
    hand, slope, inside, flooded, lon, lat = grids()
    t = terrain_flood(hand, slope)
    res = {"reference": "Copernicus EMS EMSR720 flood extent, 5 Taquari AOIs (images 2024-05-04..15)",
           "grid": "90 m HAND (static-v1 hand_90m.tif)", "h0_m": H0, "dates": {}}
    sel = inside & np.isfinite(hand)
    y = flooded[sel]
    t30 = np.clip(1 - hand / 30.0, 0, 1)   # HAND-only ramp 0-30 m, no slope gate
    res["baselines"] = {"T_flood_live": scores(t[sel], y), "T_hand30": scores(t30[sel], y),
                        "minus_HAND": scores(-hand[sel], y),
                        "T_flood_live_zero_share": round(float((t[sel] == 0).mean()), 4),
                        "slope_gt5_share": round(float((slope[sel] > 5).mean()), 4),
                        "hand_median_flooded_m": round(float(np.nanmedian(hand[sel & flooded])), 1)}
    for date in SMAP_DATES:
        v = smap_variants(lon, lat, hand, date)
        res["dates"][date] = {name: {"S": scores(s[sel], y), "S_x_T_live": scores((s * t)[sel], y),
                                     "S_x_T_hand30": scores((s * t30)[sel], y),
                                     "S_p5_p50_p95": [round(float(q), 3) for q in np.nanpercentile(s[sel], [5, 50, 95])]}
                              for name, s in v.items()}
    a.out.write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
