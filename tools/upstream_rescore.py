"""L35 experiment: flood R with an upstream-basin rain term, re-scored against the
published (current-formula) replay frames. Nothing published is modified.

  R_flood_new(cell, D) = max(R_local(cell, D), R_up(basin(cell), D))
  TPI_flood_new        = (R_flood_new · S · T_flood)^(1/3)        (weights 1/3)

R_local, S, T_flood are exactly the published inputs (R/S series bilinear onto
the 90 m T grid reproduce the published TPI_flood bit-for-bit; for the issued
GFS frames R_local = TPI^3 / (S_{D-1} · T), the same S_{D-1} the frames used).
R_up(b, D) = rain_score(P3_up, p90, p95, p99) where P3_up is the 3-day rain
(sum over [D-3d, D)) averaged over the full upstream area of HydroBASINS lev-10
basin b, and the percentiles are that same upstream-mean 3-day sum's own
climatology (2001-2023, end dates D +- 30 d step 3) — the pipeline's
climatology definition, applied to the basin-mean series. Issued-GFS frames use
observed IMERG for days < issue and the GFS 00Z run for days >= issue (L28b).

Inputs (intermediate/upstream/<case>/, from the L35 workers):
  basin_id_90m.tif  basins.geojson  upstream.json  imerg_daily.npz  gfs_issued.npz

Outputs: intermediate/upstream/<case>/cog/ (new TPI_flood series + GFS frames in
the published layout) and intermediate/upstream/compare.json (old vs new).

    PYTHONPATH=.:pipeline .venv/bin/python tools/upstream_rescore.py [--case wa_2025 ...]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.warp import Resampling, reproject
from shapely import contains_xy
from shapely.ops import unary_union

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO))
import warned_before as wb

from live.tpi import rain_score

CASES = {"wa_2025": "20251210", "rs_2024": "20240502"}
UP = REPO / "intermediate" / "upstream"
KM = 111.32
ALTO, CRIT = 0.4, 0.6


def _d(s) -> dt.date:
    s = str(s)
    return dt.date(int(s[:4]), int(s[4:6]), int(s[6:8]))


# ---------- upstream means -------------------------------------------------
def grid_weights(geom, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """Equal-area weights of grid cells whose centre lies in geom (nearest cell if none)."""
    LON, LAT = np.meshgrid(lon, lat)
    inside = contains_xy(geom, LON, LAT)
    w = np.where(inside, np.cos(np.deg2rad(LAT)), 0.0)
    if w.sum() == 0:
        c = geom.representative_point()
        i = int(np.argmin(np.abs(lat - c.y)))
        j = int(np.argmin(np.abs(lon - c.x)))
        w[i, j] = 1.0
    return w / w.sum()


def wmean(cube: np.ndarray, w: np.ndarray) -> np.ndarray:
    """Weighted mean over the last two axes, NaN-aware (NaN if all weight is NaN)."""
    m = np.isfinite(cube)
    num = (np.where(m, cube, 0.0) * w).sum((-2, -1))
    den = (m * w).sum((-2, -1))
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(den > 0.5, num / den, np.nan)


def upstream_geoms(case: str) -> dict[int, object]:
    gdf = gpd.read_file(UP / case / "basins.geojson")
    gdf["HYBAS_ID"] = gdf["HYBAS_ID"].astype("int64")
    geom = dict(zip(gdf["HYBAS_ID"], gdf.geometry))
    up = json.loads((UP / case / "upstream.json").read_text())
    sets = up.get("upstream", up)
    return {int(b): unary_union([geom[int(i)] for i in ids if int(i) in geom])
            for b, ids in sets.items() if str(b).isdigit()}


def climatology(series: dict[dt.date, float], target: dt.date) -> tuple[float, float, float] | None:
    vals = []
    for y in range(2001, 2024):
        try:
            ref = dt.date(y, target.month, target.day)
        except ValueError:
            continue
        for off in range(-30, 31, 3):
            e = ref + dt.timedelta(days=off)
            days = [e - dt.timedelta(days=k) for k in (1, 2, 3)]
            v = [series.get(x) for x in days]
            if any(x is None or not np.isfinite(x) for x in v):
                continue
            vals.append(sum(v))
    if len(vals) < 300:                       # 23 y x 21 offsets = 483 when complete
        return None
    p = np.percentile(vals, [90, 95, 99])
    return float(p[0]), float(p[1]), float(p[2])


def r_up_tables(case: str, frame_days: list[dt.date], issues: list[dt.date]):
    """{basin: {D: R_up}} for observed frames and {(issue,k): {basin: R_up}} for GFS."""
    geoms = upstream_geoms(case)
    im = np.load(UP / case / "imerg_daily.npz")
    days = [_d(x) for x in im["dates"]]
    gf = np.load(UP / case / "gfs_issued.npz")
    g_issue = [_d(x) for x in gf["issue"]]
    obs_mean, gfs_mean, clim_cache = {}, {}, {}
    for b, geom in geoms.items():
        w = grid_weights(geom, im["lat"], im["lon"])
        obs_mean[b] = dict(zip(days, wmean(im["mm"], w)))
        wg = grid_weights(geom, gf["lat"], gf["lon"])
        gfs_mean[b] = wmean(gf["mm"], wg)            # [issue, k]

    def clim(b, target):
        key = (b, target.month, target.day)
        if key not in clim_cache:
            clim_cache[key] = climatology(obs_mean[b], target)
        return clim_cache[key]

    def score(b, p3, target):
        c = clim(b, target)
        if c is None or not np.isfinite(p3):
            return np.nan
        return float(rain_score(np.array([p3]), *[np.array([x]) for x in c])[0])

    obs_r = {b: {} for b in geoms}
    for b in geoms:
        for D in frame_days:
            p3 = sum(obs_mean[b].get(D - dt.timedelta(days=k), np.nan) for k in (1, 2, 3))
            obs_r[b][D] = score(b, p3, D)
    gfs_r = {}
    for I in issues:
        if I not in g_issue:
            continue
        ii = g_issue.index(I)
        for k in (1, 2, 3):
            E = I + dt.timedelta(days=k)
            gfs_r[(I, k)] = {}
            for b in geoms:
                tot = 0.0
                for x in (E - dt.timedelta(days=j) for j in (1, 2, 3)):
                    tot += (obs_mean[b].get(x, np.nan) if x < I
                            else gfs_mean[b][ii, (x - I).days])
                gfs_r[(I, k)][b] = score(b, tot, E)
    return obs_r, gfs_r, sorted(geoms)


# ---------- rasters ---------------------------------------------------------
def to_grid(path: Path, band: int, like) -> np.ndarray:
    with rasterio.open(path) as s:
        out = np.full(like.shape, np.nan, np.float64)
        a = s.read(band).astype(np.float64)
        if s.nodata is not None and np.isfinite(s.nodata):
            a = np.where(a == s.nodata, np.nan, a)
        reproject(a, out, src_transform=s.transform, src_crs=s.crs, src_nodata=np.nan,
                  dst_transform=like.transform, dst_crs=like.crs, dst_nodata=np.nan,
                  resampling=Resampling.bilinear)
    return out


def lookup(basin_ids: np.ndarray, table: dict[int, float]) -> np.ndarray:
    out = np.zeros(basin_ids.shape, np.float64)
    for b, v in table.items():
        if np.isfinite(v):
            out[basin_ids == b] = v
    return out


def new_tpi(r_local, r_up, s, t, old):
    r = np.maximum(np.nan_to_num(r_local, nan=0.0), r_up)
    v = np.cbrt(np.clip(r, 0, None) * np.clip(s, 0, None) * np.clip(t, 0, None))
    return np.where(np.isfinite(old), v, np.nan).astype(np.float32)


def cell_km2(like) -> np.ndarray:
    t = like.transform
    rows = np.arange(like.height)
    lat = t.f + (rows + 0.5) * t.e
    return (abs(t.a * t.e) * KM * KM * np.cos(np.deg2rad(lat)))[:, None] * np.ones((1, like.width))


def run_case(case: str) -> dict:
    ref = CASES[case]
    cog = REPO / "data" / case / "cog"
    out_cog = UP / case / "cog"
    out_cog.mkdir(parents=True, exist_ok=True)
    series_old = cog / f"{case}_tpi_flood_{ref}.tif"
    like = rasterio.open(series_old)
    with rasterio.open(UP / case / "basin_id_90m.tif") as b:
        if (b.transform, b.shape) != (like.transform, like.shape):
            raise SystemExit("basin_id_90m.tif is not on the TPI grid")
        basin = b.read(1).astype(np.int64)
    t = rasterio.open(cog / f"{case}_t_flood_{ref}.tif").read(1).astype(np.float64)
    r_path, s_path = cog / f"{case}_r_series_{ref}.tif", cog / f"{case}_s_series_{ref}.tif"
    rdesc = list(rasterio.open(r_path).descriptions)
    sdesc = list(rasterio.open(s_path).descriptions)
    frame_days = [_d(x) for x in like.descriptions]
    issues = sorted(_d(p.stem.rsplit("_", 1)[1]) for p in cog.glob(f"{case}_tpi_gfs_*.tif"))
    obs_r, gfs_r, basins = r_up_tables(case, frame_days, issues)

    # observed series
    prof = dict(like.profile, count=len(frame_days), dtype="float32", nodata=np.nan)
    new_series = out_cog / f"{case}_tpi_flood_{ref}.tif"
    with rasterio.open(new_series, "w", **prof) as dst:
        for i, D in enumerate(frame_days, 1):
            tag = f"{D:%Y%m%d}"
            old = like.read(i).astype(np.float64)
            rl = to_grid(r_path, rdesc.index(tag) + 1, like)
            s = to_grid(s_path, sdesc.index(tag) + 1, like)
            ru = lookup(basin, {b: obs_r[b][D] for b in basins})
            dst.write(new_tpi(rl, ru, s, t, old), i)
            dst.set_band_description(i, tag)
    # issued GFS frames (slide bands copied unchanged)
    for I in issues:
        src_p = cog / f"{case}_tpi_gfs_{I:%Y%m%d}.tif"
        dst_p = out_cog / src_p.name
        prev = I - dt.timedelta(days=1)
        if f"{prev:%Y%m%d}" not in sdesc or (I, 1) not in gfs_r:
            shutil.copy(src_p, dst_p)
            continue
        s = to_grid(s_path, sdesc.index(f"{prev:%Y%m%d}") + 1, like)
        with rasterio.open(src_p) as src:
            p = dict(src.profile)
            bands = {n: src.read(i).astype(np.float64) for i, n in enumerate(src.descriptions, 1)}
        with rasterio.open(dst_p, "w", **p) as dst:
            for i, (n, a) in enumerate(bands.items(), 1):
                if n.endswith("_flood"):
                    k = int(n[1])
                    st = s * t
                    with np.errstate(invalid="ignore", divide="ignore"):
                        rl = np.where(st > 0, np.clip(a, 0, None) ** 3 / st, 0.0)
                    ru = lookup(basin, gfs_r[(I, k)])
                    a = new_tpi(np.clip(rl, 0, 1), ru, s, t, a)
                dst.write(a.astype(np.float32), i)
                dst.set_band_description(i, n)
    return compare(case, series_old, new_series, cog, out_cog, frame_days)


def area_stats(old_path, new_path, frame_days, like, extra_masks: dict[str, np.ndarray]) -> list[dict]:
    km2 = cell_km2(like)
    rows = []
    with rasterio.open(old_path) as o, rasterio.open(new_path) as n:
        for i, D in enumerate(frame_days, 1):
            a, b = o.read(i), n.read(i)
            row = {"date": D.isoformat()}
            for name, arr in (("old", a), ("new", b)):
                row[f"{name}_critical_km2"] = round(float(km2[arr > CRIT].sum()), 1)
                row[f"{name}_alto_plus_km2"] = round(float(km2[arr > ALTO].sum()), 1)
                for mname, m in extra_masks.items():
                    row[f"{name}_alto_plus_{mname}_km2"] = round(float(km2[(arr > ALTO) & m].sum()), 1)
                    row[f"{name}_critical_{mname}_km2"] = round(float(km2[(arr > CRIT) & m].sum()), 1)
            rows.append(row)
    return rows


def compare(case, series_old, series_new, cog_old, cog_new, frame_days) -> dict:
    like = rasterio.open(series_old)
    res: dict = {"case": case}
    if case == "wa_2025":
        lt = json.loads((REPO / "data" / case / "forecast_lead_time.json").read_text())
        anchor = dt.datetime.fromisoformat(lt["anchor_utc"])
        idx, w = wb.flood_pixels(cog_old / "wa_2025_flood_nisar_20251210.tif", like)
        for name, ser, cdir in (("old", series_old, cog_old), ("new", series_new, cog_new)):
            r = wb.evaluate(cdir, case, "flood", ser, idx, w, anchor)
            res[f"warned_{name}"] = {k: r[k] for k in ("observed", "forecast_gfs", "best_issued_gfs_before_anchor")}
            res[f"curve_{name}"] = [(c["date"], c["alto_or_critico_pct"], c["critico_pct"]) for c in r["by_date_observed"]]
            res[f"by_issue_{name}"] = [(c["issue"], c["alto_or_critico_pct"], c["critico_pct"]) for c in r["by_issue_gfs"]]
        # false-Alto+ check: inside the NISAR swath, outside confirmed flood and pre-event water
        flood = to_grid(cog_old / "wa_2025_flood_nisar_20251210.tif", 1, like) > 0.25
        water = to_grid(cog_old / "wa_2025_water_nisar_20251210.tif", 1, like) > 0.25
        swath = np.isfinite(to_grid(cog_old / "wa_2025_nisar_post_20251216_60m.tif", 1, like))
        res["areas"] = area_stats(series_old, series_new, frame_days, like,
                                  {"not_flooded_in_swath": swath & ~flood & ~water,
                                   "flooded": flood})
    else:
        res["areas"] = area_stats(series_old, series_new, frame_days, like, {})
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--case", action="append", choices=sorted(CASES))
    args = ap.parse_args(argv)
    out = {c: run_case(c) for c in (args.case or sorted(CASES))}
    p = UP / "compare.json"
    p.write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps(out, indent=1, default=str)[:6000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
