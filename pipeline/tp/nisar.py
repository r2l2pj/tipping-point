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

Auth: NASA Earthdata Login, resolved via fetch.edl — prefer EARTHDATA_TOKEN
(OAuth bearer), then EDL_USER/EDL_PASS env, then ~/.netrc (machine
urs.earthdata.nasa.gov). Never hardcode credentials.
"""
from __future__ import annotations

import json
import re
import shutil
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlparse

import earthaccess
from fetch import edl

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

# Scene-role spellings seen in event files -> plan "which" keys.
_ROLE_ALIAS = {
    "no-event reference": "ref",
    "no-event": "ref",
    "no_event": "ref",
    "reference": "ref",
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
    edl.earthaccess_login()
    return earthaccess.search_data(
        short_name=short_name,
        bounding_box=tuple(event["aoi"]["bbox"]),
        temporal=(w["start"], w["end"]),
    )


def _meta(granule) -> dict:
    """earthaccess DataGranule is a dict subclass: meta lives under keys, not
    attributes. Tolerate attribute-style objects too."""
    if isinstance(granule, dict):
        return granule.get("meta") or {}
    return getattr(granule, "meta", None) or {}


def _props(granule) -> dict:
    if isinstance(granule, dict):
        return granule.get("properties") or granule.get("umm") or {}
    return getattr(granule, "properties", None) or {}


def _granule_meta(granule) -> dict | None:
    """Parse track/look/frame/date out of a granule native-id or fileID."""
    name = granule
    if not isinstance(name, str):
        name = _meta(granule).get("native-id") or _props(granule).get(
            "fileID"
        ) or _props(granule).get("GranuleUR") or ""
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
    `uv pip install harmony-py`) and Earthdata credentials (EARTHDATA_TOKEN,
    EDL_USER/EDL_PASS env, or netrc).
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
        product = frame.get("product")
        # Tolerate both event shapes: scene dates may sit flat on the frame,
        # in a "scenes" mapping, or in a "scenes" list of
        # {date, role, granule_id} entries.
        scenes = frame.get("scenes")
        scene_map, granule_ids = {}, {}
        if isinstance(scenes, dict):
            scene_map = scenes
        elif isinstance(scenes, list):
            for s in scenes:
                if isinstance(s, dict) and s.get("role"):
                    roles = [s["role"], _ROLE_ALIAS.get(s["role"])]
                    for role in filter(None, roles):
                        scene_map.setdefault(role, s.get("date"))
                        if s.get("granule_id"):
                            granule_ids.setdefault(role, s["granule_id"])
        collections = frame.get("collections", {})
        track, frame_no = frame.get("track"), frame.get("frame")
        if product == "GCOV":
            for which, date in (
                (
                    "ref",
                    scene_map.get("no-event reference")
                    or scene_map.get("no_event")
                    or scene_map.get("ref")
                    or frame.get("ref"),
                ),
                ("pre", scene_map.get("pre") or frame.get("pre")),
                ("post", scene_map.get("post") or frame.get("post")),
            ):
                if not date:
                    continue
                for var in ("HHHH", "HVHV", "mask"):
                    h5path = HDF5_PATHS["GCOV"][var]
                    plan.append(
                        {
                            "product": "GCOV",
                            "which": which,
                            "date": date,
                            "variable": var,
                            "h5_path": h5path,
                            "track": track,
                            "frame": frame_no,
                            "granule_id": granule_ids.get(which),
                            "collection": collections.get(
                                "BETA", COLLECTIONS["GCOV"]
                            ),
                            "out": f"gcov_{var.lower()}_{which}.tif",
                        }
                    )
        elif product == "GUNW":
            for which, pair in (
                (
                    "ref",
                    scene_map.get("reference_pair")
                    or scene_map.get("ref")
                    or frame.get("reference_pair"),
                ),
                (
                    "evt",
                    scene_map.get("event_pair")
                    or scene_map.get("evt")
                    or frame.get("event_pair"),
                ),
            ):
                if not pair:
                    continue
                plan.append(
                    {
                        "product": "GUNW",
                        "which": which,
                        "date": pair,
                        "variable": "coherenceMagnitude",
                        "h5_path": HDF5_PATHS["GUNW"]["coherenceMagnitude"],
                        "track": track,
                        "frame": frame_no,
                        "granule_id": granule_ids.get(which),
                        "collection": collections.get(
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


def _netcdf_dsn(h5_url: str, h5_path: str) -> str:
    """GDAL dataset name for one HDF5 variable streamed over /vsicurl/."""
    return f'NETCDF:"/vsicurl/{h5_url}":{h5_path}'


def gdal_subset_cmd(h5_url: str, h5_path: str, bbox: tuple, out_path: Path) -> str:
    """Return the GDAL /vsicurl/ fallback command for one HDF5 variable.

    With EARTHDATA_TOKEN set the Bearer token rides in GDAL_HTTP_HEADERS via
    an env-var reference (the token value is never printed); otherwise the
    command expects ~/.netrc and GDAL_HTTP_NETRC=YES. GDAL < 3.13 needs the
    NETCDF: prefix (HDF5 driver can't read NISAR's CF georeferencing).
    """
    w, s, e, n = bbox
    src = _netcdf_dsn(h5_url, h5_path)
    auth = (
        'GDAL_HTTP_HEADERS="Authorization: Bearer $EARTHDATA_TOKEN" '
        if edl.edl_token()
        else "GDAL_HTTP_NETRC=YES "
    )
    return (
        f"CPL_VSIL_CURL_USE_HEAD=NO {auth}"
        f"gdalwarp -te_srs EPSG:4326 -te {w} {s} {e} {n} "
        f"-srcnodata nan -dstnodata nan -of COG '{src}' {out_path}"
    )


def _step_dates(step: dict) -> list[str]:
    """ISO dates embedded in a plan entry: '2025-12-04' or 'a_to_b' GUNW pair."""
    return re.findall(r"\d{4}-\d{2}-\d{2}", str(step.get("date") or ""))


def _track_look(value) -> tuple[int, str] | None:
    """'172D' -> (172, 'D')."""
    m = re.match(r"^\s*(\d+)\s*([AD])\s*$", str(value or ""), re.IGNORECASE)
    return (int(m.group(1)), m.group(2).upper()) if m else None


def _native_id(granule) -> str:
    name = _meta(granule).get("native-id") or _props(granule).get(
        "fileID"
    ) or _props(granule).get("GranuleUR")
    return str(name or granule)


def _find_granule(step: dict, bbox: tuple):
    """CMR-search a plan entry's collection; pick the granule on the event's
    track/frame nearest its scene date. Raises RuntimeError with context."""
    dates = _step_dates(step)
    temporal = None
    if dates:
        lo = date.fromisoformat(min(dates)) - timedelta(days=1)
        hi = date.fromisoformat(max(dates)) + timedelta(days=1)
        temporal = (lo.isoformat(), hi.isoformat())
    try:
        results = earthaccess.search_data(
            short_name=step["collection"],
            bounding_box=bbox,
            temporal=temporal,
        )
    except Exception as e:
        raise RuntimeError(
            f"CMR search failed for {step['collection']} "
            f"({step['product']} {step['which']} {step.get('date')}): {e}"
        ) from e
    if not results:
        raise RuntimeError(
            f"No {step['collection']} granules intersect the AOI around "
            f"{step.get('date')} — check the event's NISAR scene dates."
        )

    # A granule_id declared in the event wins outright.
    wanted = step.get("granule_id")
    if wanted:
        hit = next((g for g in results if _native_id(g) == wanted), None)
        if hit is not None:
            return hit

    tl = _track_look(step.get("track"))
    frame_txt = str(step.get("frame") or "")
    frame_no = int(frame_txt) if frame_txt.isdigit() else None
    best, best_score = None, -1
    for g in results:
        meta = _granule_meta(g)
        if meta is None:
            continue
        if tl is not None and (meta["track"], meta["look"]) != tl:
            continue
        if frame_no is not None and meta["frame"] != frame_no:
            continue
        score = 0
        if dates:
            score = 2 if meta["date"] == dates[-1] else int(
                meta["date"] in dates
            )
        if score > best_score:
            best, best_score = g, score
    if best is None:
        if tl is None and frame_no is None:
            return results[0]
        names = [_native_id(g) for g in results]
        raise RuntimeError(
            f"{len(results)} {step['collection']} granule(s) found but none "
            f"match track {step.get('track')} frame {step.get('frame')}: "
            f"{names}"
        )
    return best


class _StripAuthOnCrossHost(urllib.request.HTTPRedirectHandler):
    """ASF TEA 303s to a signed CloudFront URL — the bearer header must not
    leak to (or break) the signed request."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and (
            urlparse(newurl).netloc != urlparse(req.full_url).netloc
        ):
            new.remove_header("Authorization")
        return new


