"""REPLAY v2 (L44) re-score: run the published scoring tools against the
*_v2 COGs and write cases.<c>.replay_v2 into data/metrics.json.

Nothing v1 is touched: the tools are re-run with cog_suffix="_v2" (the
option added in this change), flat keys are collected from an in-memory
scratch copy of metrics.json, and ONLY `cases.<c>.replay_v2` is written —
a {method, diagnosis, before_after} blob where before_after maps each
headline key to {"v1": ..., "v2": ...}. Rows that could not be re-scored
carry "v2": "not re-scored".

Steps: warned_before (onset by_lead + sensitivity + forecast_lead_time_onset),
smap_latency_sensitivity, rs_ems_validation, rs_ems_landslide,
prediction_lift, alarm_precision, and the critical-area/people stats
(tp.bundle._critical_stats on the v2 series bands; people on the native
30" GPW raster where it covers the case grid — rs_2024 — else the case
pop COG, i.e. the v1 ~1 km path, recorded in the method).

    PYTHONPATH=.:pipeline .venv/bin/python tools/replay_v2_score.py [--skip ems,alarm]
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
import os
import sys
from pathlib import Path

import numpy as np
import rasterio
import rasterio.windows

REPO = Path(__file__).resolve().parents[1]
for _p in (str(REPO), str(REPO / "pipeline"), str(REPO / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import alarm_precision as ap  # noqa: E402
import prediction_lift as pl  # noqa: E402
import replay_v2 as rv  # noqa: E402
import rs_ems_landslide as lslide  # noqa: E402
import rs_ems_validation as emsv  # noqa: E402
import smap_latency_sensitivity as smap  # noqa: E402
import warned_before as wb  # noqa: E402
from tp.bundle import _critical_stats  # noqa: E402

SUFFIX = "_v2"
REFS = rv.CASE_REFS
# native GPW v4.11 2020 30" (people-fix work on main) — covers rs_2024 only.
GPW_NATIVE = (Path.home() / "code/r2l2pj/tipping-point-data/data/now/static/gpw"
              "/gpw_popcount_2020_30sec.tif")

METHOD = (
    "v2 = TPI re-scored with R computed on the 90 m TPI grid: P3 = 3-day IMERG "
    "sum over [D-3d, D) from the upstream imerg_daily.npz cube (0.1 deg; GEE "
    "NASA/GPM_L3/IMERG_V07 / GES DISC identical), percentiles p90/p95/p99 PER "
    "0.1 deg cell (2001-2023, 3-day sums ending D+-30d step 3), both bilinear "
    "to the 90 m grid then rain_score; S = the case s_series bilinear to 90 m "
    "(unchanged); T = the existing t_slide/t_flood COGs; TPI = (R*S*T)^(1/3) "
    "= NaN on permanent water (JRC GSW occurrence>=50% at 30 m max-resampled "
    "to 90 m OR MCD12Q1 IGBP 17). Issued GFS frames: P3 = observed IMERG days "
    "< issue + GFS 0.25 deg days >= issue (L28b), percentiles at the depicted "
    "day E=I+k, S held at band I-1. All tools re-run with cog_suffix=_v2; "
    "scoring rules/anchors/thresholds identical to v1."
)
DIAGNOSIS = (
    "v1 R came from Earth Engine at ~1 km: IMERG 0.1 deg 3-day sums scored "
    "against a climatology asset exported at 10 km with NEAREST resampling "
    "(gee/tpi.py), baking ~10 km stair steps into r_series and the TPI; and "
    "no permanent-water mask, so the Guaiba lagoon / Jacui floodplain and "
    "large lakes carried finite TPI. v2 removes both artifacts; domain-mean R "
    "v2 vs v1 agrees within a few % on smooth areas (see r_check)."
)


def _d(s) -> dt.date:
    return dt.date(int(str(s)[:4]), int(str(s)[4:6]), int(str(s)[6:8]))


def flat_wb(r: dict, key: str) -> dict:
    """The flat keys warned_before.merge would write for one case."""
    blob = {}
    head = r["forecast_gfs"] or r["observed"]
    blob[f"{key}_pct"] = head["alto_or_critico_pct"] if head else None
    blob[f"{key}_critical_pct"] = head["critico_pct"] if head else None
    blob[f"{key}_observed_pct"] = (
        r["observed"]["alto_or_critico_pct"] if r["observed"] else None)
    for h in wb.LEADS_H:
        L = r["by_lead"][str(h)]
        g, o = L["gfs"], L["observed"]
        blob[f"{key}_gfs_{h}h_pct"] = g["alto_or_critico_pct"] if g else None
        blob[f"{key}_gfs_{h}h_critical_pct"] = g["critico_pct"] if g else None
        blob[f"{key}_gfs_{h}h_issue"] = g["issue"] if g else None
        blob[f"{key}_observed_{h}h_pct"] = o["alto_or_critico_pct"] if o else None
        blob[f"{key}_observed_{h}h_critical_pct"] = o["critico_pct"] if o else None
    return blob


def lt_headline(lt: dict) -> dict:
    return {k: lt[k] for k in ("earliest_critical_issue", "would_have_warned_h",
                               "lead_h_median", "n_warned", "n_issue_days")}


def crit_headline(stats: dict) -> dict:
    """peak-day row + first-critical date, same shape as publish_case's flat keys."""
    days = sorted(stats)
    peak = max(stats.items(), key=lambda kv: kv[1]["critical_km2"],
               default=None)
    first = next((d for d in days
                  if stats[d]["critical_km2"] and stats[d]["critical_km2"] > 0),
                 None)
    return {
        "critical_km2": peak[1]["critical_km2"] if peak else None,
        "critical_km2_date": peak[0] if peak else None,
        "critical_slide_km2": peak[1]["critical_slide_km2"] if peak else None,
        "critical_flood_km2": peak[1]["critical_flood_km2"] if peak else None,
        "people_critical": peak[1]["people_critical"] if peak else None,
        "first_critical_date_gt06": first,
    }


