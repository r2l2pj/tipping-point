"""Coherence-loss helpers: polygonize, catalog hit-rates, placement baseline.

Pure array functions used by tools/wa_nisar_coherence.py — no IO, no
network. Distances are metres on the raster's projected grid (uniform
square pixels; EPSG:32610 for wa_2025).
"""
from __future__ import annotations

import numpy as np

RADII_M = (100.0, 500.0)


def point_distances_m(mask: np.ndarray, transform, crs, lons, lats) -> np.ndarray:
    """Metres from each (lon, lat) point to the nearest True pixel of `mask`.

    distance_transform_edt gives each pixel's distance (in pixels) to the
    nearest True cell; a point reads the cell it lands in. Points outside
    the raster (or non-finite) get nan.
    """
    from rasterio.warp import transform as warp_transform
    from scipy.ndimage import distance_transform_edt

    mask = np.asarray(mask, dtype=bool)
    dist_px = distance_transform_edt(~mask)
    px_m = float(abs(transform.a))
    det = transform.a * transform.e - transform.b * transform.d
    xs, ys = warp_transform("EPSG:4326", crs, list(lons), list(lats))
    out = np.full(len(xs), np.nan)
    h, w = mask.shape
    for i, (x, y) in enumerate(zip(xs, ys)):
        if not (np.isfinite(x) and np.isfinite(y)):
            continue
        dx, dy = x - transform.c, y - transform.f
        c = (transform.e * dx - transform.b * dy) / det
        r = (transform.a * dy - transform.d * dx) / det
        r, c = int(np.floor(r)), int(np.floor(c))
        if 0 <= r < h and 0 <= c < w:
            out[i] = dist_px[r, c] * px_m
    return out


def hit_stats(mask: np.ndarray, transform, crs, lons, lats,
              radii_m=RADII_M) -> dict:
    """% of catalog points within each radius of a True pixel."""
    d = point_distances_m(mask, transform, crs, lons, lats)
    valid = np.isfinite(d)
    n = int(valid.sum())
    out = {"points_total": len(lons), "points_valid": n,
           "points_skipped": int(len(lons) - n)}
    for r in radii_m:
        hits = int((d <= r).sum()) if n else 0
        out[f"hit_{int(r)}m_n"] = hits
        out[f"hit_{int(r)}m_pct"] = 100.0 * hits / n if n else 0.0
    return out


def baseline_hit_pct(steep_mask: np.ndarray, n_px: int, transform, crs,
                     lons, lats, radii_m=RADII_M, n_seeds: int = 200,
                     seed: int = 42) -> dict:
    """Mean hit% when `n_px` loss pixels are placed at random on steep terrain.

    Each seed draws n_px cells uniformly from the True cells of
    `steep_mask` (without replacement) and recomputes the hit rate — the
    same-area null model for "the loss mask sits near catalog landslides
    just because steep ground is near them".
    """
    steep_mask = np.asarray(steep_mask, dtype=bool)
    pool = np.flatnonzero(steep_mask.ravel())
    draws = min(int(n_px), pool.size)
    rng = np.random.default_rng(seed)
    per_seed = {r: [] for r in radii_m}
    for _ in range(int(n_seeds)):
        pick = rng.choice(pool.size, size=draws, replace=False)
        m = np.zeros(steep_mask.size, dtype=bool)
        m[pool[pick]] = True
        st = hit_stats(m.reshape(steep_mask.shape), transform, crs,
                       lons, lats, radii_m)
        for r in radii_m:
            per_seed[r].append(st[f"hit_{int(r)}m_pct"])
    out = {"n_seeds": int(n_seeds), "draw_px": draws}
    for r in radii_m:
        v = np.asarray(per_seed[r], dtype=float)
        out[f"hit_{int(r)}m_mean_pct"] = float(v.mean()) if v.size else 0.0
        out[f"hit_{int(r)}m_p95_pct"] = float(np.percentile(v, 95)) if v.size else 0.0
    return out


def loss_polygons(mask: np.ndarray, transform, min_px: int):
    """Connected components of `mask` with >= min_px pixels.

    Returns (geoms, labels, sizes): geoms = [(label_id, geojson_geometry)]
    in the raster CRS (reproject upstream), labels = component id raster,
    sizes = per-label pixel counts.
    """
    import rasterio.features
    from scipy.ndimage import label

    labels, n = label(np.asarray(mask, dtype=bool))
    sizes = np.bincount(labels.ravel())
    geoms = []
    for i in range(1, n + 1):
        if sizes[i] < min_px:
            continue
        comp = labels == i
        for geom, _ in rasterio.features.shapes(
                np.uint8(comp), mask=comp, transform=transform):
            geoms.append((i, geom))
    return geoms, labels, sizes
