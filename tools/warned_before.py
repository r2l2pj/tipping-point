"""Modo 3 "O alerta veio antes?": share of the damage that the TPI already
flagged Alto/Crítico 24-48 h before the damage anchor (replay cases).

  wa_2025 flood  — NISAR-confirmed new-flood AREA (cog/wa_2025_flood_nisar_*.tif,
                   GCOV post 2025-12-16) vs TPI_flood; anchor = the case damage
                   anchor (King County Flood Warning Center update 2025-12-09 00:35Z,
                   data/wa_2025/forecast_lead_time.json anchor_utc).
  rs_2024 slide  — the 16,027 RS 2024 inventory POINTS
                   (geojson/landslide_catalog.geojson) vs TPI_slide; anchor =
                   2024-05-01 00:00Z (the fixed RS dating used by the ML TEST set,
                   chosen independently of rain).
  rs_2024 flood  — null: no flood reference map exists for RS 2024 (pre-NISAR,
                   HLS 100 % cloud, OPERA DSWx-S1 has no granules for the window).

Each damage pixel/point takes the TPI cell (~90 m) that contains it. Window =
frames whose information time is 24-48 h before the anchor:
  observed  — series bands dated anchor-2d..anchor-1d (band date D = rain ending
              D 00Z; IMERG/SMAP latency ignored, so this is an upper bound)
  GFS       — L22 issued frames (cog/<case>_tpi_gfs_<issue>.tif, d1..d3, depicted
              date = issue + k) from 00Z runs 24-48 h before the anchor, using only
              frames depicting dates <= the anchor day.
A pixel/point is "warned" when its max TPI over the window frames reaches Alto
(>= 0.4); Crítico is > 0.6. Also reports the per-date curve and the best issued
GFS at any lead before the anchor.

    PYTHONPATH=.:pipeline .venv/bin/python tools/warned_before.py [--data-root data] [--write]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path

import numpy as np
import rasterio

REPO = Path(__file__).resolve().parents[1]
ALTO, CRIT = 0.4, 0.6
KM_PER_DEG = 111.32


def _day(s: str) -> dt.date:
    return dt.date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def _cells_from_xy(xs, ys, grid) -> np.ndarray:
    gc, gr = (~grid.transform) * (np.asarray(xs), np.asarray(ys))
    gr = np.floor(gr).astype(int)
    gc = np.floor(gc).astype(int)
    ok = (gr >= 0) & (gr < grid.height) & (gc >= 0) & (gc < grid.width)
    if not ok.all():
        raise SystemExit(f"{(~ok).sum()} damage locations fall outside the TPI grid")
    return gr * grid.width + gc


def flood_pixels(flood_path: Path, grid) -> tuple[np.ndarray, np.ndarray]:
    """(TPI-cell index, km2) per flooded pixel (value 1)."""
    with rasterio.open(flood_path) as src:
        a = src.read(1)
        t = src.transform
    rows, cols = np.nonzero(a == 1)
    xs = t.c + (cols + 0.5) * t.a
    ys = t.f + (rows + 0.5) * t.e
    km2 = abs(t.a * t.e) * KM_PER_DEG ** 2 * np.cos(np.deg2rad(ys))
    return _cells_from_xy(xs, ys, grid), km2


def inventory_points(path: Path, grid) -> tuple[np.ndarray, np.ndarray]:
    """(TPI-cell index, weight 1) per inventory point (Point or MultiPoint features)."""
    fc = json.loads(path.read_text())
    pts = []
    for f in fc["features"]:
        g = f["geometry"]
        pts.extend([g["coordinates"]] if g["type"] == "Point" else g["coordinates"])
    xy = np.asarray(pts, float)
    return _cells_from_xy(xy[:, 0], xy[:, 1], grid), np.ones(len(xy))


def shares(tpi_flat: np.ndarray, idx: np.ndarray, w: np.ndarray) -> dict:
    v = tpi_flat[idx]
    valid = np.isfinite(v)
    tot = float(w.sum())
    return {"alto_or_critico_pct": round(100 * float(w[valid & (v >= ALTO)].sum()) / tot, 2),
            "critico_pct": round(100 * float(w[valid & (v > CRIT)].sum()) / tot, 2),
            "no_tpi_pct": round(100 * float(w[~valid].sum()) / tot, 2)}


def _nanmax(arrays: list[np.ndarray]) -> np.ndarray:
    st = np.stack(arrays)
    fin = np.isfinite(st)
    return np.where(fin.any(0), np.max(np.where(fin, st, -1.0), 0), np.nan)


def evaluate(cog: Path, case: str, hazard: str, series: Path, idx: np.ndarray, w: np.ndarray,
             anchor: dt.datetime) -> dict:
    anchor_day = anchor.date()

    def hours_before(day: dt.date) -> float:
        return (anchor - dt.datetime.combine(day, dt.time(0), tzinfo=dt.UTC)).total_seconds() / 3600

    obs = rasterio.open(series)
    bands = {d: i + 1 for i, d in enumerate(obs.descriptions)}
    curve = [{"date": _day(d).isoformat(), "hours_before_anchor": round(hours_before(_day(d)), 1),
              **shares(obs.read(b).ravel(), idx, w)} for d, b in bands.items()]
    win = [d for d in bands if 24 <= hours_before(_day(d)) <= 48]
    observed = ({"frames": win, **shares(_nanmax([obs.read(bands[d]).ravel() for d in win]), idx, w)}
                if win else None)

    def gfs_frames(issue: dt.date):
        p = cog / f"{case}_tpi_gfs_{issue:%Y%m%d}.tif"
        if not p.exists():
            return [], []
        names, arrays = [], []
        with rasterio.open(p) as src:
            if (src.transform, src.shape) != (obs.transform, obs.shape):
                raise SystemExit(f"{p.name}: grid differs from the observed series")
            for b, name in enumerate(src.descriptions, start=1):
                k = int(name[1])
                if name.endswith(f"_{hazard}") and issue + dt.timedelta(days=k) <= anchor_day:
                    names.append(f"issued {issue} d{k} -> {issue + dt.timedelta(days=k)}")
                    arrays.append(src.read(b).ravel())
        return names, arrays

    win_names, win_arrays = [], []
    by_issue = []
    for back in range(0, 15):
        issue = anchor_day - dt.timedelta(days=back)
        names, arrays = gfs_frames(issue)
        if not arrays:
            continue
        s = shares(_nanmax(arrays), idx, w)
        by_issue.append({"issue": issue.isoformat(), "hours_before_anchor": round(hours_before(issue), 1),
                         "frames": names, **s})
        if 24 <= hours_before(issue) <= 48:
            win_names += names
            win_arrays += arrays
    forecast = ({"frames": win_names, **shares(_nanmax(win_arrays), idx, w)} if win_arrays else None)
    best = max(by_issue, key=lambda r: r["alto_or_critico_pct"]) if by_issue else None
    return {"observed": observed, "forecast_gfs": forecast, "best_issued_gfs_before_anchor": best,
            "by_issue_gfs": sorted(by_issue, key=lambda r: r["issue"]), "by_date_observed": curve}


COMMON_DEF = ("TPI Alto or higher (>= 0.4; Crítico > 0.6) in the frames available 24-48 h before the "
              "anchor (max over window frames; observed = series bands dated anchor-2d..anchor-1d, "
              "latency ignored; forecast_gfs = L22 issued GFS 00Z runs 24-48 h before the anchor, "
              "frames depicting dates <= the anchor day); each damage location takes the ~90 m TPI "
              "cell that contains it")


def run(data_root: Path) -> dict:
    out = {}
    # wa_2025 flood (area)
    wa = data_root / "wa_2025"
    lt = json.loads((wa / "forecast_lead_time.json").read_text())
    anchor = dt.datetime.fromisoformat(lt["anchor_utc"])
    series = wa / "cog" / "wa_2025_tpi_flood_20251210.tif"
    with rasterio.open(series) as g:
        idx, w = flood_pixels(wa / "cog" / "wa_2025_flood_nisar_20251210.tif", g)
    out["wa_2025"] = {
        "hazard": "flood", "unit": "% of confirmed flood area",
        "definition": "share of the NISAR-confirmed new-flood AREA (GCOV, post 2025-12-16) with " + COMMON_DEF,
        "anchor_utc": anchor.isoformat(), "anchor_source": "King County Flood Warning Center update "
        "(case damage anchor, data/wa_2025/lead_time_metrics.json)",
        "confirmed_flood_km2": round(float(w.sum()), 3),
        **evaluate(wa / "cog", "wa_2025", "flood", series, idx, w, anchor),
        "explanation": ("TPI_flood is pixel-local: at the flooded pixels T_flood is high (HAND, mean "
                        "~0.96) but local 3-day rain stayed below the pixel's P90 (R = 0) and SMAP "
                        "wetness low until the 2025-12-10 frame. The river flooding was driven by rain "
                        "upstream in the Cascades, which a pixel-local R does not see; the index rose "
                        "on the flooded valley floors only after the first damage report."),
    }
    # rs_2024 landslides (points)
    rs = data_root / "rs_2024"
    anchor = dt.datetime(2024, 5, 1, tzinfo=dt.UTC)
    series = rs / "cog" / "rs_2024_tpi_slide_20240502.tif"
    with rasterio.open(series) as g:
        idx, w = inventory_points(rs / "geojson" / "landslide_catalog.geojson", g)
    out["rs_2024"] = {
        "hazard": "landslide", "unit": "% of inventory points",
        "definition": f"share of the {len(w)} RS 2024 inventory landslide points with " + COMMON_DEF,
        "anchor_utc": anchor.isoformat(), "anchor_source": ("fixed RS dating 2024-05-01 (as the ML "
                                                           "TEST set; chosen independently of rain)"),
        "n_points": int(len(w)),
        **evaluate(rs / "cog", "rs_2024", "slide", series, idx, w, anchor),
    }
    out["rs_2024_flood"] = None
    return out


def merge(data_root: Path, res: dict) -> None:
    path = data_root / "metrics.json"
    m = json.loads(path.read_text())
    for case, key in (("wa_2025", "flood_warned"), ("rs_2024", "slide_warned")):
        blob = m["cases"][case]
        r = res[case]
        head = r["forecast_gfs"] or r["observed"]
        blob[key] = r
        blob[f"{key}_pct"] = head["alto_or_critico_pct"]
        blob[f"{key}_critical_pct"] = head["critico_pct"]
        blob[f"{key}_observed_pct"] = r["observed"]["alto_or_critico_pct"] if r["observed"] else None
        blob[f"{key}_basis"] = "forecast_gfs (issued 24-48 h before the anchor)"
    rsb = m["cases"]["rs_2024"]
    rsb["flood_warned_pct"] = None
    rsb.setdefault("_null_reasons", {})["flood_warned_pct"] = (
        "no flood reference map for RS 2024: event predates NISAR, OPERA DSWx-HLS was 100 % cloud, "
        "OPERA DSWx-S1 has no granules for 2024-05-01..20 (CMR) and the S1 RTC route is pending the "
        "ASF OPERA RTC-S1 EULA")
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(m, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-root", type=Path, default=REPO / "data")
    ap.add_argument("--write", action="store_true", help="merge into metrics.json")
    args = ap.parse_args(argv)
    res = run(args.data_root)
    for case in ("wa_2025", "rs_2024"):
        r = res[case]
        print(case, r["hazard"], "observed", r["observed"], "\n  gfs", r["forecast_gfs"],
              "\n  best issued", {k: v for k, v in (r["best_issued_gfs_before_anchor"] or {}).items() if k != "frames"})
        for c in r["by_date_observed"]:
            print("   ", c)
    if args.write:
        merge(args.data_root, res)
        print("merged into", args.data_root / "metrics.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
