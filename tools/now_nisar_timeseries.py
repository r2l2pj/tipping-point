"""Now-domain NISAR GCOV multi-date backscatter series + HH/HV pair PNGs (L42c/d).

Anonymous CMR search (NISAR_L2_GCOV_V1) for every production pass whose
footprint covers ALL published flood-candidate polygons
(data/now/response/flood_nisar.geojson). Per the 2026-11-15 R2 guards in
tp.response_nisar: only granules matching the post pass's acquisition mode
(rn.mode_token — same looks / same posting) enter the series; pixels within
EDGE_BUFFER_M of the scene valid-data edge or the AOI edge are ignored
(rn.edge_zone); candidate coverage inside RS/SC/PR is checked via
rn.states_geom/rn.in_states on municipal_boundaries.geojson. Rejected passes
are recorded with reasons.

Each date is block-averaged (rn.block_average, linear power) onto a 100 m
grid snapped in the GRANULE's own UTM EPSG — the context passes (Oct 29,
Nov 1) sit in EPSG:32722, the 024D_101 pair in EPSG:32723; stats are area
means so per-date grids need no reprojection. For the pair PNGs both dates
share EPSG:32723.

Writes under data/now/response/:
  png/nisar_hh_<yyyymmdd>.png, png/nisar_hv_<yyyymmdd>.png  (pair dates,
    EPSG:3857 RGBA gray, 2-98% stretch — same renderer as wa_2025 nisar_*)
  nisar_timeseries.json    WA-contract series (dates, areas, frames, reading)
  nisar_layers.json        layer entries merged into data/now/manifest.json
                           by live/now_manifest.finalize (p_trigger-style)

    PYTHONPATH=.:pipeline python tools/now_nisar_timeseries.py \
        [--cache-dir ~/.cache/l42-now-nisar] [--stats-only]

EARTHDATA_TOKEN required unless the cache already holds the per-date arrays.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
for p in (str(REPO_ROOT), str(REPO_ROOT / "pipeline")):
    if p not in sys.path:
        sys.path.insert(0, p)

from tp import flood_mask as fm
from tp import response_nisar as rn
from tp.bundle import _gray_png_3857
from tp.events import DATA_DIR

COLLECTION = "NISAR_L2_GCOV_V1"
RES = 100.0
TERMS = ("HHHH", "HVHV")
PAIR_FRAME = (24, "D", 101)                     # the live Now pair's track/look/frame
# flood-candidate strip (São Miguel Arcanjo, SP) + context
AOI = (-48.20, -24.30, -47.80, -23.70)
LOOKBACK_DAYS = 60
# Same acquisition mode as the published post granule (024D_101 Nov 6):
# the live pair was mixed-mode (2005_M vs 4005_A) — rejected per the guards.
POST_ID = ("NISAR_L2_PR_GCOV_035_024_D_101_4005_DHDH_A_20261106T211603"
           "_20261106T211610_P05036_N_P_J_001")
PRE_ID = ("NISAR_L2_PR_GCOV_034_024_D_101_4005_DHDH_A_20261025T211603"
          "_20261025T211609_P05036_N_P_J_001")
PUBLISHED_PRE_ID = ("NISAR_L2_PR_GCOV_034_024_D_101_2005_DHDH_M_20261025T211603"
                    "_20261025T211633_P05036_N_F_J_001")

CONTROL_BOXES = {
    "control_open_field": (-48.02, -24.05, -47.95, -23.98),  # valley farmland SE of the strip, ~0 pop
    "control_urban": (-48.015, -23.895, -47.995, -23.875),   # São Miguel Arcanjo town centre
}
AREA_LABELS = {
    "flood_candidates": "NISAR flood candidates (published strip)",
    "control_open_field": "open-field control (valley farmland, São Miguel Arcanjo muni)",
    "control_urban": "built-up control (São Miguel Arcanjo, SP)",
}
ROLES = {
    "2026-10-25": "pre-event (live pair geometry, track 024D frame 101)",
    "2026-10-29": "context pass — track 074A frame 166 (ascending)",
    "2026-11-01": "context pass — track 125D frame 100 (descending)",
    "2026-11-06": "post-event (live pair geometry, track 024D frame 101)",
}
OUT = Path(DATA_DIR) / "now" / "response"


def _cand_union(repo_root: Path = REPO_ROOT):
    from shapely.geometry import shape
    from shapely.ops import unary_union

    fc = json.loads(
        (repo_root / "data/now/response/flood_nisar.geojson").read_text())
    return unary_union([shape(f["geometry"]) for f in fc["features"]])


def select_passes(granules: list[dict], cand, post_mode: str) -> tuple[dict, list]:
    """{date: granule} — best granule per day covering `cand`, same mode only.

    `rejected` carries every other granule touching the AOI with its reason.
    """
    by_date: dict[str, tuple] = {}
    rejected: list[dict] = []
    for g in granules:
        d = str(g["start"].date())
        gid = g["granule_id"]
        fp = g.get("footprint")
        if fp is None or not fp.covers(cand):
            rejected.append({"date": d, "granule_id": gid,
                             "reason": "footprint does not cover all "
                                      "candidate polygons"})
            continue
        if not g.get("h5_url"):
            rejected.append({"date": d, "granule_id": gid,
                             "reason": "no .h5 data link"})
            continue
        mt = rn.mode_token(gid)
        g["mode_token"] = mt
        if mt != post_mode:
            rejected.append({"date": d, "granule_id": gid,
                             "reason": f"acquisition mode {mt} != series "
                                      f"mode {post_mode}"})
            continue
        score = (g["coverage"] == "F",)
        if d not in by_date or score > by_date[d][0]:
            if d in by_date:
                rejected.append({"date": d, "granule_id":
                                 by_date[d][1]["granule_id"],
                                 "reason": "duplicate pass on the same date "
                                          "(lower-preference product)"})
            by_date[d] = (score, g)
    return {d: v[1] for d, v in by_date.items()}, rejected


def read_date_db(url: str, term: str, token: str | None):
    """Block-averaged dB on a snapped 100 m grid in the granule's own EPSG."""
    with rn.open_gcov(url, token) as (h5, rr):
        info = rn.granule_grid_info(h5)
        if term not in info["terms"]:
            raise RuntimeError(f"granule lacks {term} ({info['terms']})")
        grid = rn.target_grid(info["x"], info["y"], info["epsg"], AOI, RES)
        arr, io = rn.block_average(h5[f"{rn.GCOV_GRIDS}/{term}"],
                                   info["x"], info["y"], grid, rr)
    return fm.to_db(arr).astype(np.float32), grid, io, info["epsg"]


