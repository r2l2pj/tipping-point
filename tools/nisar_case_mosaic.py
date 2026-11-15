"""Merged NISAR mosaics per window (complementary tracks), kind nisar_mosaic.

Per window: on ONE common grid (case box, MOSAIC_DEG), each pixel takes the pass
closest to the window reference date (tie -> later pass), from the published
fixed-stretch nisar_frame PNGs (decoded exactly to dB). HH always; HV only when
every pass of the window has HV. Writes gray PNG (same -25..0 dB stretch), a
per-pixel source PNG, a footprint GeoJSON per contributing pass; entries merged
into png/_index.json (content-hashed filenames, never in place).

    PYTHONPATH=.:pipeline python tools/nisar_case_mosaic.py --case rs_uruguai_2026
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
for _p in (str(REPO), str(REPO / "pipeline")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

MOSAIC_DEG = 0.001
WINDOWS = {"rs_uruguai_2026": [("2026-09-18", "2026-09-23", "2026-09-18"),
                               ("2026-09-27", "2026-10-05", "2026-09-30"),
                               ("2026-10-09", "2026-10-17", "2026-10-12"),
                               ("2026-10-21", "2026-10-29", "2026-10-24"),
                               ("2026-11-02", "2026-11-05", "2026-11-05")]}
PALETTE = ["#1b9e77", "#d95f02", "#7570b3", "#e7298a", "#66a61e", "#e6ab02"]
NOTE = ("Merged NISAR radar mosaic: each pixel shows the pass closest to the reference date "
        "within the window (tie → later). Different tracks look at different incidence angles, "
        "so seams and brightness steps between passes are expected. Fixed −25..0 dB stretch. "
        "Backscatter, not a flood map.")


def _frames(index, pol):
    from live import nisar_water as nw
    out = []
    for e in index:
        if e.get("kind") != "nisar_frame" or not e.get("track") or not nw.fixed_stretch(e):
            continue
        epol = e.get("polarization") or ("HV" if "_hv" in e["id"] else "HH")
        if epol == pol:
            out.append(e)
    # one per (date, track)
    seen, uniq = set(), []
    for e in out:
        k = (e["date"], e["track"])
        if k not in seen:
            seen.add(k)
            uniq.append(e)
    return uniq


def _png_gray(db: np.ndarray, tr) -> tuple[bytes, list]:
    from live import nisar_stack as ns
    return ns.gray_png(db.astype(np.float32), tr, "HH")


def _src_png(src: np.ndarray, tr, n: int) -> tuple[bytes, list]:
    from live.render import palette_png_bytes, warp_to_3857
    s3857, _t, b = warp_to_3857(src.astype(np.uint8), tr)
    return palette_png_bytes(s3857, s3857 > 0, {i + 1: PALETTE[i % len(PALETTE)] for i in range(n)}), b


def run(case: str) -> dict:
    import yaml
    from rasterio.features import shapes
    from rasterio.transform import from_origin
    from shapely.geometry import mapping, shape
    from tp.bundle import merge_index

    from live import nisar_water as nw

    bbox = tuple(yaml.safe_load((REPO / "config.yaml").read_text())["cases"][case]["aoi"])
    w, s, e, n = bbox
    tr = from_origin(w, n, MOSAIC_DEG, MOSAIC_DEG)
    shape_ = (round((n - s) / MOSAIC_DEG), round((e - w) / MOSAIC_DEG))
    lat = n - (np.arange(shape_[0]) + 0.5) * MOSAIC_DEG
    km2 = (MOSAIC_DEG * 111.32 * np.cos(np.deg2rad(lat)))[:, None] * MOSAIC_DEG * 110.574 * np.ones((1, shape_[1]))
    root = REPO / "data"
    ipath = root / case / "png" / "_index.json"
    ix = json.loads(ipath.read_text())
    new, report = [], []
    for start, end, ref in WINDOWS[case]:
        d0, d1, r = (dt.date.fromisoformat(x) for x in (start, end, ref))
        hh = [f for f in _frames(ix["layers"], "HH") if d0 <= dt.date.fromisoformat(f["date"]) <= d1]
        hv = [f for f in _frames(ix["layers"], "HV") if d0 <= dt.date.fromisoformat(f["date"]) <= d1]
        pols = [("HH", hh)]
        if hh and {(f["date"], f["track"]) for f in hv} == {(f["date"], f["track"]) for f in hh}:
            pols.append(("HV", hv))
        # priority: closest to ref, tie -> later
        order = sorted(hh, key=lambda f: (abs((dt.date.fromisoformat(f["date"]) - r).days),
                                          -dt.date.fromisoformat(f["date"]).toordinal()))
        keys = [(f["date"], f["track"]) for f in order]
        src = np.zeros(shape_, np.uint8)
        dbs = {}
        for pol, frs in pols:
            by = {(f["date"], f["track"]): f for f in frs}
            mos = np.full(shape_, np.nan)
            for i, k in enumerate(keys):                    # highest priority first
                f_db = nw._frame_db_on(by[k], root, shape_, tr)
                fill = np.isnan(mos) & np.isfinite(f_db)
                mos[fill] = f_db[fill]
                if pol == "HH":
                    src[fill] = i + 1
            dbs[pol] = mos
        covered = src > 0
        tot = float(km2[covered].sum())
        passes = [{"date": k[0], "track": k[1],
                   "share_pct": round(100 * float(km2[src == i + 1].sum()) / tot, 1) if tot else 0.0}
                  for i, k in enumerate(keys)]
        cov = round(100 * float(km2[covered].sum()) / float(km2.sum()), 1)
        ymd = r.strftime("%Y%m%d")
        spng, _sb = _src_png(src, tr, len(keys))
        sname = f"nisar_mosaic_src_{ymd}_{hashlib.sha256(spng).hexdigest()[:8]}.png"
        (root / case / "png" / sname).write_bytes(spng)
        feats = []
        for i, k in enumerate(keys):
            m = (src == i + 1).astype(np.uint8)
            geoms = [shape(g) for g, v in shapes(m, mask=m.astype(bool), transform=tr) if v == 1]
            if geoms:
                from shapely.ops import unary_union
                u = unary_union(geoms).simplify(0.005)
                feats.append({"type": "Feature", "geometry": mapping(u),
                              "properties": {"date": k[0], "track": k[1], "share_pct": passes[i]["share_pct"],
                                             "color": PALETTE[i % len(PALETTE)]}})
        gj = root / case / "geojson" / f"nisar_mosaic_footprints_{ymd}.geojson"
        gj.write_text(json.dumps({"type": "FeatureCollection", "features": feats}, separators=(",", ":")))
        src_legend = [{"label": f"{k[0]} {k[1]}", "color": PALETTE[i % len(PALETTE)]} for i, k in enumerate(keys)]
        for pol, mos in dbs.items():
            png, b = _png_gray(mos, tr)
            name = f"nisar_mosaic_{pol.lower()}_{ymd}_{hashlib.sha256(png).hexdigest()[:8]}.png"
            (root / case / "png" / name).write_bytes(png)
            from live import nisar_stack as ns
            new.append({"id": f"nisar_mosaic_{pol.lower()}_{ymd}", "kind": "nisar_mosaic", "date": ref,
                        "window": [start, end], "polarization": pol, "passes": passes,
                        "png": f"{case}/png/{name}", "cog": None, "bounds": b,
                        "stretch_db": {"db_min": ns.DB_MIN, "db_max": ns.DB_MAX}, "legend": ns.LEGEND[pol],
                        "coverage_pct": cov, "source_png": f"{case}/png/{sname}", "source_legend": src_legend,
                        "footprints": f"{case}/geojson/{gj.name}", "sensor": "NISAR",
                        "label": f"NISAR radar mosaic {pol} · {start} → {end}", "note": NOTE})
        report.append({"ref": ref, "passes": passes, "coverage_pct": cov, "pols": [p for p, _ in pols]})
        print(case, ref, "coverage", cov, passes, [p for p, _ in pols], flush=True)
    ix = json.loads(ipath.read_text())
    ix["layers"] = merge_index(ix["layers"], new)
    tmp = ipath.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(ix, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, ipath)
    return {"case": case, "windows": report}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--case", default="rs_uruguai_2026")
    print(json.dumps(run(ap.parse_args(argv).case))[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
