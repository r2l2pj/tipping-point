"""P(landslide trigger within 24 h / 48 h / 72 h / 7 d / 14 d) frames for
the Now job (L23/L27), served with lead-ml's ml.local.serve.TriggerModel.

OFF unless NOW_P_TRIGGER=1 (now.yml passes the repo variable). When off,
nothing is written and the manifest carries p_trigger_enabled=false and no
p_trigger layers (the app keys on the layers being present).

Inputs on the NOW 0.01 deg grid (L28b alignment, issue day D = today UTC):
  static   ml_static_now.tif — named bands: elevation, slope, aspect_sin,
           aspect_cos, curvature, relief, landcover, ndvi, clay, sand,
           porosity, p99_daily, p99_3day (built once from lead-ml's sources)
  soil     latest T10:30Z SPL4SMGP sm_surface / sm_rootzone /
           sm_profile_wetness (as in training), held
  rain_obs IMERG Early daily D-7..D-1 (data/now/cog/imerg_early, written by
           run_now each run)
  rain_fc  NOAA GFS 00Z run of D, daily D..D+13
Outputs (data/now/): cog/p_trigger_<h>.tif, png/p_trigger_<h>.png,
p_trigger/layers.json (manifest layers; merged by live.now_manifest).

    python -m live.p_trigger [--date YYYY-MM-DD] [--out-dir data/now]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

import numpy as np

from live.config import NOW_BOUNDS, NOW_DIR, REPO_ROOT

FLAG_ENV = "NOW_P_TRIGGER"
MODEL_PATH = Path(os.environ.get("NOW_P_TRIGGER_MODEL",
                                 REPO_ROOT / "ml" / "models" / "p_trigger_xgb.json"))
STATIC_PATH = Path(os.environ.get(
    "NOW_P_TRIGGER_STATIC",
    REPO_ROOT / "data" / "static" / "south_brazil" / "ml" / "ml_static_now.tif"))
STATIC_BANDS = ("elevation", "slope", "aspect_sin", "aspect_cos", "curvature",
                "relief", "landcover", "ndvi")
TEXTURE_BANDS = ("clay", "sand", "porosity")
SOIL_VARS = ("sm_surface", "sm_rootzone", "sm_profile_wetness")
SMAP_TIME_TAG = "T103000"   # lead-ml trained on the 10:30Z granule only
# default only: the frames served are the model meta's "horizons" (ML v2
# serves 24h/72h/7d), so a model swap needs no code change here
HORIZONS = {"24h": 1, "48h": 2, "72h": 3, "7d": 7, "14d": 14}
LOW_SKILL_DAYS = 7                        # L27: horizons >= 7 d are long-range
# probability bins -> RGBA (transparent below 2 %)
PROB_STOPS = (0.02, 0.10, 0.25, 0.50, 0.75)
PROB_RGB = ((255, 255, 178), (254, 204, 92), (253, 141, 60),
            (240, 59, 32), (189, 0, 38))


def enabled() -> bool:
    return os.environ.get(FLAG_ENV, "") == "1"


def load_static_stack(path: Path = STATIC_PATH) -> dict[str, np.ndarray]:
    """Named bands (band descriptions) of the Now-grid static stack."""
    import rasterio
    with rasterio.open(path) as ds:
        names = list(ds.descriptions)
        if not all(names):
            raise ValueError(f"{path}: every band needs a description")
        arr = ds.read().astype(np.float64)
        if ds.nodata is not None and np.isfinite(ds.nodata):
            arr[arr == ds.nodata] = np.nan
    return dict(zip(names, arr))


def prob_png_3857(p: np.ndarray, transform):
    """Probability (0-1, NaN nodata) -> EPSG:3857 RGBA PNG + bounds."""
    import rasterio
    from live.render import warp_to_3857
    b = np.digitize(np.nan_to_num(p, nan=-1.0), PROB_STOPS)   # 0 = below 2 %
    b = np.where(np.isfinite(p), b, 0).astype(np.uint8)
    b3857, _, bounds = warp_to_3857(b, transform)
    rgba = np.zeros((4, *b3857.shape), dtype=np.uint8)
    for k, rgb in enumerate(PROB_RGB, start=1):
        m = b3857 == k
        for c in range(3):
            rgba[c][m] = rgb[c]
        rgba[3][m] = 210
    with rasterio.MemoryFile() as mf:
        with mf.open(driver="PNG", height=b3857.shape[0], width=b3857.shape[1],
                     count=4, dtype="uint8") as dst:
            dst.write(rgba)
        return mf.read(), bounds


def obs_series(out_dir: Path, today: dt.date, lats, lons) -> np.ndarray:
    """(7, H, W) IMERG Early mm/day for D-7..D-1 from run_now's daily COGs,
    regridded like the TPI obs (linear on cell centres)."""
    import rasterio
    import xarray as xr
    from live.grid import regrid_da
    out = []
    for k in range(7, 0, -1):
        d = today - dt.timedelta(days=k)
        p = out_dir / "cog" / "imerg_early" / f"imerg_early_{d:%Y%m%d}.tif"
        with rasterio.open(p) as ds:
            a = ds.read(1).astype(np.float64)
            t = ds.transform
        la = t.f + (np.arange(a.shape[0]) + 0.5) * t.e
        lo = t.c + (np.arange(a.shape[1]) + 0.5) * t.a
        da = xr.DataArray(a[::-1], dims=("lat", "lon"),
                          coords={"lat": la[::-1], "lon": lo})
        out.append(regrid_da(da, lats, lons))
    return np.stack(out)


def frame_layers(today: dt.date, gfs_issued: str, files: dict,
                 bounds: dict, model_meta: dict,
                 horizons: dict | None = None) -> list[dict]:
    layers = []
    issued = gfs_issued[:16].replace("T", " ") + " UTC"
    serve_label = model_meta.get("serve_label") or (
        "P(landslide trigger within H)")
    for h, n in sorted((horizons or HORIZONS).items(), key=lambda kv: kv[1]):
        if f"cog/p_trigger_{h}.tif" not in files:
            continue
        obs = [(today - dt.timedelta(days=k)).isoformat() for k in range(7, 0, -1)]
        fc = [(today + dt.timedelta(days=k)).isoformat() for k in range(n)]
        layers.append({
            "id": f"p_trigger_{h}", "date": today.isoformat(), "horizon_days": n,
            "png": f"png/p_trigger_{h}.png", "cog": f"cog/p_trigger_{h}.tif",
            "legend": {"type": ("relative_index" if model_meta.get("serve_combine")
                                else "probability"),
                       "stops": [0, 0.1, 0.25, 0.5, 0.75, 1]},
            "title": serve_label.replace("within H", f"within {h}"),
            "serve_note": model_meta.get("serve_note"),
            "bounds": bounds[h], "forecast": True, "partly_forecast": True,
            "basis": "observed+forecast", "obs_days": obs, "forecast_days": fc,
            "obs_through": obs[-1], "gfs_issued": gfs_issued,
            "basis_label": (f"{serve_label.replace('within H', f'within {h}').split(',')[0]}"
                            f" — observed IMERG to "
                            f"{(today - dt.timedelta(days=1)):%b %-d} + NOAA GFS "
                            f"forecast to {(today + dt.timedelta(days=n - 1)):%b %-d} "
                            f"(issued {issued})"),
            "low_skill": n >= LOW_SKILL_DAYS,
            "domain": ("Brazilian land only — product scope: no landslide labels "
                       "and no Brazilian civil-defence audience outside Brazil"),
            "model": {"name": model_meta.get("model") or model_meta.get("name", "p_trigger_xgb"),
                      "combine": model_meta.get("serve_combine"),
                      "version": model_meta.get("version"),
                      "test_metrics": "data/ml/ml_card.json"},
        })
    return layers


def run(out_dir: Path, today: dt.date, bounds=NOW_BOUNDS, model_path=MODEL_PATH,
        static_path=STATIC_PATH, rain_fc=None, gfs_issued=None, soil=None) -> list[dict]:
    from ml.local.serve import TriggerModel

    from live import fetch, render
    from live.grid import dst_lats_lons, dst_transform, regrid_da, regrid_points

    lats, lons = dst_lats_lons(tuple(bounds))
    transform = dst_transform(tuple(bounds))
    tm = TriggerModel.load(Path(model_path))
    horizons = dict(sorted(tm.horizons.items(), key=lambda kv: kv[1]))
    st = load_static_stack(Path(static_path))
    static = {k: st[k] for k in STATIC_BANDS}
    texture = {k: st[k] for k in TEXTURE_BANDS}
    if soil is None:
        sla, slo, vals, _ = fetch.fetch_smap_l4_vars(tuple(bounds), SOIL_VARS,
                                                     time_tag=SMAP_TIME_TAG)
        soil = {}
        for v in SOIL_VARS:
            g = regrid_points(sla, slo, vals[v], lats, lons)
            cov = regrid_points(sla, slo, np.isfinite(vals[v]).astype(float),
                                lats, lons, method="nearest")
            g[cov < 0.5] = np.nan
            soil[v] = g
    obs = obs_series(Path(out_dir), today, lats, lons)
    if rain_fc is None:
        fc_da, gfs_issued = fetch.fetch_gfs_daily(tuple(bounds), today,
                                                  ndays=max(horizons.values()))
        rain_fc = np.stack([regrid_da(fc_da.isel(time=i), lats, lons)
                            for i in range(fc_da.sizes["time"])])
    out = tm.predict(today, static=static, soil=soil, texture=texture,
                     rain_obs=obs, rain_fc=rain_fc, p99_daily=st["p99_daily"],
                     p99_3day=st["p99_3day"])
    # lead-ml serving rule (meta 'serve_combine'): P_live_H = p_trigger_H x
    # t_ml — "when" (rain/soil trigger) x "where" (susceptibility band t_ml)
    combine = str(tm.meta.get("serve_combine", ""))
    if "t_ml" in combine:
        if "t_ml" not in st:
            raise ValueError("model asks for x t_ml but the static stack has no t_ml band")
        tml = st["t_ml"]
        out = {k: (v * tml).astype(np.float32) for k, v in out.items()}
    # product scope, not model domain: outside Brazilian land (IBGE outline
    # minus MCD12Q1 water) there are no landslide labels to check the index
    # against and no Brazilian civil-defence audience, so it is nodata there
    # (2026-11-15: 32 % of the 14 d > 0.1 area fell in Paraguay/Argentina)
    from live.run_now import now_land_mask
    land = now_land_mask(tuple(bounds), lats.size, lons.size)
    if land is not None:
        out = {k: np.where(land, v, np.nan).astype(np.float32) for k, v in out.items()}
    files, bnds = {}, {}
    for h in horizons:
        p = out.get(f"p_trigger_{h}")
        if p is None:
            continue
        files[f"cog/p_trigger_{h}.tif"] = render.cog_bytes(p, transform)
        files[f"png/p_trigger_{h}.png"], bnds[h] = prob_png_3857(p, transform)
    layers = frame_layers(today, gfs_issued or "", files, bnds, tm.meta, horizons)
    out_dir = Path(out_dir)
    # frames of horizons the current model no longer serves must not linger
    # in the published snapshot (v1 48h/14d after the v2 swap)
    for sub, ext in (("cog", "tif"), ("png", "png")):
        for old in (out_dir / sub).glob(f"p_trigger_*.{ext}"):
            if f"{sub}/{old.name}" not in files:
                old.unlink()
    for rel, blob in files.items():
        (out_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        (out_dir / rel).write_bytes(blob)
    meta = out_dir / "p_trigger" / "layers.json"
    meta.parent.mkdir(parents=True, exist_ok=True)
    meta.write_text(json.dumps({"generated_at": dt.datetime.now(dt.UTC).isoformat(),
                                "layers": layers}, indent=1))
    return layers


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--date", type=dt.date.fromisoformat,
                    default=dt.datetime.now(dt.UTC).date())
    ap.add_argument("--out-dir", type=Path, default=Path(NOW_DIR))
    args = ap.parse_args(argv)
    if not enabled():
        print(f"[p_trigger] {FLAG_ENV} != 1 — P(trigger) frames are OFF")
        return 0
    layers = run(args.out_dir, args.date)
    print(f"[p_trigger] wrote {len(layers)} frames: {[l['id'] for l in layers]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
