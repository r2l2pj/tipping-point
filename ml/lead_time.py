"""Lead time: first frame ≥ threshold vs an independent anchor timestamp.

Two input series are supported:

* uint8 hazard COG (0-100 scaled probabilities, nodata 255) — the original
  input; the hazard cube is scanned frame by frame and each 8-connected
  cluster of pixels that ever crosses the threshold gets a first-crossing
  time, with ``lead_hours = anchor − first_crossing`` (spec §3-§5.3).
* float TPI series COG (0-1, bands named YYYYMMDD, NaN nodata) — dtype
  auto-detected or forced with ``--tpi``; the first frame with TPI ≥ 0.6 is
  the crossing. With ``--labels`` (CSV of labeled landslide points, the
  ml/labels.py GEE-table flow: ``lon,lat[,t_utc]`` columns) each labeled
  point is classed ``pos`` (crossed strictly before the anchor),
  ``neg`` (never crossed or only at/after it) or ``null`` (nodata pixel —
  cannot be evaluated) and gets its anticipation in hours vs the anchor.

The anchor must be an independently sourced, dated first-slide/fatality
report — never the assigned label time, which would be circular (spec §2.2).

    uv run python -m ml.lead_time --hazard data/rs-2024-05/cog/rs_2024_hazard_slide_series_20240502.tif \
        --anchor 2024-05-01T00:00:00Z --threshold 0.12
    uv run python -m ml.lead_time --hazard data/rs-2024-05/cog/rs_2024_tpi_slide_20240502.tif \
        --labels data/raw/rs2024_slides.csv --anchor 2024-05-02T00:00:00Z
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from rasterio.transform import rowcol
from rasterio.warp import transform as warp_transform
from scipy import ndimage
from tp.config import case_id
from tp.metrics import merge_metrics

from . import config as cfg

log = logging.getLogger(__name__)

TPI_THRESHOLD = 0.6  # Alto cut — config.yaml thresholds.tpi_classes
_FLAT_KEYS = ("lead_time_mean_h", "lead_time_median_h", "warned_before_pct")


def read_hazard_series(path: str | Path) -> np.ndarray:
    """Multiband uint8 hazard COG → float32 probability cube (T, H, W)."""
    with rasterio.open(path) as src:
        arr = src.read().astype(np.float32)
        nodata = src.nodata if src.nodata is not None else cfg.RASTER_NODATA
    prob = arr / cfg.HAZARD_SCALE
    prob[arr == nodata] = np.nan
    return prob


def read_tpi_series(path: str | Path) -> np.ndarray:
    """Multiband float TPI COG → float32 cube (T, H, W); nodata → NaN."""
    with rasterio.open(path) as src:
        arr = src.read().astype(np.float32)
        nodata = src.nodata
    if nodata is not None:
        arr[arr == nodata] = np.nan
    return arr


def is_float_series(path: str | Path) -> bool:
    """True when the COG stores floats (TPI series) rather than uint8 hazard."""
    with rasterio.open(path) as src:
        return np.issubdtype(np.dtype(src.dtypes[0]), np.floating)


def read_series(path: str | Path, tpi: bool | None = None) -> np.ndarray:
    """Hazard or TPI series → probability cube; dtype-detected unless ``tpi``."""
    if tpi is None:
        tpi = is_float_series(path)
    return read_tpi_series(path) if tpi else read_hazard_series(path)


def frame_times_from_bands(path: str | Path) -> pd.DatetimeIndex | None:
    """Band descriptions 'YYYYMMDD' → DatetimeIndex; None when not date-named."""
    with rasterio.open(path) as src:
        desc = list(src.descriptions)
    if not desc or not all(d and d.strip() for d in desc):
        return None
    try:
        return pd.DatetimeIndex(pd.to_datetime(
            [d.strip() for d in desc], format="%Y%m%d", utc=True))
    except ValueError:
        return None


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


def point_lead_times(
    prob_series: np.ndarray,
    frame_times: pd.DatetimeIndex,
    points: pd.DataFrame,
    transform,
    crs,
    threshold: float,
    anchor: pd.Timestamp,
) -> pd.DataFrame:
    """Per-label anticipation: class pos/neg/null + hours vs ``anchor``.

    ``points`` is the labeled-landslide table (ml/labels.py GEE-table flow,
    ``lon``/``lat`` columns, EPSG:4326). Each point samples its pixel's first
    crossing ≥ ``threshold``: ``pos`` = crossed strictly before the anchor
    (``anticipation_h`` > 0), ``neg`` = never crossed or only at/after it,
    ``null`` = nodata pixel or outside the raster (cannot be evaluated).
    """
    first = first_crossing_frame(prob_series, threshold)
    h, w = first.shape
    xs, ys = warp_transform("EPSG:4326", crs,
                            list(points["lon"]), list(points["lat"]))
    rows = []
    for i, (x, y) in enumerate(zip(xs, ys, strict=True)):
        row = {"point": i, "klass": "null",
               "first_time": pd.NaT, "anticipation_h": np.nan}
        r, c = rowcol(transform, x, y)
        if 0 <= r < h and 0 <= c < w and not np.isnan(prob_series[:, r, c]).all():
            f = first[r, c]
            if f >= 0:
                t_first = frame_times[f]
                lead = (anchor - t_first).total_seconds() / 3600.0
                row.update(klass="pos" if lead > 0 else "neg",
                           first_time=t_first, anticipation_h=lead)
            else:
                row["klass"] = "neg"
        rows.append(row)
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


def summarize_points(points: pd.DataFrame) -> dict:
    """metrics.json lead-time fields for the label-point flow.

    mean/median over the ``pos`` anticipations; ``warned_before_pct`` is the
    share of evaluable (non-null) labeled points warned before the anchor.
    """
    counts = points["klass"].value_counts()
    n_pos, n_neg = int(counts.get("pos", 0)), int(counts.get("neg", 0))
    out = {"n_label_points": len(points), "n_pos": n_pos, "n_neg": n_neg,
           "n_null": int(counts.get("null", 0))}
    if n_pos == 0:
        return {"lead_time_mean_h": 0.0, "lead_time_median_h": 0.0,
                "warned_before_pct": 0.0, **out}
    pos = points.loc[points["klass"] == "pos", "anticipation_h"]
    return {
        "lead_time_mean_h": float(pos.mean()),
        "lead_time_median_h": float(pos.median()),
        "warned_before_pct": float(n_pos / max(n_pos + n_neg, 1) * 100),
        **out,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--hazard", required=True,
                    help="uint8 hazard series COG, or float TPI series COG (--tpi)")
    ap.add_argument("--tpi", action="store_true",
                    help="treat --hazard as a float TPI series (0-1; default ≥0.6)")
    ap.add_argument("--labels", default=None,
                    help="CSV of labeled landslide points (lon,lat) — point flow")
    ap.add_argument("--anchor", required=True,
                    help="independent anchor timestamp (ISO-8601, e.g. 2024-05-01T00:00:00Z)")
    ap.add_argument("--threshold", type=float, default=None)
    ap.add_argument("--frame-times", nargs="+", default=None,
                    help="one ISO-8601 time per band (default: band names, "
                         "else config FRAME_TIMES_UTC)")
    ap.add_argument("--min-cluster-px", type=int, default=4)
    ap.add_argument("--event", default=cfg.EVENT_ID)
    ap.add_argument("--metrics-out", default=None)
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    tpi = args.tpi or is_float_series(args.hazard)
    prob = read_series(args.hazard, tpi=tpi)
    threshold = (args.threshold if args.threshold is not None
                 else TPI_THRESHOLD if tpi else cfg.HAZARD_THRESHOLD)

    if args.frame_times:
        times = pd.DatetimeIndex(pd.to_datetime(args.frame_times))
    else:
        times = frame_times_from_bands(args.hazard)
        if times is None:
            times = pd.DatetimeIndex(pd.to_datetime(cfg.FRAME_TIMES_UTC))
    if len(times) != prob.shape[0]:
        raise ValueError(f"{len(times)} frame times for {prob.shape[0]} bands")
    anchor = pd.Timestamp(args.anchor)

    detail: dict = {"tpi_input": bool(tpi), "threshold": threshold}
    if args.labels:
        points = pd.read_csv(args.labels)
        if not {"lon", "lat"} <= set(points.columns):
            raise ValueError(f"{args.labels} needs lon,lat columns "
                             f"(got {list(points.columns)})")
        with rasterio.open(args.hazard) as src:
            table = point_lead_times(prob, times, points, src.transform, src.crs,
                                     threshold, anchor)
        summary = summarize_points(table)
        detail["mode"] = "label_points"
        log.info("label points: %s", table["klass"].value_counts().to_dict())
    else:
        clusters = cluster_lead_times(prob, times, threshold, anchor,
                                      min_cluster_px=args.min_cluster_px)
        summary = summarize(clusters)
        detail["mode"] = "clusters"
    detail.update(summary)
    log.info(json.dumps(summary, indent=2, default=str))

    metrics_path = Path(args.metrics_out) if args.metrics_out else cfg.DATA_DIR / "metrics.json"
    flat = {k: summary[k] for k in _FLAT_KEYS}
    # Detailed sub-results nest under landslide_ml in the case blob of the
    # root data/metrics.json (L4; same pattern as ml/evaluate.py).
    blob = {}
    if metrics_path.exists():
        blob = json.loads(metrics_path.read_text()).get("cases", {}).get(
            case_id(args.event), {})
    flat["landslide_ml"] = {**blob.get("landslide_ml", {}), "lead_hours": detail}
    merge_metrics(args.event, flat=flat, path=metrics_path)
    log.info("wrote %s", metrics_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
