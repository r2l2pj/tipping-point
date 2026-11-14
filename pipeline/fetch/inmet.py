"""INMET automatic-station daily precipitation -> validation series JSON.

Source: the INMET historical CSV archive yearly zip
(https://portal.inmet.gov.br/uploads/dadoshistoricos/<year>.zip, linked from
portal.inmet.gov.br/dadoshistoricos). The zip holds one latin-1 ';' CSV per
station-year; each file carries an 8-line metadata header (station code,
name, lat/lon as comma decimals) followed by hourly rows with
'PRECIPITAÇÃO TOTAL, HORÁRIO (mm)'. Hourly values are summed per station
into daily mm keyed by the CSV's own UTC 'Data' date.

The apitempo.inmet.gov.br API was considered but only retains ~1 year of
data, so 2024 dates return empty — hence the archive. The zip is ~100 MB
(all of Brazil); it is streamed to a temp file and only in-bbox RS station
members are parsed.

    uv run python -m pipeline.fetch.inmet --event rs-2024-05 \
        --start 2024-04-20 --end 2024-05-10 \
        [--zip PATH_OR_URL] [--out data/rs_2024/series/inmet_stations.json]

--zip accepts a local file path (tests/offline) or the portal URL.
License: Brazilian government open data per Lei de Acesso à Informação
(12.527/2011); INMET publishes the archive for public reuse with
attribution. Validation-only product — not redistributed raw data.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import sys
import tempfile
import zipfile
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ARCHIVE_URL = "https://portal.inmet.gov.br/uploads/dadoshistoricos/{year}.zip"
LICENSE = (
    "Brazilian government open data (Lei de Acesso a Informacao "
    "12.527/2011); INMET public archive, attribution required"
)
SOURCE = "INMET historical CSV archive (portal.inmet.gov.br/dadoshistoricos)"
PRECIP_COL = "PRECIPITAÇÃO TOTAL, HORÁRIO (mm)"
CHUNK = 1 << 20


def _num(text: str) -> float | None:
    text = (text or "").strip().replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_station_csv(text: str) -> tuple[dict, dict[str, float]]:
    """One station-year CSV -> (station meta, {YYYY-MM-DD: mm})."""
    meta = {}
    body = io.StringIO()
    header_done = False
    for line in text.splitlines():
        if not header_done:
            key, _, value = line.partition(":")
            key = key.strip().upper()
            if key in ("REGIAO", "UF", "ESTACAO", "CODIGO (WMO)",
                       "LATITUDE", "LONGITUDE", "ALTITUDE",
                       "DATA DE FUNDACAO"):
                meta[key] = value.strip().lstrip(";").strip()
            elif line.startswith("Data"):
                body.write(line + "\n")
                header_done = True
        else:
            body.write(line + "\n")
    body.seek(0)
    daily: dict[str, float] = {}
    for row in csv.DictReader(body, delimiter=";"):
        mm = _num(row.get(PRECIP_COL, ""))
        date = (row.get("Data") or "").strip()
        if mm is None or not date:
            continue
        date = date.replace("/", "-")
        parts = date.split("-")
        if len(parts[0]) == 2:  # DD-MM-YYYY -> YYYY-MM-DD
            date = f"{parts[2]}-{parts[1]}-{parts[0]}"
        daily[date] = daily.get(date, 0.0) + mm
    station = {
        "id": meta.get("CODIGO (WMO)", "").upper(),
        "name": meta.get("ESTACAO", ""),
        "lat": _num(meta.get("LATITUDE", "")),
        "lon": _num(meta.get("LONGITUDE", "")),
    }
    return station, daily


def iter_station_csvs(zip_path: Path):
    """Yield (station, daily) for every member ending in .CSV/.csv."""
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            if not name.upper().endswith(".CSV"):
                continue
            text = zf.read(name).decode("latin-1")
            yield parse_station_csv(text)


def _in_bbox(lat: float | None, lon: float | None, bbox) -> bool:
    if lat is None or lon is None:
        return False
    w, s, e, n = bbox
    return w <= lon <= e and s <= lat <= n


def _download(url: str, dest: Path) -> None:
    import requests

    headers = {"User-Agent": "Mozilla/5.0 (compatible; tipping-point-data)"}
    with requests.get(url, headers=headers, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(dest, "wb") as fh:
            fh.writelines(r.iter_content(CHUNK))


def fetch(zip_source: str, bbox, start: str, end: str, url: str | None = None
          ) -> dict:
    """Build the series dict from a local zip path or a URL."""
    is_local = Path(zip_source).exists()
    with tempfile.NamedTemporaryFile(
            suffix=".zip", delete=not is_local) as tmp:
        zip_path = Path(zip_source) if is_local else Path(tmp.name)
        if not is_local:
            _download(zip_source, zip_path)
        stations = []
        for meta, daily in iter_station_csvs(zip_path):
            if not _in_bbox(meta["lat"], meta["lon"], bbox):
                continue
            window = {d: round(v, 1) for d, v in sorted(daily.items())
                      if start <= d <= end}
            stations.append({**meta, "daily": window})
    stations.sort(key=lambda s: s["id"])
    return {
        "source": SOURCE,
        "url": url or zip_source,
        "license": LICENSE,
        "fetched_utc": dt.datetime.now(dt.UTC)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "stations": stations,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--event", default="rs-2024-05",
                    help="event id under events/ providing aoi.bbox")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--zip", default=None,
                    help="local zip path or URL (default: portal archive "
                         "for --start's year)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)

    event = json.loads(
        Path(f"events/{args.event}.json").read_text())
    bbox = event["aoi"]["bbox"]
    zip_source = args.zip or ARCHIVE_URL.format(year=args.start[:4])
    out = fetch(zip_source, bbox, args.start, args.end,
                url=None if args.zip else zip_source)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"{len(out['stations'])} stations -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
