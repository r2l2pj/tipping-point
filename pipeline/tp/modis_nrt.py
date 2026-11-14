"""MODIS NRT flood product (MCDWD) fetch + agreement vs a reference mask.

LANCE ships a daily ~250 m global flood product from Terra+Aqua MODIS.
Verified CMR short names (2026-11, user guide Rev F): MCDWD_L3_NRT is the
4-band HDF (1-day, 1-day CS, 2-day, 3-day composites); each composite also
ships as a GeoTIFF under MCDWD_L3_F1_NRT / _F1C_NRT (cloud-shadow
screened) / _F2_NRT / _F3_NRT. The data plan's 'MCDWD_L3_FWD_NRT' label is
kept as the default short_name and resolved against the real names at run
time — the first candidate that returns granules wins.

Flood-water layer values: 0 no water, 1 surface water (matches reference
water), 2 recurring flood, 3 flood; >= 250 is fill/flag and is excluded
from the denominator. Tiles are 10x10 deg in EPSG:4326 (linear lat/lon).

Auth: NASA Earthdata Login. earthaccess is imported lazily inside the
functions that need it; creds are resolved at call time via fetch.edl —
EARTHDATA_TOKEN (bearer), then EDL_USER/EDL_PASS env, then ~/.netrc.
Never hardcode credentials.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import rioxarray as rxr

SHORT_NAME = "MCDWD_L3_FWD_NRT"
KNOWN_SHORT_NAMES = (
    "MCDWD_L3_F1C_NRT",   # 1-day GeoTIFF, cloud-shadow screened (preferred)
    "MCDWD_L3_F1_NRT",    # 1-day GeoTIFF
    "MCDWD_L3_F2_NRT",    # 2-day composite GeoTIFF
    "MCDWD_L3_F3_NRT",    # 3-day composite GeoTIFF
    "MCDWD_L3_NRT",       # HDF bundle with all four composites
)

WATER_VALS = (1, 2, 3)    # surface water, recurring flood, flood
FILL_MIN = 250            # >= 250: fill/flag values, excluded from metrics

_SCENE_RE = re.compile(r"\.A(\d{4})(\d{3})")   # AYYYYDOY in product filenames


def search_params(
    event: dict,
    short_name: str = SHORT_NAME,
    start: str | None = None,
    end: str | None = None,
) -> dict:
    """The CMR query for an event — also what --dry-run prints (no creds)."""
    w = event["window"]
    return {
        "short_names": list(dict.fromkeys((short_name, *KNOWN_SHORT_NAMES))),
        "bounding_box": list(event["aoi"]["bbox"]),
        "temporal": [start or w["start"], end or w["end"]],
    }


def search(
    event: dict,
    short_name: str = SHORT_NAME,
    start: str | None = None,
    end: str | None = None,
) -> list:
    """Search MCDWD granules intersecting the event AOI and window.

    Tries `short_name` first, then the verified MCDWD names, so the data
    plan's FWD label resolves to whichever real collection has scenes.
    Returns [] (never raises) when nothing matches or auth/search fails.
    """
    p = search_params(event, short_name, start, end)
    try:
        import earthaccess
        from fetch import edl
    except ImportError:
        return []
    try:
        edl.earthaccess_login()
    except Exception:  # noqa: BLE001 — no EDL creds configured -> no scenes
        return []
    for name in p["short_names"]:
        try:
            hits = earthaccess.search_data(
                short_name=name,
                bounding_box=tuple(p["bounding_box"]),
                temporal=tuple(p["temporal"]),
            )
        except Exception:  # noqa: BLE001 — unknown name/offline -> try next
            hits = []
        if hits:
            return hits
    return []


def clip_to_aoi(src: Path, bbox: tuple, out_dir: Path | None = None) -> Path:
    """Crop a raster to the EPSG:4326 AOI bbox as '<stem>_aoi<ext>'.

    On any read/clip failure (HDF bundle, no overlap) the source path is
    returned unchanged — the comparison step decides what it can read.
    """
    src = Path(src)
    out = Path(out_dir or src.parent) / f"{src.stem}_aoi{src.suffix}"
    try:
        da = rxr.open_rasterio(src).squeeze()
        clipped = da.rio.clip_box(
            minx=bbox[0], miny=bbox[1], maxx=bbox[2], maxy=bbox[3],
            crs="EPSG:4326",
        )
        clipped.rio.to_raster(out)
        return out
    except Exception:  # noqa: BLE001 — unreadable/empty clip keeps the source
        return src


def fetch_nrt(
    event: dict,
    dest_dir: Path,
    short_name: str = SHORT_NAME,
    start: str | None = None,
    end: str | None = None,
) -> list[Path]:
    """Search + download MCDWD granules; clip GeoTIFFs to the event bbox."""
    granules = search(event, short_name, start, end)
    if not granules:
        return []
    import earthaccess
    from fetch import edl

    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    bbox = tuple(event["aoi"]["bbox"])
    # Pin the Bearer token onto earthaccess's shared HTTPS session so
    # EARTHDATA_TOKEN rides granule downloads as Authorization: Bearer.
    session = getattr(earthaccess, "get_requests_https_session", lambda: None)()
    if session is not None:
        session.headers.update(edl.edl_auth_headers())
    out = []
    for f in earthaccess.download(granules, str(dest_dir)) or []:
        p = Path(f)
        if p.suffix.lower() in (".tif", ".tiff"):
            out.append(clip_to_aoi(p, bbox))
        else:
            out.append(p)
    return out


def scene_date(path: Path | str) -> str | None:
    """ISO date from a MCDWD filename's AYYYYDOY field, else None."""
    m = _SCENE_RE.search(Path(path).name)
    if not m:
        return None
    try:
        d = date(int(m.group(1)), 1, 1) + timedelta(days=int(m.group(2)) - 1)
    except ValueError:
        return None
    return d.isoformat()


