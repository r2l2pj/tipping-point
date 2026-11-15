"""IMERG Final daily vs INMET station gauges — rs_2024 validation (G6).

Ruling L19: INMET stations are a VALIDATION check of IMERG, never an index
input. This tool pairs each station's gauge daily rain (UTC 00-24, summed
from the INMET hourly archive) with the NASA IMERG V07 daily *Final* cell
(GPM_3IMERGDF, the 0.1 deg cell containing the station) over the case
window and emits per-station correlation, bias and event-total ratios.

IMERG Final daily is fetched per day via the GES DISC OPeNDAP ascii subset
(`3B-DAY.MS.MRG.3IMERG.<YYYYMMDD>-S000000-E235959.V07B.nc4.ascii?
precipitation[0][i0:i1][j0:j1]`, EARTHDATA_TOKEN bearer) and cached as one
.npy per day under --cache-dir (gitignored data/raw/). The IMERG day runs
UTC 00-24; the INMET CSV 'Data' column is already UTC, so gauge days are
comparable without a timezone shift. A station-day is skipped when more
than --max-missing-hours of its 24 hourly precip values are missing.

    PYTHONPATH=.:pipeline .venv/bin/python tools/imerg_vs_gauge.py \
        --case rs_2024 --zip /tmp/inmet2024.zip \
        [--start 2024-04-20] [--end 2024-05-15] [--no-metrics]

Outputs: data/<case>/series/imerg_vs_gauge.json (daily pairs) and the
`imerg_vs_gauge` key merged into data/metrics.json via
tp.metrics.merge_metrics.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import os
import re
import statistics
import sys
import time
import zipfile
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
for _p in (str(REPO), str(REPO / "pipeline")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fetch.inmet import ARCHIVE_URL, PRECIP_COL, _num

IMERG_BASE = "https://gpm1.gesdisc.eosdis.nasa.gov/opendap/GPM_L3/GPM_3IMERGDF.07"
IMERG_PRODUCT = "GPM_3IMERGDF V07 daily Final (3B-DAY.MS.MRG.3IMERG.*.V07B)"
IMERG_RES = 0.1                     # deg; cell index = floor((lon+180)/0.1)
FILE_REVS = ("V07B", "V07C", "V07A")  # tried in order per day
RETRIES = 6
EVENT_TOTAL_WINDOW = ("2024-04-27", "2024-05-06")
CAVEAT = "point gauge vs 0.1 deg cell"
SOURCE = ("GES DISC OPeNDAP IMERG Final daily subset at station cells + "
          "INMET historical archive hourly gauges (validation-only, L19)")
STATION_MEMBER_RE = re.compile(r"INMET_S_\w+_([A-Z]\d{3})_")


def cell_index(lon: float, lat: float) -> tuple[int, int]:
    """Global IMERG 0.1 deg grid indices (i=lon, j=lat) holding the point."""
    return (int(np.floor((lon + 180.0) / IMERG_RES)),
            int(np.floor((lat + 90.0) / IMERG_RES)))


def cell_center(i: int, j: int) -> tuple[float, float]:
    return (-180.0 + (i + 0.5) * IMERG_RES, -90.0 + (j + 0.5) * IMERG_RES)


# --------------------------------------------------------------------------- #
# gauge side: INMET hourly -> UTC daily mm + valid-hour count
# --------------------------------------------------------------------------- #
def parse_station_hours(text: str) -> dict[str, dict]:
    """One station-year CSV -> {YYYY-MM-DD: {"mm": x, "hours": n}}.

    `hours` counts rows with a parseable precip value that UTC date; a day
    with >2 missing hours is unreliable and skipped downstream.
    """
    body = io.StringIO()
    header_done = False
    for line in text.splitlines():
        if not header_done:
            if line.startswith("Data"):
                body.write(line + "\n")
                header_done = True
        else:
            body.write(line + "\n")
    body.seek(0)
    days: dict[str, dict] = {}
    for row in csv.DictReader(body, delimiter=";"):
        date = (row.get("Data") or "").strip().replace("/", "-")
        parts = date.split("-")
        if len(parts) != 3:
            continue
        if len(parts[0]) == 2:  # DD-MM-YYYY -> YYYY-MM-DD
            date = f"{parts[2]}-{parts[1]}-{parts[0]}"
        mm = _num(row.get(PRECIP_COL, ""))
        rec = days.setdefault(date, {"mm": 0.0, "hours": 0})
        if mm is not None:
            rec["mm"] += mm
            rec["hours"] += 1
    return days


def gauge_daily(zip_path: Path, station_ids: set[str],
                start: str, end: str) -> dict[str, dict[str, dict]]:
    """{station_id: {date: {mm, hours}}} over start..end from the INMET zip.

    Only members whose file name carries a wanted station code are parsed
    (the ~100 MB yearly archive holds ~565 station CSVs).
    """
    out: dict[str, dict[str, dict]] = {s: {} for s in station_ids}
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            if not name.upper().endswith(".CSV"):
                continue
            m = STATION_MEMBER_RE.search(name)
            if not m or m.group(1) not in station_ids:
                continue
            days = parse_station_hours(zf.read(name).decode("latin-1"))
            out[m.group(1)] = {
                d: {"mm": round(v["mm"], 1), "hours": v["hours"]}
                for d, v in sorted(days.items()) if start <= d <= end}
    return out


# --------------------------------------------------------------------------- #
# IMERG side: GES DISC OPeNDAP ascii subset, one file per UTC day
# --------------------------------------------------------------------------- #
def _ascii_url(day: dt.date, rev: str, i0: int, i1: int,
               j0: int, j1: int) -> str:
    return (f"{IMERG_BASE}/{day:%Y}/{day:%m}/"
            f"3B-DAY.MS.MRG.3IMERG.{day:%Y%m%d}-S000000-E235959.{rev}.nc4.ascii"
            f"?precipitation[0][{i0}:{i1}][{j0}:{j1}]")


def parse_ascii(text: str, n_i: int, n_j: int) -> np.ndarray:
    """OPeNDAP ascii -> float32 array [i=lon, j=lat] of daily mm.

    Each `precipitation.precipitation[...][lon=x], v0, v1, ...` line is one
    lon column holding the lat values in subset order.
    """
    rows = [ln.split(",", 1)[1] for ln in text.splitlines()
            if ln.startswith("precipitation.precipitation[")]
    arr = np.array([[float(v) for v in row.split(",")] for row in rows],
                   dtype=np.float32)
    if arr.shape != (n_i, n_j):
        raise ValueError(f"ascii grid {arr.shape} != expected {(n_i, n_j)}")
    return arr


def fetch_imerg_day(day: dt.date, i0: int, i1: int, j0: int, j1: int,
                    cache: Path, token: str) -> np.ndarray:
    """One UTC day's IMERG Final grid subset; .npy-cached under `cache`."""
    f = cache / f"{day:%Y%m%d}.npy"
    if f.exists():
        return np.load(f)
    import requests

    err = None
    for rev in FILE_REVS:
        url = _ascii_url(day, rev, i0, i1, j0, j1)
        r = None
        for attempt in range(RETRIES):
            try:
                r = requests.get(url, timeout=120, headers={
                    "Authorization": f"Bearer {token}"})
                if r.status_code == 200 and "precipitation" in r.text:
                    arr = parse_ascii(r.text, i1 - i0 + 1, j1 - j0 + 1)
                    cache.mkdir(parents=True, exist_ok=True)
                    np.save(f, arr)
                    return arr
                err = f"HTTP {r.status_code}"
            except (requests.RequestException, ValueError) as e:
                err = str(e)
            time.sleep(2 * (attempt + 1))
        if r is not None and r.status_code == 404:  # try the next revision
            continue
        break
    raise RuntimeError(f"IMERG Final {day}: fetch failed ({err})")


