"""L28: regenerate the replay GFS forecast-rain COGs from NOAA AWS GRIB.

GEE's NOAA/GFS0P25 registration is one row (0.25 deg) south of the NCEP
grid, so the replay COGs are rebuilt from the authoritative source: the
public bucket noaa-gfs-bdp-pds, APCP 6-h bucket messages read by GDAL
(``live.fetch.fetch_gfs_daily``). Same semantics as before: 00Z run issued
on D, band d<k> = forecast mm over calendar day D+k-1 = [D+k-1, D+k)
(fh 6..24, 30..48, 54..72, 78..96; 4 bands — L28b needs day D+3 for the
'D+3' frame's 3-day window ending D+3).

Verification (independent of fetch_gfs_daily's georeferencing): for >= 5
cells per COG, re-read the same bucket messages with rasterio and sample
them with GDAL's own ``index(lon, lat)``, sum the buckets, and require the
COG value at (lon, lat) to match exactly (float32).

    PYTHONPATH=.:pipeline .venv/bin/python tools/regen_gfs_replay.py \
        [--case rs_2024|wa_2025] [--cells 6]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

from live import fetch as lf

REPO = Path(__file__).resolve().parents[1]
N_DAYS = 4
SOURCE = "NOAA GFS via AWS noaa-gfs-bdp-pds (GRIB2 APCP, GDAL)"
WINDOWS = {
    "rs_2024": (dt.date(2024, 4, 22), dt.date(2024, 5, 7)),
    "wa_2025": (dt.date(2025, 11, 30), dt.date(2025, 12, 20)),
    "rs_uruguai_2026": (dt.date(2026, 9, 20), dt.date(2026, 11, 15)),
}


def case_aoi(case: str) -> tuple[float, float, float, float]:
    import yaml

    cfg = yaml.safe_load((REPO / "config.yaml").read_text())
    return tuple(cfg["cases"][case]["aoi"])


def day_fhs(j: int) -> list[int]:
    """00Z-run forecast hours whose trailing-6h buckets cover calendar day
    D+j-1 (band d<j>): buckets ending D+j-1 06,12,18Z and D+j 00Z."""
    return [24 * (j - 1) + h for h in (6, 12, 18, 24)]


def fetch_days(aoi, issue: dt.date):
    """(cube[N_DAYS, lat_asc, lon], lats_asc, lons) from the D 00Z run.

    Selects forecast hours itself (day_fhs) — independent of
    fetch_gfs_daily's day labels — and decodes with live.fetch's GDAL path.
    """
    w, s, e, n = aoi
    if not lf._gfs_head_ok(f"{lf._GFS_BASE}/{lf._gfs_key(issue, 0, day_fhs(N_DAYS)[-1])}.idx"):
        raise RuntimeError(f"{issue} 00Z run lacks f{day_fhs(N_DAYS)[-1]:03d}")
    slabs, mlat, mlon, lats, lons = [], None, None, None, None
    for j in range(1, N_DAYS + 1):
        acc = None
        for fh in day_fhs(j):
            got = lf._gfs_apcp_grid(issue, 0, fh)
            if got is None:
                raise RuntimeError(f"{issue} 00Z f{fh:03d}: no 6h APCP bucket")
            arr, la, lo = got
            if mlat is None:
                mlat = (la >= s - 1e-6) & (la <= n + 1e-6)
                mlon = (lo >= w - 1e-6) & (lo <= e + 1e-6)
                lats, lons = la[mlat], lo[mlon]
            sub = arr[np.ix_(mlat, mlon)]
            acc = sub if acc is None else acc + sub
        slabs.append(acc)
    return np.stack(slabs), lats, lons


def write_cog(path: Path, cube, lats, lons, label: str) -> None:
    lats = np.asarray(lats, dtype=np.float64)   # ascending
    lons = np.asarray(lons, dtype=np.float64)
    arr = np.asarray(cube, dtype=np.float32)[:, ::-1, :]  # north-up
    transform = from_origin(lons[0] - 0.125, lats[-1] + 0.125, 0.25, 0.25)
    profile = {"driver": "COG", "dtype": "float32", "count": arr.shape[0],
               "height": arr.shape[1], "width": arr.shape[2], "crs": "EPSG:4326",
               "transform": transform, "nodata": np.nan}
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(arr)
        for i in range(arr.shape[0]):
            dst.set_band_description(i + 1, f"d{i + 1}")
        dst.update_tags(source=SOURCE, label=label)


def bucket_messages(cycle: dt.datetime, fhs: list[int]) -> list[bytes]:
    out = []
    for fh in fhs:
        base = f"{lf._GFS_BASE}/{lf._gfs_key(cycle.date(), cycle.hour, fh)}"
        rng = lf._gfs_apcp_range(lf._gfs_get_text(f"{base}.idx"), fh)
        out.append(lf._gfs_get_range(base, *rng))
    return out


def verify(path: Path, cycle: dt.datetime, issue: dt.date, n_cells: int,
           rng: np.random.Generator) -> list[dict]:
    """COG(lon,lat) == sum of GDAL-sampled GRIB buckets at (lon,lat)."""
    checks = []
    with rasterio.open(path) as cog:
        cube = cog.read()
        rows = rng.choice(cog.height, size=n_cells)
        cols = rng.choice(cog.width, size=n_cells)
        pts = [cog.xy(int(r), int(c)) for r, c in zip(rows, cols)]
        for k in range(1, N_DAYS + 1):
            fhs = day_fhs(k)
            sums = np.zeros(len(pts))
            for raw in bucket_messages(cycle, fhs):
                with rasterio.MemoryFile(raw) as mem, mem.open() as g:
                    band = g.read(1)
                    for i, (x, y) in enumerate(pts):
                        r, c = g.index(x, y)
                        sums[i] += band[r, c]
            for i, (x, y) in enumerate(pts):
                r, c = cog.index(x, y)
                got = float(cube[k - 1, r, c])
                ok = np.float32(got) == np.float32(sums[i])
                checks.append({"band": f"d{k}", "lon": x, "lat": y,
                               "cog": got, "grib": float(sums[i]), "ok": bool(ok)})
    return checks


def regen_case(case: str, n_cells: int, verify_only: bool = False,
               start: dt.date | None = None, end: dt.date | None = None) -> dict:
    aoi = case_aoi(case)
    lo, hi = WINDOWS[case]
    lo, hi = max(lo, start or lo), min(hi, end or hi)
    out_dir = REPO / "data" / case / "forecast_rain"
    rng = np.random.default_rng(28)
    report = {}
    day = lo
    while day <= hi:
        cycle = dt.datetime.combine(day, dt.time(0))
        issued_iso = f"{day.isoformat()}T00:00:00Z"
        label = f"forecast: NOAA GFS (issued {day.isoformat()})"
        path = out_dir / f"{case}_gfs_{day:%Y%m%d}.tif"
        if not verify_only:
            cube, lats, lons = fetch_days(aoi, day)
            write_cog(path, cube, lats, lons, label)
        checks = verify(path, cycle, day, n_cells, rng)
        bad = [c for c in checks if not c["ok"]]
        side = path.with_suffix(".json")
        meta = json.loads(side.read_text()) if side.exists() else {}
        meta.update({
            "case": case, "issue_date": day.isoformat(),
            "creation_time": issued_iso, "label": label, "source": SOURCE,
            "collection": "noaa-gfs-bdp-pds gfs.tYYz.pgrb2.0p25.fFFF",
            "band": "APCP surface (N-6..N hour acc fcst)",
            "grid": "native NCEP GFS 0.25 deg cell centres (GDAL geotransform), no resampling",
            "cog": f"data/{case}/forecast_rain/{path.name}",
            "bands": [f"d{k}" for k in range(1, N_DAYS + 1)],
            "days": {f"d{k}": [(day + dt.timedelta(days=k - 1)).isoformat(),
                               (day + dt.timedelta(days=k)).isoformat()]
                     for k in range(1, N_DAYS + 1)},
            "alignment": "d<k> = forecast precip over calendar day D+k-1 (UTC)",
            "verified_cells": len(checks), "verified_mismatches": len(bad),
        })
        for k in ("scale_m",):
            meta.pop(k, None)
        side.write_text(json.dumps(meta, indent=2) + "\n")
        report[day.isoformat()] = {"cells": len(checks), "mismatches": len(bad),
                                   "max_abs_diff": max(abs(c["cog"] - c["grib"])
                                                       for c in checks)}
        print(f"{case} {day}: {path.name} "
              f"checks={len(checks)} bad={len(bad)}", flush=True)
        if bad:
            raise RuntimeError(f"{case} {day}: verification failed {bad[:3]}")
        day += dt.timedelta(days=1)
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--case", choices=sorted(WINDOWS))
    ap.add_argument("--cells", type=int, default=6)
    ap.add_argument("--verify-only", action="store_true",
                    help="re-check existing COGs against GRIB, write nothing but the report")
    ap.add_argument("--start", type=dt.date.fromisoformat, help="first issue day (default: window start)")
    ap.add_argument("--end", type=dt.date.fromisoformat, help="last issue day (default: window end)")
    args = ap.parse_args(argv)
    full = {}
    for case in WINDOWS:
        if args.case and case != args.case:
            continue
        full[case] = regen_case(case, args.cells, args.verify_only, args.start, args.end)
    import fcntl

    out = REPO / "data" / "gfs_replay_verification.json"
    lock = open(out.with_suffix(".lock"), "w")   # noqa: SIM115 - held to the end
    fcntl.flock(lock, fcntl.LOCK_EX)
    prev = json.loads(out.read_text()) if out.exists() else {}
    for case, days in full.items():   # per-day merge: date-range runs can run in parallel
        prev.setdefault(case, {}).update(days)
        prev[case] = dict(sorted(prev[case].items()))
    out.write_text(json.dumps(prev, indent=2) + "\n")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