def bands_by_date(cog_dir: Path, case: str, ref: str, suffix: str) -> dict:
    out = {"slide": {}, "flood": {}}
    for haz in ("slide", "flood"):
        p = cog_dir / f"{case}_tpi_{haz}_{ref}{suffix}.tif"
        with rasterio.open(p) as src:
            for i, d in enumerate(src.descriptions, 1):
                out[haz][_d(d)] = (p, i)
    return out


TMP = Path.home() / ".cache/lead-analysis/replay-v2"


def pop_cog_for(case: str, cog_dir: Path) -> tuple[Path | None, str]:
    """Native 30" GPW cropped to the case bbox (covers both cases), else the
    case pop COG. Crop needed: _critical_stats allocates a full-extent frac
    array on the pop grid and global GPW would not fit the 2G scope."""
    if GPW_NATIVE.exists():
        with rasterio.open(GPW_NATIVE) as ps, \
                rasterio.open(cog_dir / f"{case}_tpi_slide_{REFS[case]}{SUFFIX}.tif") as like:
            pb = ps.bounds
            lb = like.bounds
            if (pb.left <= lb.left and pb.right >= lb.right
                    and pb.bottom <= lb.bottom and pb.top >= lb.top):
                win = rasterio.windows.from_bounds(
                    lb.left - 0.02, lb.bottom - 0.02,
                    lb.right + 0.02, lb.top + 0.02, ps.transform)
                win = win.round_offsets().round_lengths()
                arr = ps.read(1, window=win)
                TMP.mkdir(parents=True, exist_ok=True)
                out = TMP / f"gpw_crop_{case}.tif"
                prof = ps.profile
                prof.update(height=arr.shape[0], width=arr.shape[1],
                            transform=ps.window_transform(win),
                            count=1, compress="deflate")
                with rasterio.open(out, "w", **prof) as dst:
                    dst.write(arr, 1)
                return out, "native 30 arc-sec GPW v4.11 2020 (people-fix, cropped)"
    pops = sorted(cog_dir.glob(f"{case}_pop_gpw_*.tif"))
    return (pops[-1] if pops else None,
            "case pop COG (~1 km EE-resampled GPW, as v1)")


def collect_flat(m: dict, case: str, keys: list[str]) -> dict:
    blob = m["cases"][case]
    return {k: blob.get(k) for k in keys}


