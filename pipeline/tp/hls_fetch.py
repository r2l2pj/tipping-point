"""HLS (Harmonized Landsat Sentinel-2) post-event scene fetch via earthaccess.

HLS v2.0 collections (LP DAAC): HLSL30 (Landsat 8/9) and HLSS30 (Sentinel-2),
30 m reflectance COGs, ~2-3 day combined revisit. Used for optical
flood-confirmation scenes and landslide-scar candidates — the first
cloud-free post-event days. CMR indexes them as short_name+version pairs;
SHORT_NAMES uses the "<SHORT>_<VER>" spec form ("HLSL30_2.0") and
_collection_ref() splits it. Both are verified against CMR at run time.

Auth: NASA Earthdata Login is needed for downloads only — CMR metadata
search is unauthenticated, so search-only paths (granule list, --dry-run,
--no-download) work without creds. When downloading, creds are resolved at
call time: EDL_USER/EDL_PASS env (mapped to EARTHDATA_USERNAME/PASSWORD,
same convention as live/fetch.py) or a ~/.netrc entry for
urs.earthdata.nasa.gov. earthaccess is imported lazily so `--help` and the
test suite need neither creds nor network.

Record schema — search_scenes()/fetch_scenes() return a list of these, and
10_hls_fetch.py prints them as JSON for the later hls_scar unit:

    granule      str    CMR native-id, e.g. "HLS.S30.T11SMT.2025350T183921.v2.0"
    date         str    acquisition date "YYYY-MM-DD" (TemporalExtent start)
    cloud_cover  float  CMR CloudCover percent 0-100, or null if absent
    local_path   str    per-granule download dir under data/raw/hls/<event>/,
                        or null for search-only (no-download) results

Records are sorted by cloud_cover ascending (missing last), then date —
the least-cloudy post-event scenes come first.
"""
from __future__ import annotations

import datetime as dt
import os
import re
from pathlib import Path

from tp.events import DATA_DIR

SHORT_NAMES = ("HLSL30_2.0", "HLSS30_2.0")
RAW_HLS_DIR = DATA_DIR / "raw" / "hls"

_EDL_MSG = (
    "NASA Earthdata Login credentials are required for HLS search/download. "
    "Set EDL_USER/EDL_PASS (or EARTHDATA_USERNAME/EARTHDATA_PASSWORD), or add "
    "a ~/.netrc entry for urs.earthdata.nasa.gov. See .env.example."
)

# HLS native-id: HLS.<S30|L30>.T<tile>.<YYYYDDD>T<HHMMSS>.v2.0
_HLS_NAME_RE = re.compile(r"HLS\.[LS]30\.T\w+\.(?P<yday>\d{7})T\d{6}")


def _collection_ref(spec: str) -> tuple[str, str | None]:
    """Split a '<SHORT>_<VER>' spec ('HLSL30_2.0') into CMR short_name/version."""
    short, _, ver = spec.rpartition("_")
    if short and re.fullmatch(r"\d+(\.\d+)*", ver):
        return short, ver
    return spec, None


def resolve_window(
    event: dict, after: str | None = None, before: str | None = None
) -> tuple[str, str]:
    """Post-event search window: reference_date .. window end by default."""
    return after or event["reference_date"], before or event["window"]["end"]


def _edl_login():
    """earthaccess.login() from env or ~/.netrc, with a clean error if neither."""
    os.environ.setdefault("EARTHDATA_USERNAME", os.environ.get("EDL_USER", ""))
    os.environ.setdefault("EARTHDATA_PASSWORD", os.environ.get("EDL_PASS", ""))
    import earthaccess

    try:
        if os.environ.get("EARTHDATA_USERNAME") and os.environ.get(
            "EARTHDATA_PASSWORD"
        ):
            return earthaccess.login(strategy="environment")
        if (Path.home() / ".netrc").exists():
            return earthaccess.login(strategy="netrc")
    except Exception as e:
        raise RuntimeError(_EDL_MSG) from e
    raise RuntimeError(_EDL_MSG)


def _verify_collections(short_names: tuple[str, ...]) -> None:
    """Confirm each configured HLS collection exists in CMR (run-time check)."""
    import earthaccess

    bad = []
    for spec in short_names:
        short, ver = _collection_ref(spec)
        kwargs = {"version": ver} if ver else {}
        try:
            found = earthaccess.search_datasets(
                short_name=short, count=1, **kwargs
            )
        except (OSError, RuntimeError, ValueError):  # CMR/network hiccup -> unverified
            found = []
        if not found:
            bad.append(spec)
    if bad:
        raise RuntimeError(
            f"HLS collection(s) not found in CMR: {bad} — check SHORT_NAMES"
        )