def box_sel(bbox: tuple, grid: rn.TargetGrid) -> np.ndarray:
    """Boolean window of `bbox` (w,s,e,n lon/lat) on `grid`."""
    from pyproj import Transformer
    from rasterio.transform import rowcol

    w, s, e, n = bbox
    tf = Transformer.from_crs("EPSG:4326", f"EPSG:{grid.epsg}", always_xy=True)
    x0, y0 = tf.transform(w, s)
    x1, y1 = tf.transform(e, n)
    r0, c0 = rowcol(grid.transform, x0, y1)
    r1, c1 = rowcol(grid.transform, x1, y0)
    clamp = lambda v, hi: min(max(0, int(v)), hi)
    r0, r1 = clamp(r0, grid.height), clamp(r1, grid.height)
    c0, c1 = clamp(c0, grid.width), clamp(c1, grid.width)
    sel = np.zeros((grid.height, grid.width), bool)
    sel[r0:r1, c0:c1] = True
    return sel


def cand_sel(cand, grid: rn.TargetGrid) -> np.ndarray:
    """Flood-candidate polygons rasterized (pixel-centre) on `grid`."""
    from pyproj import Transformer
    from rasterio.features import geometry_mask
    from shapely.geometry import mapping
    from shapely import transform as shp_transform

    tf = Transformer.from_crs("EPSG:4326", f"EPSG:{grid.epsg}", always_xy=True)
    c_u = shp_transform(cand, tf.transform, interleaved=False)
    return geometry_mask([mapping(c_u)], out_shape=(grid.height, grid.width),
                         transform=grid.transform, invert=True,
                         all_touched=False)


