"""M1 (external review, L59): every TPI lift next to a terrain-only baseline.

lift_vs_terrain = TPI lift / lift of a T-only map that flags the SAME number of cells
(the k cells with the highest T; ties broken by stable order). > 1 means rain and soil
add something to "where" beyond terrain; <= 1 means terrain alone points as well.
Function as specified in .crew/review/10-correcoes-revisao-externa.md §M1, generalised
to a scoring domain mask and to reference cells given as flat indices (one entry per
inventory point / flooded pixel, so references are point- or area-weighted).

Rows (cuts Alto+ > 0.4 and Crítico > 0.6):
  rs_inventory_slide      GFS run >= 24 h before the L37 onset (tools/prediction_lift.run_frames),
                          16,027 RS inventory points, T = t_slide, domain = case grid
  rs_ems_flood_gfs24      same run, flood, Copernicus EMS flood extent, domain = EMS AOIs
  rs_ems_flood_event      observed replay, max TPI_flood Apr 29-May 5, EMS flood, EMS AOIs
  wa_nisar_flood_gfs24    GFS run >= 24 h before onset, NISAR flood candidates, case grid
  wa_nisar_flood_event    observed replay, max TPI_flood Dec 8-12, NISAR raster extent
  uru_nisar_flood_sep30   Uruguaiana corridor, observed replay Sep 30 (bands tpi, t), 7 NISAR polygons

    PYTHONPATH=.:pipeline python tools/lift_vs_terrain.py [--out ml/models/lift_vs_terrain.json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
for p in (str(REPO), str(REPO / "pipeline"), str(REPO / "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

CUTS = {"alto_plus": 0.4, "critico": 0.6}


def lift_vs_terrain(tpi, t, ref_idx, domain=None, cut=0.4) -> dict:
    """tpi, t: 2-D arrays on one grid; ref_idx: flat indices of the reference cells.

    As review §M1: a reference cell without a TPI value counts as a miss (published lift
    convention); domain only drops references outside the scoring area. T is piecewise
    (plateaus at 0 and 1), so the k-th T value is often tied across many cells; the §M1
    stable argsort then picks tied cells in raster order (north first), which is arbitrary.
    lift_terrain therefore uses the expected hit over a random tie-break; the raw §M1
    stable-order value is kept as lift_terrain_stable.
    """
    valid = np.isfinite(tpi) & np.isfinite(t)
    if domain is not None:
        valid &= domain
    flag = (tpi > cut) & valid
    k = int(flag.sum())
    ref = np.asarray(ref_idx)
    if domain is not None:
        ref = ref[domain.ravel()[ref]]
    out = {"cut": cut, "n_ref": int(ref.size), "flagged_cells": k,
           "area_share_pct": round(100 * k / max(int(valid.sum()), 1), 3)}
    if k == 0 or ref.size == 0:
        return out | {"hit_pct": 0.0 if ref.size else None, "lift": None, "lift_terrain": None,
                      "ratio": None, "reason": "the TPI flagged no cell of the domain" if k == 0 else "no reference"}
    share = k / valid.sum()
    hit = float(flag.ravel()[ref].mean())
    tv = np.where(valid, t, -np.inf).ravel()
    order = np.argsort(-tv, kind="stable")[:k]
    tflag = np.zeros(t.size, bool)
    tflag[order] = True
    thit_stable = float(tflag[ref].mean())
    kth = tv[order[-1]]
    above, tie = tv > kth, tv == kth
    n_above, n_tie = int(above.sum()), int(tie.sum())
    thit = float((above[ref].sum() + (k - n_above) / n_tie * tie[ref].sum()) / ref.size)
    return out | {"hit_pct": round(100 * hit, 2), "terrain_hit_pct": round(100 * thit, 2),
                  "lift": round(hit / share, 2), "lift_terrain": round(thit / share, 2),
                  "ratio": round(hit / thit, 3) if thit > 0 else None,
                  "terrain_tie": {"t_at_k": round(float(kth), 4), "tied_cells": n_tie, "picked": k - n_above},
                  "lift_terrain_stable": round(thit_stable / share, 2),
                  "ratio_stable": round(hit / thit_stable, 3) if thit_stable > 0 else None}


def _read(path, band=1):
    import rasterio
    with rasterio.open(path) as src:
        return src.read(band).astype(np.float64), src


def _rasterize(geojson: Path, shape, transform, all_touched=False) -> np.ndarray:
    import geopandas as gpd
    from rasterio.features import geometry_mask
    g = gpd.read_file(geojson).to_crs(4326)
    return ~geometry_mask(g.geometry, out_shape=shape, transform=transform, all_touched=all_touched)


def _event_max(path: Path, lo: str, hi: str) -> np.ndarray:
    import rasterio
    with rasterio.open(path) as src:
        bands = [i + 1 for i, d in enumerate(src.descriptions) if lo <= d <= hi]
        return np.fmax.reduce([src.read(b).astype(np.float64) for b in bands])


def rows(data_root: Path) -> dict:
    import prediction_lift as pl
    import rasterio
    import warned_before as wb
    out = {}
    on_rs = wb.ANCHORS["rs_2024"][0]["utc"]
    on_wa = wb.ANCHORS["wa_2025"][0]["utc"]
    rs, wa = data_root / "rs_2024", data_root / "wa_2025"

    # RS slide, GFS 24 h before onset, inventory points
    _, tpi, names, tr, _ = pl.run_frames(rs / "cog", "rs_2024", "slide", on_rs)
    t_slide, _ = _read(rs / "cog" / "rs_2024_t_slide_20240502.tif")
    grid = rasterio.open(rs / "cog" / "rs_2024_t_slide_20240502.tif")
    idx, _ = wb.inventory_points(rs / "geojson" / "landslide_catalog.geojson", grid)
    out["rs_inventory_slide"] = {"frames": names, "reference": "16,027 RS 2024 inventory points",
                                 **{c: lift_vs_terrain(tpi, t_slide, idx, None, v) for c, v in CUTS.items()}}

    # RS flood vs EMS: GFS 24 h before onset, and the observed event period
    t_flood, _ = _read(rs / "cog" / "rs_2024_t_flood_20240502.tif")
    shape = t_flood.shape
    aoi = _rasterize(rs / "geojson" / "flood_ems_aoi.geojson", shape, tr)
    ems = _rasterize(rs / "geojson" / "flood_ems.geojson", shape, tr) & aoi
    ems_idx = np.flatnonzero(ems.ravel())
    _, tpi_f, names_f, _, _ = pl.run_frames(rs / "cog", "rs_2024", "flood", on_rs)
    out["rs_ems_flood_gfs24"] = {"frames": names_f, "reference": "Copernicus EMS EMSR720 flood extent (cells)",
                                 **{c: lift_vs_terrain(tpi_f, t_flood, ems_idx, aoi, v) for c, v in CUTS.items()}}
    ev = _event_max(rs / "cog" / "rs_2024_tpi_flood_20240502.tif", "20240429", "20240505")
    out["rs_ems_flood_event"] = {"frames": "observed replay, max 2024-04-29..05-05",
                                 "reference": "Copernicus EMS EMSR720 flood extent (cells)",
                                 **{c: lift_vs_terrain(ev, t_flood, ems_idx, aoi, v) for c, v in CUTS.items()}}

    # WA flood vs NISAR candidates
    t_wf, _ = _read(wa / "cog" / "wa_2025_t_flood_20251210.tif")
    wgrid = rasterio.open(wa / "cog" / "wa_2025_t_flood_20251210.tif")
    nis = wa / "cog" / "wa_2025_flood_nisar_20251210.tif"
    widx, _ = wb.flood_pixels(nis, wgrid)
    _, tpi_w, names_w, trw, _ = pl.run_frames(wa / "cog", "wa_2025", "flood", on_wa)
    out["wa_nisar_flood_gfs24"] = {"frames": names_w, "reference": "NISAR flood candidates (12 m pixels, area-weighted)",
                                   **{c: lift_vs_terrain(tpi_w, t_wf, widx, None, v) for c, v in CUTS.items()}}
    with rasterio.open(nis) as s:
        b = s.bounds
    rr, cc = np.indices(t_wf.shape)
    lon, lat = trw * (cc + 0.5, rr + 0.5)
    ext = (lon >= b.left) & (lon <= b.right) & (lat >= b.bottom) & (lat <= b.top)
    evw = _event_max(wa / "cog" / "wa_2025_tpi_flood_20251210.tif", "20251208", "20251212")
    out["wa_nisar_flood_event"] = {"frames": "observed replay, max 2025-12-08..12",
                                   "reference": "NISAR flood candidates (12 m pixels, area-weighted)",
                                   "domain": "NISAR raster extent",
                                   **{c: lift_vs_terrain(evw, t_wf, widx, ext, v) for c, v in CUTS.items()}}

    # Uruguaiana corridor, observed replay Sep 30 (0.01 deg; bands tpi, r, s, t)
    uf = data_root / "rs_uruguai_2026" / "cog" / "rs_uruguai_2026_tpi_flood_obs_20260930.tif"
    with rasterio.open(uf) as s:
        d = {n: s.read(i + 1).astype(np.float64) for i, n in enumerate(s.descriptions)}
        utr = s.transform
    poly = _rasterize(data_root / "rs_uruguai_2026" / "geojson" / "flood_confirmed.geojson", d["tpi"].shape, utr,
                      all_touched=True)  # polygons are smaller than a 0.01 deg cell
    rr, cc = np.indices(d["tpi"].shape)
    lon, lat = utr * (cc + 0.5, rr + 0.5)
    corr = (lon >= -57.25) & (lon <= -56.45) & (lat >= -29.85) & (lat <= -29.05)
    out["uru_nisar_flood_sep30"] = {"frames": "observed replay 2026-09-30 (local rain)",
                                    "reference": "7 NISAR new-water candidate polygons (0.01 deg cells they touch)",
                                    "domain": "Uruguaiana-Itaqui corridor",
                                    **{c: lift_vs_terrain(d["tpi"], d["t"], np.flatnonzero(poly.ravel()), corr, v)
                                       for c, v in CUTS.items()}}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=REPO / "ml/models/lift_vs_terrain.json")
    a = ap.parse_args(argv)
    res = rows(REPO / "data")
    a.out.write_text(json.dumps(res, indent=1, default=str))
    for k, v in res.items():
        print(k, {c: {x: v[c].get(x) for x in ("hit_pct", "terrain_hit_pct", "area_share_pct", "lift",
                                                "lift_terrain", "ratio", "ratio_stable")} for c in CUTS})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
