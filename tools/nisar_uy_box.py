"""L67: every Uruguaiana NISAR pass clipped to a small box (kind nisar_frame_uy).

From the published fixed-stretch nisar_frame PNGs (decoded exactly to dB), each
(date, track, polarization) is re-rendered on ONE grid = the box at DEG (100 m),
same -25..0 dB stretch, content-hashed filename; coverage_pct = share of THE BOX
imaged. Entries merged into rs_uruguai_2026/png/_index.json.

    PYTHONPATH=.:pipeline python tools/nisar_uy_box.py --box -57.48 -29.97 -57.03 -29.20
"""
from __future__ import annotations

import argparse
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

CASE = "rs_uruguai_2026"
DEG = 0.001


def run(box: tuple, min_cov: float = 1.0) -> list[dict]:
    from rasterio.transform import from_origin
    from tp.bundle import merge_index

    from live import nisar_stack as ns
    from live import nisar_water as nw

    w, s, e, n = box
    tr = from_origin(w, n, DEG, DEG)
    shape = (int(round((n - s) / DEG)), int(round((e - w) / DEG)))
    root = REPO / "data"
    ipath = root / CASE / "png" / "_index.json"
    ix = json.loads(ipath.read_text())
    seen, new = set(), []
    frames = [f for f in ix["layers"] if f.get("kind") == "nisar_frame" and f.get("track") and nw.fixed_stretch(f)]
    for f in sorted(frames, key=lambda f: (f["date"], f["track"])):
        pol = f.get("polarization") or ("HV" if "_hv" in f["id"] else "HH")
        key = (f["date"], f["track"], pol)
        if key in seen:
            continue
        seen.add(key)
        db = nw._frame_db_on(f, root, shape, tr)
        cov = round(100 * float(np.isfinite(db).mean()), 1)
        if cov < min_cov:
            continue
        png, bounds = ns.gray_png(db.astype(np.float32), tr, pol)
        lid = f"nisar_uy_{pol.lower()}_{f['date'].replace('-', '')}_{f['track']}"
        name = f"{lid}_{hashlib.sha256(png).hexdigest()[:8]}.png"
        (root / CASE / "png" / name).write_bytes(png)
        ent = {k: f[k] for k in ("date", "track", "mode", "frames", "granules", "sensor", "source") if k in f}
        ent.update({"id": lid, "kind": "nisar_frame_uy", "box": "uruguaiana", "box_bounds": list(box),
                    "polarization": pol, "png": f"{CASE}/png/{name}", "cog": None, "bounds": bounds,
                    "stretch_db": {"db_min": ns.DB_MIN, "db_max": ns.DB_MAX}, "legend": ns.LEGEND[pol],
                    "coverage_pct": cov, "label": f"NISAR radar {pol} · track {f['track']} · {f['date']} (Uruguaiana box)",
                    "note": ("Single NISAR pass clipped to the Uruguaiana box (100 m, one grid for every pass); "
                             "fixed −25..0 dB stretch; coverage_pct = share of the box imaged. Not a flood map.")})
        new.append(ent)
        print(lid, cov, flush=True)
    ix["layers"] = merge_index([x for x in ix["layers"] if x.get("kind") != "nisar_frame_uy"], new)
    tmp = ipath.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(ix, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, ipath)
    return new


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--box", nargs=4, type=float, required=True, metavar=("W", "S", "E", "N"))
    a = ap.parse_args(argv)
    new = run(tuple(a.box))
    print(len(new), "entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
