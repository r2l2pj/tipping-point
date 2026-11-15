"""Alarm precision of the GFS TPI forecast — municipality x issue-day units.

Unit = municipality (IBGE municipio for rs_2024; US Census TIGER county for
wa_2025) x GFS 00Z run issue day D, over every replay issue day that exists as
data/<case>/cog/<case>_tpi_gfs_<D>.tif.

ALARM(muni, D) = any landslide-TPI cell (~90 m, cell centre inside the
municipality polygon) with TPI over the d1..d3 `_slide` bands > 0.6 (Critico)
or, separately, > 0.4 (Alto+). Bands d1..d3 of the TPI COG depict D+1..D+3
(the `tools/warned_before.py` gfs_frames convention: the frame `d<k>` is the
3-day rain window ending D+k). The variant that re-reads the same bands as
depicting D..D+2 (the forecast_rain COG `d<j>` = day D+j-1 convention) is
reported alongside — the alarm masks are identical; only the hit-overlap
test shifts one day earlier.

HIT(muni, D) = the municipality holds >= 1 mapped landslide AND the run's
depicted window overlaps the trigger window (the inventories are undated,
imagery-mapped, so every slide is assumed inside it — see
docs/conventions-dates.md and .crew/research/event-onsets.md):
  rs_2024: 2024-04-29..2024-05-02 (onset 2024-04-29 03:00Z, L37)
  wa_2025: 2025-12-08..2025-12-12 (onset 2025-12-08 08:00Z, L37)

References scored separately: rs_2024 catalog (16,027 UFRGS/Cemaden inventory
points) and ems (497 Copernicus EMS EMSR720 mass-movement polygons); wa_2025
catalog (15 WGS points). Flat keys mirror the `catalog` reference only.

  PYTHONPATH=.:pipeline .venv/bin/python tools/alarm_precision.py [--data-root data] [--write]
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
from rasterio.features import rasterize
from shapely.geometry import shape
from shapely import STRtree

REPO = Path(__file__).resolve().parents[1]
TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
import warned_before as wb

ALTO, CRIT = wb.ALTO, wb.CRIT
CACHE = Path.home() / ".cache" / "lead-analysis" / "alarm-prec"
WA_COUNTIES = CACHE / "wa_counties.geojson"

CUTS = {"critical": CRIT, "alto_plus": ALTO}
# depicted-window shift: 0 -> band d<k> depicts D+k (TPI convention, primary);
# -1 -> the same bands read as depicting D+k-1 (forecast_rain COG convention).
VARIANTS = {"depict_d1_d3": 0, "depict_d0_d2": -1}
VARIANT_LABELS = {
    "depict_d1_d3": "bands d1..d3 depict D+1..D+3 (TPI frame convention)",
    "depict_d0_d2": "bands d1..d3 re-read as depicting D..D+2 (forecast_rain "
                    "COG d<j> = day D+j-1 convention; same arrays, hit test "
                    "only)",
}
TRIGGER_WINDOWS = {
    "rs_2024": (dt.date(2024, 4, 29), dt.date(2024, 5, 2)),
    "wa_2025": (dt.date(2025, 12, 8), dt.date(2025, 12, 12)),
}
REFERENCES = {
    "rs_2024": [("catalog", "landslide_catalog.geojson"),
                ("ems", "landslide_ems.geojson")],
    "wa_2025": [("catalog", "landslide_catalog.geojson")],
}
FLAT_REFERENCE = "catalog"


# ---------------------------------------------------------------- geometry
def iter_issue_files(cog_dir: Path, case: str) -> list[tuple[dt.date, Path]]:
    """(issue date, path) for every <case>_tpi_gfs_<YYYYMMDD>.tif, sorted."""
    out = []
    for p in sorted(cog_dir.glob(f"{case}_tpi_gfs_*.tif")):
        out.append((wb._day(p.stem.rsplit("_", 1)[1]), p))
    return out


def slide_bands(descriptions: tuple[str, ...]) -> list[tuple[int, int]]:
    """(horizon k, 1-based band index) of the `_slide` bands, sorted by k."""
    out = []
    for i, name in enumerate(descriptions, start=1):
        if name.endswith("_slide"):
            out.append((int(name[1]), i))
    return sorted(out)


def depicted_window(issue: dt.date, horizons: list[int],
                    shift: int) -> tuple[dt.date, dt.date]:
    """(first, last) calendar day depicted by the run's slide bands."""
    days = [issue + dt.timedelta(days=k + shift) for k in horizons]
    return min(days), max(days)


def windows_overlap(a: tuple[dt.date, dt.date],
                    b: tuple[dt.date, dt.date]) -> bool:
    return a[0] <= b[1] and b[0] <= a[1]


