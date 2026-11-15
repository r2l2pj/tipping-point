"""Case NISAR frame stack: every GCOV pass over a case AOI in a window, HH + HV.

Same per-pass renderer and contract as the Now stack (live/nisar_stack.py):
id nisar_<hh|hv>_<YYYYMMDD>_<TTTL>, kind nisar_frame, track, polarization, png
EPSG:3857 gray+alpha clipped to the swath, bounds, legend (dB object), sensor.
All three CMR GCOV collections (V1 > PROVISIONAL > BETA for the same acquisition), all tracks;
mixed-mode ('_M') granules skipped. ONE fixed −25..0 dB stretch and ONE grid per
track (every pass of a track has identical PNG size + bounds). Older frame entries
of the same pass + polarization (nisar_hh_<date>, nisar_pre/post, …) get the new
pixels IN PLACE (same id + png path: swipe and nisar_timeseries links keep working);
other passes get new entries. MERGED into _index.json after every pass; a failed
pass is reported and retried on the next run.

    PYTHONPATH=.:pipeline python tools/nisar_case_stack.py --case wa_2025 \
        --start 2025-10-15 --end 2025-12-31 [--res 200]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
for _p in (str(REPO), str(REPO / "pipeline")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# preference order for the SAME acquisition (track, look, frame, start) found in more
# than one collection; BETA holds frames the others lack (e.g. WA 172D f064).
COLLECTIONS = ("NISAR_L2_GCOV_V1", "NISAR_L2_GCOV_PROVISIONAL_V1", "NISAR_L2_GCOV_BETA_V1")


def search(bbox, start: dt.date, end: dt.date) -> list[dict]:
    from fetch import nisar as fn
    seen, acq, out = set(), set(), []
    for coll in COLLECTIONS:
        try:
            gs = fn.search_gcov_domain(bbox, f"{start}T00:00:00Z", f"{end}T23:59:59Z", collection=coll)
        except Exception as exc:  # noqa: BLE001
            print(f"search {coll}: {exc}")
            continue
        for g in gs:
            key = (int(g["track"]), g["look"], int(g["frame"]), str(g["start"])[:16])
            if g["granule_id"] in seen or key in acq:
                continue
            seen.add(g["granule_id"])
            acq.add(key)
            out.append(dict(g, collection=coll))
        print(f"{coll}: {len(gs)} granules")
    return out


def existing_passes(index: list[dict]) -> set[tuple[str, str]]:
    return {(e.get("date"), e.get("track")) for e in index
            if e.get("kind") == "nisar_frame" and e.get("date") and e.get("track")}


FRAME_LIKE = ("nisar_hh_2", "nisar_hv_2", "nisar_pre", "nisar_post", "nisar_hv_pre", "nisar_hv_post",
              "nisar_ref")


def _legacy(index: list[dict], day: str, pol: str, track: str) -> list[dict]:
    """Older frame entries for the same pass + polarization (ids kept: swipe and
    nisar_timeseries link them). WA's have no polarization/track: HH unless 'hv'."""
    out = []
    for e in index:
        i = e.get("id", "")
        if not i.startswith(FRAME_LIKE) or e.get("kind") in ("nisar_change", "nisar_extra"):
            continue
        epol = e.get("polarization") or ("HV" if "_hv" in i else "HH")
        if e.get("date") == day and epol == pol and e.get("track") in (None, track):
            out.append(e)
    return out


def run(case: str, start: dt.date, end: dt.date, res: float, deg: float) -> dict:
    import shutil

    import yaml
    from shapely.geometry import box
    from tp.bundle import merge_index

    from live import nisar_stack as ns

    bbox = tuple(yaml.safe_load((REPO / "config.yaml").read_text())["cases"][case]["aoi"])
    pdir = REPO / "data" / case / "png"
    ipath = pdir / "_index.json"
    granules = search(bbox, start, end)
    plan = ns.plan_passes(granules, box(*bbox), keep=None)
    by_track = {}
    for (t, lk, _day), gs in plan.items():
        by_track.setdefault(ns.tag(t, lk), []).extend(gs)
    grids = {trk: ns.track_bounds(gs, bbox, deg) for trk, gs in by_track.items()}
    token = os.environ.get("EARTHDATA_TOKEN")
    added, failed, inplace = [], [], []
    for key in sorted(plan, key=lambda k: (k[2], k[0])):
        t, lk, day = key
        trk = ns.tag(t, lk)
        try:
            ents = ns.render_pass(key, plan[key], bbox, token, pdir, f"{case}/png", res=res, deg=deg,
                                  fixed=grids[trk])
        except Exception as exc:  # noqa: BLE001
            print(f"{trk}_{day} failed: {type(exc).__name__}: {exc}", flush=True)
            failed.append(f"{trk}_{day}")
            continue
        ix = json.loads(ipath.read_text())
        new = []
        for e in ents:
            e.pop("pass", None)
            old = _legacy(ix["layers"], day, e["polarization"], trk)
            if not old:
                new.append(e)
                continue
            src = REPO / "data" / e["png"]
            for o in old:          # new pixels IN PLACE, same id + png path
                shutil.copyfile(src, REPO / "data" / o["png"])
                o.update({k: e[k] for k in ("bounds", "legend", "track", "mode", "frames", "note",
                                            "grid", "stretch_db", "granules", "sensor", "source")})
                o.setdefault("polarization", e["polarization"])
                inplace.append(o["id"])
            src.unlink()
        ix["layers"] = merge_index(ix["layers"], new)
        tmp = ipath.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(ix, indent=2, ensure_ascii=False) + "\n")
        os.replace(tmp, ipath)
        added += [e["id"] for e in new]
    res_ = {"case": case, "passes_planned": len(plan), "track_grids": grids,
            "re_rendered_in_place": inplace, "added_ids": added, "failed": failed}
    print(json.dumps({k: (v if not isinstance(v, list) else len(v)) for k, v in res_.items()}))
    return res_


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--case", required=True)
    ap.add_argument("--start", type=dt.date.fromisoformat, required=True)
    ap.add_argument("--end", type=dt.date.fromisoformat, required=True)
    ap.add_argument("--res", type=float, default=200.0)
    ap.add_argument("--deg", type=float, default=0.002)
    a = ap.parse_args(argv)
    out = run(a.case, a.start, a.end, a.res, a.deg)
    Path(os.path.expanduser(f"~/.cache/lead-analysis/nisar-stack/{a.case}_result.json")).write_text(
        json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
