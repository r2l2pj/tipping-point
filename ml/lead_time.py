"""Lead time: first frame ≥ threshold per cluster vs the anchor timestamp.

The hazard series is scanned frame by frame; each spatially connected cluster
of pixels that ever crosses the threshold gets a first-crossing time, and
``lead_hours = anchor − first_crossing`` (spec §3-§5.3).

The anchor must be an independently sourced, dated first-slide/fatality report —
never the assigned label time, which would be circular (spec §2.2).

    uv run python -m ml.lead_time --hazard data/rs-2024-05/cog/rs_2024_hazard_slide_series_20240502.tif \
        --anchor 2024-05-01T00:00:00Z --threshold 0.12
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from scipy import ndimage

from . import config as cfg

log = logging.getLogger(__name__)


def read_hazard_series(path: str | Path) -> np.ndarray:
    """Multiband uint8 hazard COG → float32 probability cube (T, H, W)."""
    with rasterio.open(path) as src:
        arr = src.read().astype(np.float32)
        nodata = src.nodata if src.nodata is not None else cfg.RASTER_NODATA
    prob = arr / cfg.HAZARD_SCALE
    prob[arr == nodata] = np.nan
    return prob


def first_crossing_frame(prob_series: np.ndarray, threshold: float) -> np.ndarray:
    """First frame index ≥ threshold per pixel; -1 if never."""
    cross = prob_series >= threshold  # NaN comparisons are False
    first = np.where(cross.any(axis=0), cross.argmax(axis=0), -1)
    return first.astype(np.int32)


def cluster_lead_times(
    prob_series: np.ndarray,
    frame_times: pd.DatetimeIndex,
    threshold: float,
    anchor: pd.Timestamp,
    min_cluster_px: int = 4,
) -> pd.DataFrame:
    """Per-cluster first-crossing and lead hours.

    Clusters are 8-connected components of pixels that ever reach the
    threshold; a cluster's time is its earliest crossing pixel.
    """
    first = first_crossing_frame(prob_series, threshold)
    ever = first >= 0
    labels, n = ndimage.label(ever, structure=np.ones((3, 3), dtype=int))
    rows = []
    for cid in range(1, n + 1):
        idx = labels == cid
        n_px = int(idx.sum())
        if n_px < min_cluster_px:
            continue
        t_first = frame_times[first[idx].min()]
        rows.append({
            "cluster_id": cid,
            "n_px": n_px,
            "first_time": t_first,
            "lead_hours": (anchor - t_first).total_seconds() / 3600.0,
        })
    return pd.DataFrame(rows)


def summarize(clusters: pd.DataFrame) -> dict:
    """metrics.json lead-time fields — flat v4 keys per data/SCHEMA.md."""
    if clusters.empty:
        return {"lead_time_mean_h": 0.0, "lead_time_median_h": 0.0,
                "warned_before_pct": 0.0, "n_clusters": 0}
    return {
        "lead_time_mean_h": float(clusters["lead_hours"].mean()),
        "lead_time_median_h": float(clusters["lead_hours"].median()),
        "warned_before_pct": float((clusters["lead_hours"] > 0).mean() * 100),
        "n_clusters": len(clusters),
        "lead_hours_max": float(clusters["lead_hours"].max()),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--hazard", required=True, help="hazard series COG")
    ap.add_argument("--anchor", required=True,
                    help="independent anchor timestamp (ISO-8601, e.g. 2024-05-01T00:00:00Z)")
    ap.add_argument("--threshold", type=float, default=cfg.HAZARD_THRESHOLD)
    ap.add_argument("--frame-times", nargs="+", default=cfg.FRAME_TIMES_UTC)
    ap.add_argument("--min-cluster-px", type=int, default=4)
    ap.add_argument("--event", default=cfg.EVENT_ID)
    ap.add_argument("--metrics-out", default=None)
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    prob = read_hazard_series(args.hazard)
    times = pd.DatetimeIndex(pd.to_datetime(args.frame_times))
    if len(times) != prob.shape[0]:
        raise ValueError(f"{len(times)} frame times for {prob.shape[0]} bands")
    anchor = pd.Timestamp(args.anchor)

    clusters = cluster_lead_times(prob, times, args.threshold, anchor,
                                  min_cluster_px=args.min_cluster_px)
    summary = summarize(clusters)
    log.info(json.dumps(summary, indent=2))

    metrics_path = Path(args.metrics_out) if args.metrics_out else cfg.DATA_DIR / args.event / "metrics.json"
    metrics = {"event": args.event}
    if metrics_path.exists():
        metrics = json.loads(metrics_path.read_text())
    metrics.update({k: v for k, v in summary.items()
                    if k in ("lead_time_mean_h", "lead_time_median_h",
                             "warned_before_pct")})
    metrics.setdefault("landslide_ml", {})["lead_hours"] = summary
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.write_text(json.dumps(metrics, indent=2, default=str) + "\n")
    log.info("wrote %s", metrics_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