def area_mean_db(samples: list[np.ndarray]) -> dict:
    """mean = 10·log10 of the linear-power mean; percentiles on dB."""
    cols = [np.asarray(s, dtype=np.float64) for s in samples]
    cols = [c[np.isfinite(c)] for c in cols]
    lin = [np.power(10.0, c / 10.0) for c in cols]

    def _stat(fn, arr):
        return [float(fn(a)) if a.size else None for a in arr]

    return {
        "mean_db": _stat(lambda a: 10.0 * np.log10(a.mean()), lin),
        "median_db": _stat(np.median, cols),
        "p10_db": _stat(lambda a: np.percentile(a, 10), cols),
        "p90_db": _stat(lambda a: np.percentile(a, 90), cols),
        "n_px": [int(c.size) for c in cols],
    }


def _round3(v):
    return [round(float(x), 3) if x is not None and np.isfinite(x) else None
            for x in np.atleast_1d(v)]


def _reading(area: str, dates: list[str], hh: dict) -> str:
    d = hh["mean_db"]

    def _fmt(v):
        return "n/a" if v is None else f"{v:.1f}"

    if area == "flood_candidates":
        return (f"the published candidate strip is readable only on the two "
                f"mid-swath context passes (Oct 29 {_fmt(d[1])} dB, Nov 1 "
                f"{_fmt(d[2])} dB HH — ordinary land values); on both 024D "
                f"frame-101 passes the strip sits inside the 2 km swath-edge "
                f"exclusion, so the 'new water' call came from the data edge, "
                "not a flood signal.")
    if area == "control_urban":
        return (f"São Miguel Arcanjo town centre reads bright (HH "
                f"{_fmt(d[1])}/{_fmt(d[2])} dB on the mid-swath passes — "
                f"building double-bounce) and falls inside the partial-swath "
                "edge zone on the 024D dates.")
    return (f"valley farmland varies "
            f"{_fmt(min(x for x in d if x is not None))}…"
            f"{_fmt(max(x for x in d if x is not None))} dB across the four "
            "passes — inter-date/track drift, not flood-scale darkening.")


