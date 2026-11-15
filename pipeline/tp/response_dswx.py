"""Near-real-time flood candidates for the Now domain from OPERA DSWx-S1.

L27 response contract (.crew/plans/response.md, R1). Outputs under
data/now/response/: flood_dswx.geojson, dswx_state.npz, dswx_meta.json.

Method
------
* WTR classes (DSWx-S1 v1 product spec, confirmed against the GeoTIFF
  colormap): 0 not water, 1 open water, 2 partial surface water (unused by
  S1 but kept), 3 inundated vegetation -> water = {1, 2, 3}, dry = {0};
  250 HAND-masked, 251 layover/shadow, 252/253 (unused), 254 ocean-masked,
  255 fill -> unobserved.
* Each 30 m UTM tile is reprojected (Resampling.average of a 1/0/nan water
  indicator) onto a fixed EPSG:4326 grid of ``res`` degrees (default
  0.0025 deg, ~280 x 230 m here). A coarse pixel is observed when any 30 m
  sample is observed; it is water when the observed water fraction >= 0.5.
* Per pixel the state keeps the most recent observation (``cur``) and a
  baseline of all EARLIER observations (``water_n`` / ``obs_n``). When a
  newer pass observes a pixel, the previous ``cur`` is folded into the
  baseline - except water at pixels that were flood candidates in the
  previous run, so a long flood does not absorb itself into its own
  baseline. Tiles from the same pass (within SAME_PASS_H) merge instead.
  Counts are halved when ``obs_n`` reaches ROLL_MAX - a cheap rolling
  window that keeps recent seasons dominant.
* Flood candidate = latest obs is water, acquired within ``window_h`` of
  the newest acquisition, baseline has >= ``min_obs`` (default 3) observations and
  baseline water frequency < ``base_max``. Pixels with too little baseline
  are reported as ``unbaselined_water_km2``, never as candidates.
* Context (lead-analysis review): raw candidates on MCD12Q1 2025 LC_Type1
  classes 11 (permanent wetland) / 17 (water body), dilated 1 LC cell, are
  EXCLUDED (Ibera, river margins, reservoirs); candidates outside the IBGE
  Brazil outline are dropped from polygons/headline (outside_brazil_km2);
  cropland 12/14 (e.g. RS rice paddies flooded for planting) is kept as
  confidence "lower". Headline flooded_km2/people_flooded = standard only, summed over the
  mapped polygons (>= min_area_km2); smaller clusters -> sub_mmu_km2.
  Seasonal/occasional water (JRC GSW v1.4 1984-2021 cell-mean occurrence
  >= GSW_OCC_MIN % or seasonality >= GSW_MONTHS_MIN months) is also kept as
  confidence "lower" (``lower_reason`` names the rule); missing GSW rasters
  -> the rule is skipped and ``_null_reasons.seasonal_water`` says so.
* No supporting rain: a candidate whose 8-day IMERG Early total is below
  RAIN_MIN_MM both locally AND everywhere within RAIN_RADIUS_KM (a
  direction-agnostic upstream proxy) AND whose JRC occurrence is 0 (never
  water 1984-2021) is kept as "lower" (``lower_reason`` "no supporting rain
  within 50 km (8 d IMERG)"). Missing/unknown rain never downgrades.
  ``all_candidates_km2`` / ``all_candidates_people`` = standard + lower.
  Missing land cover or outline -> headline null + reason (unfiltered water
  is never published).
* change: "new" = candidate now and not in the previous run's candidate
  mask (``prev_flood``); "persisting" otherwise. receded = previous
  candidate whose latest (recent) observation is dry.

All outputs are "candidate" - never "confirmed". Peak memory is a handful
of full-grid arrays (4600 x 4000 at the default) plus one tile at a time.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np

WTR_WATER = (1, 2, 3)
WTR_DRY = (0,)
NONE = 255                    # cur: unobserved
SAME_PASS_H = 1               # obs within 1 h of cur = same pass (tile overlap)
ROLL_MAX = 60                 # halve counts when obs_n hits this
EPOCH = datetime(2024, 1, 1, tzinfo=UTC)   # cur_t = uint16 hours since EPOCH
STATE_VERSION = 1
SOURCE = "opera-dswx-s1"


# --- time helpers ----------------------------------------------------------

def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s).astimezone(UTC)


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_hours(dt: datetime) -> int:
    h = int((dt - EPOCH).total_seconds() // 3600)
    if not 0 <= h < 65535:
        raise ValueError(f"acquisition time out of state range: {dt}")
    return h


def from_hours(h: int) -> datetime:
    return EPOCH + timedelta(hours=int(h))


# --- grid ------------------------------------------------------------------

@dataclass(frozen=True)
class Grid:
    west: float
    south: float
    east: float
    north: float
    res: float

    @property
    def shape(self) -> tuple[int, int]:
        return (round((self.north - self.south) / self.res), round((self.east - self.west) / self.res))

    @property
    def transform(self):
        from rasterio.transform import from_origin

        return from_origin(self.west, self.north, self.res, self.res)

    def row_area_km2(self) -> np.ndarray:
        """Spherical pixel area (km2) per row, shape (rows, 1)."""
        r = 6371.0088
        rows = self.shape[0]
        lat_top = self.north - self.res * np.arange(rows)
        lat_bot = lat_top - self.res
        a = r * r * math.radians(self.res) * (np.sin(np.radians(lat_top)) - np.sin(np.radians(lat_bot)))
        return a.reshape(-1, 1)


def classify_wtr(wtr: np.ndarray) -> np.ndarray:
    """float32 indicator: 1 water, 0 dry, nan unobserved."""
    out = np.full(wtr.shape, np.nan, dtype=np.float32)
    out[np.isin(wtr, WTR_DRY)] = 0.0
    out[np.isin(wtr, WTR_WATER)] = 1.0
    return out


def tile_to_grid(wtr: np.ndarray, transform, crs, grid: Grid):
    """Reproject one WTR tile onto the grid window it covers.

    Returns (row0, col0, frac) where frac is the observed-water fraction
    (nan = unobserved) for grid[row0:row0+h, col0:col0+w], or None when the
    tile does not intersect the grid.
    """
    from rasterio.transform import array_bounds, from_origin
    from rasterio.warp import Resampling, reproject, transform_bounds

    h, w = wtr.shape
    b = transform_bounds(crs, "EPSG:4326", *array_bounds(h, w, transform), densify_pts=21)
    rows, cols = grid.shape
    c0 = max(0, math.floor((b[0] - grid.west) / grid.res))
    c1 = min(cols, math.ceil((b[2] - grid.west) / grid.res))
    r0 = max(0, math.floor((grid.north - b[3]) / grid.res))
    r1 = min(rows, math.ceil((grid.north - b[1]) / grid.res))
    if c1 <= c0 or r1 <= r0:
        return None
    dst = np.full((r1 - r0, c1 - c0), np.nan, dtype=np.float32)
    reproject(
        classify_wtr(wtr), dst,
        src_transform=transform, src_crs=crs, src_nodata=np.nan,
        dst_transform=from_origin(grid.west + c0 * grid.res, grid.north - r0 * grid.res, grid.res, grid.res),
        dst_crs="EPSG:4326", dst_nodata=np.nan,
        resampling=Resampling.average,
    )
    return r0, c0, dst


# --- state -----------------------------------------------------------------

class State:
    """Rolling per-pixel DSWx state (see module docstring)."""

    def __init__(self, grid: Grid):
        self.grid = grid
        shape = grid.shape
        self.water_n = np.zeros(shape, np.uint16)
        self.obs_n = np.zeros(shape, np.uint16)
        self.cur = np.full(shape, NONE, np.uint8)
        self.cur_t = np.zeros(shape, np.uint16)
        self.cur_g = np.zeros(shape, np.uint16)
        self.prev_flood = np.zeros(shape, np.uint8)
        self.gids: list[str] = [""]          # index 0 = none
        self.seen: dict[str, str] = {}       # granule id -> acquired ISO (recent only)
        self.last_acquired: str | None = None
        self.bootstrap: dict = {}
        self.n_ingested = 0
        self.has_prev = False

    # --- persistence
    @classmethod
    def load(cls, path: Path, grid: Grid) -> State | None:
        path = Path(path)
        if not path.is_file():
            return None
        with np.load(path, allow_pickle=False) as z:
            meta = json.loads(str(z["meta"]))
            g = Grid(*meta["grid"])
            if meta.get("version") != STATE_VERSION or g != grid:
                return None
            st = cls(grid)
            for k in ("water_n", "obs_n", "cur", "cur_t", "cur_g", "prev_flood"):
                setattr(st, k, z[k].copy())
            st.gids = [str(x) for x in z["gids"]]
        st.seen = meta["seen"]
        st.last_acquired = meta["last_acquired"]
        st.bootstrap = meta.get("bootstrap") or {}
        st.n_ingested = meta.get("n_ingested", 0)
        st.has_prev = meta.get("has_prev", False)
        return st

    def save(self, path: Path) -> None:
        self._compact_gids()
        if self.last_acquired:
            keep_after = parse_iso(self.last_acquired) - timedelta(hours=72)
            self.seen = {k: v for k, v in self.seen.items() if parse_iso(v) >= keep_after}
        g = self.grid
        meta = {
            "version": STATE_VERSION, "grid": [g.west, g.south, g.east, g.north, g.res],
            "epoch": iso(EPOCH), "seen": self.seen, "last_acquired": self.last_acquired,
            "bootstrap": self.bootstrap, "n_ingested": self.n_ingested, "has_prev": self.has_prev,
        }
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".npz")
        os.close(fd)
        with open(tmp, "wb") as f:
            np.savez_compressed(
                f, water_n=self.water_n, obs_n=self.obs_n, cur=self.cur, cur_t=self.cur_t,
                cur_g=self.cur_g, prev_flood=self.prev_flood,
                gids=np.array(self.gids, dtype=str), meta=np.array(json.dumps(meta)),
            )
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)

    def _compact_gids(self) -> None:
        used = np.unique(self.cur_g[self.cur != NONE])
        used = used[used > 0]
        remap = np.zeros(len(self.gids), np.uint16)
        remap[used] = np.arange(1, len(used) + 1, dtype=np.uint16)
        self.cur_g = remap[self.cur_g]
        self.gids = [""] + [self.gids[i] for i in used]

    def _gid(self, granule_id: str) -> int:
        if len(self.gids) >= 65535:
            self._compact_gids()
        self.gids.append(granule_id)
        return len(self.gids) - 1

    # --- ingest
    def ingest(self, r0: int, c0: int, frac: np.ndarray, acquired: datetime, granule_id: str) -> int:
        """Merge one tile's grid window; returns observed pixel count."""
        t = to_hours(acquired)
        h, w = frac.shape
        sl = (slice(r0, r0 + h), slice(c0, c0 + w))
        obs = ~np.isnan(frac)
        if not obs.any():
            return 0
        wat = (np.nan_to_num(frac, nan=0.0) >= 0.5).astype(np.uint8)
        cur, cur_t = self.cur[sl], self.cur_t[sl]
        wn, on, cg = self.water_n[sl], self.obs_n[sl], self.cur_g[sl]
        has = cur != NONE
        dt = t - cur_t.astype(np.int32)
        same = obs & has & (np.abs(dt) <= SAME_PASS_H)
        newer = obs & (~has | (dt > SAME_PASS_H))
        older = obs & has & (dt < -SAME_PASS_H)
        # a pixel's candidate-flood water never feeds its own baseline (else a
        # long flood would absorb itself); its dry observations still do
        fold = newer & has & ~((self.prev_flood[sl] == 1) & (cur == 1))
        # previous latest -> baseline; late-arriving older obs -> baseline directly
        wn[fold] += cur[fold]
        on[fold] += 1
        wn[older] += wat[older]
        on[older] += 1
        gid = self._gid(granule_id)
        cur[newer] = wat[newer]
        cur_t[newer] = t
        cg[newer] = gid
        cur[same] = np.maximum(cur[same], wat[same])
        roll = on >= ROLL_MAX
        if roll.any():
            wn[roll] //= 2
            on[roll] //= 2
        self.n_ingested += 1
        return int(obs.sum())

    # --- evaluate
    def evaluate(self, window_h: int, min_obs: int, base_max: float) -> dict:
        """Masks for the current pass (no mutation)."""
        recent_obs = self.cur != NONE
        if self.last_acquired:
            t_now = to_hours(parse_iso(self.last_acquired))
            recent_obs &= self.cur_t.astype(np.int32) >= t_now - window_h
        water_now = recent_obs & (self.cur == 1)
        baselined = self.obs_n >= min_obs
        freq = np.where(baselined, self.water_n / np.maximum(self.obs_n, 1), 1.0)
        flood = water_now & baselined & (freq < base_max)
        prev = self.prev_flood.astype(bool) if self.has_prev else np.zeros_like(flood)
        return {
            "observed": recent_obs,
            "flood": flood,
            "new": flood & ~prev,
            "persisting": flood & prev,
            "receded": prev & recent_obs & (self.cur == 0),
            "unbaselined": water_now & ~baselined,
        }


