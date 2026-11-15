"""DEPRECATED STUB — retired (L10b).

OPERA DSWx is already in the Earth Engine catalog
(OPERA/DSWX/L3_V1/HLS|S1); no fetch or ingest step is needed. The real
fetcher was removed by ruling L10b; this stub remains only so
`python -m pipeline.fetch.opera` exits 0 until live/jobs.py drops the
step (lead-live owns that swap).
"""
import sys

RETIRED_MESSAGE = (
    "retired (L10b): OPERA DSWx is in the EE catalog "
    "(OPERA/DSWX/L3_V1/HLS|S1); no fetch needed"
)


def main(argv=None) -> None:
    print(RETIRED_MESSAGE)
    sys.exit(0)


if __name__ == "__main__":
    main()


# --- L27 response: OPERA DSWx-S1 near-real-time search + WTR download -------
# The EE catalog copy lags; the Now response path reads PO.DAAC directly.
# CMR search is anonymous; WTR downloads use the EARTHDATA_TOKEN bearer
# (fetch.edl). Never log the token or the Authorization header.

CMR_GRANULES = "https://cmr.earthdata.nasa.gov/search/granules.umm_json"
DSWX_S1_SHORT_NAME = "OPERA_L3_DSWX-S1_V1"


def _granule_record(item: dict) -> dict | None:
    """{id, acquired, available, wtr_url} from one CMR umm_json item (or None)."""
    umm = item.get("umm") or {}
    wtr = next(
        (u["URL"] for u in umm.get("RelatedUrls") or []
         if u.get("Type") == "GET DATA" and str(u.get("URL", "")).startswith("https://")
         and u["URL"].endswith("_B01_WTR.tif")),
        None,
    )
    rng = (umm.get("TemporalExtent") or {}).get("RangeDateTime") or {}
    if not wtr or not rng.get("BeginningDateTime"):
        return None
    insert = next(
        (d["Date"] for d in umm.get("ProviderDates") or [] if d.get("Type") == "Insert"),
        (item.get("meta") or {}).get("revision-date"),
    )
    return {
        "id": umm.get("GranuleUR") or (item.get("meta") or {}).get("native-id"),
        "acquired": rng["BeginningDateTime"],
        "available": insert,
        "wtr_url": wtr,
    }


def search_dswx_s1(
    bbox: tuple[float, float, float, float],
    start: str,
    end: str | None = None,
    session=None,
    page_size: int = 2000,
    short_name: str = DSWX_S1_SHORT_NAME,
) -> list[dict]:
    """All DSWx-S1 granules over bbox acquired in [start, end], oldest first.

    Paginates with CMR-Search-After. start/end are ISO UTC strings.
    """
    import requests

    s = session or requests.Session()
    params = {
        "short_name": short_name,
        "bounding_box": ",".join(str(v) for v in bbox),
        "temporal": f"{start},{end or ''}",
        "page_size": page_size,
        "sort_key": "start_date",
    }
    out, headers = [], {}
    while True:
        r = s.get(CMR_GRANULES, params=params, headers=headers, timeout=60)
        r.raise_for_status()
        items = r.json().get("items") or []
        out.extend(rec for rec in map(_granule_record, items) if rec)
        after = r.headers.get("CMR-Search-After")
        if not items or not after or len(items) < page_size:
            break
        headers = {"CMR-Search-After": after}
    out.sort(key=lambda g: (g["acquired"], g["id"]))
    return out


def download_wtr(url: str, session=None, retries: int = 3, timeout: int = 120) -> bytes:
    """Bytes of one WTR GeoTIFF (bearer auth). Raises RuntimeError after retries.

    Error messages carry the HTTP status only — never headers or the token.
    """
    import time

    import requests

    from fetch.edl import edl_auth_headers

    s = session or requests.Session()
    last = "no attempt"
    for attempt in range(retries):
        try:
            r = s.get(url, headers=edl_auth_headers(), timeout=timeout, allow_redirects=True)
            if r.status_code == 200 and r.content[:2] in (b"II", b"MM"):
                return r.content
            last = f"HTTP {r.status_code}"
            if r.status_code in (401, 403):
                break
        except requests.RequestException as e:
            last = type(e).__name__
        time.sleep(2 ** attempt)
    raise RuntimeError(f"WTR download failed ({last}): {url.rsplit('/', 1)[-1]}")
