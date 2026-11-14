"""River level series from SWOT via the Hydrocron API (PO.DAAC).

Find reach_id for the river segment in the SWOT viewer / SWORD database.
SWOT revisits every ~21 days — confirm an overpass in the event window.
"""
from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import requests

HYDROCRON_URL = "https://soto.podaac.earthdatacloud.nasa.gov/hydrocron/v1/timeseries"


def river_series(reach_id: str, start: str, end: str, out_path: Path) -> pd.DataFrame:
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
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_json(out_path, orient="records")
    return df