# --- people ----------------------------------------------------------------

def people_grid(gpw_sources: list[str], grid: Grid) -> tuple[np.ndarray | None, str | None, str | None]:
    """GPW count per grid pixel (float32), first source that opens.

    Returns (array, used_source, reason_if_none). Counts are conserved:
    GPW is reprojected as density (count / src pixel area ratio) then scaled
    to the grid pixel.
    """
    import rasterio
    from rasterio.warp import Resampling, reproject

    errors = []
    for src_path in gpw_sources:
        try:
            with rasterio.open(src_path) as src:
                ratio = (grid.res / abs(src.res[0])) * (grid.res / abs(src.res[1]))
                dst = np.zeros(grid.shape, np.float32)
                band = src.read(1).astype(np.float32)
                if src.nodata is not None and not np.isnan(src.nodata):
                    band[band == src.nodata] = np.nan
                band = np.nan_to_num(band, nan=0.0)
                reproject(
                    band, dst, src_transform=src.transform, src_crs=src.crs,
                    dst_transform=grid.transform, dst_crs="EPSG:4326",
                    resampling=Resampling.nearest if ratio < 1 else Resampling.sum,
                )
                return (dst * ratio if ratio < 1 else dst), str(src_path), None
        except Exception as e:  # noqa: BLE001 - try next source
            errors.append(f"{Path(str(src_path)).name}: {type(e).__name__}")
    return None, None, "GPW population raster unavailable (" + "; ".join(errors or ["no source"]) + ")"


