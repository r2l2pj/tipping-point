"""L61 Now: flood index with NISAR-observed water (tpi_flood_nisar_d0..d3).

Runs after the NISAR frame stack step (now.yml). TPI_flood d0..d3 from
data/now/places_tpi.npz (Now 0.01 deg grid); water from the fixed-stretch HH
frames in data/now/nisar_stack/layers.json (live.nisar_water: HH < -18 dB, latest
pass with map_day - 12 <= date <= map_day, outside permanent water). Writes
  png/tpi_flood_nisar_d{k}.png, png/nisar_water.png, cog/tpi_flood_nisar_today.tif
  nisar_tpi/layers.json   (merged into the Now manifest by live/now_manifest)
  nisar_tpi/water.npz     (mask for the agreement scorer)
and adds summary.json "nisar_water". No usable pass -> no layers (app falls back).

    PYTHONPATH=.:pipeline python -m live.now_nisar_tpi --now-dir data/now
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path

import numpy as np

from live import nisar_water as nw

WATER_COLOR = "#08519c"


def _grid(z):
    from rasterio.transform import from_bounds
    w, s, e, n = (float(x) for x in z["bounds"])
    h, wd = z["flood"].shape[1:]
    return (h, wd), from_bounds(w, s, e, n, wd, h)


def run(now_dir: Path, gsw: Path | None = None, hand: Path | None = None) -> dict:
    import rasterio
    from rasterio.warp import Resampling, reproject
    from tp.bundle import _class_png_3857

    from live.run_now import now_water_mask
    from live.tpi import classify

    out = now_dir / "nisar_tpi"
    out.mkdir(parents=True, exist_ok=True)
    lpath = out / "layers.json"
    npz = now_dir / "places_tpi.npz"
    stack = now_dir / "nisar_stack" / "layers.json"
    if not npz.is_file() or not stack.is_file():
        lpath.write_text(json.dumps({"layers": [], "reason": "no places_tpi.npz or NISAR stack"}))
        return {"layers": 0}
    z = np.load(npz)
    shape, tr = _grid(z)
    frames = nw.hh_frames(json.loads(stack.read_text()).get("layers", []))
    bounds = tuple(float(x) for x in z["bounds"])
    perm = None
    if gsw is not None and Path(gsw).is_file():
        perm, _ = now_water_mask(bounds, *shape, occ_path=gsw)
    fp = None
    if hand is not None and Path(hand).is_file():
        with rasterio.open(hand) as src:
            h = np.full(shape, np.nan, np.float32)
            reproject(rasterio.band(src, 1), h, dst_transform=tr, dst_crs="EPSG:4326",
                      dst_nodata=np.nan, resampling=Resampling.bilinear)
        fp = np.nan_to_num(h, nan=99) <= 5.0
    lat = tr.f + (np.arange(shape[0]) + 0.5) * tr.e
    km2 = (abs(tr.a) * 111.32 * np.cos(np.deg2rad(lat)))[:, None] * abs(tr.e) * 110.574 * np.ones((1, shape[1]))
    layers, summary = [], None
    for k, ds in enumerate(z["dates"]):
        day = dt.date.fromisoformat(str(ds))
        tpi = z["flood"][k].astype(np.float32)
        pw = ~np.isfinite(tpi) if perm is None else (perm | ~np.isfinite(tpi))
        water, observed, used = nw.water_mask(frames, day, now_dir, shape, tr, perm_water=pw)
        if not used:
            continue
        comb = nw.combine(tpi, water)
        valid = np.isfinite(comb)
        png, b = _class_png_3857(classify(np.where(valid, comb, np.nan)), valid, tr, "EPSG:4326")
        (now_dir / "png").mkdir(exist_ok=True)
        (now_dir / "png" / f"tpi_flood_nisar_d{k}.png").write_bytes(png)
        ag = nw.agreement(tpi, water, km2, fp)
        last = max(used, key=lambda u: u["date"])
        ent = {"id": f"tpi_flood_nisar_d{k}", "date": day.isoformat(), "png": f"png/tpi_flood_nisar_d{k}.png",
               "cog": None, "bounds": b, "legend": ["Baixo", "Moderado", "Alto", "Crítico"],
               "forecast": k > 0, "label": nw.LABEL, "note": nw.NOTE,
               "nisar_passes": [{"date": u["date"], "track": u.get("track"), "id": u["id"]} for u in used],
               "nisar_water_km2": ag["water_km2"], "nisar_outside_floodplain_pct": ag.get("outside_floodplain_pct")}
        if k == 0:
            cog = now_dir / "cog" / "tpi_flood_nisar_today.tif"
            cog.parent.mkdir(exist_ok=True)
            prof = {"driver": "GTiff", "height": shape[0], "width": shape[1], "count": 3, "dtype": "float32",
                    "crs": "EPSG:4326", "transform": tr, "nodata": np.nan, "compress": "deflate"}
            with rasterio.open(cog, "w", **prof) as dst:
                dst.write(np.round(comb, 3), 1)
                dst.write(water.astype(np.float32), 2)
                dst.write(observed.astype(np.float32), 3)
                dst.set_band_description(1, "tpi")
                dst.set_band_description(2, "water")
                dst.set_band_description(3, "observed")
            ent["cog"] = "cog/tpi_flood_nisar_today.tif"
            from live.render import palette_png_bytes, warp_to_3857
            w3857, _t, wb = warp_to_3857(water.astype(np.uint8), tr)
            (now_dir / "png" / "nisar_water.png").write_bytes(palette_png_bytes(w3857, w3857 > 0, {1: WATER_COLOR}))
            age = (day - dt.date.fromisoformat(last["date"])).days
            layers.append({"id": "nisar_water", "date": day.isoformat(), "kind": "nisar_extra",
                           "png": "png/nisar_water.png", "cog": None, "bounds": wb,
                           "legend": [{"label": "NISAR-observed water (candidate)", "color": WATER_COLOR}],
                           "label": "NISAR-observed water (candidate)", "sensor": "NISAR",
                           "pass_date": last["date"], "pass_track": last.get("track"), "age_days": age,
                           "note": "HH < −18 dB on the latest NISAR pass ≤ 12 d old, outside permanent "
                                   "water; candidate (also lights flooded rice / irrigation)."})
            np.savez_compressed(out / "water.npz", water=water, observed=observed,
                                transform=np.array(list(tr)[:6]), crs="EPSG:4326",
                                pass_ids=np.array([u["id"] for u in used]),
                                pass_dates=np.array([u["date"] for u in used]), map_day=day.strftime("%Y%m%d"))
            summary = {"pass_date": last["date"], "pass_track": last.get("track"), "age_days": age,
                       "water_km2": ag["water_km2"], "outside_floodplain_pct": ag.get("outside_floodplain_pct"),
                       "tpi_today": {"high_plus": ag["high_plus"], "critical": ag["critical"]},
                       "definition": "NISAR water = HH < −18 dB outside permanent water, latest pass ≤ 12 d "
                                     "before today; hit = share of that water the TPI flags (today's map)"}
        layers.append(ent)
    tmp = lpath.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"layers": layers}, indent=2, ensure_ascii=False))
    os.replace(tmp, lpath)
    sp = now_dir / "summary.json"
    if sp.is_file():
        s = json.loads(sp.read_text())
        s["nisar_water"] = summary
        if summary is None:
            s.setdefault("_null_reasons", {})["nisar_water"] = "no NISAR HH pass within 12 days"
        tmp = sp.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(s, indent=2, ensure_ascii=False))
        os.replace(tmp, sp)
    print(f"now_nisar_tpi: {len(layers)} layers; summary {summary and {k: summary[k] for k in ('pass_date', 'water_km2')}}")
    return {"layers": len(layers), "summary": summary}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--now-dir", type=Path, default=Path("data/now"))
    ap.add_argument("--gsw", type=Path, default=Path("data/now/static/jrc_gsw/gsw_occurrence_2021.tif"))
    ap.add_argument("--hand", type=Path, default=Path("data/static/south_brazil/terrain/hand_m.tif"))
    a = ap.parse_args(argv)
    run(a.now_dir, a.gsw, a.hand)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
