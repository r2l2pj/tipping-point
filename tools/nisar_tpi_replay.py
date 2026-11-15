"""L61 replays: tpi_flood_nisar per observed day for a case (WA, Uruguaiana).

For every observed tpi_flood day D in data/<case>/png/_index.json with a usable NISAR
HH pass (fixed-stretch nisar_frame, D-12 <= pass date <= D): water = HH < -18 dB
outside permanent water (= where the served TPI is NaN), tpi_flood_nisar = max(TPI,
0.8*water). Writes, MERGED into the index:
  <case>/png/tpi_flood_nisar_<D>.png  (class PNG, same palette as tpi_flood)
  <case>/cog/<case>_tpi_flood_nisar_<D>.tif  (bands tpi, water)
  <case>/png/nisar_water_<D>.png  (water-only overlay, kind nisar_extra)
and ~/.cache/lead-analysis/l61/<case>/water_<D>.npz for the agreement scorer.

    PYTHONPATH=.:pipeline python tools/nisar_tpi_replay.py --case rs_uruguai_2026
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

import numpy as np
import rasterio

REPO = Path(__file__).resolve().parents[1]
for _p in (str(REPO), str(REPO / "pipeline"), str(REPO / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from live import nisar_water as nw

CACHE = Path.home() / ".cache/lead-analysis/l61"
WATER_COLOR = "#08519c"


def floodplain(case: str, shape, transform) -> np.ndarray | None:
    """HAND <= 5 m on the case TPI grid (None when unavailable)."""
    try:
        if case == "rs_uruguai_2026":
            import rs_uruguai_tpi_flood_replay as base
            _ts, _tf, hand = base.terrain()
            return np.nan_to_num(hand, nan=99) <= 5.0 if hand.shape == shape else None
        z = np.load(Path.home() / f".cache/lead-analysis/v3/{case}/inputs.npz")
        return z["hand"] <= 5.0 if z["hand"].shape == shape else None
    except Exception:  # noqa: BLE001
        return None


def _water_png(water: np.ndarray, transform) -> tuple[bytes, list]:
    from live.render import palette_png_bytes, warp_to_3857
    w3857, _tr, bounds = warp_to_3857(water.astype(np.uint8), transform)
    return palette_png_bytes(w3857, w3857 > 0, {1: WATER_COLOR}), bounds


def run(case: str) -> dict:
    from tp.bundle import _class_png_3857, merge_index

    from live.tpi import classify

    root = REPO / "data"
    ipath = root / case / "png" / "_index.json"
    ix = json.loads(ipath.read_text())
    frames = nw.hh_frames(ix["layers"])
    days = [e for e in ix["layers"] if e["id"] == "tpi_flood" and not e.get("forecast")]
    out_dir = CACHE / case
    out_dir.mkdir(parents=True, exist_ok=True)
    new, rows = [], []
    fp = None
    for e in sorted(days, key=lambda e: e["date"]):
        day = dt.date.fromisoformat(e["date"])
        if not nw.passes_for(frames, day):
            continue
        with rasterio.open(root / e["cog"]) as src:
            desc = list(src.descriptions)
            band = desc.index(day.strftime("%Y%m%d")) + 1 if day.strftime("%Y%m%d") in desc else 1
            tpi = src.read(band).astype(np.float32)
            tpi[~np.isfinite(tpi)] = np.nan
            tr, crs, prof = src.transform, src.crs, src.profile
        if fp is None:
            fp = floodplain(case, tpi.shape, tr)
        water, observed, used = nw.water_mask(frames, day, root, tpi.shape, tr,
                                              perm_water=~np.isfinite(tpi))
        comb = nw.combine(tpi, water)
        lat = tr.f + (np.arange(tpi.shape[0]) + 0.5) * tr.e
        km2 = (abs(tr.a) * 111.32 * np.cos(np.deg2rad(lat)))[:, None] * abs(tr.e) * 110.574 \
            * np.ones((1, tpi.shape[1]))
        ag = nw.agreement(tpi, water, km2, fp)
        ymd = day.strftime("%Y%m%d")
        cog = root / case / "cog" / f"{case}_tpi_flood_nisar_{ymd}.tif"
        p = dict(prof, count=2, dtype="float32", nodata=np.nan, compress="deflate", predictor=3)
        with rasterio.open(cog, "w", **p) as dst:
            dst.write(np.round(comb, 3).astype(np.float32), 1)
            dst.write(water.astype(np.float32), 2)
            dst.set_band_description(1, "tpi")
            dst.set_band_description(2, "water")
        valid = np.isfinite(comb)
        png, bounds = _class_png_3857(classify(np.where(valid, comb, np.nan)), valid, tr, crs)
        (root / case / "png" / f"tpi_flood_nisar_{ymd}.png").write_bytes(png)
        wpng, wb = _water_png(water, tr)
        (root / case / "png" / f"nisar_water_{ymd}.png").write_bytes(wpng)
        passes = [{"date": u["date"], "track": u.get("track"), "id": u["id"]} for u in used]
        last = max(used, key=lambda u: u["date"])
        common = {"nisar_passes": passes, "nisar_water_km2": ag["water_km2"],
                  "nisar_outside_floodplain_pct": ag.get("outside_floodplain_pct")}
        new.append({"id": "tpi_flood_nisar", "date": day.isoformat(), "kind": "observed",
                    "forecast": False, "png": f"{case}/png/tpi_flood_nisar_{ymd}.png",
                    "cog": f"{case}/cog/{cog.name}", "bounds": bounds,
                    "legend": ["Baixo", "Moderado", "Alto", "Crítico"], "label": nw.LABEL,
                    "note": nw.NOTE, "critical_km2": round(float(km2[valid & (comb > 0.6)].sum()), 2),
                    **common})
        new.append({"id": f"nisar_water_{ymd}", "date": day.isoformat(), "kind": "nisar_extra",
                    "png": f"{case}/png/nisar_water_{ymd}.png", "cog": None, "bounds": wb,
                    "legend": [{"label": "NISAR-observed water (candidate)", "color": WATER_COLOR}],
                    "label": "NISAR-observed water (candidate)", "sensor": "NISAR",
                    "pass_date": last["date"], "pass_track": last.get("track"),
                    "age_days": (day - dt.date.fromisoformat(last["date"])).days,
                    "note": "HH < −18 dB on the latest NISAR pass ≤ 12 d before this day, outside "
                            "permanent water; candidate (also lights flooded rice / irrigation).",
                    **common})
        np.savez_compressed(out_dir / f"water_{ymd}.npz", water=water, observed=observed,
                            transform=np.array(list(tr)[:6]), crs=str(crs),
                            pass_ids=np.array([u["id"] for u in used]),
                            pass_dates=np.array([u["date"] for u in used]), map_day=ymd,
                            floodplain=fp if fp is not None else np.zeros(0, bool))
        rows.append({"date": day.isoformat(), "passes": [u["date"] for u in used], **ag})
        print(case, day, "passes", [u["date"] + "/" + str(u.get("track")) for u in used],
              "water km2", ag["water_km2"], "outside floodplain %", ag.get("outside_floodplain_pct"),
              "TPI High+ hit %", ag["high_plus"]["hit_pct"], flush=True)
    ix = json.loads(ipath.read_text())
    ix["layers"] = merge_index(ix["layers"], new)
    tmp = ipath.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(ix, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, ipath)
    (out_dir / "rows.json").write_text(json.dumps(rows, indent=1))
    return {"case": case, "days": len(rows), "frames_hh": len(frames)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--case", required=True)
    print(run(ap.parse_args(argv).case))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