# --- context: land cover exclusion/confidence + Brazil clip ---------------

IGBP = {1: "evergreen needleleaf forest", 2: "evergreen broadleaf forest",
        3: "deciduous needleleaf forest", 4: "deciduous broadleaf forest", 5: "mixed forest",
        6: "closed shrubland", 7: "open shrubland", 8: "woody savanna", 9: "savanna",
        10: "grassland", 11: "permanent wetland", 12: "cropland", 13: "urban",
        14: "cropland/natural mosaic", 15: "snow/ice", 16: "barren", 17: "water body"}
LC_EXCLUDE = (11, 17)        # permanent wetlands + water bodies (dilated 1 LC cell)
LC_LOWER = (12, 14)          # croplands: e.g. RS rice paddies flooded for planting
GSW_OCC_MIN = 25             # JRC GSW occurrence (%) at/above which water is "occasional"
GSW_MONTHS_MIN = 3           # JRC GSW seasonality (months/yr) at/above which water is seasonal
REASON_CROP = "cropland (MCD12Q1)"
REASON_SEAS = "seasonal/occasional water (JRC GSW)"
REASON_NORAIN = "no supporting rain within 50 km (8 d IMERG)"
RAIN_DAYS = 8                # IMERG Early daily files summed for the rain-support rule
RAIN_MIN_MM = 10.0           # below this (local AND 50 km neighbourhood) = no supporting rain
RAIN_RADIUS_KM = 50.0        # upstream proxy: max over this radius, all directions


