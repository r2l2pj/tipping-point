"""rs_uruguai_2026 TPI-flood replay (observed + issued NOAA GFS), Uruguay River corridor.

Same index as the live Now job (live.tpi.tpi: R = rain_score(3-day rain vs the IMERG
0.1 deg climatology clim_imerg_0p1.tif), S = SMAP L4 rootzone wetness, T = terrain),
on the Now 0.01 deg grid over the case bbox; permanent water (JRC GSW >= 50 %) = nodata.

  observed band D : P3 = IMERG Early daily, days D-3 .. D-1 (rain through D-1; replay
                    convention, docs/conventions-dates.md); S = SMAP daily mean of D-1
  GFS issue I, k  : frame E = I+k (k = 1..3), 3 days E-2..E: IMERG Early for days < I,
                    GFS 00Z run of I for days >= I (L28b); S held at I-1

IMERG *Final* is not released for Oct 2026, so observed rain is IMERG *Early*, scored
against the Final-based climatology (same known bias as the live Now job).

Inputs: ~/.cache/lead-analysis/uru-replay/imerg_e/<YYYYMMDD>.npy (GPM_3IMERGDE subsets),
GFS cubes rs_uruguai_2026_gfs_<issue>.tif (d1..d4 = days I..I+3; tools/regen_gfs_replay.py) in $URU_GFS_DIR,
SMAP via Earth Engine NASA/SMAP/SPL4SMGP/008, south_brazil terrain statics.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import Resampling, reproject

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "pipeline")]

from live import static_fetch  # noqa: E402
from live.grid import dst_lats_lons, dst_transform  # noqa: E402
from live.run_now import now_water_mask  # noqa: E402
from live.tpi import classify, terrain_flood, terrain_slide, tpi  # noqa: E402

CASE = "rs_uruguai_2026"
# case AOI (config.yaml): Uruguay River reach of the Fronteira Oeste, Barra do Quaraí .. São Borja
BOUNDS = (-58.0, -30.6, -55.3, -28.2)
CACHE = Path.home() / ".cache/lead-analysis/uru-replay"
IMERG = CACHE / "imerg_e_big"          # GPM_3IMERGDE subsets over the AOI + 0.1 deg
MAIN = Path.home() / "code/r2l2pj/tipping-point-data"
TERRAIN = MAIN / "data/static/south_brazil/terrain"
GSW = Path.home() / ".cache/lead-analysis/gsw/gsw_occurrence_2021.tif"
GFS_DIR = Path(os.environ.get("URU_GFS_DIR", REPO / "data" / CASE / "forecast_rain"))  # tools/regen_gfs_replay.py
OBS = [dt.date(2026, 9, 28) + dt.timedelta(days=k) for k in range(35)]      # Sep 28 .. Nov 1
# every daily 00Z issue Sep 25 .. Oct 31: any target day Sep 28 .. Nov 1 has leads 1-3
ISSUES = [dt.date(2026, 9, 25) + dt.timedelta(days=k) for k in range(37)]
GPW = MAIN / "data/now/static/gpw/gpw_popcount_2020_30sec.tif"

LATS, LONS = dst_lats_lons(BOUNDS)
TR = dst_transform(BOUNDS)
SHAPE = (LATS.size, LONS.size)


def regrid(arr, src_tr, method=Resampling.bilinear):
    out = np.full(SHAPE, np.nan, np.float64)
    reproject(np.asarray(arr, np.float64), out, src_transform=src_tr, src_crs="EPSG:4326",
              src_nodata=np.nan, dst_transform=TR, dst_crs="EPSG:4326", dst_nodata=np.nan,
              resampling=method)
    return out


def imerg_day(d: dt.date):
    f = IMERG / f"{d:%Y%m%d}.npy"
    if not f.exists():
        return None
    i0, i1, j0, j1 = np.load(IMERG / "_meta.npy")
    a = np.load(f).astype(np.float64).T          # [lon, lat asc] -> [lat asc, lon]
    a[(a < 0) | ~np.isfinite(a)] = np.nan
    a = a[::-1]                                   # north-up
    from rasterio.transform import from_origin
    tr = from_origin(-180 + i0 * 0.1, -90 + (j1 + 1) * 0.1, 0.1, 0.1)
    return regrid(a, tr)


def gfs_day(issue: dt.date, day: dt.date):
    p = GFS_DIR / f"{CASE}_gfs_{issue:%Y%m%d}.tif"
    k = (day - issue).days + 1
    if not p.exists() or not 1 <= k <= 4:
        return None
    with rasterio.open(p) as s:
        return regrid(s.read(k).astype(np.float64), s.transform)


def smap_days(days):
    """{date: wetness on grid} via Earth Engine SPL4SMGP/008 daily mean (bilinear)."""
    import io
    from gee.common import ee_init
    ee_init()
    import ee
    col = ee.ImageCollection("NASA/SMAP/SPL4SMGP/008").select("sm_rootzone_wetness")
    out = {}
    imgs = []
    for d in days:
        imgs.append(col.filterDate(d.isoformat(), (d + dt.timedelta(days=1)).isoformat())
                    .mean().resample("bilinear").rename(f"d{d:%Y%m%d}"))
    req = {"expression": ee.Image.cat(imgs), "fileFormat": "NPY",
           "grid": {"dimensions": {"width": SHAPE[1], "height": SHAPE[0]},
                    "affineTransform": {"scaleX": TR.a, "shearX": 0, "translateX": TR.c,
                                        "shearY": 0, "scaleY": TR.e, "translateY": TR.f},
                    "crsCode": "EPSG:4326"}}
    arr = np.load(io.BytesIO(ee.data.computePixels(req)))
    for d in days:
        a = np.asarray(arr[f"d{d:%Y%m%d}"], dtype=np.float64)
        a[a <= -9000] = np.nan
        out[d] = a
    return out


def terrain():
    def rd(name):
        with rasterio.open(TERRAIN / name) as s:
            return regrid(s.read(1).astype(np.float64), s.transform)
    slope, hand = rd("slope_deg.tif"), rd("hand_m.tif")
    return terrain_slide(slope), terrain_flood(hand, slope), hand


def clim(day: dt.date):
    path = static_fetch._clim_file()
    return static_fetch._clim_from_file(path, day.timetuple().tm_yday, BOUNDS)


def main() -> int:
    from tp.bundle import _class_png_3857
    out_cog = REPO / "data" / CASE / "cog"
    out_png = REPO / "data" / CASE / "png"
    out_cog.mkdir(parents=True, exist_ok=True)
    t_slide, t_flood, hand = terrain()
    water, wbasis = now_water_mask(BOUNDS, *SHAPE, occ_path=GSW)
    water = np.zeros(SHAPE, bool) if water is None else water
    corridor = (np.nan_to_num(hand, nan=99) <= 5.0) & ~water
    rain = {d: imerg_day(d) for d in [dt.date(2026, 9, 22) + dt.timedelta(days=k) for k in range(40)]}  # Sep 22 .. Oct 31
    missing = [d.isoformat() for d, a in rain.items() if a is None]
    sm_days = sorted({d - dt.timedelta(days=1) for d in OBS} | {i - dt.timedelta(days=1) for i in ISSUES})
    sm = smap_days(sm_days)
    lat_c = LATS[:, None] * np.ones((1, SHAPE[1]))
    km2 = (TR.a * 111.32) * (abs(TR.e) * 110.574) * np.cos(np.deg2rad(lat_c))
    rows, layers = [], []
    # per-municipality summary for the City card (live.places helpers)
    from shapely.geometry import shape as _shape
    from live import places as lp
    mfc = json.loads((REPO / "data" / CASE / "geojson" / "municipal_boundaries.geojson").read_text())["features"]
    mgeoms = [_shape(f["geometry"]).buffer(0) for f in mfc]
    mlab = lp._ensure_cell(lp._rasterize(mgeoms, SHAPE, TR), mgeoms, TR).ravel()
    nm = len(mgeoms)
    daily = {f["properties"]["code"]: {"name": f["properties"]["name"], "uf": f["properties"]["uf"],
                                       "observed": {}, "forecast": {}} for f in mfc}

    def muni(tf, cls):
        mx = lp.group_max(mlab, np.where(np.isfinite(tf), tf, np.nan).ravel(), nm)
        _pop, ph = lp._people(mgeoms, cls >= 2, TR, GPW, nm)
        out = {}
        for i, f in enumerate(mfc):
            v = mx[i + 1]
            out[f["properties"]["code"]] = {
                "flood_class_max": None if not np.isfinite(v) else int(classify(np.array([v]))[0]),
                "tpi_max": None if not np.isfinite(v) else round(float(v), 3),
                "people_high": None if ph is None else int(round(ph[i + 1]))}
        return out

    def finish(tpi_f, valid):
        tpi_f = np.where(water | ~valid, np.nan, tpi_f)
        cls = classify(tpi_f)
        return tpi_f, cls

    def stats(tpi_f, cls):
        fin = np.isfinite(tpi_f)
        cor = corridor & fin
        return {"critical_km2": round(float(km2[(cls >= 3) & fin].sum()), 2),
                "alert_km2": round(float(km2[(cls >= 2) & fin].sum()), 2),
                "corridor_max_class": int(cls[cor].max()) if cor.any() else None,
                "corridor_max_tpi": round(float(np.nanmax(tpi_f[cor])), 3) if cor.any() else None,
                "corridor_alert_km2": round(float(km2[(cls >= 2) & cor].sum()), 2),
                "corridor_critical_km2": round(float(km2[(cls >= 3) & cor].sum()), 2)}

    # observed
    for d in OBS:
        parts = [rain.get(d - dt.timedelta(days=j)) for j in (1, 2, 3)]
        if any(p is None for p in parts):
            continue
        p3 = parts[0] + parts[1] + parts[2]
        s = sm.get(d - dt.timedelta(days=1))
        o = tpi(p3, s, clim(d), t_slide, t_flood)
        tf, cls = finish(o["tpi_flood"], np.isfinite(p3) & np.isfinite(s))
        tag = f"{d:%Y%m%d}"
        prof = dict(driver="GTiff", height=SHAPE[0], width=SHAPE[1], count=4, dtype="float32",
                    crs="EPSG:4326", transform=TR, nodata=np.nan, compress="deflate")
        cog = out_cog / f"{CASE}_tpi_flood_obs_{tag}.tif"
        with rasterio.open(cog, "w", **prof) as dst:
            for i, (n, a) in enumerate((("tpi", tf), ("r", o["r"]), ("s", o["s"]), ("t", t_flood)), 1):
                dst.write(np.asarray(a, np.float32), i)
                dst.set_band_description(i, n)
        png, bounds = _class_png_3857(cls, np.isfinite(tf), TR, "EPSG:4326")
        (out_png / f"tpi_flood_{tag}.png").write_bytes(png)
        st = stats(tf, cls)
        for code, v in muni(tf, cls).items():
            daily[code]["observed"][d.isoformat()] = v
        rows.append({"date": d.isoformat(), "kind": "observed", "p3_corridor_mm": round(float(np.nanmean(p3[corridor])), 1),
                     "r_corridor_max": round(float(np.nanmax(np.where(corridor, o["r"], np.nan))), 3), **st})
        layers.append({"id": "tpi_flood", "date": d.isoformat(), "png": f"{CASE}/png/tpi_flood_{tag}.png",
                       "cog": f"{CASE}/cog/{cog.name}", "bounds": bounds, "forecast": False, "kind": "observed",
                       "critical_km2": st["critical_km2"], "data_basis": "IMERG Early (Final not released) vs IMERG Final 0.1 deg climatology"})
    # GFS issues
    for I in ISSUES:
        bands, names = [], []
        s = sm.get(I - dt.timedelta(days=1))
        for k in (1, 2, 3):
            E = I + dt.timedelta(days=k)
            parts = []
            for x in (E - dt.timedelta(days=2), E - dt.timedelta(days=1), E):
                parts.append(rain.get(x) if x < I else gfs_day(I, x))
            if any(p is None for p in parts):
                continue
            p3 = parts[0] + parts[1] + parts[2]
            o = tpi(p3, s, clim(E), t_slide, t_flood)
            tf, cls = finish(o["tpi_flood"], np.isfinite(p3) & np.isfinite(s))
            bands += [tf, o["r"]]
            names += [f"d{k}_flood", f"d{k}_r"]
            png, bounds = _class_png_3857(cls, np.isfinite(tf), TR, "EPSG:4326")
            pn = f"tpi_flood_gfs_d{k}_{E:%Y%m%d}.png"
            (out_png / pn).write_bytes(png)
            st = stats(tf, cls)
            for code, v in muni(tf, cls).items():
                daily[code]["forecast"].setdefault(I.isoformat(), {})[E.isoformat()] = v
            rows.append({"date": E.isoformat(), "kind": "gfs", "issue": I.isoformat(), "k": k,
                         "p3_corridor_mm": round(float(np.nanmean(p3[corridor])), 1), **st})
            layers.append({"id": f"tpi_flood_gfs_d{k}", "date": E.isoformat(), "issued": I.isoformat(),
                           "png": f"{CASE}/png/{pn}", "cog": f"{CASE}/cog/{CASE}_tpi_gfs_{I:%Y%m%d}.tif",
                           "bounds": bounds, "forecast": True, "forecast_source": "noaa-gfs",
                           "kind": "forecast", "critical_km2": st["critical_km2"]})
        if bands:
            prof = dict(driver="GTiff", height=SHAPE[0], width=SHAPE[1], count=len(bands), dtype="float32",
                        crs="EPSG:4326", transform=TR, nodata=np.nan, compress="deflate")
            with rasterio.open(out_cog / f"{CASE}_tpi_gfs_{I:%Y%m%d}.tif", "w", **prof) as dst:
                for i, (n, a) in enumerate(zip(names, bands, strict=True), 1):
                    dst.write(np.asarray(a, np.float32), i)
                    dst.set_band_description(i, n)
    (REPO / "data" / CASE / "places_daily.json").write_text(json.dumps({
        "schema": "tipping-point/places-daily@1", "case": CASE,
        "definition": ("per municipality and frame: flood_class_max = class of the max TPI-flood over the "
                       "municipality's 0.01 deg cells (0-3 Baixo..Crítico); tpi_max; people_high = people in "
                       "High+ (TPI-flood > 0.4) cells, native 30\" GPW covered fraction. observed[date] = band "
                       "with rain through date-1 (IMERG Early); forecast[issue][date] = NOAA GFS run of issue"),
        "places": daily}, ensure_ascii=False, separators=(",", ":")))
    res = {"rows": rows, "layers": layers, "imerg_missing": missing, "water_mask": wbasis,
           "corridor": "HAND <= 5 m cells (south_brazil HAND static) outside permanent water"}
    (CACHE / "replay_result.json").write_text(json.dumps(res, indent=1, default=str))
    for r in rows:
        print(r["kind"], r.get("issue", ""), r["date"], "corridor max class", r["corridor_max_class"],
              "tpi", r["corridor_max_tpi"], "P3", r["p3_corridor_mm"], "crit km2", r["critical_km2"], "alert", r["alert_km2"])
    print("imerg missing:", missing)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