def pick_scene(paths: list, reference_date: str | None) -> Path | None:
    """The downloaded scene nearest the event reference_date (sorted, stable)."""
    if not paths:
        return None
    ref = date.fromisoformat(reference_date) if reference_date else None

    def key(p: Path) -> int:
        d = scene_date(p)
        if d is None or ref is None:
            return 10**9
        return abs((date.fromisoformat(d) - ref).days)

    return min(sorted(Path(p) for p in paths), key=key)


def classify(arr: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """-> (water, valid). MCDWD: 0 land, 1/2/3 water classes, >=250 fill."""
    a = np.asarray(arr, dtype=np.float64)
    valid = np.isfinite(a) & (a < FILL_MIN)
    return np.isin(a, WATER_VALS) & valid, valid


def compare_water(
    nrt_mask: Path | str | None,
    ref_raster: Path | str | None,
    granule: str | None = None,
    scene: str | None = None,
) -> dict:
    """Align the NRT water mask to the reference raster; agreement metrics.

    Emits flat modis_nrt_* keys for the metrics.json merge. Missing
    scene/reference or unreadable rasters -> null values plus
    modis_nrt_reason; never raises.
    """
    out = {
        "modis_nrt_agreement_pct": None,
        "modis_nrt_iou": None,
        "modis_nrt_f1": None,
        "modis_nrt_water_px": None,
        "modis_nrt_ref_water_px": None,
        "modis_nrt_valid_px": None,
        "modis_nrt_scene_date": scene,
        "modis_nrt_granule": granule,
        "modis_nrt_reason": None,
    }

    if nrt_mask is None:
        out["modis_nrt_reason"] = "no NRT scene downloaded"
        return out
    nrt_path = Path(nrt_mask)
    if not nrt_path.exists():
        out["modis_nrt_reason"] = f"NRT raster not found: {nrt_path}"
        return out
    if out["modis_nrt_scene_date"] is None:
        out["modis_nrt_scene_date"] = scene_date(nrt_path)
    if out["modis_nrt_granule"] is None:
        out["modis_nrt_granule"] = nrt_path.stem

    if ref_raster is None:
        out["modis_nrt_reason"] = "no reference raster (--ref)"
        return out
    ref_path = Path(ref_raster)
    if not ref_path.exists():
        out["modis_nrt_reason"] = f"reference raster not found: {ref_path}"
        return out

    try:
        ref = rxr.open_rasterio(ref_path).squeeze()
        nrt = (
            rxr.open_rasterio(nrt_path)
            .squeeze()
            .rio.reproject_match(ref, resampling=0)
        )
    except Exception as e:  # noqa: BLE001 — nulls + reason, never a crash
        out["modis_nrt_reason"] = f"unreadable raster: {e}"
        return out

    nrt_water, nrt_valid = classify(nrt.values)
    ref_arr = np.asarray(ref.values, dtype=np.float64)
    valid = nrt_valid & np.isfinite(ref_arr)
    n = int(valid.sum())
    if n == 0:
        out["modis_nrt_reason"] = "no overlapping valid pixels"
        return out

    ref_water = ref_arr > 0
    tp = int((ref_water & nrt_water & valid).sum())
    fp = int((~ref_water & nrt_water & valid).sum())
    fn = int((ref_water & ~nrt_water & valid).sum())
    out.update(
        {
            "modis_nrt_agreement_pct": float(100 * ((nrt_water == ref_water) & valid).sum() / n),
            "modis_nrt_iou": tp / (tp + fp + fn) if (tp + fp + fn) else 0.0,
            "modis_nrt_f1": 2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) else 0.0,
            "modis_nrt_water_px": int((nrt_water & valid).sum()),
            "modis_nrt_ref_water_px": int((ref_water & valid).sum()),
            "modis_nrt_valid_px": n,
        }
    )
    return out
