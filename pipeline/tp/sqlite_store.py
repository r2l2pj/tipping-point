"""data/tipping_point.sqlite — a queryable copy of the published JSON/GeoJSON.

Built ONLY from files already under data/ (stdlib sqlite3, no new deps); the
app keeps reading JSON. Rebuilt from scratch on every run (idempotent), so the
file never drifts from the JSON it mirrors. Missing inputs leave their table
empty and are listed in the `build_notes` table — never invented.

Tables (all also documented in data/SCHEMA.md):
  events(event_id, case_id, title, reference_date, west, south, east, north)
  nisar_passes(granule, acquired, path, direction)
  metrics(case_id, key, value, unit, updated_at, source)
  series(case_id, date, var, value)
  zones(run_id, municipality, code, uf, hazard, area_km2, people, class, rank)
  response_candidates(run_id, source, hazard, area_km2, people, municipality,
                      confidence, status, acquired, lon, lat)
  runs(run_id, kind, updated_at, verified, data_dates) — one row per Now run:
      earlier 'now' rows are carried over from the previous database file
  build_notes(item, note)
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
from pathlib import Path

DDL = """
CREATE TABLE events (event_id TEXT PRIMARY KEY, case_id TEXT, title TEXT, reference_date TEXT,
                     west REAL, south REAL, east REAL, north REAL);
CREATE TABLE nisar_passes (granule TEXT PRIMARY KEY, acquired TEXT, path INTEGER, direction TEXT);
CREATE TABLE metrics (case_id TEXT, key TEXT, value, unit TEXT, updated_at TEXT, source TEXT,
                      PRIMARY KEY (case_id, key));
CREATE TABLE series (case_id TEXT, date TEXT, var TEXT, value REAL, PRIMARY KEY (case_id, date, var));
CREATE TABLE zones (run_id TEXT, municipality TEXT, code TEXT, uf TEXT, hazard TEXT,
                    area_km2 REAL, people REAL, class TEXT, rank INTEGER);
CREATE TABLE response_candidates (run_id TEXT, source TEXT, hazard TEXT, area_km2 REAL, people REAL,
                                  municipality TEXT, confidence TEXT, status TEXT, acquired TEXT,
                                  lon REAL, lat REAL);