def _download_h5(url: str, dest: Path, creds: dict) -> Path:
    """Linear-download one granule HDF5 to `dest` (Bearer on TEA only).

    /vsicurl/ range requests through the TEA 303 redirect starve on NISAR's
    ~7 GB frames; a straight GET is far faster end to end.
    """
    headers = edl.edl_auth_headers()
    if "user" in creds:
        import base64

        raw = f"{creds['user']}:{creds['password']}".encode()
        headers = {"Authorization": f"Basic {base64.b64encode(raw).decode()}"}
    req = urllib.request.Request(url, headers=headers)
    opener = urllib.request.build_opener(_StripAuthOnCrossHost)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    with opener.open(req) as r, tmp.open("wb") as f:
        shutil.copyfileobj(r, f, 1024 * 1024)
    tmp.rename(dest)
    return dest


def _subset_h5_variable(
    src: str, h5_path: str, bbox: tuple, out_path: Path, gdal_env: dict
) -> Path:
    """Read one HDF5 variable from a local file or /vsicurl/ URL, clip to
    `bbox`, write a COG."""
    import rasterio
    from rasterio.vrt import WarpedVRT
    from rasterio.windows import from_bounds

    if src.startswith(("http://", "https://")):
        dsn = _netcdf_dsn(src, h5_path)
        # ASF TEA serves a 303 to a signed URL on HEAD too — GDAL reads the
        # empty redirect body as the file and reports "not a supported
        # format". Skip HEAD so the first request is a byte-range GET that
        # follows the redirect.
        env = {**gdal_env, "CPL_VSIL_CURL_USE_HEAD": "NO"}
    else:
        dsn = f'NETCDF:"{src}":{h5_path}'
        env = gdal_env
    w, s, e, n = bbox
    try:
        with (
            rasterio.Env(**env),
            rasterio.open(dsn) as h5src,
            WarpedVRT(h5src, crs="EPSG:4326") as vrt,
        ):
            win = from_bounds(
                w, s, e, n, transform=vrt.transform
            ).round_offsets().round_lengths()
            data = vrt.read(window=win)
            profile = dict(vrt.profile)
            profile.update(
                driver="COG",
                count=int(vrt.count),
                height=int(win.height),
                width=int(win.width),
                transform=vrt.window_transform(win),
                nodata=vrt.nodata,
                compress="deflate",
            )
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with rasterio.open(out_path, "w", **profile) as dst:
                dst.write(data)
    except Exception as exc:
        raise RuntimeError(
            f"GDAL subset failed for {h5_path} in {src}: {exc}"
        ) from exc
    return out_path


