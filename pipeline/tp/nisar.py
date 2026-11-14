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

import re
from pathlib import Path

import earthaccess

COLLECTIONS = {
    "GCOV": "NISAR_L2_GCOV_BETA_V1",
    "GUNW": "NISAR_L2_GUNW_BETA_V1",
    "WATERMASK": "NISAR_WATERMASK",
    "DEM": "NISAR_DEM",
}

# HDF5 variable paths per product, relative to the granule.
HDF5_PATHS = {
    "GCOV": {
        "HHHH": "/science/LSAR/GCOV/grids/frequencyA/HHHH",
        "HVHV": "/science/LSAR/GCOV/grids/frequencyA/HVHV",
        "mask": "/science/LSAR/GCOV/grids/frequencyA/mask",
        "numberOfLooks": "/science/LSAR/GCOV/grids/frequencyA/numberOfLooks",
    },
    "GUNW": {
        "coherenceMagnitude": (
            "/science/LSAR/GUNW/grids/frequencyA/"
            "wrappedInterferogram/HH/coherenceMagnitude"
        ),
    },
}

# NISAR_L2_PR_<PROD>_<cycle>_<track>_<D|A>_<frame>_<mode>_<pols>_A_<YYYYMMDD>T...
GRANULE_RE = re.compile(
    r"NISAR_L2_\w+?_(?P<product>GCOV|GUNW)_\d+_(?P<track>\d+)_(?P<look>[AD])_"
    r"(?P<frame>\d+)_.+?_(?P<date>\d{8})T\d{6}"
)


def search(event: dict, product: str = "GCOV", collection: str | None = None) -> list:
    """Search NISAR granules intersecting the event AOI and window.

    `product` maps through COLLECTIONS (GCOV/GUNW/WATERMASK/DEM); pass
    `collection` to override with a full CMR short_name.
    """
    w = event["window"]
    short_name = collection or COLLECTIONS[product]
    earthaccess.login()
    return earthaccess.search_data(
        short_name=short_name,
        bounding_box=tuple(event["aoi"]["bbox"]),
        temporal=(w["start"], w["end"]),
    )


def _granule_meta(granule) -> dict | None:
    """Parse track/look/frame/date out of a granule native-id or fileID."""
    name = granule
    if not isinstance(name, str):
        meta = getattr(granule, "meta", None) or {}
        props = getattr(granule, "properties", None) or {}
        name = meta.get("native-id") or props.get("fileID") or ""
    m = GRANULE_RE.search(str(name))
    if not m:
        return None
    d = m.groupdict()
    return {
        "product": d["product"],
        "track": int(d["track"]),
        "look": d["look"],
        "frame": int(d["frame"]),
        "date": f"{d['date'][:4]}-{d['date'][4:6]}-{d['date'][6:]}",
    }


def pick_pairs(granules: list, reference_date: str) -> dict:
    """Choose nearest pre/post granules on the same track+frame.

    Groups by (track, look, frame) and picks the last acquisition strictly
    before and the first after `reference_date`. Relative change on the same
    frame tolerates BETA calibration error. Returns
    {key: {"pre": ..., "post": ...}} keyed by "172D_064"-style strings.
    """
    groups: dict[str, list] = {}
    for g in granules:
        meta = _granule_meta(g)
        if meta is None:
            continue
        key = f"{meta['track']:03d}{meta['look']}_{meta['frame']:03d}"
        groups.setdefault(key, []).append((meta["date"], g))

    pairs = {}
    for key, items in groups.items():
        items.sort(key=lambda t: t[0])
        pre = next(
            (g for d, g in reversed(items) if d <= reference_date), None
        )
        post = next((g for d, g in items if d > reference_date), None)
        if pre is not None and post is not None:
            pairs[key] = {"pre": pre, "post": post}
    return pairs


def h5_url(granule) -> str | None:
    """Direct HTTPS URL of a granule's HDF5, for GDAL /vsicurl/ access."""
    links = granule.data_links(access="external") if hasattr(granule, "data_links") else []
    return next((u for u in links if u.endswith((".h5", ".hdf5", ".he5"))), None)


