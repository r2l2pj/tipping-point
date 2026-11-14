"""NISAR search/download via earthaccess + Harmony/GDAL subsetting.

NISAR GCOV and GUNW ship as HDF5 (~6-7 GB / ~2.4 GB per frame) — NOT GeoTIFF.
Do NOT download full frames: use Harmony (Earthdata) to extract one variable
clipped to the AOI as COG, or GDAL /vsicurl/ streaming with the NETCDF: prefix
(GDAL < 3.13). HyP3 does not process NISAR on demand.

Verified CMR short names (2026-11): NISAR_L2_GCOV_BETA_V1, NISAR_L2_GUNW_BETA_V1,
NISAR_L2_*_PROVISIONAL_V1, NISAR_WATERMASK, NISAR_DEM.

HDF5 paths (spec D-102274/D-102272):
  GCOV  /science/LSAR/GCOV/grids/frequencyA/HHHH   float32 linear gamma0 power
        /science/LSAR/GCOV/grids/frequencyA/HVHV
        /science/LSAR/GCOV/grids/frequencyA/{mask,numberOfLooks}
  GUNW  /science/LSAR/GUNW/grids/frequencyA/wrappedInterferogram/HH/coherenceMagnitude  (20 m)
        /science/LSAR/GUNW/grids/frequencyA/unwrappedInterferogram/HH/coherenceMagnitude (80 m)

Auth: NASA Earthdata Login. Prefer ~/.netrc (machine urs.earthdata.nasa.gov);
earthaccess.login() handles auth. Never hardcode credentials.
"""
from __future__ import annotations

from pathlib import Path

import earthaccess


def search(event: dict, collection: str = "NISAR_L2_GCOV_BETA_V1") -> list:
    """Search NISAR granules intersecting the event AOI and window."""
    w = event["window"]
    earthaccess.login()
    return earthaccess.search_data(
        short_name=collection,
        bounding_box=tuple(event["aoi"]["bbox"]),
        temporal=(w["start"], w["end"]),
    )


def pick_pairs(granules: list, reference_date: str) -> dict:
    """Choose nearest pre/post granules on the same track+frame.

    TODO: group by track/frame attributes, pick last-before and first-after
    reference_date. Relative change on the same frame tolerates BETA
    calibration error. See events/*.json nisar.frames for a known-good pair.
    """
    raise NotImplementedError


def harmony_subset(granule, variables: list[str], bbox: tuple, out_path: Path) -> Path:
    """Submit a Harmony job: extract `variables` clipped to bbox as COG.

    TODO: harmony-py request syntax (unverified) — do the first job in the
    Earthdata Search UI and copy the request. Always select variables
    explicitly; no selection means every grid is extracted (slow).
    """
    raise NotImplementedError


def gdal_subset_cmd(h5_url: str, h5_path: str, bbox: tuple, out_path: Path) -> str:
    """Return the GDAL /vsicurl/ fallback command for one HDF5 variable.

    Requires ~/.netrc and GDAL_HTTP_NETRC=YES. GDAL < 3.13 needs the NETCDF:
    prefix (HDF5 driver can't read NISAR's CF georeferencing).
    """
    w, s, e, n = bbox
    src = f'NETCDF:"/vsicurl/{h5_url}":{h5_path}'
    return f"gdalwarp -te {w} {s} {e} {n} -of COG {src} {out_path}"