def lead_h(issue: dt.date, onset: dt.datetime) -> float:
    """Hours from the issue 00Z to the onset (negative when issued after)."""
    t0 = dt.datetime.combine(issue, dt.time(0), tzinfo=dt.UTC)
    return round((onset - t0).total_seconds() / 3600, 1)


def municipality_ids(geoms: list, transform, shape_hw) -> np.ndarray:
    """Rasterized municipality index per TPI cell (0 = outside all polygons).

    Cell-centre rule (all_touched=False): each ~90 m cell belongs to the
    municipality containing its centre; polygons never overlap so the
    partition is unique.
    """
    r = rasterize([(g, i + 1) for i, g in enumerate(geoms)],
                  out_shape=shape_hw, transform=transform, fill=0,
                  dtype="int32", all_touched=False)
    return r


def slide_municipalities(slide_geoms: list, muni_geoms: list) -> set[int]:
    """Municipality indices (0-based) intersected by >= 1 slide geometry."""
    tree = STRtree(muni_geoms)
    hit = set()
    for g in slide_geoms:
        hit.update(int(i) for i in tree.query(g, predicate="intersects"))
    return hit


def _slide_geoms(path: Path) -> list:
    """Shapely geometries of every slide feature/member in the geojson."""
    fc = json.loads(path.read_text())
    geoms = []
    for f in fc["features"]:
        g = shape(f["geometry"])
        geoms.extend(g.geoms if hasattr(g, "geoms") else [g])
    return geoms


def _muni_features(case: str, data_root: Path) -> tuple[list[str], list]:
    """(names, shapely geoms) — IBGE municipios for RS, TIGER counties for WA."""
    if case == "wa_2025":
        fc = json.loads(WA_COUNTIES.read_text())
        feats = [f for f in fc["features"] if f["properties"].get("admin") == "county"]
    else:
        fc = json.loads(
            (data_root / case / "geojson" / "municipal_boundaries.geojson").read_text())
        feats = [f for f in fc["features"] if f["properties"].get("admin") == "municipio"]
    return ([f["properties"]["name"] for f in feats],
            [shape(f["geometry"]) for f in feats])


# ---------------------------------------------------------------- scoring
def _median_iqr(xs: list[float]) -> dict:
    if not xs:
        return {"median": None, "iqr": [None, None], "n": 0}
    return {"median": round(float(np.percentile(xs, 50)), 1),
            "iqr": [round(float(np.percentile(xs, 25)), 1),
                    round(float(np.percentile(xs, 75)), 1)],
            "n": len(xs)}


def summarize(units: list[dict], n_munis: int, n_slide_munis: int,
              onset: dt.datetime) -> dict:
    """All statistics for one reference x variant x cut.

    `units` rows: {muni, issue (date), alarm (bool), hit (bool), overlap
    (bool)}. `hit` is the unit property (slide in municipality AND depicted
    window overlaps the trigger window); `alarm` is the TPI>cut verdict.
    A slide municipality is detected when >= 1 of its hit units is alarmed;
    "before onset" restricts the qualifying runs to issue 00Z < onset.
    """
    n_units = len(units)
    hit_units = [u for u in units if u["hit"]]
    alarms = [u for u in units if u["alarm"]]
    hits = [u for u in alarms if u["hit"]]
    n_alarms, n_hits = len(alarms), len(hits)
    precision = 100.0 * n_hits / n_alarms if n_alarms else None
    baseline = 100.0 * len(hit_units) / n_units if n_units else None
    detected_pre = {u["muni"] for u in hits
                    if dt.datetime.combine(u["issue"], dt.time(0), tzinfo=dt.UTC) < onset}
    detected_any = {u["muni"] for u in hits}
    leads = [lead_h(u["issue"], onset) for u in hits]
    leads_pre = [x for x in leads if x > 0]
    by_issue = []
    for issue in sorted({u["issue"] for u in units}):
        rows = [u for u in units if u["issue"] == issue]
        a = sum(1 for u in rows if u["alarm"])
        h = sum(1 for u in rows if u["alarm"] and u["hit"])
        by_issue.append({"issue": issue.isoformat(),
                         "window_overlaps_trigger": bool(rows[0]["overlap"]),
                         "n_alarms": a, "n_hits": h,
                         "precision_pct": round(100.0 * h / a, 2) if a else None})
    return {
        "n_units": n_units,
        "n_hit_units": len(hit_units),
        "n_alarms": n_alarms,
        "n_hits": n_hits,
        "false_alarms": n_alarms - n_hits,
        "precision_pct": round(precision, 2) if precision is not None else None,
        "baseline_pct": round(baseline, 2) if baseline is not None else None,
        "lift": round(precision / baseline, 2)
                if precision is not None and baseline else None,
        "n_slide_municipalities": n_slide_munis,
        "n_municipalities": n_munis,
        "pod_before_onset_pct": round(100.0 * len(detected_pre) / n_slide_munis, 2)
                                if n_slide_munis else None,
        "pod_before_window_end_pct": round(100.0 * len(detected_any) / n_slide_munis, 2)
                                     if n_slide_munis else None,
        "alarm_lead_h_all": _median_iqr(leads),
        "alarm_lead_h_issued_before_onset": _median_iqr(leads_pre),
        "by_issue_day": by_issue,
    }


