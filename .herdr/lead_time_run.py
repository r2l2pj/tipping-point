"""One-off: per-case lead-time metrics -> data/<case>/lead_time_metrics.json."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio

from ml.lead_time import (
    cluster_lead_times, damage_time, first_crossing_frame,
    frame_times_from_bands, read_tpi_series,
)

REPO = Path(__file__).resolve().parent.parent
CRITICAL = 0.6  # thresholds.tpi_classes[-1]


def hist_array(leads, bin_h=6):
    a = np.asarray(leads, dtype=float)
    lo = float(np.floor(a.min() / bin_h) * bin_h)
    hi = float(np.floor(a.max() / bin_h) * bin_h + bin_h)
    counts, _ = np.histogram(a, bins=np.arange(lo, hi + 1, bin_h))
    return counts.tolist(), lo


def summarize_flat(leads):
    a = np.asarray(leads, dtype=float)
    counts, lo = hist_array(a)
    return {
        "lead_time_median_h": float(np.median(a)),
        "lead_time_iqr_h": np.percentile(a, [25, 75]).tolist(),
        "lead_time_n": int(len(a)),
        "warned_before_pct": float(100 * (a > 0).mean()),
        "warned_24h_pct": float(100 * (a >= 24).mean()),
        "lead_time_hist": counts,
        "lead_time_hist_bin_h": 6,
        "lead_time_hist_from_h": lo,
    }


def event_first_crossing(cube, times):
    first = first_crossing_frame(cube, CRITICAL)
    ever = first >= 0
    return times[first[ever].min()] if ever.any() else None


def anchor_ts(event, tz):
    raw = event.get("damage_anchor") or {}
    at = raw.get("time_utc") or raw.get("at") or raw.get("date")
    return (damage_time(at, tz)[0], raw) if at else (None, raw)


def warning_delta(event, tz, crossing):
    w = event.get("official_warning") or {}
    at = w.get("at") or w.get("time_utc")
    if not at or crossing is None:
        return None
    t, kind = damage_time(at, tz)
    return (t - crossing).total_seconds() / 3600 if kind == "hour" else None


def rs_2024():
    cog = REPO / "data/rs_2024/cog/rs_2024_tpi_slide_20240502.tif"
    event = json.loads((REPO / "events/rs-2024-05.json").read_text())
    tz = "America/Sao_Paulo"
    cube = read_tpi_series(cog)
    times = frame_times_from_bands(cog)
    anchor, raw_anchor = anchor_ts(event, tz)
    reasons = {}
    flat = {}
    # Inventory stub is empty (0 pts) -> cluster-lead vs anchor per brief.
    clusters = cluster_lead_times(cube, times, CRITICAL, anchor, min_cluster_px=4)
    if clusters.empty:
        for k in ("lead_time_median_h", "lead_time_iqr_h", "lead_time_n",
                  "warned_before_pct", "warned_24h_pct", "lead_time_hist"):
            flat[k] = None
            reasons[k] = "No >=4px cluster crossed TPI >= 0.6 before/around the anchor"
    else:
        flat.update(summarize_flat(clusters["lead_hours"]))
        flat["lead_time_hist_bin_h"] = 6
    flat["never_critical_pct"] = None
    reasons["never_critical_pct"] = (
        "Cluster mode has no never-crossing denominator: clusters are defined "
        "by ever crossing Critical; the landslide catalog stub is empty (0 dated points)")
    flat["mode"] = "cluster_lead_vs_damage_anchor"
    flat["n_clusters"] = int(len(clusters))
    flat["threshold_tpi"] = CRITICAL
    crossing = event_first_crossing(cube, times)
    flat["first_critical_time"] = str(crossing) if crossing is not None else None
    flat["event_lead_h"] = (
        (anchor - crossing).total_seconds() / 3600 if crossing is not None else None)
    flat["official_warning_delta_h"] = warning_delta(event, tz, crossing)
    flat["damage_anchor"] = {
        "at": raw_anchor.get("time_utc") or raw_anchor.get("date"),
        "source": raw_anchor.get("source"),
    }
    flat["n_landslide_points"] = 0
    reasons["point_lead_times"] = (
        "data/rs_2024/geojson/landslide_catalog.geojson is an empty stub "
        "(1 feature, 0 coordinates): no dated inventory points available; "
        "fell back to cluster-lead vs damage_anchor")
    out = {"case": "rs_2024", "metrics": flat, "_null_reasons": reasons}
    (REPO / "data/rs_2024/lead_time_metrics.json").write_text(
        json.dumps(out, indent=2, default=str))
    return out


def wa_2025():
    cog = REPO / "data/wa_2025/cog/wa_2025_tpi_slide_20251210.tif"
    event = json.loads((REPO / "events/wa-2025-12.json").read_text())
    tz = "America/Los_Angeles"
    cube = read_tpi_series(cog)
    times = frame_times_from_bands(cog)
    anchor, raw_anchor = anchor_ts(event, tz)
    flat = {k: None for k in (
        "lead_time_median_h", "lead_time_iqr_h", "lead_time_n",
        "warned_before_pct", "warned_24h_pct", "never_critical_pct",
        "lead_time_hist")}
    reasons = {k: ("WGS landslide inventory points are undated and "
                   "data/wa_2025/geojson/landslide_catalog.geojson is an empty "
                   "stub: point-level lead time is undefined")
               for k in flat}
    crossing = event_first_crossing(cube, times)
    flat["mode"] = "event_first_critical_only"
    flat["threshold_tpi"] = CRITICAL
    flat["first_critical_time"] = str(crossing) if crossing is not None else None
    flat["event_lead_h"] = (
        (anchor - crossing).total_seconds() / 3600 if crossing is not None else None)
    if crossing is None:
        reasons["event_lead_h"] = "No pixel crossed TPI >= 0.6 in the series window"
    flat["official_warning_delta_h"] = warning_delta(event, tz, crossing)
    if flat["official_warning_delta_h"] is None:
        reasons["official_warning_delta_h"] = (
            "No hour-precision official warning or no observed Critical crossing")
    flat["damage_anchor"] = {
        "at": raw_anchor.get("time_utc") or raw_anchor.get("date"),
        "source": raw_anchor.get("source"),
    }
    flat["n_landslide_points"] = 0
    out = {"case": "wa_2025", "metrics": flat, "_null_reasons": reasons}
    (REPO / "data/wa_2025/lead_time_metrics.json").write_text(
        json.dumps(out, indent=2, default=str))
    return out


for name, fn in (("rs_2024", rs_2024), ("wa_2025", wa_2025)):
    print(json.dumps(fn(), indent=2, default=str))