def fetch_imerg_window(start: dt.date, end: dt.date, i0: int, i1: int,
                       j0: int, j1: int, cache: Path, token: str
                       ) -> dict[str, np.ndarray]:
    grids = {}
    day = start
    while day <= end:
        grids[day.isoformat()] = fetch_imerg_day(
            day, i0, i1, j0, j1, cache, token)
        day += dt.timedelta(days=1)
    return grids


# --------------------------------------------------------------------------- #
# stats
# --------------------------------------------------------------------------- #
def pearson(xs: list[float], ys: list[float]) -> float | None:
    """Pearson r; None under 3 pairs or zero variance."""
    if len(xs) < 3:
        return None
    x, y = np.asarray(xs, float), np.asarray(ys, float)
    if x.std() == 0.0 or y.std() == 0.0:
        return None
    return float(np.corrcoef(x, y)[0, 1])


def pair_days(gauge: dict[str, dict], dates: list[str], imerg_val,
              max_missing_hours: int) -> tuple[list[dict], list[dict]]:
    """Join gauge and IMERG daily values into (pairs, skipped).

    pairs: {date, gauge_mm, gauge_hours, imerg_mm} — both sides present and
    the gauge day has <= max_missing_hours missing. skipped: {date, reason}
    covers every window date not paired (absent gauge day, too many missing
    hours, no IMERG value). `imerg_val(date_str)` -> float | None.
    """
    pairs, skipped = [], []
    for date in dates:
        rec = gauge.get(date)
        if rec is None:
            skipped.append({"date": date, "reason": "no gauge data"})
            continue
        missing = 24 - rec["hours"]
        if missing > max_missing_hours:
            skipped.append({"date": date,
                            "reason": f"{missing} missing gauge hours"})
            continue
        iv = imerg_val(date)
        if iv is None or not np.isfinite(iv):
            skipped.append({"date": date, "reason": "no IMERG value"})
            continue
        pairs.append({"date": date, "gauge_mm": rec["mm"],
                      "gauge_hours": rec["hours"],
                      "imerg_mm": round(float(iv), 2)})
    return pairs, skipped