def rain_support_grid(imerg_dir, end: date, grid: Grid, days: int = RAIN_DAYS,
                      radius_km: float = RAIN_RADIUS_KM):
    """8-day IMERG rain support on `grid`: (support_mm, info, None) or (None, info, reason).

    support = max(local 8-day total, max over a `radius_km` ellipse) on the
    IMERG 0.1 deg grid — a direction-agnostic superset of "upstream" (no flow
    routing). Cells with any missing day are UNKNOWN (+inf: never downgraded).
    Files must cover the grid bbox (a transposed lon-first file does not) and
    at least days-1 of the `days` files ending `end` must exist.
    """
    import rasterio
    from rasterio.warp import Resampling, reproject
    from scipy.ndimage import maximum_filter

    imerg_dir = Path(imerg_dir)
    want = [end - timedelta(days=k) for k in range(days - 1, -1, -1)]
    paths = [imerg_dir / f"imerg_early_{d:%Y%m%d}.tif" for d in want]
    have = [p for p in paths if p.exists()]
    info = {"window": [want[0].isoformat(), want[-1].isoformat()], "n_files": len(have),
            "threshold_mm": RAIN_MIN_MM, "radius_km": radius_km,
            "upstream_proxy": "max over a 50 km radius, all directions (superset of upstream; no flow routing)"}
    if len(have) < days - 1:
        return None, info, f"IMERG daily files: {len(have)}/{days} for {info['window']}"
    total = tf = None
    for p in have:
        with rasterio.open(p) as src:
            b = src.bounds
            tol = abs(src.res[0]) / 2 + 1e-6       # half a cell
            if (b.left > grid.west + tol or b.right < grid.east - tol
                    or b.bottom > grid.south + tol or b.top < grid.north - tol):
                return None, info, (f"{p.name} bounds {tuple(round(v, 2) for v in b)} do not cover the "
                                    "domain (transposed or truncated IMERG COG)")
            a = src.read(1).astype(np.float64)
            if src.nodata is not None and not np.isnan(src.nodata):
                a[a == src.nodata] = np.nan
            if total is None:
                total, tf, crs, shape = a, src.transform, src.crs, a.shape
            elif a.shape != shape or src.transform != tf:
                return None, info, f"{p.name}: grid differs from the other IMERG files"
            else:
                total = total + a                       # NaN anywhere -> unknown
    total = np.where(np.isfinite(total), total, np.inf)
    lat_mid = math.radians((grid.north + grid.south) / 2)
    ry = radius_km / (111.32 * abs(tf.e))
    rx = radius_km / (111.32 * math.cos(lat_mid) * abs(tf.a))
    yy, xx = np.mgrid[-math.ceil(ry):math.ceil(ry) + 1, -math.ceil(rx):math.ceil(rx) + 1]
    support = maximum_filter(total, footprint=(yy / ry) ** 2 + (xx / rx) ** 2 <= 1.0,
                             mode="constant", cval=np.inf)       # edge = unknown
    out = np.full(grid.shape, np.inf, np.float32)
    reproject(support.astype(np.float32), out, src_transform=tf, src_crs=crs,
              dst_transform=grid.transform, dst_crs="EPSG:4326", resampling=Resampling.nearest,
              src_nodata=None, dst_nodata=None)
    info["local_median_mm"] = round(float(np.median(total[np.isfinite(total)])), 1) if np.isfinite(total).any() else None
    return out, info, None


def gsw_grid(occ_path, seas_path, grid: Grid, occ_min: int = GSW_OCC_MIN,
             months_min: int = GSW_MONTHS_MIN):
    """(bool seasonal-water mask on the grid, None) or (None, reason).

    Inputs are the tools/convert_jrc_gsw.py COGs (cell means on this grid,
    uint8, 255 = no data); read with nearest so any grid works.
    """
    import rasterio
    from rasterio.warp import Resampling, reproject

    try:
        out = []
        for path in (occ_path, seas_path):
            with rasterio.open(path) as src:
                a = np.full(grid.shape, 255, np.uint8)
                reproject(rasterio.band(src, 1), a, src_nodata=255, dst_nodata=255,
                          dst_transform=grid.transform, dst_crs="EPSG:4326",
                          resampling=Resampling.nearest)
            out.append(a)
        occ, seas = out
        seasonal = ((occ != 255) & (occ >= occ_min)) | ((seas != 255) & (seas >= months_min))
        return seasonal, occ == 0, None          # occ == 0: never water 1984-2021 (255 = unknown)
    except Exception as e:  # noqa: BLE001
        return None, None, f"JRC GSW raster unavailable ({Path(str(occ_path)).name}: {type(e).__name__})"


