"""Add GFS forecast daily rain to the case rain/soil chart series (L28b).

For each calendar day X in data/<case>/series/<case>_rain_soil_<tag>.json and
data/<case>/series/imerg_smap_daily.json (the file the app charts):
  gfs_mm        AOI-mean GFS precip for day X from the run issued X-1 00Z
                (band d2 of data/<case>/forecast_rain/<case>_gfs_<X-1>.tif)
  gfs_mm_lead0  same day from the run issued X 00Z (band d1)
  gfs_issued    X-1 (ISO date) when gfs_mm is present
  gfs_rain3_mm  AOI-mean GFS 3-day total over days X-2..X from the run issued
                X-2 00Z (bands d1+d2+d3) — the like-for-like companion of
                imerg_mm / rain3_mm, which are OBSERVED 3-day totals ending X
  gfs_rain3_issued  X-2 (ISO date) when gfs_rain3_mm is present
Band d<j> of an issue-D COG = forecast precip over calendar day D+j-1 (UTC),
see tools/regen_gfs_replay.py. Missing COG/band -> null.

    PYTHONPATH=.:pipeline .venv/bin/python tools/gfs_series.py
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "pipeline"))
from tp.gfs_series import GFS_SOURCE, gfs_fields


def add_gfs(series_path: Path) -> int:
    doc = json.loads(series_path.read_text())
    case = series_path.parent.parent.name   # data/<case>/series/
    fr_dir = series_path.parent.parent / "forecast_rain"
    n = 0
    for row in doc["days"]:
        row.update(gfs_fields(fr_dir, case, dt.date.fromisoformat(row["date"])))
        n += row["gfs_mm"] is not None
    doc["gfs_source"] = GFS_SOURCE
    series_path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    return n


def main() -> int:
    for case in ("rs_2024", "wa_2025"):
        sdir = REPO / "data" / case / "series"
        for path in sorted([*sdir.glob(f"{case}_rain_soil_*.json"),
                            *sdir.glob("imerg_smap_daily.json")]):
            print(f"{path.relative_to(REPO)}: gfs_mm on {add_gfs(path)} days")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