def run_case(data_root: Path, case: str) -> dict:
    issues = iter_issue_files(data_root / case / "cog", case)
    if not issues:
        raise SystemExit(f"{case}: no tpi_gfs COGs")
    names, geoms = _muni_features(case, data_root)
    onset = wb.ANCHORS[case][0]["utc"]
    win = TRIGGER_WINDOWS[case]

    with rasterio.open(issues[0][1]) as src:
        transform, hw = src.transform, (src.height, src.width)
        bands = slide_bands(src.descriptions)
    horizons = [k for k, _ in bands]
    ids = municipality_ids(geoms, transform, hw).ravel()
    covered = int((ids > 0).sum())

    # per-day municipality max TPI over the slide bands (reduceat layout)
    order = np.argsort(ids, kind="stable")
    ids_sorted = ids[order]
    first = int(np.searchsorted(ids_sorted, 1))
    seg_ids = ids_sorted[first:]
    seg_start = np.flatnonzero(np.r_[True, np.diff(seg_ids) > 0])
    starts = first + seg_start
    seg_muni = seg_ids[seg_start] - 1          # 0-based municipality index
    per_issue_max = {}                          # issue -> {muni: max_tpi}
    for issue, path in issues:
        with rasterio.open(path) as src:
            tpi = wb._nanmax([src.read(b).ravel() for _, b in bands])
        tpi_sorted = np.where(np.isfinite(tpi[order]), tpi[order], -np.inf)
        mx = np.maximum.reduceat(tpi_sorted, starts)
        per_issue_max[issue] = mx              # aligned with seg_muni

    refs = {}
    for ref_id, geojson_name in REFERENCES[case]:
        sg = _slide_geoms(data_root / case / "geojson" / geojson_name)
        slide_set = slide_municipalities(sg, geoms)
        slide_names = sorted(names[i] for i in slide_set)
        variants = {}
        for var, shift in VARIANTS.items():
            cuts = {}
            for cut, thr in CUTS.items():
                units = []
                for issue in per_issue_max:
                    dw = depicted_window(issue, horizons, shift)
                    overlap = windows_overlap(dw, win)
                    mx_map = dict(zip(seg_muni.tolist(),
                                      per_issue_max[issue].tolist()))
                    for m in range(len(names)):
                        units.append({"muni": m, "issue": issue,
                                      "alarm": bool(mx_map.get(m, -np.inf) > thr),
                                      "overlap": overlap,
                                      "hit": bool(m in slide_set and overlap)})
                cuts[cut] = summarize(units, len(names), len(slide_set), onset)
            variants[var] = {"depicted_window": VARIANT_LABELS[var], "cuts": cuts}
        refs[ref_id] = {
            "file": f"data/{case}/geojson/{geojson_name}",
            "n_slides": len(sg),
            "slide_municipalities": slide_names,
            "variants": variants,
        }

    admin = "county" if case == "wa_2025" else "municipio"
    muni_src = ("US Census TIGER BAS 2026 counties intersecting the case AOI "
                "(tigerweb State_County MapServer layer 1, cached "
                "~/.cache/lead-analysis/alarm-prec/wa_counties.geojson — the "
                "published municipal_boundaries.geojson holds places/county "
                "subdivisions, not counties)"
                if case == "wa_2025" else
                "IBGE municipios intersecting the case AOI "
                "(data/rs_2024/geojson/municipal_boundaries.geojson)")
    return {
        "unit": "municipality x GFS 00Z run issue day",
        "municipality_source": muni_src,
        "municipality_admin": admin,
        "n_municipalities": len(names),
        "municipalities": sorted(names),
        "n_tpi_cells_in_municipalities": covered,
        "n_issue_days": len(issues),
        "issue_days": [d.isoformat() for d, _ in issues],
        "onset_utc": onset.isoformat(),
        "onset_local": wb.ANCHORS[case][0]["local"],
        "trigger_window": [win[0].isoformat(), win[1].isoformat()],
        "dating_assumption": ("the landslide references are UNDATED "
                              "(imagery-mapped inventories); every slide is "
                              "assumed inside the trigger window, so a unit "
                              "is a hit iff the municipality holds >= 1 slide "
                              "AND the run's depicted window overlaps the "
                              "trigger window"),
        "definitions": {
            "alarm": "any ~90 m TPI_slide cell whose centre falls inside the "
                     "municipality polygon has TPI > 0.6 (critical cut) / "
                     "> 0.4 (alto_plus cut); max over bands d1..d3 _slide",
            "hit": "municipality holds >= 1 mapped landslide (point/polygon "
                   "intersects it) AND the run's depicted window overlaps the "
                   "trigger window",
            "precision": "alarm_precision_*_pct = 100 * alarmed hit units / "
                         "alarmed units",
            "baseline": "alarm_baseline_pct = 100 * hit units / all units — "
                        "what a uniformly random alarm scores",
            "lift": "alarm_lift = precision_pct / baseline_pct (critical cut)",
            "pod": "alarm_pod_before_onset_pct = % of slide municipalities "
                   "with >= 1 alarmed hit unit issued (00Z) before the onset; "
                   "pod_before_window_end_pct relaxes it to any in-window run",
            "lead": "alarm_lead_h_* = onset - issue 00Z, hours, over alarmed "
                    "hit units; median + IQR over all and over issues before "
                    "the onset",
        },
        "flat_keys_reference": FLAT_REFERENCE,
        "flat_keys_variant": "depict_d1_d3",
        "flat_keys_cut": {"alarm_precision_critical_*": "critical",
                          "alarm_precision_alto_plus_*": "alto_plus",
                          "alarm_pod_before_onset_pct": "critical",
                          "alarm_lead_h_median": "critical, issued before onset",
                          "alarm_baseline_pct": "cut-independent",
                          "alarm_lift": "critical"},
        "references": refs,
    }


