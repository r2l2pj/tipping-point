"""Modo 3 "O alerta veio antes?": share of the damage that the TPI already
flagged Alto/Crítico 24-72 h before the event ONSET (L37) — replay cases.

  wa_2025 flood  — NISAR-confirmed new-flood AREA (cog/wa_2025_flood_nisar_*.tif,
                   GCOV post 2025-12-16) vs TPI_flood; onset = 2025-12-08 00:00 PST
                   (heavy rain begins, Skagit EOC activation 21:00 PST; WA EMD sitrep).
                   Sensitivity: King County FWC update 2025-12-09 00:35Z.
  rs_2024 slide  — the 16,027 RS 2024 inventory POINTS
                   (geojson/landslide_catalog.geojson) vs TPI_slide; onset =
                   2024-04-29 00:00 BRT (first deaths of the event; MetSul laudo).
                   Sensitivity: 2024-04-30 00:00 BRT (first landslide deaths) and the
                   previous fixed dating 2024-05-01 00Z. The inventory has no per-slide
                   times, so every point is scored against the onset.
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
    return {"alto_or_critico_pct": round(100 * float(w[valid & (v > ALTO)].sum()) / tot, 2),
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
    for back in range(15):
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


ONSET_SRC_RS = ("MetSul laudo https://metsul.com/wp-content/uploads/2024/06/metsul-laudo_V4_assinado.pdf ; "
                "Jornal do Comércio 2024-04-29 https://www.jornaldocomercio.com/geral/2024/04/"
                "1152501-homem-morre-arrastado-pela-correnteza-em-paverama.html ; SGB "
                "https://rigeo.sgb.gov.br/bitstream/doc/25165/1/guimaraes_et_al_evento_catastrofico_serra_rs.pdf")
ONSET_SRC_WA = ("WA EMD SEOC sitrep 25-4783 (2025-12-10) https://content.govdelivery.com/attachments/"
                "WAMILITARY/2025/12/11/file_attachments/3490229/SEOC_Sitrep-25-4783_12102025.pdf")
# L37: 'before the damage' = before the documented event ONSET (local midnight of the
# first documented damage day), not the peak. Sensitivity rows keep the other anchors.
ANCHORS = {
    "rs_2024": [
        {"basis": "onset", "utc": dt.datetime(2024, 4, 29, 3, tzinfo=dt.UTC),
         "local": "2024-04-29 00:00 BRT (UTC-3)",
         "label": "event onset: first deaths of the event (2 flood deaths, Paverama, 2024-04-29); "
                  "heavy rain from 2024-04-27, stalled from 04-29",
         "source": ONSET_SRC_RS},
        {"basis": "first_landslide_deaths", "utc": dt.datetime(2024, 4, 30, 3, tzinfo=dt.UTC),
         "local": "2024-04-30 00:00 BRT (UTC-3)",
         "label": "sensitivity: first landslide deaths (Itaara, Silveira Martins, 2024-04-30)",
         "source": ONSET_SRC_RS},
        {"basis": "fixed_dating_may1", "utc": dt.datetime(2024, 5, 1, tzinfo=dt.UTC),
         "local": "2024-04-30 21:00 BRT",
         "label": "sensitivity: previous fixed RS dating 2024-05-01 00Z (ML TEST set; superseded by L37)",
         "source": "project convention (decisions L36a)"},
    ],
    "wa_2025": [
        {"basis": "onset", "utc": dt.datetime(2025, 12, 8, 8, tzinfo=dt.UTC),
         "local": "2025-12-08 00:00 PST (UTC-8)",
         "label": "event onset day: heavy rain begins 2025-12-08; Skagit County EOC full activation "
                  "21:00 PST; damage and evacuations 12-09/10",
         "source": ONSET_SRC_WA},
        {"basis": "king_county_update", "utc": dt.datetime(2025, 12, 9, 0, 35, tzinfo=dt.UTC),
         "local": "2025-12-08 16:35 PST",
         "label": "sensitivity: previous anchor, King County Flood Warning Center update "
                  "2025-12-09 00:35Z (data/wa_2025/forecast_lead_time.json)",
         "source": "King County Flood Warning Center (case damage anchor)"},
    ],
}
LEADS_H = (24, 48, 72)


def by_lead(r: dict, anchor: dt.datetime) -> dict:
    """{h: {gfs, observed}} — the 00Z GFS run of date (anchor - h) (the newest run issued
    at least h hours before the anchor) and the observed band dated (anchor - h) (its
    information time, rain ending that day 00Z, is >= h hours before the anchor)."""
    gfs = {g["issue"]: g for g in r["by_issue_gfs"]}
    obs = {c["date"]: c for c in r["by_date_observed"]}
    out = {}
    for h in LEADS_H:
        day = (anchor - dt.timedelta(hours=h)).date().isoformat()
        g, o = gfs.get(day), obs.get(day)
        out[str(h)] = {
            "gfs": ({"issue": day, "hours_before_anchor": g["hours_before_anchor"],
                     "alto_or_critico_pct": g["alto_or_critico_pct"], "critico_pct": g["critico_pct"],
                     "frames": g["frames"]} if g else None),
            "observed": ({"band_date": day, "hours_before_anchor": o["hours_before_anchor"],
                          "alto_or_critico_pct": o["alto_or_critico_pct"], "critico_pct": o["critico_pct"]}
                         if o else None),
        }
    return out


def anchored(cog, case, hazard, series, idx, w, a: dict) -> dict:
    r = evaluate(cog, case, hazard, series, idx, w, a["utc"])
    return {"anchor_basis": a["basis"], "anchor_utc": a["utc"].isoformat(), "anchor_local": a["local"],
            "anchor_label": a["label"], "anchor_source": a["source"], **r, "by_lead": by_lead(r, a["utc"])}


def onset_lead_time(cog: Path, case: str, anchor: dt.datetime) -> dict:
    """forecast_lead_time (L22/L28b definition) re-anchored on the onset: an issue day I
    (00Z run, I < anchor) 'warned' if any of its frames depicting a date <= the onset
    day has an AOI max (slide or flood) > 0.6; lead_h = anchor - I 00Z."""
    rows = []
    for p in sorted(cog.glob(f"{case}_tpi_gfs_*.tif")):
        issue = _day(p.stem.rsplit("_", 1)[1])
        t0 = dt.datetime.combine(issue, dt.time(0), tzinfo=dt.UTC)
        if t0 >= anchor:
            continue
        first = None
        with rasterio.open(p) as src:
            for b, name in enumerate(src.descriptions, start=1):
                k = int(name[1])
                day = issue + dt.timedelta(days=k)
                if day > anchor.date():
                    continue
                a = src.read(b)
                if np.isfinite(a).any() and np.nanmax(a) > CRIT and (first is None or day < first[1]):
                    first = (k, day, name.split("_", 1)[1])
        lead = round((anchor - t0).total_seconds() / 3600, 1)
        rows.append({"issue": issue.isoformat(), "warned": first is not None,
                     "first_critical_date": first[1].isoformat() if first else None,
                     "hazard": first[2] if first else None, "lead_h": lead if first else None})
    leads = sorted(r["lead_h"] for r in rows if r["warned"])
    q = (lambda x: round(float(np.percentile(leads, x)), 1)) if leads else (lambda x: None)
    return {"anchor_utc": anchor.isoformat(), "earliest_critical_issue": next(
                (r["issue"] for r in rows if r["warned"]), None),
            "would_have_warned_h": leads[-1] if leads else None, "lead_h_median": q(50),
            "lead_h_iqr": [q(25), q(75)] if leads else None, "n_warned": len(leads),
            "n_issue_days": len(rows), "issue_days": rows,
            "definition": "issue days = GFS 00Z runs before the onset; warned = any frame depicting a "
                          "date <= the onset day has an AOI-max TPI (slide or flood) > 0.6 (Crítico) "
                          "anywhere in the case AOI — not at the damage locations (see *_warned for "
                          "that); lead_h = onset - issue 00Z"}


COMMON_DEF = ("TPI Alto or higher (> 0.4; Crítico > 0.6, the app class cuts) in the frames available 24-48 h before the "
              "anchor (max over window frames; observed = series bands dated anchor-2d..anchor-1d, "
              "latency ignored; forecast_gfs = L22 issued GFS 00Z runs 24-48 h before the anchor, "
              "frames depicting dates <= the anchor day); each damage location takes the ~90 m TPI "
              "cell that contains it")


def _sens(r: dict) -> dict:
    """Sensitivity row: anchor + headline window + per-lead (no curves)."""
    keep = ("anchor_basis", "anchor_utc", "anchor_local", "anchor_label", "anchor_source",
            "observed", "forecast_gfs", "by_lead")
    return {k: r[k] for k in keep}


def run(data_root: Path) -> dict:
    out = {}
    # wa_2025 flood (area)
    wa = data_root / "wa_2025"
    series = wa / "cog" / "wa_2025_tpi_flood_20251210.tif"
    with rasterio.open(series) as g:
        idx, w = flood_pixels(wa / "cog" / "wa_2025_flood_nisar_20251210.tif", g)
    on, *sens = ANCHORS["wa_2025"]
    out["wa_2025"] = {
        "hazard": "flood", "unit": "% of confirmed flood area",
        "definition": "share of the NISAR-confirmed new-flood AREA (GCOV, post 2025-12-16) with " + COMMON_DEF,
        "confirmed_flood_km2": round(float(w.sum()), 3),
        **anchored(wa / "cog", "wa_2025", "flood", series, idx, w, on),
        "sensitivity": [_sens(anchored(wa / "cog", "wa_2025", "flood", series, idx, w, a)) for a in sens],
        "explanation": ("TPI_flood is pixel-local: at the flooded pixels T_flood is high (HAND, mean "
                        "~0.96) but local 3-day rain stayed below the pixel's P90 (R = 0) and SMAP "
                        "wetness low until the 2025-12-10 frame. The river flooding was driven by rain "
                        "upstream in the Cascades, which a pixel-local R does not see; the index rose "
                        "on the flooded valley floors only after the first damage report."),
    }
    # rs_2024 landslides (points)
    rs = data_root / "rs_2024"
    series = rs / "cog" / "rs_2024_tpi_slide_20240502.tif"
    with rasterio.open(series) as g:
        idx, w = inventory_points(rs / "geojson" / "landslide_catalog.geojson", g)
    on, *sens = ANCHORS["rs_2024"]
    out["rs_2024"] = {
        "hazard": "landslide", "unit": "% of inventory points",
        "definition": f"share of the {len(w)} RS 2024 inventory landslide points with " + COMMON_DEF
                      + "; the inventory has no per-slide times (imagery-mapped), so every point is "
                        "scored against the event onset",
        "n_points": len(w),
        **anchored(rs / "cog", "rs_2024", "slide", series, idx, w, on),
        "sensitivity": [_sens(anchored(rs / "cog", "rs_2024", "slide", series, idx, w, a)) for a in sens],
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
        blob[f"{key}_pct"] = head["alto_or_critico_pct"] if head else None
        blob[f"{key}_critical_pct"] = head["critico_pct"] if head else None
        blob[f"{key}_observed_pct"] = r["observed"]["alto_or_critico_pct"] if r["observed"] else None
        blob[f"{key}_basis"] = (f"forecast_gfs (00Z runs issued 24-48 h before the event onset "
                                f"{r['anchor_local']})")
        blob[f"{key}_anchor_basis"] = r["anchor_basis"]
        blob[f"{key}_anchor_utc"] = r["anchor_utc"]
        blob[f"{key}_anchor_source"] = r["anchor_source"]
        # per-lead flat keys: the 00Z run issued >= 24/48/72 h before the onset
        for h in LEADS_H:
            L = r["by_lead"][str(h)]
            g, o = L["gfs"], L["observed"]
            blob[f"{key}_gfs_{h}h_pct"] = g["alto_or_critico_pct"] if g else None
            blob[f"{key}_gfs_{h}h_critical_pct"] = g["critico_pct"] if g else None
            blob[f"{key}_gfs_{h}h_issue"] = g["issue"] if g else None
            blob[f"{key}_observed_{h}h_pct"] = o["alto_or_critico_pct"] if o else None
            blob[f"{key}_observed_{h}h_critical_pct"] = o["critico_pct"] if o else None
        blob[f"{key}_by_lead_definition"] = (
            f"{key}_{{gfs|observed}}_{{24|48|72}}h[_critical]_pct = % of the damage reference "
            f"({r['unit']}) whose ~90 m TPI cell was Alto+ (> 0.4) [Crítico > 0.6] in: gfs = the NOAA GFS "
            f"00Z run issued on the UTC date of (onset - 24/48/72 h), i.e. at least that many hours before "
            f"the event onset {r['anchor_local']} (max over its d1..d3 frames depicting dates <= the onset "
            f"day); observed = the observed TPI band dated that day (rain ending that day 00Z, latency "
            f"ignored). Onset = {r['anchor_label']}. Locations on TPI no-data stay in the denominator. "
            f"Sensitivity anchors in {key}.sensitivity.")
    for case in ("wa_2025", "rs_2024"):
        on, *sens = ANCHORS[case]
        cog = data_root / case / "cog"
        lt = onset_lead_time(cog, case, on["utc"])
        lt.update(anchor_basis="onset", anchor_local=on["local"], anchor_label=on["label"],
                  anchor_source=on["source"], sensitivity=[
                      {"anchor_basis": a["basis"], "anchor_local": a["local"],
                       **{k: v for k, v in onset_lead_time(cog, case, a["utc"]).items()
                          if k not in ("issue_days", "definition")}} for a in sens])
        m["cases"][case]["forecast_lead_time_onset"] = lt
    rsb = m["cases"]["rs_2024"]
    rsb["slide_warned_n"] = res["rs_2024"]["n_points"]
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
