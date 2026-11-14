"""NISAR search/download via ASF (asf_search) + HDF5 -> GeoTIFF/COG subsetting.

NISAR GCOV and GUNW ship as HDF5 — NOT GeoTIFF. Extract the needed band/layer,
subset to the AOI, write COG. See NISAR Data User Guide: nisar-docs.asf.alaska.edu

Auth: NASA Earthdata Login. Prefer ~/.netrc (machine urs.earthdata.nasa.gov);
EDL_USER/EDL_PASS env vars are a fallback. Never hardcode credentials.
"""
from __future__ import annotations

import os
from pathlib import Path

import asf_search as asf


def search(event: dict, product: str = "GCOV") -> list:
    """Search NISAR products intersecting the event AOI and window."""
    w = event["window"]
    results = asf.search(
        dataset="NISAR",
        processingLevel=product,
        intersectsWith=event["aoi"]["wkt"],
        start=w["start"],
        end=w["end"],
    )
    return sorted(results, key=lambda r: r.properties["startTime"])


def pick_pairs(results: list, reference_date: str) -> dict:
    """Choose the nearest pre/post pair on the same track+frame around the event.

    TODO: group by (pathNumber, frameNumber), pick last-before and first-after
    the reference_date. Relative change on the same frame tolerates BETA
    calibration error.
    """
    raise NotImplementedError


def download(results: list, dest: Path, session: asf.ASFSession | None = None) -> None:
    """Download granules. Subset before downloading when possible — files are large."""
    if session is None:
        session = asf.ASFSession().auth_with_creds(
            os.environ["EDL_USER"], os.environ["EDL_PASS"]
        )
    results.download(path=str(dest), session=session)


def h5_to_cog(h5_path: Path, band: str, aoi_wkt: str, out_path: Path) -> Path:
    """Extract `band` from a NISAR HDF5, clip to AOI, write Cloud-Optimized GeoTIFF.

    TODO: confirm internal HDF5 paths in the NISAR Data User Guide
    (e.g. /science/LSAR/GCOV/grids/frequencyA/... for backscatter layers).
    """
    raise NotImplementedError
