"""Science review B3-6: SMAP look-ahead in the GFS replay.

The issued-GFS replay frames hold soil wetness S at the band dated I-1 (I =
issue day) with no latency, but live SMAP L4 is 2-5 days old when a 00Z run is
used. Since TPI = (R * S * T)^(1/3) (uniform weights), a frame with S taken
from an older band is exactly

    TPI_lag = TPI * (S_{I-1-lag'} / S_{I-1})^(1/3)

(R and T unchanged; S bilinear onto the 90 m TPI grid, as in the case
pipeline). This re-scores the L37 onset-anchored warned% (warned_before) with
S available at issue time = band I-3, I-4, I-5, as sensitivity rows.

    PYTHONPATH=.:pipeline .venv/bin/python tools/smap_latency_sensitivity.py [--write]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import Resampling, reproject

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
import warned_before as wb

REFS = {"rs_2024": ("20240502", "slide", "slide_warned"),
        "wa_2025": ("20251210", "flood", "flood_warned")}
S_LAGS = (3, 4, 5)          # S from band I-3 / I-4 / I-5 instead of I-1


def _s_on_grid(s_path: Path, band: int, like) -> np.ndarray:
    with rasterio.open(s_path) as s:
        a = s.read(band).astype(np.float64)
        if s.nodata is not None and np.isfinite(s.nodata):
            a = np.where(a == s.nodata, np.nan, a)
        out = np.full(like.shape, np.nan)
        reproject(a, out, src_transform=s.transform, src_crs=s.crs, src_nodata=np.nan,
                  dst_transform=like.transform, dst_crs=like.crs, dst_nodata=np.nan,
                  resampling=Resampling.bilinear)
    return out


def lagged_frames(cog: Path, case: str, ref: str, lag: int, out_dir: Path) -> list[str]:
    """Write <case>_tpi_gfs_<I>.tif copies with S from band I-lag; returns skipped issues."""
    s_path = cog / f"{case}_s_series_{ref}.tif"
    sdesc = list(rasterio.open(s_path).descriptions)
    skipped = []
    for p in sorted(cog.glob(f"{case}_tpi_gfs_*.tif")):
        issue = wb._day(p.stem.rsplit("_", 1)[1])
        d_now = f"{issue - dt.timedelta(days=1):%Y%m%d}"
        d_lag = f"{issue - dt.timedelta(days=lag):%Y%m%d}"
        with rasterio.open(p) as src:
            prof = dict(src.profile)
            names = list(src.descriptions)
            bands = [src.read(i + 1).astype(np.float64) for i in range(src.count)]
            if d_now not in sdesc or d_lag not in sdesc:
                skipped.append(issue.isoformat())
                continue
            s_now = _s_on_grid(s_path, sdesc.index(d_now) + 1, src)
            s_lag = _s_on_grid(s_path, sdesc.index(d_lag) + 1, src)
        with np.errstate(invalid="ignore", divide="ignore"):
            ratio = np.where(s_now > 0, np.clip(s_lag, 0, None) / s_now, 1.0)
        with rasterio.open(out_dir / p.name, "w", **prof) as dst:
            for i, (n, a) in enumerate(zip(names, bands, strict=True), 1):
                v = np.where(np.isfinite(a), np.clip(a, 0, None) ** 3 * ratio, np.nan)
                dst.write(np.clip(np.cbrt(v), 0, 1).astype(np.float32), i)
                dst.set_band_description(i, n)
    return skipped


def run(data_root: Path) -> dict:
    out = {}
    for case, (ref, hazard, key) in REFS.items():
        cog = data_root / case / "cog"
        anchors = wb.ANCHORS[case]
        if case == "wa_2025":
            series = cog / "wa_2025_tpi_flood_20251210.tif"
            with rasterio.open(series) as g:
                idx, w = wb.flood_pixels(cog / "wa_2025_flood_nisar_20251210.tif", g)
        else:
            series = cog / "rs_2024_tpi_slide_20240502.tif"
            with rasterio.open(series) as g:
                idx, w = wb.inventory_points(data_root / case / "geojson" / "landslide_catalog.geojson", g)
        rows = []
        for lag in (1, *S_LAGS):
            with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR_LA")) as td:
                td = Path(td)
                skipped = [] if lag == 1 else lagged_frames(cog, case, ref, lag, td)
                src = cog if lag == 1 else td
                for a in anchors[:1]:          # onset anchor only
                    r = wb.evaluate(src, case, hazard, series, idx, w, a["utc"])
                    bl = wb.by_lead(r, a["utc"])
                    rows.append({
                        "s_band": f"I-{lag}", "s_age_days_at_issue": lag,
                        "anchor_basis": a["basis"], "skipped_issues": skipped,
                        **{f"gfs_{h}h_pct": (bl[str(h)]["gfs"] or {}).get("alto_or_critico_pct")
                           for h in wb.LEADS_H},
                        **{f"gfs_{h}h_critical_pct": (bl[str(h)]["gfs"] or {}).get("critico_pct")
                           for h in wb.LEADS_H},
                    })
        out[case] = {"key": key, "rows": rows}
    return out


def merge(data_root: Path, res: dict) -> None:
    path = data_root / "metrics.json"
    m = json.loads(path.read_text())
    for case, r in res.items():
        blob = m["cases"][case]
        blob[f"{r['key']}_smap_latency"] = {
            "definition": ("onset-anchored warned% of the issued-GFS frames re-scored with soil "
                           "wetness S from the band dated I-k (I = issue day) instead of I-1: "
                           "TPI_k = TPI * (S_{I-k}/S_{I-1})^(1/3), R and T unchanged. Live SMAP "
                           "L4 is 2-5 days old at issue time, so k = 3..5 is the as-available "
                           "range; k = 1 is the published replay (no latency)."),
            "rows": r["rows"],
        }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(m, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-root", type=Path, default=REPO / "data")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)
    res = run(args.data_root)
    for case, r in res.items():
        for row in r["rows"]:
            print(case, row)
    if args.write:
        merge(args.data_root, res)
        print("merged into", args.data_root / "metrics.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