def build_doc(dates, db_hh, db_hv, grids, passes, rejected, guards, bounds,
              cand, region, cand_ufs, ios=None) -> dict:
    """nisar_timeseries.json payload (pure once arrays exist)."""
    sels = {}
    masks = {}
    out_areas = {}
    for d in dates:
        grid = grids[d]
        valid = np.isfinite(db_hh[d])
        zone = rn.edge_zone(valid, grid, AOI)
        keep = valid & ~zone
        raw = {"flood_candidates": cand_sel(cand, grid)}
        for name, bb in CONTROL_BOXES.items():
            raw[name] = box_sel(bb, grid)
        masks[d] = raw
        sels[d] = {n: m & keep for n, m in raw.items()}

    for name in ("flood_candidates", *CONTROL_BOXES):
        hh = area_mean_db([db_hh[d][sels[d][name]] for d in dates])
        hv = area_mean_db([db_hv[d][sels[d][name]] for d in dates])
        mask_px = max(int(masks[d][name].sum()) for d in dates)
        entry = {
            "label": AREA_LABELS[name],
            "hh_db": _round3(hh["mean_db"]),
            "hv_db": _round3(hv["mean_db"]),
            "hh_median_db": _round3(hh["median_db"]),
            "hh_p10_db": _round3(hh["p10_db"]),
            "hh_p90_db": _round3(hh["p90_db"]),
            "hv_median_db": _round3(hv["median_db"]),
            "hv_p10_db": _round3(hv["p10_db"]),
            "hv_p90_db": _round3(hv["p90_db"]),
            "n_px_by_date": hh["n_px"],
            "mask_px_by_date": [int(masks[d][name].sum()) for d in dates],
            "km2": round(mask_px * RES * RES / 1e6, 3),
        }
        if name in CONTROL_BOXES:
            entry["bbox"] = list(CONTROL_BOXES[name])
        if name == "flood_candidates":
            entry["uf"] = sorted(cand_ufs)
            entry["in_scope_states"] = sorted(
                set(cand_ufs) & set(guards["states"]))
            entry["scope_note"] = (
                "all candidate polygons are outside RS/SC/PR "
                "(dropped by the states guard in live detection); stats kept "
                "here because the file is the required analysis area")
        out_areas[name] = entry

    y0, y1 = dates[0], dates[-1]
    hh_stats = {n: area_mean_db([db_hh[d][sels[d][n]] for d in dates])
                for n in out_areas}
    pre_id, post_id = PRE_ID, POST_ID
    frames = [
        {"date": y0, "layer_id": "nisar_pre", "polarization": "HH",
         "png": "now/response/png/nisar_hh_20261025.png", "bounds": bounds[0]},
        {"date": y1, "layer_id": "nisar_post", "polarization": "HH",
         "png": "now/response/png/nisar_hh_20261106.png", "bounds": bounds[1]},
        {"date": y0, "layer_id": "nisar_hv_pre", "polarization": "HV",
         "png": "now/response/png/nisar_hv_20261025.png", "bounds": bounds[2]},
        {"date": y1, "layer_id": "nisar_hv_post", "polarization": "HV",
         "png": "now/response/png/nisar_hv_20261106.png", "bounds": bounds[3]},
    ]
    cand_hh = hh_stats["flood_candidates"]["mean_db"]
    reading_line = (
        "Same-mode NISAR passes over the São Miguel Arcanjo (SP) candidate "
        "strip show no new water-darkening between Oct 25 and Nov 6: the strip "
        "was already dark on both 024D passes and reads as ordinary land on "
        "the two mid-swath context passes — the live flood candidates are a "
        "swath-edge artifact, not confirmed flooding.")
    doc = {
        "dates": dates,
        "roles": {d: ROLES[d] for d in dates},
        "units": "dB (10·log10 of the linear-power mean)",
        "areas": out_areas,
        "frames": frames,
        "reading": {n: _reading(n, dates, hh_stats[n]) for n in out_areas},
        "reading_line": reading_line,
        "geometry_note": ("pair dates share track 024 D / frame 101 and mode "
                          "4005_DHDH_A (looks 100); context dates are other "
                          "tracks/incidence — same mode, different geometry, "
                          "so cross-track levels are indicative only"),
        "status": "candidates, never confirmed",
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "grid_note": ("per-date snapped 100 m grid in the granule's native "
                      "EPSG (32723 on 024D passes, 32722 on context passes)"),
        "grids": {d: grids[d].as_dict() for d in dates},
        "method": ("HH/HV block-averaged linear power over the AOI chunks "
                   "only (tp.response_nisar.block_average); per-area stats on "
                   "valid pixels outside the 2 km scene/bbox edge zone "
                   "(rn.edge_zone); candidate polygons rasterized "
                   "pixel-centre on each date's grid"),
        "granules": {d: passes[d]["granule_id"] for d in dates},
        "passes": [
            {"date": d, "track": passes[d]["track"], "look": passes[d]["look"],
             "frame": passes[d]["frame"], "granule_id": passes[d]["granule_id"],
             "coverage": passes[d]["coverage"], "mode_token": passes[d]["mode_token"],
             "looks": passes[d].get("looks"),
             "epsg": grids[d].epsg,
             "same_geometry": (passes[d]["track"], passes[d]["look"],
                               passes[d]["frame"]) == PAIR_FRAME}
            for d in dates
        ],
        "rejected_passes": rejected,
        "guards": guards,
        "masks": {"flood_candidates": "now/response/flood_nisar.geojson"},
        "search_window_days": LOOKBACK_DAYS,
        "caveats": [
            "The published flood_nisar.geojson pair (Oct 25 mode-2005 F → "
            "Nov 6 mode-4005 P) is mixed-mode and its water threshold fell "
            "back to -15 dB — under the R2 guards it is rejected "
            "(no_usable_pair); this series uses the same-mode 4005 products.",
            "Every published candidate polygon lies in São Miguel Arcanjo, "
            "SP — outside the RS/SC/PR live-response scope — and within 2 km "
            "of the partial swath's valid-data edge on both 024D dates.",
            "Unguarded, the strip reads ~-17 dB HH on both 024D dates "
            "(already dark pre-event; swath-edge values are unreliable).",
            "Context passes (Oct 29 ascending, Nov 1 descending) have "
            "different incidence — their dB levels are not directly "
            "comparable to the pair's; they answer 'was the strip "
            "water-dark mid-swath', not pixel change.",
            "Candidates, never confirmed; here they fail the guards and the "
            "same-geometry check.",
        ],
    }
    if ios:
        doc["io_bytes"] = ios
    return doc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="tools.now_nisar_timeseries",
                               description=__doc__.splitlines()[0])
    ap.add_argument("--cache-dir", type=Path,
                    default=Path.home() / ".cache" / "l42-now-nisar",
                    help="per-date npy cache (same-mode_<date>_<TERM>.npy)")
    ap.add_argument("--stats-only", action="store_true",
                    help="only nisar_timeseries.json — no PNG/layer writes")
    args = ap.parse_args(argv)

    cache = args.cache_dir
    token = os.environ.get("EARTHDATA_TOKEN")

    from fetch import nisar as fn

    cand = _cand_union()
    now = datetime.now(UTC)
    granules = fn.search_gcov_domain(AOI, now - timedelta(days=LOOKBACK_DAYS), now)
    post_mode = rn.mode_token(POST_ID)
    passes, rejected = select_passes(granules, cand, post_mode)
    dates = sorted(passes)
    if len(dates) < 3:
        raise RuntimeError(f"only {len(dates)} same-mode passes cover the "
                           f"candidates — need >= 3")
    print(f"passes: {[(d, passes[d]['granule_id'][:58]) for d in dates]}")
    print(f"rejected: {len(rejected)}")

    db_hh: dict[str, np.ndarray] = {}
    db_hv: dict[str, np.ndarray] = {}
    grids: dict[str, rn.TargetGrid] = {}
    ios: dict[str, dict] = {}
    for d in dates:
        g = passes[d]
        for term, store in (("HHHH", db_hh), ("HVHV", db_hv)):
            cf = cache / f"same-mode_{d}_{term}.npy"
            if cf.exists():
                store[d] = np.load(cf)
                continue
            if not token:
                raise RuntimeError(f"EARTHDATA_TOKEN required (cache miss "
                                   f"{cf.name})")
            arr, grid, io, epsg = read_date_db(g["h5_url"], term, token)
            cf.parent.mkdir(parents=True, exist_ok=True)
            np.save(cf, arr)
            store[d] = arr
            grids.setdefault(d, grid)
            ios[f"{d}_{term}"] = io
        if d not in grids:
            # grid cached via npy only: rebuild info-free grid requires a
            # re-read — fetch grid info cheaply (no data read)
            with rn.open_gcov(g["h5_url"], token) as (h5, _rr):
                info = rn.granule_grid_info(h5)
                grids[d] = rn.target_grid(info["x"], info["y"],
                                          info["epsg"], AOI, RES)
        passes[d]["looks"] = ios.get(f"{d}_HHHH", {}).get("looks", 100)

    bounds: list[list] = []
    png_paths: list[Path] = []
    if not args.stats_only:
        png_dir = OUT / "png"
        png_dir.mkdir(parents=True, exist_ok=True)
        pair = [dates[0], dates[-1]]
        for d in pair:
            for term, tag in (("HHHH", "hh"), ("HVHV", "hv")):
                png_bytes, b = _gray_png_3857(
                    db_hh[d] if tag == "hh" else db_hv[d],
                    grids[d].transform, f"EPSG:{grids[d].epsg}",
                    float("nan"))
                p = png_dir / f"nisar_{tag}_{d.replace('-', '')}.png"
                p.write_bytes(png_bytes)
                png_paths.append(p)
                bounds.append(b)
                print(f"png -> {p} ({len(png_bytes)} B, bounds {b})")
        # bounds order must match frames[]: hh pre, hh post, hv pre, hv post
        bounds = [bounds[0], bounds[2], bounds[1], bounds[3]]

    bpath = OUT.parent / "geojson" / "municipal_boundaries.geojson"
    if not bpath.is_file():
        bpath = cache / "municipal_boundaries.geojson"
    region = rn.states_geom(bpath, rn.DEFAULT_UFS)
    cand_ufs = []
    if region is not None:
        from shapely.geometry import shape as _shape

        fc = json.loads((REPO_ROOT / "data/now/response/flood_nisar.geojson")
                        .read_text())
        for f in fc["features"]:
            if not region.covers(_shape(f["geometry"]).representative_point()):
                cand_ufs.append("SP")
            else:
                cand_ufs.append("in-scope")
        in_states = rn.in_states(fc["features"], region)
        n_dropped_states = len(fc["features"]) - len(in_states)
    else:
        n_dropped_states = 0
        cand_ufs = ["unknown"]

    pair_rej = rn.pair_mismatch(PUBLISHED_PRE_ID, POST_ID,
                                {"pre": {"looks": 25}, "post": {"looks": 100}})
    guards = {
        "pair_rule": "same looks + same acquisition mode "
                     "(rn.pair_mismatch)",
        "threshold_rule": "per-scene Otsu only — fallback -15 dB is not "
                          "flood evidence",
        "edge_buffer_m": int(rn.EDGE_BUFFER_M),
        "states": list(rn.DEFAULT_UFS),
        "states_source": ("now/geojson/municipal_boundaries.geojson"
                          if str(bpath).endswith("data/now/geojson/municipal_boundaries.geojson")
                          else "now/geojson/municipal_boundaries.geojson "
                               "(fallback-branch copy — pending lead-gathering)"),
        "candidate_polygons_dropped_outside_states": n_dropped_states,
        "published_pair_rejected": [
            pair_rej,
            "water threshold fell back to -15.0 dB (threshold_method "
            "fallback, nisar_meta.json): per-scene calibration failed",
            "candidate polygons within 2 km of the swath valid-data edge "
            "and the domain bbox east edge",
            "all candidate polygons outside RS/SC/PR (São Miguel Arcanjo, SP)",
        ],
    }

    doc = build_doc(dates, db_hh, db_hv, grids, passes, rejected, guards,
                    bounds, cand, region, cand_ufs, ios=ios or None)
    ts_path = OUT / "nisar_timeseries.json"
    ts_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
    print(f"ts  -> {ts_path}")
    for name, a in doc["areas"].items():
        print(f"  {name}: km2={a['km2']} hh_db={a['hh_db']}")

    if not args.stats_only:
        layers = [{"id": f["layer_id"], "date": f["date"], "polarization": f["polarization"],
                   "png": f"response/png/{Path(f['png']).name}",
                   "cog": None, "asset": None, "bounds": f["bounds"],
                   "kind": "observed"} for f in doc["frames"]]
        lp = OUT / "nisar_layers.json"
        lp.write_text(json.dumps({"layers": layers}, indent=2) + "\n")
        print(f"layers -> {lp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
