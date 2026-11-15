"""WA 2025 SWOT river water-surface-elevation series (chart input).

Queries PO.DAAC Hydrocron per SWORD reach in events/wa-2025-12.json
(swot.reach_ids) over the event window and writes
data/wa_2025/series/swot.json. Fill values (wse <= -999999, 'no_data'
times) are dropped and counted per reach; a reach Hydrocron has nothing
for is kept with series [] and a _null_reasons entry. Never invents values.

    PYTHONPATH=.:pipeline uv run --no-sync python tools/wa_swot_series.py
"""
from __future__ import annotations

import csv
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parents[1]
EVENT = REPO / "events" / "wa-2025-12.json"
OUT = REPO / "data" / "wa_2025" / "series" / "swot.json"
HYDROCRON_URL = "https://soto.podaac.earthdatacloud.nasa.gov/hydrocron/v1/timeseries"
FIELDS = "reach_id,time_str,wse,wse_u,reach_q,river_name,p_lon,p_lat"
FILL = -999999.0


def fetch(reach_id: str, start: str, end: str) -> tuple[list[dict] | None, str | None]:
    """Raw Hydrocron rows for one reach, or (None, reason)."""
    try:
        r = requests.get(HYDROCRON_URL, params={
            "feature": "Reach", "feature_id": reach_id,
            "start_time": f"{start}T00:00:00Z", "end_time": f"{end}T23:59:59Z",
            "output": "csv", "fields": FIELDS,
        }, timeout=180)
    except requests.RequestException as e:
        return None, f"Hydrocron request failed: {type(e).__name__}"
    if r.status_code != 200:
        try:
            msg = r.json().get("error") or r.text[:200]
        except ValueError:
            msg = r.text[:200]
        return None, f"Hydrocron HTTP {r.status_code}: {msg}"
    text = (r.json().get("results") or {}).get("csv") or ""
    rows = list(csv.DictReader(io.StringIO(text)))
    return rows, None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def to_reach(reach_id: str, rows: list[dict], start: str, end: str) -> dict:
    lo = datetime.fromisoformat(f"{start}T00:00:00+00:00")
    hi = datetime.fromisoformat(f"{end}T23:59:59+00:00")
    series, dropped, out_of_window = [], 0, 0
    name = lon = lat = None
    for row in rows:
        name = name or (row.get("river_name") or None)
        lon = lon if lon is not None else _num(row.get("p_lon"))
        lat = lat if lat is not None else _num(row.get("p_lat"))
        wse, t = _num(row.get("wse")), row.get("time_str") or ""
        try:
            ts = datetime.fromisoformat(t.replace("Z", "+00:00"))
        except ValueError:
            ts = None
        if wse is None or wse <= FILL or ts is None:
            dropped += 1
            continue
        if not lo <= ts <= hi:
            out_of_window += 1
            continue
        wse_u = _num(row.get("wse_u"))
        q = _num(row.get("reach_q"))
        series.append({
            "time": ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "wse_m": round(wse, 4),
            "wse_u_m": round(wse_u, 5) if wse_u is not None and wse_u > FILL else None,
            "quality": int(q) if q is not None else None,
        })
    series.sort(key=lambda s: s["time"])
    return {"reach_id": reach_id, "name": name, "lon": lon, "lat": lat,
            "series": series, "dropped_fill": dropped,
            "dropped_out_of_window": out_of_window}


def build(event: dict, fetcher=fetch) -> dict:
    start, end = event["window"]["start"], event["window"]["end"]
    reaches, reasons = [], {}
    for rid in (event.get("swot") or {}).get("reach_ids") or []:
        rows, err = fetcher(rid, start, end)
        if err:
            reach = {"reach_id": rid, "name": None, "lon": None, "lat": None,
                     "series": [], "dropped_fill": 0, "dropped_out_of_window": 0}
            reasons[rid] = err
        else:
            reach = to_reach(rid, rows, start, end)
            if not reach["series"]:
                reasons[rid] = (f"no valid SWOT pass in window "
                                f"({reach['dropped_fill']} fill rows dropped)")
        reaches.append(reach)
    if not reaches:
        reasons["reaches"] = "no reach ids in events/wa-2025-12.json swot.reach_ids"
    elif not any(r["series"] for r in reaches):
        reasons["reaches"] = "Hydrocron returned no valid observations for any reach"
    return {
        "case": "wa_2025",
        "source": "SWOT L2 HR RiverSP via PO.DAAC Hydrocron",
        "window": [start, end],
        "event_dates": {"start": event.get("start_date"),
                        "peak": event.get("reference_date")},
        "quality_legend": {"0": "good", "1": "suspect", "2": "degraded", "3": "bad"},
        "reaches": reaches,
        "_null_reasons": reasons,
    }


def main() -> int:
    doc = build(json.loads(EVENT.read_text()))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, indent=1) + "\n")
    for r in doc["reaches"]:
        qs = {}
        for s in r["series"]:
            qs[s["quality"]] = qs.get(s["quality"], 0) + 1
        print(f"{r['reach_id']} {r['name']}: {len(r['series'])} obs, "
              f"dropped fill={r['dropped_fill']} oow={r['dropped_out_of_window']}, "
              f"quality={dict(sorted(qs.items()))}")
    print(f"wrote {OUT.relative_to(REPO)}; null reasons: {doc['_null_reasons']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