def run(data_root: Path, skip: set[str] = frozenset()) -> dict:
    m_v1 = json.loads((data_root / "metrics.json").read_text())
    m_v2 = copy.deepcopy(m_v1)
    ba: dict[str, dict] = {"rs_2024": {}, "wa_2025": {}}

    def put(case, key, v2):
        ba[case][key] = {"v1": m_v1["cases"][case].get(key), "v2": v2}

    def put_not_rescored(case, keys):
        for k in keys:
            ba[case][k] = {"v1": m_v1["cases"][case].get(k),
                           "v2": "not re-scored"}

    # ---- warned_before + forecast_lead_time_onset ----------------------
    res_wb = wb.run(data_root, SUFFIX)
    wb.apply(res_wb, data_root, m_v2, SUFFIX)   # v2 flat keys into scratch
    for case, key in (("wa_2025", "flood_warned"), ("rs_2024", "slide_warned")):
        for k, v in flat_wb(res_wb[case], key).items():
            put(case, k, v)
        lt2 = m_v2["cases"][case]["forecast_lead_time_onset"]
        for k, v in lt_headline(lt2).items():
            put(case, f"forecast_lead_time_onset.{k}", v)

    # ---- smap latency ---------------------------------------------------
    res_smap = smap.run(data_root, SUFFIX)
    for case, r in res_smap.items():
        for row in r["rows"]:
            for h in wb.LEADS_H:
                put(case,
                    f"{r['key']}_smap_latency.I-{row['s_age_days_at_issue']}"
                    f".gfs_{h}h_pct", row[f"gfs_{h}h_pct"])

    # ---- EMS flood + landslide (rs_2024) --------------------------------
    if "ems" not in skip:
        res_ems = emsv.score(data_root, cog_suffix=SUFFIX)
        emsv.apply(res_ems, m_v2)
        for k in ("flood_ems_iou", "flood_ems_f1", "flood_ems_precision",
                  "flood_ems_recall", "flood_ems_flagged_before_onset_pct",
                  "flood_ems_flagged_before_onset_critical_pct"):
            put("rs_2024", k, m_v2["cases"]["rs_2024"][k])
        res_lslide = lslide.score(data_root, cog_suffix=SUFFIX, cases=m_v2)
        lslide.apply(res_lslide, m_v2)
        for k in ("landslide_ems_warned_observed_pct",
                  "landslide_ems_warned_observed_critical_pct",
                  *(f"landslide_ems_warned_gfs_{h}h_pct" for h in (24, 48, 72)),
                  *(f"landslide_ems_warned_gfs_{h}h_critical_pct"
                    for h in (24, 48, 72))):
            put("rs_2024", k, m_v2["cases"]["rs_2024"][k])
    else:
        put_not_rescored("rs_2024", [
            "flood_ems_iou", "flood_ems_f1", "flood_ems_precision",
            "flood_ems_recall", "flood_ems_flagged_before_onset_pct",
            "flood_ems_flagged_before_onset_critical_pct",
            "landslide_ems_warned_observed_pct",
            "landslide_ems_warned_observed_critical_pct",
            *(f"landslide_ems_warned_gfs_{h}h_pct" for h in (24, 48, 72)),
            *(f"landslide_ems_warned_gfs_{h}h_critical_pct"
              for h in (24, 48, 72))])

    # ---- prediction_lift ------------------------------------------------
    res_lift = pl.run(data_root, SUFFIX, cases=m_v2["cases"])
    for row, (case, pre) in pl.KEYS.items():
        r = res_lift[row]
        put(case, f"{pre}_area_share_pct", r["area"]["alto_plus_share_pct"])
        put(case, f"{pre}_critical_area_share_pct",
            r["area"]["critico_share_pct"])
        put(case, f"{pre}_lift", r["lift"]["alto_plus"])
        put(case, f"{pre}_critical_lift", r["lift"]["critico"])
        if row == "rs_ems_landslide_centroid":
            put(case, f"{pre}_pct", r["hit"]["alto_plus"])
            put(case, f"{pre}_critical_pct", r["hit"]["critico"])

    # ---- alarm precision -------------------------------------------------
    res_alarm: dict | None = None
    if "alarm" not in skip:
        res_alarm = {}
        for case in ("rs_2024", "wa_2025"):
            blob = ap.run_case(data_root, case, SUFFIX)
            res_alarm[case] = blob
            for k, v in ap.flat_keys(blob).items():
                put(case, k, v)
    else:
        for case in ("rs_2024", "wa_2025"):
            put_not_rescored(case, list(ap.flat_keys(
                m_v1["cases"][case]["alarm_precision"]).keys()))

    # ---- critical area + people on the v2 series -------------------------
    crit_detail = {}
    for case in ("rs_2024", "wa_2025"):
        cog_dir = data_root / case / "cog"
        bbd = bands_by_date(cog_dir, case, REFS[case], SUFFIX)
        pop, pop_src = pop_cog_for(case, cog_dir)
        stats = _critical_stats(bbd, pop)
        crit_detail[case] = stats
        head = crit_headline(stats)
        for k, v in head.items():
            put(case, k, v)
        put(case, "people_critical_source_v2", pop_src)

    detail = {
        "warned_before": {c: {"observed": res_wb[c]["observed"],
                              "forecast_gfs": res_wb[c]["forecast_gfs"],
                              "by_lead": res_wb[c]["by_lead"],
                              "by_date_observed": res_wb[c]["by_date_observed"],
                              "by_issue_gfs": res_wb[c]["by_issue_gfs"]}
                          for c in ("rs_2024", "wa_2025")},
        "forecast_lead_time_onset": {
            c: m_v2["cases"][c]["forecast_lead_time_onset"]
            for c in ("rs_2024", "wa_2025")},
        "smap_latency": res_smap,
        "prediction_lift": res_lift,
        "critical_stats": crit_detail,
    }
    if "ems" not in skip:
        detail["flood_ems"] = {"headline": res_ems["headline"],
                               "flagged_before_onset":
                               res_ems["flagged_before_onset"]}
        detail["landslide_ems"] = res_lslide["tpi_warned"]
    if res_alarm is not None:
        detail["alarm_precision"] = res_alarm
    return {"before_after": ba, "detail": detail}


