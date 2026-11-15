"""Per-frame critical stats for the replay NOAA-GFS forecast layers.

For every entry in data/<case>/png/_index.json with
forecast_source == "noaa-gfs" (ids tpi_<slide|flood>_gfs_d<j>), adds:
  critical_km2     area of pixels with TPI > 0.6 (strict) in THAT frame's
                   band (d<j>_<hazard> of the entry's cog), per-row cos(lat)
  people_critical  GPWv4 count under those pixels (coverage-fraction
                   reproject onto the native 30 arc-sec GPW grid,
                   data/<case>/cog/<case>_pop_gpw_native.tif)
Both come from tp.bundle._critical_stats called with a single hazard band,
so the definition is the one the series rows and metrics use. A value that
can't be computed is null with a reason in the entry's `_null_reasons`.
Only these fields are touched; key order / indent=2 / ensure_ascii=False kept.

    PYTHONPATH=.:pipeline .venv/bin/python tools/gfs_layer_stats.py [case ...]
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from pathlib import Path

import rasterio

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "pipeline") not in sys.path:
    sys.path.insert(0, str(REPO / "pipeline"))

from tp.bundle import _critical_stats  # noqa: E402

CASES = ("rs_2024", "wa_2025")
ID_RE = re.compile(r"^tpi_(slide|flood)_gfs_d(\d)$")


def layer_stats(entry: dict, data_dir: Path, pop_cog: Path | None) -> dict:
    """{critical_km2, people_critical, _null_reasons?} for one index entry."""
    reasons = {}
    out = {"critical_km2": None, "people_critical": None}
    m = ID_RE.match(entry.get("id", ""))
    cog = data_dir / entry["cog"] if entry.get("cog") else None
    if m is None:
        reasons["critical_km2"] = f"unrecognised GFS layer id {entry.get('id')!r}"
    elif cog is None or not cog.exists():
        reasons["critical_km2"] = f"forecast COG missing: {entry.get('cog')}"
    else:
        hazard, lead = m.group(1), int(m.group(2))
        want = f"d{lead}_{hazard}"
        with rasterio.open(cog) as src:
            descs = list(src.descriptions)
        if want not in descs:
            reasons["critical_km2"] = f"band {want} not in {cog.name}"
        else:
            day = dt.date.fromisoformat(entry["date"])
            s = _critical_stats({hazard: {day: (cog, descs.index(want) + 1)}},
                                pop_cog).get(day.isoformat())
            if s is None:
                reasons["critical_km2"] = "no stats returned"
            else:
                out["critical_km2"] = s["critical_km2"]
                out["people_critical"] = s["people_critical"]
    if out["people_critical"] is None:
        reasons["people_critical"] = (
            reasons.get("critical_km2") if out["critical_km2"] is None
            else "no population COG (GPW)")
    if reasons:
        out["_null_reasons"] = reasons
    return out


def update_case(case: str, data_root: Path) -> int:
    idx_path = data_root / case / "png" / "_index.json"
    idx = json.loads(idx_path.read_text())
    pops = sorted((data_root / case / "cog").glob(f"{case}_pop_gpw_native.tif"))
    if not pops:  # fallback: the ~1 km GEE export (under-counts ~13%)
        pops = sorted((data_root / case / "cog").glob(f"{case}_pop_gpw_*.tif"))
    pop_cog = pops[-1] if pops else None
    n = 0
    for entry in idx["layers"]:
        if entry.get("forecast_source") != "noaa-gfs":
            continue
        for k in ("critical_km2", "people_critical", "_null_reasons"):
            entry.pop(k, None)   # idempotent re-run: fresh values at the end
        entry.update(layer_stats(entry, data_root, pop_cog))
        n += 1
    idx_path.write_text(json.dumps(idx, indent=2, ensure_ascii=False) + "\n")
    return n


def main() -> None:
    data_root = REPO / "data"
    for case in sys.argv[1:] or CASES:
        print(f"{case}: {update_case(case, data_root)} GFS layers updated")


if __name__ == "__main__":
    main()
