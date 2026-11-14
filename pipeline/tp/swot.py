"""River level series from SWOT via the Hydrocron API (PO.DAAC).

Find reach_id for the river segment in the SWOT viewer / SWORD database.
SWOT revisits every ~21 days — confirm an overpass in the event window.
The API is public (no auth). Output records carry river_m (water surface
elevation, meters) keyed by time_str, ready to merge into the
imerg_smap_daily series.
"""
from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import requests

from tp.events import artifact_stem, event_data_dir

HYDROCRON_URL = "https://soto.podaac.earthdatacloud.nasa.gov/hydrocron/v1/timeseries"


def resolve_reach_ids(cli_ids: list[str] | None, event: dict) -> list[str]:
    """--reach-id values win; else event['swot']['reach_ids']."""
    ids = cli_ids or (event.get("swot") or {}).get("reach_ids") or []
    if not ids:
        raise ValueError(
            f"no SWOT reach ids: pass --reach-id or add "
            f"'swot': {{'reach_ids': [...]}} to events/{event.get('id', '?')}.json"
        )
    return list(ids)


def series_path(event_id: str) -> Path:
    """data/<case>/series/<caso>_swot_<AAAAMMDD>.json (doc v4 naming rule)."""
    return event_data_dir(event_id) / "series" / f"{artifact_stem(event_id, 'swot')}.json"


def fetch_reach(reach_id: str, start: str, end: str) -> pd.DataFrame:
    """One reach -> df(reach_id, time_str, river_m); fill values dropped."""
    r = requests.get(
        HYDROCRON_URL,
        params={
            "feature": "Reach",
            "feature_id": reach_id,
            "start_time": f"{start}T00:00:00Z",
            "end_time": f"{end}T00:00:00Z",
            "output": "csv",
            "fields": "reach_id,time_str,wse",
        },
        timeout=60,
    )
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.json()["results"]["csv"]))
    df = df[df["wse"] > -1e9]  # drop fill values
    return df.rename(columns={"wse": "river_m"})


def river_series(
    reach_ids: list[str], start: str, end: str, out_path: Path
) -> pd.DataFrame:
    """Fetch each reach, merge sorted by time_str, write records JSON."""
    df = (
        pd.concat([fetch_reach(rid, start, end) for rid in reach_ids],
                  ignore_index=True)
        .sort_values("time_str", ignore_index=True)
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_json(out_path, orient="records")
    return df