def merge(data_root: Path, res: dict, build: dict) -> None:
    path = data_root / "metrics.json"
    m = json.loads(path.read_text())
    for case in ("rs_2024", "wa_2025"):
        m["cases"][case]["replay_v2"] = {
            "generated": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
            "method": METHOD,
            "diagnosis": DIAGNOSIS,
            "r_check": {k: build[case][k] for k in
                        ("r_v1_mean", "r_v2_mean", "water_cells",
                         "water_cells_frac_of_finite", "gsw_ge50_cells",
                         "mcd12q1_17_cells")} | {"per_day": build[case]["series"],
                                                "gfs_v2_written":
                                                build[case]["gfs_v2_written"],
                                                "gfs_skipped":
                                                build[case]["gfs_skipped"]},
            "before_after": res["before_after"][case],
            "detail": res["detail"] if case == "rs_2024" else
            {k: (v[case] if isinstance(v, dict) and case in v else v)
             for k, v in res["detail"].items()},
        }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(m, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def main(argv=None) -> int:
    apx = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    apx.add_argument("--data-root", type=Path, default=REPO / "data")
    apx.add_argument("--skip", default="",
                     help="comma-separated steps to mark not re-scored (ems,alarm)")
    apx.add_argument("--build-report", type=Path, default=None,
                     help="JSON of replay_v2.build_case results for r_check")
    args = apx.parse_args(argv)
    skip = {s for s in args.skip.split(",") if s}
    build = (json.loads(args.build_report.read_text())
             if args.build_report else
             {c: {"r_v1_mean": None, "r_v2_mean": None, "water_cells": None,
                  "water_cells_frac_of_finite": None, "gsw_ge50_cells": None,
                  "mcd12q1_17_cells": None, "series": [],
                  "gfs_v2_written": [], "gfs_skipped": []}
              for c in ("rs_2024", "wa_2025")})
    res = run(args.data_root, skip)
    merge(args.data_root, res, build)
    for case, rows in res["before_after"].items():
        print(f"== {case} ==")
        for k, v in rows.items():
            print(f"  {k}: v1={v['v1']}  v2={v['v2']}")
    print("merged cases.<c>.replay_v2 into", args.data_root / "metrics.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
