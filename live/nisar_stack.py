"""Now NISAR frame stack: recent GCOV passes over the Now domain, one mosaic per pass.

For "compare two passes" in Now (real time). A *pass* = one NISAR track + look
direction on one UTC date (its consecutive frames are mosaicked). Per pass and
polarization (HH, HV) this writes a gray EPSG:3857 PNG clipped to the swath
(transparent outside), with a FIXED dB stretch so two dates of the same track
compare by eye:

    data/now/nisar_stack/png/nisar_<hh|hv>_<YYYYMMDD>_<TTTL>.png
    data/now/nisar_stack/layers.json   (nisar_frame entries, merged into the
                                        Now manifest by live/now_manifest)

Rolling and incremental: the stack is restored from the fallback branch before
this step (now.yml), only passes not yet rendered are read (at most
MAX_GRANULES granules per run, newest first), and only the newest KEEP passes
per track+look are kept. Production GCOV only (NISAR_L2_GCOV_V1); mixed-mode
granules ('_M') are skipped; each layer records its acquisition mode
(rn.mode_token) — passes of different modes (20 vs 40 MHz) are not
calibrated-equal, the app shows the mode next to the date.

    PYTHONPATH=.:pipeline python -m live.nisar_stack --now-dir data/now
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

KEEP = 3                     # passes per track+look
LOOKBACK_DAYS = 60
MAX_GRANULES = 10            # per run (each ~30 s for HH+HV): bounds the Now job
RES_M = 400.0                # multiple of the 10 m / 20 m postings
MOSAIC_DEG = 0.004
MIN_DOMAIN_SHARE = 0.05      # skip frames that barely touch the domain
# ONE fixed linear dB -> gray stretch for EVERY nisar_frame PNG (all cases, tracks,
# HH and HV), so the app can build an RGB change composite from any two frames
# (R = before, G = B = after): gray = 1 + (dB - DB_MIN) / (DB_MAX - DB_MIN) * 254,
# alpha 0 = no data.
DB_MIN, DB_MAX = -25.0, 0.0
STRETCH = {"HH": (DB_MIN, DB_MAX), "HV": (DB_MIN, DB_MAX)}
_STOPS = [{"v": -25, "color": "#000000", "label": "−25 dB · smooth (open water; HV: bare/water)"},
          {"v": -12.5, "color": "#808080", "label": "−12.5 dB"},
          {"v": 0, "color": "#ffffff", "label": "0 dB · bright (urban, flooded trees)"}]
LEGEND = {pol: {"unit": "dB", "db_min": DB_MIN, "db_max": DB_MAX,
                "gray": "1 + (dB − db_min) / (db_max − db_min) × 254; alpha 0 = no data",
                "stops": _STOPS} for pol in ("HH", "HV")}
SCHEMA = "tipping-point/now-nisar-stack@1"


def tag(track, look) -> str:
    return f"{int(track):03d}{look}"


def plan_passes(granules: list[dict], domain, keep: int = KEEP) -> dict:
    """{(track, look, date): [granules]} for the newest `keep` dates per track+look.
    Mixed-mode granules and frames with < MIN_DOMAIN_SHARE of their footprint in
    the domain are dropped."""
    from shapely import wkt
    from tp import response_nisar as rn

    by = defaultdict(list)
    for g in granules:
        mode = rn.mode_token(g["granule_id"]) or ""
        if mode.endswith("_M"):
            continue
        fp = g.get("footprint")
        geom = wkt.loads(fp) if isinstance(fp, str) else fp
        if geom is None or geom.is_empty or geom.intersection(domain).area < MIN_DOMAIN_SHARE * geom.area:
            continue
        day = str(g["start"])[:10]
        by[(int(g["track"]), str(g["look"]), day)].append(g)
    per_track = defaultdict(list)
    for (t, lk, day) in by:
        per_track[(t, lk)].append(day)
    out = {}
    for (t, lk), days in per_track.items():
        for day in sorted(days, reverse=True)[:keep] if keep else sorted(days, reverse=True):
            out[(t, lk, day)] = sorted(by[(t, lk, day)], key=lambda g: int(g["frame"]))
    return out


def _read_db(url: str, term: str, bbox, token, res: float = RES_M):
    from tp import flood_mask as fm
    from tp import response_nisar as rn

    with rn.open_gcov(url, token) as (h5, rr):
        info = rn.granule_grid_info(h5)
        if term not in info["terms"]:
            return None
        grid = rn.target_grid(info["x"], info["y"], info["epsg"], bbox, res)
        if grid is None:
            return None
        arr, _io = rn.block_average(h5[f"{rn.GCOV_GRIDS}/{term}"], info["x"], info["y"], grid, rr)
    return fm.to_db(arr).astype(np.float32), grid


def track_bounds(gs: list[dict], bbox, deg: float = MOSAIC_DEG) -> tuple:
    """One fixed grid per track: union of the track's granule footprints within
    the AOI, snapped outward to `deg` — every pass of the track renders onto it
    (identical PNG size + bounds)."""
    import math

    from shapely import wkt
    from shapely.geometry import box
    from shapely.ops import unary_union
    u = unary_union([wkt.loads(g["footprint"]) if isinstance(g["footprint"], str) else g["footprint"]
                     for g in gs]).intersection(box(*bbox))
    w, s_, e, n = u.bounds
    return (math.floor(w / deg) * deg, math.floor(s_ / deg) * deg,
            math.ceil(e / deg) * deg, math.ceil(n / deg) * deg)


def mosaic(parts, bbox, deg: float = MOSAIC_DEG, fixed: tuple | None = None):
    """[(db array, TargetGrid)] -> (db mosaic on a 0.004 deg EPSG:4326 grid, transform)."""
    from rasterio.transform import from_origin
    from rasterio.warp import Resampling, reproject, transform_bounds

    if fixed:
        w, s, e, n = fixed
    else:
        bnds = [transform_bounds(f"EPSG:{g.epsg}", "EPSG:4326", g.left, g.top - g.height * g.res,
                                 g.left + g.width * g.res, g.top, densify_pts=21) for _, g in parts]
        w = max(bbox[0], min(b[0] for b in bnds))
        s = max(bbox[1], min(b[1] for b in bnds))
        e = min(bbox[2], max(b[2] for b in bnds))
        n = min(bbox[3], max(b[3] for b in bnds))
    tr = from_origin(w, n, deg, deg)
    shape = (int(np.ceil((n - s) / deg)), int(np.ceil((e - w) / deg)))
    out = np.full(shape, np.nan, np.float32)
    for arr, g in parts:
        tmp = np.full(shape, np.nan, np.float32)
        reproject(arr, tmp, src_transform=g.transform, src_crs=f"EPSG:{g.epsg}",
                  src_nodata=np.nan, dst_transform=tr, dst_crs="EPSG:4326",
                  dst_nodata=np.nan, resampling=Resampling.bilinear)
        fill = np.isnan(out) & np.isfinite(tmp)
        out[fill] = tmp[fill]
    if fixed:                       # identical grid per track: no crop
        return out, tr
    # crop to the valid swath (granule grids are rectangles around a rotated swath)
    rows, cols = np.where(np.isfinite(out).any(axis=1))[0], np.where(np.isfinite(out).any(axis=0))[0]
    if rows.size:
        out = out[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]
        tr = from_origin(w + cols[0] * deg, n - rows[0] * deg, deg, deg)
    return out, tr


def gray_png(db: np.ndarray, tr, pol: str) -> tuple[bytes, list]:
    """Fixed-stretch gray + alpha PNG in EPSG:3857 (nodata transparent)."""
    import io

    from PIL import Image

    from live.render import warp_to_3857

    lo, hi = STRETCH[pol]
    v = np.isfinite(db)
    u8 = np.zeros(db.shape, np.uint8)
    u8[v] = np.clip(1 + (db[v] - lo) / (hi - lo) * 254, 1, 255).astype(np.uint8)
    g3857, _tr, bounds = warp_to_3857(u8, tr)
    alpha = np.where(g3857 > 0, 255, 0).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(np.dstack([g3857, alpha]), mode="LA").save(buf, format="PNG", optimize=True)
    return buf.getvalue(), bounds


def render_pass(key, gs, bbox, token, pdir: Path, rel_dir: str, *, read=_read_db,
                res: float = RES_M, deg: float = MOSAIC_DEG, source: str | None = None,
                fixed: tuple | None = None) -> list[dict]:
    """HH + HV mosaics of one pass -> PNGs in pdir; nisar_frame entries (png under rel_dir)."""
    from tp import response_nisar as rn

    t, lk, day = key
    pid = f"{tag(t, lk)}_{day}"
    modes = sorted({rn.mode_token(g["granule_id"]) for g in gs})
    colls = sorted({"beta" if "BETA" in str(g.get("h5_url", "")) else
                    "provisional" if "PROVISIONAL" in str(g.get("h5_url", "")) else "V1" for g in gs})
    out = []
    for pol, term in (("HH", "HHHH"), ("HV", "HVHV")):
        parts = [p for p in (read(g["h5_url"], term, bbox, token, res) if read is _read_db
                             else read(g["h5_url"], term, bbox, token) for g in gs) if p is not None]
        if not parts:
            continue
        db, tr = mosaic(parts, bbox, deg, fixed)
        png, bounds = gray_png(db, tr, pol)
        lid = f"nisar_{pol.lower()}_{day.replace('-', '')}_{tag(t, lk)}"
        (pdir / f"{lid}.png").write_bytes(png)
        out.append({
            "id": lid, "date": day, "pass": pid, "kind": "nisar_frame", "track": tag(t, lk),
            "frames": [int(g["frame"]) for g in gs], "mode": modes[0] if len(modes) == 1 else modes,
            "polarization": pol, "png": f"{rel_dir}/{lid}.png", "cog": None,
            "bounds": bounds, "legend": LEGEND[pol],
            "sensor": "NISAR" if colls == ["V1"] else "NISAR (GCOV provisional)",
            "source": source or ("NISAR L2 GCOV (ASF, " + ", ".join(
                {"provisional": "NISAR_L2_GCOV_PROVISIONAL_V1", "beta": "NISAR_L2_GCOV_BETA_V1"}.get(
                    c, "NISAR_L2_GCOV_V1") for c in colls) + ")"),
            "label": f"NISAR radar {pol} · track {tag(t, lk)} · {day}",
            "note": (f"Backscatter, ONE fixed {DB_MIN:g}..{DB_MAX:g} dB stretch for every NISAR frame "
                     f"({res:g} m; same grid for every pass of track {tag(t, lk)}); compare two passes "
                     "of the SAME track. Not a flood map."),
            "grid": {"deg": deg, "bounds_4326": list(fixed) if fixed else None},
            "stretch_db": {"db_min": DB_MIN, "db_max": DB_MAX},
            "granules": [g["granule_id"] for g in gs]})
        print(f"nisar_stack: {pid} {pol} {len(parts)} frame(s) {len(png) // 1024} KB", flush=True)
    return out


def _checkpoint(lpath: Path, layers: list[dict]) -> None:
    """Partial layers.json after every pass (a crash keeps what is rendered)."""
    doc = json.loads(lpath.read_text()) if lpath.is_file() else {"schema": SCHEMA}
    doc["layers"] = layers
    _write_json(lpath, doc)


def _write_json(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def update(now_dir: Path, bbox, granules: list[dict], token, *, keep: int = KEEP,
           max_granules: int = MAX_GRANULES, read=_read_db) -> dict:
    from shapely.geometry import box

    sdir = now_dir / "nisar_stack"
    pdir = sdir / "png"
    pdir.mkdir(parents=True, exist_ok=True)
    lpath = sdir / "layers.json"
    old = []
    if lpath.is_file():
        try:
            old = json.loads(lpath.read_text()).get("layers", [])
        except ValueError:
            old = []
    old = [lay for lay in old if (now_dir / lay["png"]).is_file()]
    want = plan_passes(granules, box(*bbox), keep)
    by_track = {}
    for (t, lk, day), gs in want.items():
        by_track.setdefault(tag(t, lk), []).extend(gs)
    grids = {trk: track_bounds(gs, bbox) for trk, gs in by_track.items()}
    stale = {lay["pass"] for lay in old
             if (lay.get("grid") or {}).get("bounds_4326") != list(grids.get(lay["track"], ()))
             or (lay.get("legend") or {}).get("db_min") != DB_MIN}
    old = [lay for lay in old if lay["pass"] not in stale]
    done = {lay["pass"] for lay in old}
    budget, new, skipped = max_granules, [], []
    for key in sorted(want, key=lambda k: k[2], reverse=True):      # newest first
        t, lk, day = key
        pid = f"{tag(t, lk)}_{day}"
        if pid in done:
            continue
        gs = want[key]
        if len(gs) > budget:
            skipped.append(pid)
            continue
        budget -= len(gs)
        try:
            ents = render_pass(key, gs, bbox, token, pdir, "nisar_stack/png", read=read,
                               fixed=grids[tag(t, lk)])
        except Exception as exc:   # noqa: BLE001 — network: retried next run
            print(f"nisar_stack: {pid} failed ({type(exc).__name__}: {exc}) — retried next run")
            skipped.append(pid)
            continue
        new += ents
        _checkpoint(lpath, old + new)
    # roll: keep only passes still wanted (newest KEEP per track+look)
    wanted = {f"{tag(t, lk)}_{day}" for (t, lk, day) in want}
    layers = [lay for lay in old + new if lay["pass"] in wanted]
    for lay in old:
        if lay["pass"] not in wanted:
            (now_dir / lay["png"]).unlink(missing_ok=True)
    keep_png = {Path(lay["png"]).name for lay in layers}
    for p in pdir.glob("*.png"):
        if p.name not in keep_png:
            p.unlink()
    layers.sort(key=lambda lay: (lay["track"], lay["date"], lay["polarization"]))
    doc = {"schema": SCHEMA, "updated_at": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "keep_per_track": keep, "lookback_days": LOOKBACK_DAYS, "res_m": RES_M,
           "stretch_db": STRETCH, "pending_passes": skipped,
           "tracks": sorted({lay["track"] for lay in layers}), "layers": layers}
    _write_json(lpath, doc)
    print(f"nisar_stack: {len(layers)} layers ({len(new)} new), {len(skipped)} pass(es) deferred")
    return doc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--now-dir", type=Path, default=Path("data/now"))
    ap.add_argument("--max-granules", type=int, default=MAX_GRANULES)
    ap.add_argument("--keep", type=int, default=KEEP)
    args = ap.parse_args(argv)
    from fetch import nisar as fn

    from live.config import NOW_BOUNDS

    now = dt.datetime.now(dt.UTC)
    granules = fn.search_gcov_domain(NOW_BOUNDS, now - dt.timedelta(days=LOOKBACK_DAYS), now)
    update(args.now_dir, tuple(NOW_BOUNDS), granules, os.environ.get("EARTHDATA_TOKEN"),
           keep=args.keep, max_granules=args.max_granules)
    return 0


if __name__ == "__main__":
    sys.exit(main())