def landcover_grid(path, grid: Grid):
    """(lc uint8, exclude bool) on the grid from the MCD12Q1 LC_Type1 COG.

    Exclusion = classes LC_EXCLUDE dilated by 1 LC cell (3x3) at the COG's
    native resolution, then nearest onto the grid. Returns (None, None, reason)
    when the file is unreadable.
    """
    import rasterio
    from rasterio.warp import Resampling, reproject
    from scipy.ndimage import binary_dilation

    try:
        with rasterio.open(path) as src:
            lc_src = src.read(1)
            excl_src = binary_dilation(np.isin(lc_src, LC_EXCLUDE), structure=np.ones((3, 3), bool))
            kw = {"src_transform": src.transform, "src_crs": src.crs, "dst_transform": grid.transform,
                  "dst_crs": "EPSG:4326", "resampling": Resampling.nearest}
            lc = np.full(grid.shape, 255, np.uint8)
            reproject(lc_src, lc, src_nodata=255, dst_nodata=255, **kw)
            excl = np.zeros(grid.shape, np.uint8)
            reproject(excl_src.astype(np.uint8), excl, **kw)
        return lc, excl.astype(bool), None
    except Exception as e:  # noqa: BLE001
        return None, None, f"land cover raster unavailable ({Path(str(path)).name}: {type(e).__name__})"


def brazil_grid(path, grid: Grid):
    """(bool mask of grid cells whose centre is inside Brazil, None) or (None, reason)."""
    from rasterio.features import rasterize

    try:
        fc = json.loads(Path(path).read_text())
        geoms = [f["geometry"] for f in fc["features"]]
        m = rasterize(((g, 1) for g in geoms), out_shape=grid.shape, transform=grid.transform,
                      fill=0, dtype="uint8")
        return m.astype(bool), None
    except Exception as e:  # noqa: BLE001
        return None, f"Brazil outline unavailable ({Path(str(path)).name}: {type(e).__name__})"


def apply_context(masks: dict, lc: np.ndarray, excl: np.ndarray, br: np.ndarray, prev: np.ndarray,
                  seasonal: np.ndarray | None = None, norain: np.ndarray | None = None) -> dict:
    """Split raw candidates into excluded / outside Brazil / standard / lower.

    lower = cropland OR seasonal/occasional water (when the GSW mask exists);
    ``seasonal_downgraded`` = candidates that would be standard without the
    GSW rule (what that rule moves).
    """
    flood = masks["flood"]
    kept = flood & ~excl
    inbr = kept & br
    crop = inbr & np.isin(lc, LC_LOWER)
    seas = inbr & seasonal if seasonal is not None else np.zeros_like(inbr)
    dry = inbr & norain if norain is not None else np.zeros_like(inbr)
    lower = crop | seas | dry
    std = inbr & ~lower
    return {
        **masks,
        "excluded": flood & excl,
        "outside": kept & ~br,
        "candidate": inbr,
        "standard": std,
        "lower": lower,
        "lower_crop": crop,
        "lower_seasonal": seas,
        "seasonal_downgraded": seas & ~crop,
        "lower_norain": dry,
        "norain_downgraded": dry & ~crop & ~seas,
        "std_new": std & ~prev,
        "lower_new": lower & ~prev,
        "receded": masks["receded"] & br,
    }


# --- outputs ---------------------------------------------------------------

