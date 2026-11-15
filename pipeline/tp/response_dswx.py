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
  the newest acquisition, baseline has >= ``min_obs`` observations and
  baseline water frequency < ``base_max``. Pixels with too little baseline
  are reported as ``unbaselined_water_km2``, never as candidates.
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
from datetime import UTC, datetime, timedelta
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


# --- outputs ---------------------------------------------------------------

def polygonize(state: State, masks: dict, pop: np.ndarray | None, out_path: Path,
               min_area_km2: float, simplify_m: float, max_bytes: int = 5_000_000) -> dict:
    """flood_dswx.geojson with per-polygon props; returns summary stats."""
    import rasterio
    from rasterio.features import rasterize
    from shapely.geometry import shape

    from tp.polygonize import mask_to_geojson

    grid = state.grid
    cls = np.zeros(grid.shape, np.uint8)
    cls[masks["new"]] = 1
    cls[masks["persisting"]] = 2
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
            mask_to_geojson(tif, raw, values=(1, 2), min_area_km2=min_area_km2,
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
                    "change": "new" if f["properties"]["value"] == 1 else "persisting",
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
    return {"features": len(feats), "bytes": len(text.encode()), "simplify_m": simplify_m,
            "min_area_km2": min_area_km2}


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
    now: datetime | None = None,
    bootstrap_days: int = 30,
    overlap_h: int = 24,
    window_h: int = 144,
    min_obs: int = 2,
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

    valid = state.bootstrap.get("ok", False) and state.last_acquired is not None
    flood_path = out_dir / "flood_dswx.geojson"
    if valid:
        poly = polygonize(state, masks, pop, flood_path, min_area_km2, simplify_m)
        people = None if pop is None else round(float(pop[masks["flood"]].sum()), 1)
        stats = {
            "flooded_km2": km2(masks["flood"]), "new_km2": km2(masks["new"]),
            "receded_km2": km2(masks["receded"]) if state.has_prev else None,
            "people_flooded": people,
        }
        if not state.has_prev:
            reasons["receded_km2"] = "first pass: no previous candidate mask to compare"
        state.prev_flood = masks["flood"].astype(np.uint8)
        state.has_prev = True
    else:
        _atomic_write(flood_path, json.dumps({"type": "FeatureCollection", "features": []}) + "\n")
        poly = {"features": 0}
        stats = {"flooded_km2": None, "new_km2": None, "receded_km2": None, "people_flooded": None}
        reasons.setdefault("flooded_km2", "no valid baseline/observations (see bootstrap)")

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
        "unbaselined_water_km2": km2(masks["unbaselined"]),
        "observed_km2": km2(masks["observed"]),
        "coverage_pct": round(100 * float((masks["observed"] * area).sum() / (area.sum() * grid.shape[1])), 2),
        "features": poly["features"],
        "grid": {"crs": "EPSG:4326", "res_deg": grid.res, "shape": list(grid.shape),
                 "bbox": [grid.west, grid.south, grid.east, grid.north]},
        "params": {"window_h": window_h, "min_obs": min_obs, "base_max": base_max,
                   "min_area_km2": poly.get("min_area_km2", min_area_km2),
                   "simplify_m": poly.get("simplify_m", simplify_m),
                   "water_classes": list(WTR_WATER), "bootstrap_days": bootstrap_days},
        "bootstrap": state.bootstrap,
        "people_source": pop_src,
        "_null_reasons": reasons,
    }
    del masks, pop
    state.save(state_path)
    write_meta(out_dir / "dswx_meta.json", meta)      # meta last
    return meta