def _cloud_cover(granule) -> float | None:
    """Cloud-cover percent from granule UMM (CloudCover or AdditionalAttributes)."""
    umm = granule.get("umm", {}) if hasattr(granule, "get") else {}
    for key in ("CloudCover", "cloudCover"):
        if umm.get(key) is not None:
            return float(umm[key])
    for attr in umm.get("AdditionalAttributes") or []:
        if "cloud" in str(attr.get("Name", "")).lower():
            vals = attr.get("Values") or []
            if vals:
                return float(vals[0])
    props = granule.get("properties", {}) if hasattr(granule, "get") else {}
    for key in ("cloudCover", "cloud_cover"):
        if props.get(key) is not None:
            return float(props[key])
    return None


def _granule_date(granule) -> str | None:
    """Acquisition date 'YYYY-MM-DD' from TemporalExtent, else the native-id."""
    umm = granule.get("umm", {}) if hasattr(granule, "get") else {}
    begin = (
        (umm.get("TemporalExtent") or {}).get("RangeDateTime") or {}
    ).get("BeginningDateTime") or (umm.get("TemporalExtent") or {}).get(
        "SingleDateTime"
    )
    if begin:
        return str(begin)[:10]
    native = _granule_id(granule) or ""
    m = _HLS_NAME_RE.search(native)
    if m:
        return dt.datetime.strptime(m["yday"], "%Y%j").date().isoformat()
    return None


def _granule_id(granule) -> str | None:
    meta = granule.get("meta", {}) if hasattr(granule, "get") else {}
    umm = granule.get("umm", {}) if hasattr(granule, "get") else {}
    return meta.get("native-id") or umm.get("GranuleUR") or (
        None if isinstance(granule, dict) else str(granule)
    )


def granule_record(granule, local_path: str | None = None) -> dict:
    """The {granule, date, cloud_cover, local_path} record (see module docstring)."""
    return {
        "granule": _granule_id(granule),
        "date": _granule_date(granule),
        "cloud_cover": _cloud_cover(granule),
        "local_path": local_path,
    }


def sort_granules(granules: list) -> list:
    """Least-cloudy first; scenes with no cloud-cover metadata sort last."""
    return sorted(
        granules,
        key=lambda g: (
            float("inf") if _cloud_cover(g) is None else _cloud_cover(g),
            _granule_date(g) or "",
        ),
    )


def search_granules(
    event: dict,
    after: str | None = None,
    before: str | None = None,
    limit: int | None = None,
    short_names: tuple[str, ...] = SHORT_NAMES,
) -> list:
    """CMR search over the event AOI for post-event HLS scenes, sorted.

    No credentials needed — CMR granule/collection search is public (login
    happens only on download, in fetch_scenes). `limit` keeps the N
    least-cloudy granules across all short_names combined.
    """
    import earthaccess

    _verify_collections(short_names)
    temporal = resolve_window(event, after, before)
    bbox = tuple(event["aoi"]["bbox"])
    granules = []
    for spec in short_names:
        short, ver = _collection_ref(spec)
        kwargs = {"version": ver} if ver else {}
        granules += earthaccess.search_data(
            short_name=short,
            bounding_box=bbox,
            temporal=temporal,
            **kwargs,
        )
    return sort_granules(granules)[:limit]


def fetch_scenes(
    event: dict,
    after: str | None = None,
    before: str | None = None,
    limit: int | None = None,
    download: bool = True,
    out_dir: Path | None = None,
    short_names: tuple[str, ...] = SHORT_NAMES,
) -> list[dict]:
    """Search + optionally download HLS scenes; returns JSON-able records.

    With download=True each granule goes to
    `<out_dir or data/raw/hls>/<event_id>/<granule>/` (gitignored) and the
    record's local_path points at that directory. With download=False the
    records are metadata-only (local_path null).
    """
    granules = search_granules(event, after, before, limit, short_names)
    if not download:
        return [granule_record(g) for g in granules]

    import earthaccess

    _edl_login()
    base = (out_dir or RAW_HLS_DIR) / event["id"]
    records = []
    for g in granules:
        dest = base / (granule_record(g)["granule"] or "unknown")
        earthaccess.download([g], str(dest))
        records.append(granule_record(g, str(dest)))
    return records
