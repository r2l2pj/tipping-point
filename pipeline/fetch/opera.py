"""OPERA DSWx-HLS fetch + ingest — independent water-map validation scenes.

OPERA_L3_DSWX-HLS_V1 (PO.DAAC, ~30 m, HLS-derived): the B01_WTR layer is the
dynamic surface water map used to validate the radar flood mask (PLAN.md
validation.yml ruling L8a). CMR granule search is unauthenticated; Earthdata
Login is needed only at download time — EARTHDATA_TOKEN bearer first, then
EDL_USER/EDL_PASS (ruling L6, same convention as live/fetch.py).

This module IS the CLI (no tools/ wrapper):

    python -m pipeline.fetch.opera --case rs_2024 --limit 4
    python -m pipeline.fetch.opera --event rs-2024-05 --no-download
    python -m pipeline.fetch.opera --event wa-2025-12 --start 2025-12-10 \\
        --end 2025-12-20 --dry-run

--event reads aoi + window from events/*.json via tp.events (default window:
reference_date .. window.end, the post-event days). --case reads the
config.yaml case (aoi + event_date .. event_date+window_after_days) and also
names the output case when given together with --event. --start/--end
override either window bound.

Flow per granule (least-cloudy first, --limit N):
  CMR search -> pick the B01_WTR band link -> download (auth happens here)
  -> clip to the case AOI -> COG under gitignored
  data/raw/opera/<case>/opera_dswx_<yyyymmdd>.tif -> stage_to_gcs
  (bucket NOW_GCS_BUCKET) -> ingest_asset as image
  projects/$GEE_PROJECT/assets/tipping-point/<case>/opera/opera_dswx_<yyyymmdd>
  -> append_manifest. Re-runs skip asset ids already present in
  data/<case>/assets.json.

The ingest helpers land on unit/ingest as pipeline/fetch/ingest.py
(stage_to_gcs / ingest_asset / append_manifest). The import is lazy and
guarded so --no-download / --dry-run work before that unit merges.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
from pathlib import Path

from tp import hls_fetch
from tp.config import case as config_case
from tp.events import DATA_DIR, case_key, load_event

SHORT_NAME = "OPERA_L3_DSWX-HLS_V1"
RAW_DIR = DATA_DIR / "raw" / "opera"

_EDL_MSG = (
    "NASA Earthdata Login credentials are required to download OPERA "
    "DSWx-HLS (CMR search needs none). Set EARTHDATA_TOKEN (bearer, "
    "preferred) or EDL_USER/EDL_PASS, or add a ~/.netrc entry for "
    "urs.earthdata.nasa.gov. Use --no-download for search-only."
)

_WTR_RE = re.compile(r"_WTR\.")
_OPERA_DATE_RE = re.compile(r"_(\d{8})T\d{6}Z")


def _wtr_rank(name: str) -> int | None:
    """0 for the B01_WTR band file, 1 for other *_WTR.* names, else None."""
    if "B01_WTR" in name:
        return 0
    return 1 if _WTR_RE.search(name) else None


def _edl_login():
    """earthaccess.login() on the download path only.

    EARTHDATA_TOKEN bearer first; then EDL_USER/EDL_PASS — mapped onto
    EARTHDATA_USERNAME/PASSWORD only when both are set (or those set
    directly); then a ~/.netrc entry. Clean error naming what's missing.
    """
    import earthaccess

    try:
        if os.environ.get("EARTHDATA_TOKEN"):
            return earthaccess.login(strategy="environment")
        edl_user = os.environ.get("EDL_USER")
        edl_pass = os.environ.get("EDL_PASS")
        if edl_user and edl_pass:
            os.environ.setdefault("EARTHDATA_USERNAME", edl_user)
            os.environ.setdefault("EARTHDATA_PASSWORD", edl_pass)
        if (
            os.environ.get("EARTHDATA_USERNAME")
            and os.environ.get("EARTHDATA_PASSWORD")
        ):
            return earthaccess.login(strategy="environment")
        if (Path.home() / ".netrc").exists():
            return earthaccess.login(strategy="netrc")
    except Exception as e:
        raise RuntimeError(_EDL_MSG) from e
    raise RuntimeError(_EDL_MSG)


def _ingest():
    """The unit/ingest contract module, imported lazily + guarded."""
    try:
        from . import ingest

        return ingest
    except ImportError:
        pass
    try:
        import ingest  # script-context fallback (sys.path=pipeline/fetch)

        return ingest
    except ImportError as e:
        raise RuntimeError(
            "pipeline/fetch/ingest.py (unit/ingest) is not available — "
            "stage_to_gcs/ingest_asset/append_manifest are required on the "
            "download path. Use --no-download for search-only."
        ) from e


def resolve_target(
    event_id: str | None = None,
    case_id: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict:
    """Resolve {case, bbox, start, end} from --event and/or --case.

    --event supplies the AOI and default window (reference_date ..
    window.end) and derives the case key; --case supplies them from
    config.yaml (event_date .. event_date + window_after_days) or, when
    given with --event, overrides only the output case name.
    """
    if event_id:
        ev = load_event(event_id)
        bbox = list(ev["aoi"]["bbox"])
        window = (str(ev["reference_date"]), str(ev["window"]["end"]))
        case = case_id or case_key(event_id)
    elif case_id:
        c = config_case(case_id)
        bbox = list(c["aoi"])
        day0 = dt.date.fromisoformat(str(c["event_date"]))
        window = (
            day0.isoformat(),
            (day0 + dt.timedelta(days=int(c.get("window_after_days", 0))))
            .isoformat(),
        )
        case = case_id
    else:
        raise ValueError("resolve_target needs an event_id or a case_id")
    return {
        "case": case,
        "bbox": bbox,
        "start": start or window[0],
        "end": end or window[1],
    }


def _data_links(granule) -> list[str]:
    """HTTPS data links from a DataGranule (.data_links()) or a CMR dict."""
    fn = getattr(granule, "data_links", None)
    if callable(fn):
        try:
            return list(fn(access="external"))
        except TypeError:
            return list(fn())
    if not hasattr(granule, "get"):
        return []
    links = []
    for rel in (granule.get("umm", {}) or {}).get("RelatedUrls") or []:
        url = rel.get("URL") if hasattr(rel, "get") else None
        if url:
            links.append(url)
    for link in granule.get("links") or []:
        if isinstance(link, str):
            links.append(link)
        elif hasattr(link, "get") and link.get("href"):
            links.append(link["href"])
    return links


def wtr_link(granule) -> str | None:
    """Best WTR-band download link (B01_WTR preferred), or None."""
    cands = [
        link for link in _data_links(granule)
        if _wtr_rank(str(link).split("?")[0]) is not None
    ]
    return min(cands, key=lambda l: _wtr_rank(str(l).split("?")[0]),
               default=None)


def _opera_date(granule) -> str | None:
    """YYYY-MM-DD from the OPERA native id when TemporalExtent is absent."""
    native = hls_fetch._granule_id(granule) or ""
    m = _OPERA_DATE_RE.search(native)
    if m:
        return f"{m.group(1)[:4]}-{m.group(1)[4:6]}-{m.group(1)[6:]}"
    return None


def granule_record(granule, **extra) -> dict:
    """hls_fetch record plus the WTR link and any ingest fields."""
    rec = hls_fetch.granule_record(granule)
    rec["date"] = rec["date"] or _opera_date(granule)
    rec["wtr"] = wtr_link(granule)
    rec.update(extra)
    return rec


def asset_name(rec: dict) -> str:
    """opera_dswx_<yyyymmdd>; granule-derived fallback when date is null."""
    if rec.get("date"):
        return f"opera_dswx_{rec['date'].replace('-', '')}"
    slug = re.sub(r"[^0-9A-Za-z]+", "_", str(rec.get("granule") or "scene"))
    return f"opera_dswx_{slug.strip('_')}"


def asset_id_for(case: str, name: str) -> str:
    proj = os.environ.get("GEE_PROJECT")
    if not proj:
        raise RuntimeError(
            "GEE_PROJECT is not set; cannot build the EE asset id")
    return (
        f"projects/{proj}/assets/tipping-point/{case}/opera/{name}")


def _manifest_path(case: str) -> Path:
    return DATA_DIR / case / "assets.json"


def _manifest_assets(case: str) -> set:
    """asset_ids already ingested for this case (idempotent re-runs)."""
    try:
        doc = json.loads(_manifest_path(case).read_text())
    except (OSError, ValueError):
        return set()
    entries = doc.get("assets", doc) if isinstance(doc, dict) else doc
    return {
        e["asset_id"] for e in entries
        if isinstance(e, dict) and e.get("asset_id")
    }


def search_granules(target: dict, limit: int | None = None) -> list:
    """Unauthenticated CMR search over the case AOI/window, sorted.

    Least-cloudy first (missing cloud cover last), then date; `limit`
    keeps the N best granules.
    """
    import earthaccess

    granules = earthaccess.search_data(
        short_name=SHORT_NAME,
        bounding_box=tuple(target["bbox"]),
        temporal=(target["start"], target["end"]),
    )
    return hls_fetch.sort_granules(list(granules))[:limit]


def _download_wtr(granule, dl_dir: Path) -> Path:
    """Download just the WTR band file (whole granule if unlinked)."""
    import earthaccess

    link = wtr_link(granule)
    got = earthaccess.download([link] if link else [granule], str(dl_dir))
    paths = [Path(p) for p in got]
    ranked = [(r, p) for p in paths
              if (r := _wtr_rank(p.name)) is not None]
    if not ranked:
        raise RuntimeError(f"no WTR band file among downloaded: {paths}")
    return min(ranked, key=lambda rp: rp[0])[1]


def clip_to_aoi(src_path: Path, bbox: list, out_path: Path) -> Path:
    """Clip the WTR raster to the AOI bbox, write a COG (GTiff fallback)."""
    import rasterio
    from rasterio.mask import mask
    from rasterio.warp import transform_bounds

    with rasterio.open(src_path) as src:
        if src.crs and str(src.crs) != "EPSG:4326":
            w, s, e, n = transform_bounds("EPSG:4326", src.crs, *bbox)
        else:
            w, s, e, n = bbox
        geom = {"type": "Polygon", "coordinates": [
            [[w, s], [e, s], [e, n], [w, n], [w, s]]]}
        data, transform = mask(src, [geom], crop=True)
        profile = dict(src.profile)
    profile.update(
        driver="COG",
        height=data.shape[-2],
        width=data.shape[-1],
        count=data.shape[0] if data.ndim == 3 else 1,
        transform=transform,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        dst = rasterio.open(out_path, "w", **profile)
    except Exception:  # noqa: BLE001 — COG driver absent on old GDAL -> GTiff
        profile.update(driver="GTiff", tiled=True)
        dst = rasterio.open(out_path, "w", **profile)
    with dst:
        dst.write(data)
    return out_path


def fetch_scenes(
    target: dict,
    limit: int | None = None,
    download: bool = True,
    out_dir: Path | None = None,
    bucket: str | None = None,
) -> list[dict]:
    """Search + optionally download/clip/stage/ingest DSWx-HLS scenes.

    With download=False the records are metadata-only (local_path null)
    and no credentials, GCS bucket, GEE_PROJECT or ingest module are
    needed. With download=True each new granule (asset_id not already in
    data/<case>/assets.json) is downloaded, clipped to the AOI into
    <out_dir or data/raw/opera>/<case>/, staged to gs://$NOW_GCS_BUCKET and
    ingested as a GEE image asset before append_manifest records it.
    """
    granules = search_granules(target, limit)
    if not download:
        return [granule_record(g) for g in granules]

    case = target["case"]
    base = Path(out_dir or RAW_DIR) / case
    seen = _manifest_assets(case)
    bucket = bucket or os.environ.get("NOW_GCS_BUCKET")
    auth = None
    records = []
    for g in granules:
        rec = granule_record(g)
        name = asset_name(rec)
        asset_id = asset_id_for(case, name)  # raises if GEE_PROJECT unset
        cog = base / f"{name}.tif"
        if asset_id in seen:
            rec.update(asset_id=asset_id, status="exists",
                       local_path=str(cog) if cog.exists() else None)
            records.append(rec)
            continue
        if auth is None:
            auth = _edl_login()
        if not bucket:
            raise RuntimeError(
                "NOW_GCS_BUCKET is not set; required to stage the COG to "
                "GCS (or use --no-download for search-only)")
        src = _download_wtr(g, base / "_dl" / name)
        clip_to_aoi(src, target["bbox"], cog)
        ingest = _ingest()
        gcs_uri = ingest.stage_to_gcs(
            str(cog), bucket=bucket, case=case, name=name)
        result = ingest.ingest_asset(
            gcs_uri, asset_id=asset_id, kind="image")
        entry = {
            "name": name,
            "asset_id": asset_id,
            "gcs_uri": gcs_uri,
            "kind": "image",
            "case": case,
            "date": rec["date"],
            "granule": rec["granule"],
            "cloud_cover": rec["cloud_cover"],
            "local_path": str(cog),
            "result": result,
        }
        ingest.append_manifest(case, entry)
        rec.update(asset_id=asset_id, gcs_uri=gcs_uri,
                   local_path=str(cog), status="ingested")
        records.append(rec)
    return records


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(
        prog="pipeline.fetch.opera",
        description="Fetch OPERA DSWx-HLS WTR scenes for a case and "
                    "stage+ingest them as GEE image assets.")
    ap.add_argument("--event", help="event id, e.g. rs-2024-05 "
                    "(aoi + window from events/*.json)")
    ap.add_argument("--case", dest="case_id", help="config.yaml case id, "
                    "e.g. rs_2024 (or override the event's case name)")
    ap.add_argument("--start", help="window start YYYY-MM-DD")
    ap.add_argument("--end", help="window end YYYY-MM-DD")
    ap.add_argument("--limit", type=int,
                    help="keep the N least-cloudy scenes")
    ap.add_argument("--no-download", action="store_true",
                    help="search only — no auth needed, records stay "
                    "metadata-only")
    ap.add_argument("--dry-run", action="store_true",
                    help="print resolved search params and exit")
    args = ap.parse_args(argv)
    if not (args.event or args.case_id):
        ap.error("give --event <id> or --case <id>")

    target = resolve_target(
        args.event, args.case_id, args.start, args.end)

    if args.dry_run:
        print(json.dumps({
            "case": target["case"],
            "short_name": SHORT_NAME,
            "bounding_box": target["bbox"],
            "temporal": [target["start"], target["end"]],
            "limit": args.limit,
            "download": not args.no_download,
            "out_dir": str(RAW_DIR / target["case"]),
            "asset_root": "projects/$GEE_PROJECT/assets/tipping-point/"
                          f"{target['case']}/opera",
        }, indent=2))
        return

    records = fetch_scenes(
        target, limit=args.limit, download=not args.no_download)
    print(json.dumps(records, indent=2))


if __name__ == "__main__":
    main()