CREATE TABLE runs (run_id TEXT PRIMARY KEY, kind TEXT, updated_at TEXT, verified INTEGER, data_dates TEXT);
CREATE TABLE build_notes (item TEXT, note TEXT);
CREATE INDEX metrics_key ON metrics (key);
CREATE INDEX series_case_var ON series (case_id, var);
CREATE INDEX cand_run ON response_candidates (run_id, source);
"""

CASES = ("rs_2024", "wa_2025")
UNIT_SUFFIX = (("_km2", "km2"), ("_pct", "%"), ("_h", "h"), ("_mm", "mm"), ("_m", "m"),
               ("_deg", "deg"), ("_db", "dB"))
SERIES_SKIP = {"date", "offset", "kind", "gfs_issued", "gfs_rain3_issued"}


def _load(path: Path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def unit_for(key: str) -> str | None:
    k = key.lower()
    if k.startswith("people") or k.endswith("_people") or "people_" in k:
        return "persons"
    for suf, unit in UNIT_SUFFIX:
        if k.endswith(suf):
            return unit
    return None


def _scalar(v):
    """SQLite value: numbers/strings/bools as-is (bool -> 0/1), containers as JSON text."""
    if isinstance(v, bool):
        return int(v)
    if v is None or isinstance(v, (int, float, str)):
        return v
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


def _rep_point(geom: dict):
    try:
        from shapely.geometry import shape

        p = shape(geom).representative_point()
        return round(p.x, 5), round(p.y, 5)
    except Exception:  # noqa: BLE001 — position is optional
        return None, None


def build(data_root: Path, out: Path | None = None, prev: Path | None = None) -> dict:
    """(Re)build the database from data_root; returns row counts per table."""
    data_root = Path(data_root)
    out = Path(out) if out else data_root / "tipping_point.sqlite"
    tmp = out.with_suffix(".sqlite.tmp")
    if tmp.exists():
        tmp.unlink()
    con = sqlite3.connect(tmp)
    con.executescript(DDL)
    notes = []
    # runs history: carry every earlier Now run row over from the previous
    # database (CI downloads it from the fallback branch before this step),
    # so `runs` keeps one row per Now run instead of only the latest
    prev_db = Path(prev) if prev else out
    carried = 0
    if prev_db.exists():
        try:
            old = sqlite3.connect(f"file:{prev_db}?mode=ro", uri=True)
            rows = old.execute("SELECT run_id, kind, updated_at, verified, data_dates "
                               "FROM runs WHERE kind = 'now'").fetchall()
            old.close()
            con.executemany("INSERT OR IGNORE INTO runs VALUES (?,?,?,?,?)", rows)
            carried = len(rows)
        except sqlite3.Error as exc:
            notes.append(("runs.history", f"previous database unreadable: {exc}"))

    def note(item, msg):
        notes.append((item, msg))

    root_manifest = _load(data_root / "manifest.json") or {}
    case_updated = root_manifest.get("updated_at")

    # events
    ev = _load(data_root / "events.json")
    if ev:
        for e in ev.get("events", []):
            bb = (e.get("bbox") or [None] * 4) + [None] * 4
            case_id = "_".join(e["id"].split("-")[:2])     # 'rs-2024-05' -> 'rs_2024'
            con.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?)",
                        (e["id"], case_id, e.get("title"), e.get("reference_date"), *bb[:4]))
    else:
        note("events", "data/events.json missing or unreadable")

    # metrics (replay cases)
    met = _load(data_root / "metrics.json")
    if met:
        for case, blob in (met.get("cases") or {}).items():
            for k, v in blob.items():
                if k.startswith("_"):
                    continue
                con.execute("INSERT INTO metrics VALUES (?,?,?,?,?,?)",
                            (case, k, _scalar(v), unit_for(k), case_updated, "metrics.json"))
            for k, reason in (blob.get("_null_reasons") or {}).items():
                con.execute("INSERT OR IGNORE INTO metrics VALUES (?,?,?,?,?,?)",
                            (case, f"_null_reasons.{k}", reason, None, case_updated, "metrics.json"))
    else:
        note("metrics", "data/metrics.json missing or unreadable")

    # series (replay daily chart series, long format)
    for case in CASES:
        s = _load(data_root / case / "series" / "imerg_smap_daily.json")
        if not s:
            note(f"series.{case}", "imerg_smap_daily.json missing")
            continue
        for row in s.get("days", []):
            for var, v in row.items():
                if var in SERIES_SKIP or isinstance(v, bool) or not isinstance(v, (int, float)):
                    continue
                con.execute("INSERT OR REPLACE INTO series VALUES (?,?,?,?)", (case, row["date"], var, v))

    # Now run: runs, nisar passes, metrics(now summary), zones, candidates
    now_manifest = _load(data_root / "now" / "manifest.json") or {}
    run_id = now_manifest.get("updated_at")
    if run_id:
        con.execute("INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?)",
                    (run_id, "now", run_id, int(bool(now_manifest.get("verified"))),
                     json.dumps(now_manifest.get("data_dates") or {}, ensure_ascii=False)))
        for p in now_manifest.get("nisar_passes") or []:
            con.execute("INSERT OR IGNORE INTO nisar_passes VALUES (?,?,?,?)",
                        (p.get("granule"), p.get("acquired"), p.get("path"), p.get("direction")))
    else:
        note("runs.now", "data/now/manifest.json missing updated_at")
    for case in CASES:
        con.execute("INSERT INTO runs VALUES (?,?,?,?,?)", (case, "replay", case_updated, None, None))

    summ = _load(data_root / "now" / "summary.json")
    if summ and run_id:
        flat = {}
        for k, v in summ.items():
            if isinstance(v, dict) and k in ("critical_km2", "nodata_km2"):
                flat.update({f"{k}.{h}": x for h, x in v.items()})
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                flat[k] = v
        for k, v in (summ.get("response") or {}).items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                flat[f"response.{k}"] = v
        for k, v in flat.items():
            con.execute("INSERT OR REPLACE INTO metrics VALUES (?,?,?,?,?,?)",
                        ("now", k, v, unit_for(k.split(".")[0]) or unit_for(k), run_id, "now/summary.json"))

    def zones_from(path, rid):
        fc = _load(path)
        if not fc:
            note(f"zones.{rid}", f"{path.relative_to(data_root)} missing")
            return
        for f in fc.get("features", []):
            p = f.get("properties") or {}
            con.execute("INSERT INTO zones VALUES (?,?,?,?,?,?,?,?,?)",
                        (rid, p.get("name"), p.get("code"), p.get("uf"), p.get("hazard"),
                         p.get("area_km2"), p.get("people"), p.get("class"), p.get("rank")))

    if run_id:
        zones_from(data_root / "now" / "geojson" / "priority_zones.geojson", run_id)
    for case in CASES:
        zones_from(data_root / case / "geojson" / "priority_zones.geojson", case)

    def cands_from(path, rid, hazard, default_source):
        fc = _load(path)
        if not fc:
            note(f"candidates.{rid}.{default_source}", f"{path.relative_to(data_root)} missing")
            return
        for f in fc.get("features", []):
            p = f.get("properties") or {}
            lon, lat = _rep_point(f.get("geometry"))
            con.execute("INSERT INTO response_candidates VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (rid, p.get("source") or default_source, hazard, p.get("area_km2"),
                         p.get("people"), p.get("municipality"), p.get("confidence") or "standard",
                         p.get("status") or "candidate", p.get("acquired") or p.get("date"), lon, lat))

    if run_id:
        resp = data_root / "now" / "response"
        cands_from(resp / "flood_dswx.geojson", run_id, "flood", "opera-dswx-s1")
        cands_from(resp / "flood_nisar.geojson", run_id, "flood", "nisar-gcov")
        cands_from(resp / "landslide_dist.geojson", run_id, "landslide", "opera-dist-alert-hls")
    for case in CASES:
        cands_from(data_root / case / "geojson" / "landslide_candidates.geojson", case,
                   "landslide", "opera-dist-alert-hls")

    con.execute("INSERT INTO build_notes VALUES (?,?)",
                ("built_at", dt.datetime.now(dt.UTC).isoformat(timespec="seconds")))
    con.executemany("INSERT INTO build_notes VALUES (?,?)", notes)
    con.commit()
    counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: S608 - fixed names
              for t in ("events", "nisar_passes", "metrics", "series", "zones",
                        "response_candidates", "runs", "build_notes")}
    con.execute("VACUUM")
    con.close()
    os.replace(tmp, out)
    counts["bytes"] = out.stat().st_size
    counts["runs_carried"] = carried
    return counts