def execute_plan(event: dict, dry_run: bool = False) -> list[Path]:
    """Fetch every entry of download_plan(event) clipped to the event AOI.

    Each granule's HDF5 variable is streamed with GDAL
    'NETCDF:"/vsicurl/<h5_url>":<h5_path>' and written as a COG under
    data/raw/nisar/ (gitignored). Granule native-ids + collection ids are
    recorded in data/<case_key>/nisar_products.json (README product-ID list).

    EDL creds via EARTHDATA_TOKEN, EDL_USER/EDL_PASS env, or ~/.netrc,
    checked at call time — raises RuntimeError naming what is missing.
    dry_run returns the planned output paths with no creds and no network.
    """
    from tp import events as _events

    plan = download_plan(event)
    raw_dir = Path(_events.DATA_DIR) / "raw" / "nisar"
    steps = [s for s in plan if s.get("h5_path")]
    paths = [raw_dir / s["out"] for s in steps]
    if dry_run:
        return paths

    # Collection ids are recorded even for steps without an HDF5 subset.
    manifest: dict = {}
    for step in plan:
        manifest.setdefault(step["product"], {})["collection"] = step[
            "collection"
        ]
    event_dir = _events.event_data_dir(event["id"])

    def _write_manifest() -> Path:
        event_dir.mkdir(parents=True, exist_ok=True)
        path = event_dir / "nisar_products.json"
        path.write_text(json.dumps(manifest, indent=2) + "\n")
        return path

    if not steps:
        _write_manifest()
        return []

    creds = edl.ensure_earthdata()
    edl.earthaccess_login(creds)
    gdal_env = edl.gdal_auth_env(creds)
    bbox = tuple(event["aoi"]["bbox"])

    granules: dict = {}
    h5_local: dict = {}
    written = []
    for step, out_path in zip(steps, paths):
        key = (
            step["collection"],
            str(step.get("date")),
            str(step.get("track")),
            str(step.get("frame")),
        )
        if key not in granules:
            granules[key] = _find_granule(step, bbox)
        g = granules[key]
        url = h5_url(g)
        if not url:
            raise RuntimeError(
                f"granule {_native_id(g)} exposes no .h5 data link"
            )
        if url not in h5_local:
            h5_local[url] = _download_h5(
                url, raw_dir / "h5" / Path(urlparse(url).path).name, creds
            )
        _subset_h5_variable(
            str(h5_local[url]), step["h5_path"], bbox, out_path, gdal_env
        )
        written.append(out_path)
        role = step["which"]
        entry = manifest.setdefault(
            step["product"], {"collection": step["collection"]}
        )
        if role in entry:
            meta = _granule_meta(g)
            role = f"{role}_{meta['date']}" if meta else f"{role}_2"
        entry[role] = _native_id(g)

    _write_manifest()
    return written