def station_stats(pairs: list[dict],
                  event_window: tuple[str, str] = EVENT_TOTAL_WINDOW) -> dict:
    """Per-station metrics from the paired daily list.

    bias_pct = 100*(sum(imerg) - sum(gauge))/sum(gauge) over paired days.
    event totals sum the paired days inside event_window (Apr 27 - May 6).
    """
    g = [p["gauge_mm"] for p in pairs]
    i = [p["imerg_mm"] for p in pairs]
    tg, ti = sum(g), sum(i)
    ev = [p for p in pairs if event_window[0] <= p["date"] <= event_window[1]]
    eg = sum(p["gauge_mm"] for p in ev)
    ei = sum(p["imerg_mm"] for p in ev)
    r = pearson(g, i)
    out = {
        "n_days": len(pairs),
        "r": round(r, 4) if r is not None else None,
        "bias_pct": round(100.0 * (ti - tg) / tg, 1) if tg > 0 else None,
        "event_total_gauge_mm": round(eg, 1),
        "event_total_imerg_mm": round(ei, 1),
        "event_total_ratio": round(ei / eg, 3) if eg > 0 else None,
    }
    reasons = {}
    if out["r"] is None:
        reasons["r"] = ("<3 paired days or zero variance"
                        if len(pairs) < 3 else "zero variance")
    if out["bias_pct"] is None:
        reasons["bias_pct"] = "zero gauge total in window"
    if out["event_total_ratio"] is None:
        reasons["event_total_ratio"] = "zero gauge total in event window"
    if reasons:
        out["stat_reasons"] = reasons
    return out


def median_or_none(vals) -> float | None:
    vals = [v for v in vals if v is not None]
    return round(float(statistics.median(vals)), 4) if vals else None


def build_metrics(stations_out: list[dict], start: str, end: str) -> dict:
    """The flat `imerg_vs_gauge` value merged into metrics.json."""
    return {
        "source": SOURCE,
        "imerg_product": IMERG_PRODUCT,
        "window": f"{start}..{end}",
        "n_stations": len(stations_out),
        "n_station_days": sum(s["n_days"] for s in stations_out),
        "pearson_r_median": median_or_none([s["r"] for s in stations_out]),
        "bias_pct_median": median_or_none(
            [s["bias_pct"] for s in stations_out]),
        "event_total_ratio_median": median_or_none(
            [s["event_total_ratio"] for s in stations_out]),
        "caveat": CAVEAT,
        "stations": [{
            "id": s["id"], "name": s["name"], "lat": s["lat"],
            "lon": s["lon"], "n_days": s["n_days"], "r": s["r"],
            "bias_pct": s["bias_pct"],
            "event_total_gauge_mm": s["event_total_gauge_mm"],
            "event_total_imerg_mm": s["event_total_imerg_mm"],
        } for s in stations_out],
    }


# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--case", default="rs_2024")
    ap.add_argument("--stations",
                    default="data/rs_2024/series/inmet_stations.json")
    ap.add_argument("--zip", default=None,
                    help="INMET yearly archive (local path or portal URL; "
                         "default: portal archive for --start's year). "
                         "Without it, committed station daily sums are used "
                         "and missing-hour filtering cannot be applied.")
    ap.add_argument("--start", default="2024-04-20")
    ap.add_argument("--end", default="2024-05-15")
    ap.add_argument("--event-window", default="2024-04-27:2024-05-06")
    ap.add_argument("--max-missing-hours", type=int, default=2)
    ap.add_argument("--cache-dir", default="data/raw/imerg_vs_gauge")
    ap.add_argument("--out", default=None,
                    help="default data/<case>/series/imerg_vs_gauge.json")
    ap.add_argument("--metrics", dest="metrics", action="store_true",
                    default=True)
    ap.add_argument("--no-metrics", dest="metrics", action="store_false")
    args = ap.parse_args(argv)

    out_path = Path(args.out or
                    f"data/{args.case}/series/imerg_vs_gauge.json")
    ev_start, ev_end = args.event_window.split(":")

    doc = json.loads(Path(args.stations).read_text())
    stations = doc["stations"]
    station_ids = {s["id"] for s in stations}

    zip_src = args.zip or ARCHIVE_URL.format(year=args.start[:4])
    if Path(zip_src).exists():
        gauges = gauge_daily(Path(zip_src), station_ids,
                             args.start, args.end)
        gauge_source = str(zip_src)
    elif zip_src.startswith("http"):
        import tempfile

        from fetch.inmet import _download
        with tempfile.NamedTemporaryFile(suffix=".zip") as tmp:
            _download(zip_src, Path(tmp.name))
            gauges = gauge_daily(Path(tmp.name), station_ids,
                                 args.start, args.end)
        gauge_source = zip_src
    else:  # no archive -> committed daily sums, no hour filter possible
        gauges = {s["id"]: {d: {"mm": v, "hours": 24}
                            for d, v in s.get("daily", {}).items()
                            if args.start <= d <= args.end}
                  for s in stations}
        gauge_source = f"{args.stations} (daily sums; no hour counts)"

    cells = {s["id"]: cell_index(s["lon"], s["lat"]) for s in stations}
    i0 = min(c[0] for c in cells.values())
    i1 = max(c[0] for c in cells.values())
    j0 = min(c[1] for c in cells.values())
    j1 = max(c[1] for c in cells.values())

    token = os.environ.get("EARTHDATA_TOKEN")
    if not token:
        ap.error("EARTHDATA_TOKEN not set (GES DISC OPeNDAP bearer)")
    grids = fetch_imerg_window(
        dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end),
        i0, i1, j0, j1, Path(args.cache_dir), token)

    dates = [(dt.date.fromisoformat(args.start)
              + dt.timedelta(days=k)).isoformat()
             for k in range(
                 (dt.date.fromisoformat(args.end)
                  - dt.date.fromisoformat(args.start)).days + 1)]
    stations_out = []
    for s in stations:
        i, j = cells[s["id"]]
        pairs, skipped = pair_days(
            gauges.get(s["id"], {}), dates,
            lambda d, i=i, j=j: (float(grids[d][i - i0, j - j0])
                                 if d in grids else None),
            args.max_missing_hours)
        st = station_stats(pairs, (ev_start, ev_end))
        clon, clat = cell_center(i, j)
        stations_out.append({
            "id": s["id"], "name": s["name"], "lat": s["lat"],
            "lon": s["lon"],
            "imerg_cell": {"i": i, "j": j, "lon": round(clon, 2),
                           "lat": round(clat, 2)},
            **st, "skipped": skipped, "days": pairs})

    series_doc = {
        "source": SOURCE,
        "gauge_source": gauge_source,
        "imerg_product": IMERG_PRODUCT,
        "window": {"start": args.start, "end": args.end},
        "event_total_window": {"start": ev_start, "end": ev_end},
        "max_missing_hours": args.max_missing_hours,
        "caveat": CAVEAT,
        "generated_utc": dt.datetime.now(dt.UTC)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stations": stations_out,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(series_doc, ensure_ascii=False,
                                   indent=1) + "\n")

    flat = build_metrics(stations_out, args.start, args.end)
    reasons = {}
    for key in ("pearson_r_median", "bias_pct_median",
                "event_total_ratio_median"):
        if flat[key] is None:
            reasons[key] = "no station produced this stat"
    if args.metrics:
        from tp.metrics import merge_metrics
        merge_metrics(args.case, {"imerg_vs_gauge": flat},
                      reasons={} if flat else
                      {"imerg_vs_gauge": "no station pairs"})
    print(f"{len(stations_out)} stations, "
          f"{flat['n_station_days']} station-days -> {out_path}")
    print(json.dumps({k: flat[k] for k in (
        "pearson_r_median", "bias_pct_median",
        "event_total_ratio_median")}, indent=1))
    if reasons:
        print("null stats:", reasons, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
