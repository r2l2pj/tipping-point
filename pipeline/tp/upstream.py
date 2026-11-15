"""Upstream-basin topology over HydroBASINS v1c level-N sub-basin tables (L35).

For each replay case, the basins intersecting the AOI get their full
upstream set — every basin whose NEXT_DOWN chain reaches it, itself
included — so flood R can aggregate upstream 3-day rain. HydroBASINS v1c
(HydroSHEDS) is the declared non-NASA reference derived from SRTM.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from rasterio import features
from shapely.geometry import box

HYBAS_ID = "HYBAS_ID"
NEXT_DOWN = "NEXT_DOWN"
ATTRS = (HYBAS_ID, NEXT_DOWN, "SUB_AREA", "UP_AREA")


def upstream_sets(df: pd.DataFrame, ids=None) -> dict[int, list[int]]:
    """{hybas_id: sorted [upstream ids]} — every basin whose NEXT_DOWN chain
    reaches the basin, itself included.

    df needs HYBAS_ID + NEXT_DOWN (0 marks coastal/sink outlets); pass the
    whole continent table so chains leaving the AOI still resolve. `ids`
    restricts which basins get a set (default: all rows). Sets computed for
    one target are memoized so a later target upstream of it reuses them.
    """
    children: dict[int, list[int]] = {}
    for h, d in zip(df[HYBAS_ID].tolist(), df[NEXT_DOWN].tolist()):
        if d:
            children.setdefault(d, []).append(h)
    targets = df[HYBAS_ID].tolist() if ids is None else [int(i) for i in ids]
    memo: dict[int, frozenset[int]] = {}
    out: dict[int, list[int]] = {}
    for t in targets:
        seen: set[int] = set()
        stack = [t]
        while stack:
            b = stack.pop()
            if b in seen:
                continue
            seen.add(b)
            cached = memo.get(b)
            if cached is not None:
                seen |= cached
            else:
                stack.extend(children.get(b, ()))
        memo[t] = frozenset(seen)
        out[t] = sorted(seen)
    return out


def rasterize_ids(gdf, like_path: str | Path):
    """(int64 array, GTiff profile) — HYBAS_ID per cell on exactly the grid
    of like_path (same transform/shape); 0 where no basin polygon covers
    the cell center (ocean). HYBAS_ID exceeds uint32, hence int64.
    """
    with rasterio.open(like_path) as src:
        prof = {"driver": "GTiff", "dtype": "int64", "count": 1,
                "height": src.height, "width": src.width, "crs": src.crs,
                "transform": src.transform, "nodata": 0, "tiled": True,
                "blockxsize": 512, "blockysize": 512, "compress": "deflate"}
        shapes = ((geom, int(i)) for geom, i in zip(gdf.geometry, gdf[HYBAS_ID]))
        arr = features.rasterize(shapes, out_shape=(src.height, src.width),
                                 transform=src.transform, fill=0,
                                 dtype="int64", all_touched=False)
    return arr, prof


def build_case(gdf, aoi_bbox, like_path: str | Path, out_dir: str | Path) -> dict:
    """Write basins.geojson / upstream.json / basin_id_90m.tif for one case.

    gdf: full HydroBASINS level table for the case's continent (needs at
    least the ATTRS columns + geometry). aoi_bbox: (w, s, e, n). Returns
    stats: n_aoi_basins, n_union_basins, union_bbox [w,s,e,n],
    largest_upstream_area_km2, pct_cells_with_basin.
    """
    out_dir = Path(out_dir)
    w, s, e, n = map(float, aoi_bbox)
    hits = gdf.sindex.query(box(w, s, e, n), predicate="intersects")
    aoi = gdf.iloc[np.sort(hits)]
    aoi_ids = sorted(int(i) for i in aoi[HYBAS_ID])
    sets = upstream_sets(gdf[[HYBAS_ID, NEXT_DOWN]], ids=aoi_ids)
    union_ids = sorted({i for ids in sets.values() for i in ids})
    union = gdf.loc[gdf[HYBAS_ID].isin(union_ids), list(ATTRS) + ["geometry"]]

    out_dir.mkdir(parents=True, exist_ok=True)
    union.to_file(out_dir / "basins.geojson", driver="GeoJSON")

    ub = union.total_bounds  # [w, s, e, n]
    up_area = aoi.set_index(HYBAS_ID)["UP_AREA"]
    doc: dict = {
        "union_bbox": [round(float(x), 6) for x in ub],
        "upstream_area_km2": {str(i): round(float(up_area[i]), 3) for i in aoi_ids},
    }
    doc.update({str(k): sets[k] for k in aoi_ids})
    (out_dir / "upstream.json").write_text(json.dumps(doc))

    arr, prof = rasterize_ids(union, like_path)
    with rasterio.open(out_dir / "basin_id_90m.tif", "w", **prof) as dst:
        dst.write(arr, 1)
        dst.update_tags(source="HydroBASINS v1c (HydroSHEDS, SRTM-derived)",
                        field="HYBAS_ID", nodata="0 = no basin (ocean)")

    return {
        "n_aoi_basins": len(aoi),
        "n_union_basins": len(union),
        "union_bbox": doc["union_bbox"],
        "largest_upstream_area_km2": round(float(up_area.max()), 3) if len(aoi) else 0.0,
        "pct_cells_with_basin": round(float((arr != 0).mean() * 100), 2),
    }