def polygonize(state: State, masks: dict, pop: np.ndarray | None, lc: np.ndarray, out_path: Path,
               min_area_km2: float, simplify_m: float, max_bytes: int = 5_000_000) -> dict:
    """flood_dswx.geojson with per-polygon props; returns summary stats."""
    import rasterio
    from rasterio.features import rasterize
    from shapely.geometry import shape

    from tp.polygonize import mask_to_geojson

    grid = state.grid
    # 1 new/standard, 2 persisting/standard, 3 new/lower, 4 persisting/lower
    cls = np.zeros(grid.shape, np.uint8)
    cls[masks["standard"]] = 2
    cls[masks["std_new"]] = 1
    cls[masks["lower"]] = 4
    cls[masks["lower_new"]] = 3
    out_path = Path(out_path)
    with tempfile.TemporaryDirectory() as td:
        tif = Path(td) / "dswx_flood_class.tif"
        with rasterio.open(tif, "w", driver="GTiff", height=cls.shape[0], width=cls.shape[1], count=1,
                           dtype="uint8", crs="EPSG:4326", transform=grid.transform, nodata=0,
                           compress="deflate") as dst:
            dst.write(cls, 1)
        del cls
        raw = Path(td) / "raw.geojson"
        while True:
            mask_to_geojson(tif, raw, values=(1, 2, 3, 4), min_area_km2=min_area_km2,
                            simplify_m=simplify_m, kind="flood_candidate", date="")
            fc = json.loads(raw.read_text())
            feats = fc["features"]
            if not feats:
                break
            # per-feature zonal stats via one id raster
            ids = rasterize(((f["geometry"], i + 1) for i, f in enumerate(feats)), out_shape=grid.shape,
                            transform=grid.transform, fill=0, dtype="int32")
            sel = ids > 0
            fid = ids[sel]
            n = len(feats) + 1
            t_max = np.zeros(n, np.int64)
            np.maximum.at(t_max, fid, state.cur_t[sel].astype(np.int64))
            people = np.bincount(fid, weights=pop[sel], minlength=n) if pop is not None else None
            pairs = np.unique(np.stack([fid, state.cur_g[sel].astype(np.int64)]), axis=1)
            lc_counts = np.zeros((n, 256), np.int64)
            np.add.at(lc_counts, (fid, lc[sel]), 1)
            lc_major = lc_counts.argmax(axis=1)
            del lc_counts
            n_crop = np.bincount(fid, weights=masks["lower_crop"][sel], minlength=n)
            n_seas = np.bincount(fid, weights=masks["lower_seasonal"][sel], minlength=n)
            n_dry = np.bincount(fid, weights=masks["lower_norain"][sel], minlength=n)
            # what each rule moved standard -> lower INSIDE mapped polygons
            moved = {}
            for key in ("seasonal_downgraded", "norain_downgraded"):
                md = masks[key] & sel
                moved[key] = {"km2": round(float((md * grid.row_area_km2()).sum()), 3),
                              "people": None if pop is None else round(float(pop[md].sum()), 1)}
                del md
            del ids, sel, fid
            gran: dict[int, list[str]] = {}
            for f_i, g_i in pairs.T:
                if g_i > 0:
                    gran.setdefault(int(f_i), []).append(state.gids[int(g_i)])
            out_feats = []
            for i, f in enumerate(feats, start=1):
                if shape(f["geometry"]).is_empty:
                    continue
                out_feats.append({"type": "Feature", "geometry": f["geometry"], "properties": {
                    "source": SOURCE,
                    "acquired": iso(from_hours(int(t_max[i]))),
                    "granules": sorted(gran.get(i, [])),
                    "area_km2": round(float(f["properties"]["area_km2"]), 4),
                    "people": None if people is None else round(float(people[i]), 1),
                    "status": "candidate",
                    "change": "new" if f["properties"]["value"] in (1, 3) else "persisting",
                    "confidence": "lower" if f["properties"]["value"] in (3, 4) else "standard",
                    "landcover": IGBP.get(int(lc_major[i]), "unclassified"),
                    "lower_reason": ([r for r, k in ((REASON_CROP, n_crop[i]), (REASON_SEAS, n_seas[i]),
                                                     (REASON_NORAIN, n_dry[i])) if k > 0]
                                     if f["properties"]["value"] in (3, 4) else None),
                }})
            feats = out_feats
            text = json.dumps({"type": "FeatureCollection", "features": feats}, allow_nan=False,
                              separators=(",", ":")) + "\n"
            if len(text.encode()) <= max_bytes or simplify_m > 5000:
                break
            simplify_m *= 2
            min_area_km2 = max(min_area_km2 * 2, 0.1)
    if not feats:
        text = json.dumps({"type": "FeatureCollection", "features": []}) + "\n"
    _atomic_write(out_path, text)
    # headline totals from the MAPPED polygons (>= min_area), so the numbers
    # match what the map draws; sub-MMU specks are reported separately
    if not feats:
        zero = {"km2": 0.0, "people": 0.0 if pop is not None else None}
        moved = {"seasonal_downgraded": zero, "norain_downgraded": zero}
    totals = {k: {"km2": 0.0, "people": 0.0} for k in ("standard", "std_new", "lower", "lower_new")}
    for f in feats:
        p = f["properties"]
        tier = "standard" if p["confidence"] == "standard" else "lower"
        keys = [tier] + ([("std_new" if tier == "standard" else "lower_new")] if p["change"] == "new" else [])
        for k in keys:
            totals[k]["km2"] += p["area_km2"]
            totals[k]["people"] += p["people"] or 0.0
    return {"features": len(feats), "bytes": len(text.encode()), "simplify_m": simplify_m,
            "min_area_km2": min_area_km2,
            "totals": {k: {"km2": round(v["km2"], 3), "people": round(v["people"], 1)}
                       for k, v in totals.items()},
            "moved": moved}