def harmony_subset(
    granule, variables: list[str], bbox: tuple, out_path: Path
) -> Path:
    """Submit a Harmony job: extract `variables` clipped to bbox as COG.

    Requires `harmony-py` (optional runtime dep — `pip install harmony-py` or
    `uv pip install harmony-py`) and Earthdata credentials in env/netrc.
    Always select variables explicitly; no selection means every grid is
    extracted (slow). If harmony-py is unavailable or the job fails, use
    gdal_subset_cmd() for the /vsicurl/ fallback.
    """
    try:
        from harmony import BBox, Client, Collection, Request
    except ImportError as e:
        raise RuntimeError(
            "harmony-py is not installed. Install it at runtime "
            "(uv pip install harmony-py) or fall back to gdal_subset_cmd()."
        ) from e

    w, s, e, n = bbox
    client = Client()
    collection_id = granule.concept_id if hasattr(granule, "concept_id") else None
    collection = Collection(id=collection_id) if collection_id else None
    req = Request(
        collection=collection,
        granule_id=getattr(granule, "concept_id", None),
        variables=variables,
        spatial=BBox(w, s, e, n),
        format="image/tiff",
    )
    job_id = client.submit(req)
    client.wait_for_processing(job_id, show_progress=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    results = client.download_all(job_id, directory=str(out_path.parent), overwrite=True)
    paths = [f.result() for f in results]
    return Path(paths[0]) if paths else out_path


def download_plan(event: dict) -> list[dict]:
    """The minimal set of Harmony/GDAL extractions for an event.

    One entry per (frame, product, variable) so scripts can print or submit
    them. Mirrors report §4 step 1: GCOV HHHH/HVHV/mask pre+post, GUNW 20 m
    coherence ref+event pairs, plus the NISAR_WATERMASK tile.
    """
    plan = []
    nisar = event.get("nisar", {})
    for frame in nisar.get("frames", []):
        product = frame["product"]
        if product == "GCOV":
            for which, date in (("pre", frame["pre"]), ("post", frame["post"])):
                for var in ("HHHH", "HVHV", "mask"):
                    h5path = HDF5_PATHS["GCOV"][var]
                    plan.append(
                        {
                            "product": "GCOV",
                            "which": which,
                            "date": date,
                            "variable": var,
                            "h5_path": h5path,
                            "collection": frame["collections"].get(
                                "BETA", COLLECTIONS["GCOV"]
                            ),
                            "out": f"gcov_{var.lower()}_{which}.tif",
                        }
                    )
        elif product == "GUNW":
            for which, pair in (
                ("ref", frame["reference_pair"]),
                ("evt", frame["event_pair"]),
            ):
                plan.append(
                    {
                        "product": "GUNW",
                        "which": which,
                        "date": pair,
                        "variable": "coherenceMagnitude",
                        "h5_path": HDF5_PATHS["GUNW"]["coherenceMagnitude"],
                        "collection": frame["collections"].get(
                            "BETA", COLLECTIONS["GUNW"]
                        ),
                        "out": f"gunw_coh_{which}.tif",
                    }
                )
    if "watermask" in nisar.get("aux_collections", {}):
        plan.append(
            {
                "product": "WATERMASK",
                "which": "ref",
                "date": None,
                "variable": "watermask",
                "h5_path": None,
                "collection": nisar["aux_collections"]["watermask"],
                "out": "nisar_watermask.tif",
            }
        )
    return plan


def gdal_subset_cmd(h5_url: str, h5_path: str, bbox: tuple, out_path: Path) -> str:
    """Return the GDAL /vsicurl/ fallback command for one HDF5 variable.

    Requires ~/.netrc and GDAL_HTTP_NETRC=YES. GDAL < 3.13 needs the NETCDF:
    prefix (HDF5 driver can't read NISAR's CF georeferencing).
    """
    w, s, e, n = bbox
    src = f'NETCDF:"/vsicurl/{h5_url}":{h5_path}'
    return (
        "GDAL_HTTP_NETRC=YES "
        f"gdalwarp -te_srs EPSG:4326 -te {w} {s} {e} {n} "
        f"-srcnodata nan -dstnodata nan -of COG '{src}' {out_path}"
    )
