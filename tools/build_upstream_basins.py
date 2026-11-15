"""Upstream-basin topology for the replay cases (L35-U1).

Uses HydroBASINS v1c level 10 (HydroSHEDS; declared non-NASA reference
derived from SRTM; level 9 fallback if level 10 is unavailable). Downloads
hybas_<region>_lev01-12_v1c.zip from data.hydrosheds.org into
intermediate/upstream/_hydrobasins/ (zip kept so other levels stay
reachable; if downloads fail, export WWF/HydroSHEDS/v1/Basins/hybas_10 for
the AOI from GEE — gee/common.py init() with GEE_PROJECT/GCP_SA_KEY from
the repo .env — and place the shp there instead).

Per case (AOI = bbox in data/events.json), writes under
intermediate/upstream/<case>/:
  - basins.geojson  every basin in the union of upstream sets
                    (HYBAS_ID, NEXT_DOWN, SUB_AREA, UP_AREA)
  - upstream.json   {hybas_id: [upstream ids...]} for the AOI basins,
                    plus 'union_bbox' [w,s,e,n] and 'upstream_area_km2'
  - basin_id_90m.tif int64 HYBAS_ID per cell on EXACTLY the grid of
                    data/<case>/cog/<case>_tpi_flood_<ref>.tif (same
                    transform/shape), 0 where no basin (ocean)

    PYTHONPATH=.:pipeline .venv/bin/python tools/build_upstream_basins.py \
        --case rs_2024 [--case wa_2025]
"""
from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

import geopandas as gpd
import requests

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "pipeline"))
from tp.config import case_id, event_id
from tp.upstream import ATTRS, build_case

HYDRO_DIR = REPO / "intermediate" / "upstream" / "_hydrobasins"
DL_URL = ("https://data.hydrosheds.org/file/hydrobasins/standard/"
          "hybas_{region}_lev01-12_v1c.zip")
REGION = {"rs_2024": "sa", "wa_2025": "na"}
LEVELS = (10, 9)
SHP_EXTS = (".shp", ".shx", ".dbf", ".prj", ".sbn", ".sbx", ".shp.xml")


def basin_shp(region: str, levels=LEVELS) -> Path:
    """Path to the best available level's shapefile for a continent."""
    for lev in levels:
        shp = HYDRO_DIR / f"hybas_{region}_lev{lev:02d}_v1c.shp"
        if shp.exists():
            return shp
    zip_path = HYDRO_DIR / f"hybas_{region}_lev01-12_v1c.zip"
    if not zip_path.exists():
        HYDRO_DIR.mkdir(parents=True, exist_ok=True)
        url = DL_URL.format(region=region)
        print(f"downloading {url}", flush=True)
        with requests.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            with zip_path.open("wb") as fh:
                for chunk in r.iter_content(chunk_size=1 << 22):
                    fh.write(chunk)
    with zipfile.ZipFile(zip_path) as z:
        names = set(z.namelist())
        for lev in levels:
            stem = f"hybas_{region}_lev{lev:02d}_v1c"
            members = [f"{stem}{ext}" for ext in SHP_EXTS]
            if f"{stem}.shp" in names:
                z.extractall(HYDRO_DIR, [m for m in members if m in names])
                return HYDRO_DIR / f"{stem}.shp"
    raise FileNotFoundError(
        f"no hybas_{region} level {sorted(levels)} in {zip_path}")


def run_case(case: str, out_root: Path, levels=LEVELS) -> dict:
    cid = case_id(case)
    if cid not in REGION:
        raise KeyError(f"no HydroBASINS region mapped for case {cid!r}; "
                       f"known: {sorted(REGION)}")
    events = json.loads((REPO / "data" / "events.json").read_text())
    ev = next(e for e in events["events"] if e["id"] == event_id(cid))
    ref = ev["reference_date"].replace("-", "")
    like = REPO / "data" / cid / "cog" / f"{cid}_tpi_flood_{ref}.tif"
    gdf = gpd.read_file(basin_shp(REGION[cid], levels), columns=list(ATTRS))
    stats = build_case(gdf, ev["bbox"], like, out_root / cid)
    print(f"{cid}: {stats}", flush=True)
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--case", action="append", required=True,
                    help="case id (rs_2024, wa_2025); repeatable")
    ap.add_argument("--out-root", type=Path,
                    default=REPO / "intermediate" / "upstream")
    args = ap.parse_args(argv)
    for case in args.case:
        run_case(case, args.out_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
