"""Validate a replay TPI/score COG against the Mode-1 done-when checks.

    uv run python -m gee.check_series data/<case>/cog/<caso>_tpi_slide_<Ymd>.tif \
        --case rs_2024|wa_2025

Daily series COGs (tpi_slide, tpi_flood, r_series, s_series): band count ==
window days, band names are the window dates (YYYYMMDD), values in [0,1];
TPI series also require the peak on or before the event date. Static score
COGs (t_slide, t_flood — delta L5): single band, values in [0,1]. On success
the input collection ids + derived asset ids (L8) are recorded in the case
blob of data/metrics.json via tp.metrics.merge_metrics. Exits nonzero on
any failure.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

import numpy as np
import rasterio
from tp.config import REPO_ROOT
from tp.metrics import merge_metrics

from . import common, tpi

COLLECTIONS = {
    "imerg": common.COL_IMERG,
    "smap_l4": common.COL_SMAP,
    "nasadem": common.IMG_NASADEM,
}

_STATIC_RE = re.compile(r"_t_(slide|flood)_\d{8}$")


def collections_for(case_id: str) -> dict:
    """Input collection ids + the L8 derived asset ids for metrics."""
    return {**COLLECTIONS,
            **{f"gee_{n}": aid
               for n, aid in tpi.derived_asset_ids(case_id).items()}}


def check(path: Path, case_id: str) -> list[str]:
    """Return the list of failures (empty = pass)."""
    path = Path(path)
    dates = common.event_dates(case_id)
    expected = [d.replace("-", "") for d in dates]
    event_idx = int(common.case_cfg(case_id)["window_before_days"])
    static = bool(_STATIC_RE.search(path.stem))
    tpi = "_tpi_" in path.stem
    failures: list[str] = []
    with rasterio.open(path) as ds:
        names = [ds.descriptions[i] or "" for i in range(ds.count)]
        if static:
            if ds.count != 1:
                failures.append(
                    f"static score image has {ds.count} bands, expected 1")
        else:
            if ds.count != len(dates):
                failures.append(
                    f"band count {ds.count} != window days {len(dates)}")
            for i, want in enumerate(expected[: ds.count]):
                if names[i] != want:
                    failures.append(f"band {i} name {names[i]!r} != {want!r}")
        arr = ds.read().astype(np.float64)
        if ds.nodata is not None:
            arr = np.where(arr == ds.nodata, np.nan, arr)
    finite = np.isfinite(arr)
    if not finite.any():
        failures.append("all pixels are nodata")
        return failures
    lo, hi = float(arr[finite].min()), float(arr[finite].max())
    if lo < 0.0 or hi > 1.0:
        failures.append(f"score out of [0,1]: min {lo:.4f}, max {hi:.4f}")
    if tpi and not static:
        means = np.nanmean(np.where(finite, arr, np.nan), axis=(1, 2))
        peak = int(np.nanargmax(means))
        if peak > event_idx:
            failures.append(
                f"peak TPI at band {peak} ({names[peak] or '?'}) after "
                f"event-day band {event_idx} ({expected[event_idx]})")
    return failures


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("series_cog", type=Path)
    ap.add_argument("--case", required=True, choices=("rs_2024", "wa_2025"))
    ap.add_argument("--metrics-path", type=Path,
                    default=REPO_ROOT / "data" / "metrics.json",
                    help="metrics.json path (default data/metrics.json root)")
    args = ap.parse_args(argv)

    failures = check(args.series_cog, args.case)
    if failures:
        for f in failures:
            print(f"FAIL {f}")
        return 1
    merge_metrics(args.case, collections=collections_for(args.case),
                  path=args.metrics_path)
    print(f"OK {args.series_cog}: values in [0,1], bands checked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