def _atomic_write(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def write_meta(path: Path, meta: dict) -> None:
    _atomic_write(path, json.dumps(meta, indent=2, allow_nan=False) + "\n")


# --- orchestration ---------------------------------------------------------

def read_wtr(data: bytes):
    """(array, transform, crs) of an in-memory WTR GeoTIFF."""
    from rasterio.io import MemoryFile

    with MemoryFile(data) as mf, mf.open() as src:
        return src.read(1), src.transform, src.crs


def run(
    out_dir: Path,
    grid: Grid,
    search,                  # (start_iso, end_iso|None) -> list[granule dict]
    fetch,                   # (granule dict) -> bytes
    gpw_sources: list[str],
    gpw_label: str = "GPW v4.11 Population Count 2020",
    landcover_path=None,
    outline_path=None,
    gsw_paths: tuple | None = None,      # (occurrence COG, seasonality COG)
    imerg_dir=None,                      # dir of imerg_early_YYYYMMDD.tif daily COGs
    now: datetime | None = None,
    bootstrap_days: int = 60,
    overlap_h: int = 24,
    window_h: int = 144,
    min_obs: int = 3,
    base_max: float = 0.2,
    min_area_km2: float = 0.25,
    simplify_m: float = 150.0,
    workers: int = 4,
    log=print,
) -> dict:
    """One response pass: incremental ingest -> evaluate -> write 3 outputs."""
    from concurrent.futures import ThreadPoolExecutor
    from itertools import islice

    out_dir = Path(out_dir)
    now = now or datetime.now(UTC)
    state_path = out_dir / "dswx_state.npz"
    reasons: dict[str, str] = {}
    state = State.load(state_path, grid)
    bootstrapping = state is None or not state.bootstrap.get("ok")
    if state is None:
        state = State(grid)
    if bootstrapping:
        start = now - timedelta(days=bootstrap_days)
    else:
        start = parse_iso(state.last_acquired) - timedelta(hours=overlap_h)

    failed: list[str] = []
    ingested: list[dict] = []
    try:
        granules = search(iso(start), None)
    except Exception as e:  # noqa: BLE001
        granules = []
        reasons["search"] = f"CMR search failed: {type(e).__name__}"
    todo = [g for g in granules if g["id"] not in state.seen]
    log(f"dswx: {len(granules)} granules since {iso(start)}, {len(todo)} new"
        + (" (bootstrap)" if bootstrapping else ""))

    def _get(g):
        try:
            return g, read_wtr(fetch(g)), None
        except Exception as e:  # noqa: BLE001
            return g, None, str(e) if isinstance(e, RuntimeError) else type(e).__name__

    with ThreadPoolExecutor(max_workers=workers) as pool:
        # bounded prefetch: at most `workers * 2` decoded tiles in flight
        it = iter(todo)
        pending = [pool.submit(_get, g) for g in islice(it, workers * 2)]
        done = 0
        while pending:
            g, tile, err = pending.pop(0).result()
            nxt = next(it, None)
            if nxt is not None:
                pending.append(pool.submit(_get, nxt))
            done += 1
            if tile is None:
                failed.append(g["id"])
                log(f"  [{done}/{len(todo)}] FAIL {g['id']}: {err}")
                continue
            res = tile_to_grid(*tile, grid)
            del tile
            if res is not None:
                state.ingest(*res, parse_iso(g["acquired"]), g["id"])
            state.seen[g["id"]] = g["acquired"]
            ingested.append(g)
            if not state.last_acquired or g["acquired"] > state.last_acquired:
                state.last_acquired = g["acquired"]
            if done % 50 == 0:
                log(f"  [{done}/{len(todo)}] {g['acquired']}")

    if bootstrapping:
        ok = len(ingested) > 0 and len(failed) <= 0.2 * max(len(todo), 1) and "search" not in reasons
        state.bootstrap = {"ok": ok, "start": iso(start), "end": iso(now),
                           "n_granules": len(ingested), "n_failed": len(failed)}
        if not ok:
            reasons["bootstrap"] = (
                f"baseline bootstrap failed: {len(ingested)} granules ingested, {len(failed)} failed"
                + (f", {reasons['search']}" if "search" in reasons else "")
            )

    masks = state.evaluate(window_h, min_obs, base_max)
    area = grid.row_area_km2()

    def km2(m):
        return round(float((m * area).sum()), 3)

    pop, pop_src, pop_reason = people_grid(gpw_sources, grid)
    if pop_reason:
        reasons["people"] = pop_reason

    lc, excl, lc_reason = (landcover_grid(landcover_path, grid) if landcover_path
                           else (None, None, "no land cover raster configured"))
    br, br_reason = brazil_grid(outline_path, grid) if outline_path else (None, "no Brazil outline configured")
    seasonal, never_water, gsw_reason = (gsw_grid(*gsw_paths, grid) if gsw_paths
                                         else (None, None, "no JRC GSW rasters configured"))
    rain_info, norain, rain_reason = None, None, None
    if not imerg_dir:
        rain_reason = "no IMERG daily directory configured"
    elif state.last_acquired is None:
        rain_reason = "no DSWx acquisition to anchor the rain window"
    elif never_water is None:
        rain_reason = "rule needs JRC GSW occurrence (missing)"
    else:
        end = parse_iso(state.last_acquired).date()
        support, rain_info, rain_reason = rain_support_grid(imerg_dir, end, grid)
        if support is not None:
            norain = (support < RAIN_MIN_MM) & never_water
            del support
    for k, r in (("landcover", lc_reason), ("brazil", br_reason), ("seasonal_water", gsw_reason),
                 ("rain_support", rain_reason)):
        if r:
            reasons[k] = r

    valid = state.bootstrap.get("ok", False) and state.last_acquired is not None
    flood_path = out_dir / "flood_dswx.geojson"
    stat_keys = ("flooded_km2", "new_km2", "receded_km2", "people_flooded", "lower_confidence_km2",
                 "lower_confidence_new_km2", "lower_confidence_people", "excluded_water_wetland_km2",
                 "outside_brazil_km2", "sub_mmu_km2", "seasonal_downgraded_km2",
                 "seasonal_downgraded_people", "seasonal_flagged_pixel_km2", "seasonal_flagged_pixel_people",
                 "norain_downgraded_km2", "norain_downgraded_people", "norain_flagged_pixel_km2",
                 "all_candidates_km2", "all_candidates_people")
    if valid and lc is not None and br is not None:
        prev = state.prev_flood.astype(bool) if state.has_prev else np.zeros(grid.shape, bool)
        masks = apply_context(masks, lc, excl, br, prev, seasonal, norain)
        del excl, br, seasonal, norain
        poly = polygonize(state, masks, pop, lc, flood_path, min_area_km2, simplify_m)
        tot = poly["totals"]
        stats = {
            "flooded_km2": tot["standard"]["km2"], "new_km2": tot["std_new"]["km2"],
            "receded_km2": km2(masks["receded"]) if state.has_prev else None,
            "people_flooded": tot["standard"]["people"],
            "lower_confidence_km2": tot["lower"]["km2"],
            "lower_confidence_new_km2": tot["lower_new"]["km2"],
            "lower_confidence_people": tot["lower"]["people"],
            # in-Brazil candidate pixels in clusters < min_area_km2: not mapped,
            # not in the headline
            "sub_mmu_km2": round(max(0.0, km2(masks["candidate"]) - tot["standard"]["km2"]
                                     - tot["lower"]["km2"]), 3),
            # what the GSW rule moved standard -> lower in the MAPPED polygons
            # (= change to the headline); *_pixel_* also counts sub-MMU specks.
            # null when the GSW rasters are missing
            "seasonal_downgraded_km2": poly["moved"]["seasonal_downgraded"]["km2"] if gsw_reason is None else None,
            "seasonal_downgraded_people": poly["moved"]["seasonal_downgraded"]["people"] if gsw_reason is None else None,
            "norain_downgraded_km2": poly["moved"]["norain_downgraded"]["km2"] if rain_reason is None else None,
            "norain_downgraded_people": poly["moved"]["norain_downgraded"]["people"] if rain_reason is None else None,
            "norain_flagged_pixel_km2": km2(masks["norain_downgraded"]) if rain_reason is None else None,
            # every mapped candidate, standard + lower (shown alongside the headline)
            "all_candidates_km2": round(tot["standard"]["km2"] + tot["lower"]["km2"], 3),
            "all_candidates_people": round(tot["standard"]["people"] + tot["lower"]["people"], 1),
            "seasonal_flagged_pixel_km2": km2(masks["seasonal_downgraded"]) if gsw_reason is None else None,
            "seasonal_flagged_pixel_people": ((None if pop is None else round(float(pop[masks["seasonal_downgraded"]].sum()), 1))
                                              if gsw_reason is None else None),
            "excluded_water_wetland_km2": km2(masks["excluded"]),
            "outside_brazil_km2": km2(masks["outside"]),
        }
        if not state.has_prev:
            reasons["receded_km2"] = "first pass: no previous candidate mask to compare"
        state.prev_flood = masks["candidate"].astype(np.uint8)
        state.has_prev = True
    else:
        _atomic_write(flood_path, json.dumps({"type": "FeatureCollection", "features": []}) + "\n")
        poly = {"features": 0}
        stats = dict.fromkeys(stat_keys)
        reasons.setdefault("flooded_km2", "no valid baseline/observations (see bootstrap)" if not valid
                           else "land cover or Brazil outline missing: unfiltered water is not published")
    if pop is None:
        for k in ("people_flooded", "lower_confidence_people", "all_candidates_people"):
            stats[k] = None

    # latency of the newest pass only (a bootstrap backlog would inflate it)
    newest = max((parse_iso(g["acquired"]) for g in ingested), default=None)
    lat = [parse_iso(g["available"]) - parse_iso(g["acquired"]) for g in ingested
           if g.get("available") and newest - parse_iso(g["acquired"]) <= timedelta(hours=SAME_PASS_H)]
    latency_h = round(float(np.median([x.total_seconds() for x in lat])) / 3600, 2) if lat else None
    if latency_h is None:
        reasons["latency_h"] = "no granules ingested this run"
    meta = {
        "source": SOURCE,
        "status": "candidate",
        "generated_at": iso(datetime.now(UTC)),
        "latest_acquired": state.last_acquired,
        "latency_h": latency_h,
        "n_granules": len(ingested),
        "n_failed": len(failed),
        "n_granules_total": state.n_ingested,   # granules that added observations, all runs
        **stats,
        "raw_candidate_km2": km2(masks["flood"]),
        "unbaselined_water_km2": km2(masks["unbaselined"]),
        "observed_km2": km2(masks["observed"]),
        "coverage_pct": round(100 * float((masks["observed"] * area).sum() / (area.sum() * grid.shape[1])), 2),
        "features": poly["features"],
        "grid": {"crs": "EPSG:4326", "res_deg": grid.res, "shape": list(grid.shape),
                 "bbox": [grid.west, grid.south, grid.east, grid.north]},
        "params": {"window_h": window_h, "min_obs": min_obs, "base_max": base_max,
                   "min_area_km2": poly.get("min_area_km2", min_area_km2),
                   "simplify_m": poly.get("simplify_m", simplify_m),
                   "water_classes": list(WTR_WATER), "bootstrap_days": bootstrap_days,
                   "gsw_occurrence_min_pct": GSW_OCC_MIN, "gsw_seasonality_min_months": GSW_MONTHS_MIN},
        "bootstrap": state.bootstrap,
        "people_source": gpw_label if pop_src else None,
        "landcover_source": None if lc is None else "MCD12Q1.061 2025 LC_Type1 (IGBP)",
        "brazil_outline_source": None if br_reason else "IBGE malhas v3 paises/BR",
        "seasonal_water_source": None if gsw_reason else "JRC Global Surface Water v1.4 (1984-2021), cell mean",
        "rain_rule": rain_info,
        "rain_source": None if rain_reason else "IMERG Early daily (GES DISC), 0.1 deg",
        "_null_reasons": reasons,
    }
    del masks, pop, lc
    state.save(state_path)
    write_meta(out_dir / "dswx_meta.json", meta)      # meta last
    return meta
