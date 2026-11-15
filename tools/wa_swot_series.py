"""SWOT river water-surface-elevation series (chart input), per case.

Queries PO.DAAC Hydrocron per SWORD reach in events/<event>.json
(swot.reach_ids) over the chart window and writes
data/<case>/series/swot.json. Fill values (wse <= -999999, 'no_data'
times) are dropped and counted per reach; a reach Hydrocron has nothing
for is kept with series [] and a _null_reasons entry. Never invents values.

The chart window and event dates come from the event's swot.window /
swot.event_dates when present, else event.window and
start_date/reference_date. rs_2024 pins a wider 2024-04-01..2024-06-30
window (pre-flood April baseline through the June recession) with event
start 2024-04-29 and the Guaíba/Jacuí crest 2024-05-05.

    PYTHONPATH=.:pipeline uv run --no-sync python tools/wa_swot_series.py --case wa_2025
    PYTHONPATH=.:pipeline uv run --no-sync python tools/wa_swot_series.py --case rs_2024
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parents[1]
CASES = {"rs_2024": "rs-2024-05", "wa_2025": "wa-2025-12"}
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
        rn = row.get("river_name")
        name = name or (rn if rn and rn != "no_data" else None)
        lon = lon if lon is not None else _num(row.get("p_lon"))
        lat = lat if lat is not None else _num(row.get("p_lat"))
        wse, t = _num(row.get("wse")), row.get("time_str") or ""
        try:
            ts = datetime.fromisoformat(t)
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
            "time": ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "wse_m": round(wse, 4),
            "wse_u_m": round(wse_u, 5) if wse_u is not None and wse_u > FILL else None,
            "quality": int(q) if q is not None and q > FILL else None,
        })
    series.sort(key=lambda s: s["time"])
    return {"reach_id": reach_id, "name": name, "lon": lon, "lat": lat,
            "series": series, "dropped_fill": dropped,
            "dropped_out_of_window": out_of_window}


def build(case_id: str, event: dict, fetcher=fetch) -> dict:
    swot = event.get("swot") or {}
    win = swot.get("window") or event["window"]
    start, end = win["start"], win["end"]
    event_dates = swot.get("event_dates") or {
        "start": event.get("start_date"),
        "peak": event.get("reference_date"),
    }
    reaches, reasons = [], {}
    for rid in swot.get("reach_ids") or []:
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
        reasons["reaches"] = (f"no reach ids in events/{event.get('id', '?')}.json "
                              f"swot.reach_ids")
    elif not any(r["series"] for r in reaches):
        reasons["reaches"] = "Hydrocron returned no valid observations for any reach"
    return {
        "case": case_id,
        "source": "SWOT L2 HR RiverSP via PO.DAAC Hydrocron",
        "window": [start, end],
        "event_dates": event_dates,
        "quality_legend": {"0": "good", "1": "suspect", "2": "degraded", "3": "bad"},
        "reaches": reaches,
        "_null_reasons": reasons,
    }


def _peak_rise(reach: dict, event_start: str | None) -> float | None:
    """Max WSE from event start onward minus the pre-event median (m)."""
    if not event_start or not reach["series"]:
        return None
    pre = [s["wse_m"] for s in reach["series"] if s["time"] < event_start]
    post = [s["wse_m"] for s in reach["series"] if s["time"] >= event_start]
    if not pre or not post:
        return None
    return round(max(post) - statistics.median(pre), 3)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--case", choices=sorted(CASES), required=True)
    args = ap.parse_args(argv)
    event = json.loads((REPO / "events" / f"{CASES[args.case]}.json").read_text())
    doc = build(args.case, event)
    out = REPO / "data" / args.case / "series" / "swot.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1) + "\n")
    for r in doc["reaches"]:
        qs = {}
        for s in r["series"]:
            qs[s["quality"]] = qs.get(s["quality"], 0) + 1
        rise = _peak_rise(r, doc["event_dates"].get("start"))
        rise_s = f", peak rise {rise:+.2f} m" if rise is not None else ""
        print(f"{r['reach_id']} {r['name']}: {len(r['series'])} obs, "
              f"dropped fill={r['dropped_fill']} oow={r['dropped_out_of_window']}, "
              f"quality={dict(sorted(qs.items()))}{rise_s}")
    print(f"wrote {out.relative_to(REPO)}; null reasons: {doc['_null_reasons']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