def flat_keys(res_case: dict) -> dict:
    ref = res_case["references"][FLAT_REFERENCE]["variants"]["depict_d1_d3"]["cuts"]
    c = ref["critical"]
    a = ref["alto_plus"]
    return {
        "alarm_precision_critical_pct": c["precision_pct"],
        "alarm_precision_critical_n_alarms": c["n_alarms"],
        "alarm_precision_critical_n_hits": c["n_hits"],
        "alarm_precision_critical_false_alarms": c["false_alarms"],
        "alarm_precision_alto_plus_pct": a["precision_pct"],
        "alarm_precision_alto_plus_n_alarms": a["n_alarms"],
        "alarm_precision_alto_plus_n_hits": a["n_hits"],
        "alarm_precision_alto_plus_false_alarms": a["false_alarms"],
        "alarm_pod_before_onset_pct": c["pod_before_onset_pct"],
        "alarm_lead_h_median": c["alarm_lead_h_issued_before_onset"]["median"],
        "alarm_baseline_pct": c["baseline_pct"],
        "alarm_lift": c["lift"],
    }


def merge(data_root: Path, res: dict) -> None:
    path = data_root / "metrics.json"
    m = json.loads(path.read_text())
    for case, blob in res.items():
        m["cases"][case]["alarm_precision"] = blob
        m["cases"][case].update(flat_keys(blob))
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(m, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def markdown_table(res: dict) -> str:
    lines = ["| case | reference | window | cut | n alarms | hits | false alarms | "
             "precision % | baseline % | lift | POD<onset % | lead h med (IQR) |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for case, blob in res.items():
        for ref_id, ref in blob["references"].items():
            for var, vb in ref["variants"].items():
                for cut, c in vb["cuts"].items():
                    lead = c["alarm_lead_h_issued_before_onset"]
                    iqr = lead["iqr"]
                    lead_s = (f"{lead['median']} ({iqr[0]}..{iqr[1]})"
                              if lead["median"] is not None else "—")
                    prec = (f"{c['precision_pct']:.2f}"
                            if c["precision_pct"] is not None else "—")
                    base = (f"{c['baseline_pct']:.2f}"
                            if c["baseline_pct"] is not None else "—")
                    lift = f"{c['lift']:.2f}" if c["lift"] is not None else "—"
                    pod = (f"{c['pod_before_onset_pct']:.2f}"
                           if c["pod_before_onset_pct"] is not None else "—")
                    lines.append(
                        f"| {case} | {ref_id} | {var} | {cut} | {c['n_alarms']} "
                        f"| {c['n_hits']} | {c['false_alarms']} | {prec} | {base} "
                        f"| {lift} | {pod} | {lead_s} |")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-root", type=Path, default=REPO / "data")
    ap.add_argument("--write", action="store_true", help="merge into metrics.json")
    args = ap.parse_args(argv)
    res = {case: run_case(args.data_root, case) for case in ("rs_2024", "wa_2025")}
    print(markdown_table(res))
    if args.write:
        merge(args.data_root, res)
        print("merged into", args.data_root / "metrics.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
