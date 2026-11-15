"""Expected next NISAR pass per track/frame for the Now domain (L43).

Method (declared in the product): the last observed acquisition on each
(track, look, frame) found in CMR — NISAR_L2_GCOV_V1 production plus
NISAR_L2_GCOV_BETA_V1 — refreshed by the recent L0 overpass list
(data/now/nisar_passes.json, written each Now run by
`python -m live.fetch nisar-passes`; on NOW_LOCAL runs the same list
lives in manifest.json["nisar_passes"]) when a newer pass exists on that
track, plus n×12 days — the first repeat still in the future. No tasking
plan is consulted: entries are EXPECTED passes, never guaranteed ones.

Writes data/now/nisar_next_passes.json and stamps every priority-zone
feature (geojson/priority_zones.geojson + response/priority_zones.geojson)
and every alerts.json active[] item with next_nisar_pass_utc /
next_nisar_track — the soonest expected pass whose CMR footprint
intersects the zone, the alert centroid, or its municipality mesh
(null when nothing covers it).

    python -m live.nisar_next --now-dir data/now
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

REPEAT_DAYS = 12
DAYS_BACK = 45            # ~3.7 repeats — the newest frame hit is what matters
COLLECTIONS = ("NISAR_L2_GCOV_V1", "NISAR_L2_GCOV_BETA_V1")
PRODUCT = "nisar_next_passes.json"
PASSES_JSON = "nisar_passes.json"
ZONES = ("geojson/priority_zones.geojson", "response/priority_zones.geojson")
METHOD = ("last observed pass on the same track/frame (CMR, "
          "NISAR_L2_GCOV_BETA_V1 + V1) + n×12 d, first time > now; "
          "no tasking plan used — expected, not guaranteed")
DIRECTION = {"A": "ascending", "D": "descending"}


def _iso(t: dt.datetime) -> str:
    return t.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc(text) -> dt.datetime:
    t = dt.datetime.fromisoformat(str(text))
    return t if t.tzinfo else t.replace(tzinfo=dt.UTC)


def next_pass(last_utc: dt.datetime, now: dt.datetime,
              repeat_days: int = REPEAT_DAYS) -> dt.datetime:
    """Smallest last_utc + n*repeat_days strictly after `now` (n >= 1)."""
    step = dt.timedelta(days=repeat_days)
    n = max(0, (now - last_utc) // step) + 1
    return last_utc + step * n


def covers(footprint, munis: list[dict]) -> list[str]:
    """IBGE codes whose municipality mesh intersects `footprint`.

    munis: [{"code", "name", "uf", "geom"}] as load_munis returns."""
    if footprint is None:
        return []
    return sorted({m["code"] for m in munis
                   if m["code"] and m["geom"] is not None
                   and footprint.intersects(m["geom"])})


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def load_recent_passes(now_dir: Path) -> list[dict]:
    """Recent L0 overpasses -> [{track, look, acquired}] (parseable only).

    nisar_passes.json first; NOW_LOCAL runs carry the same list inside
    manifest.json["nisar_passes"] instead (jobs.py only writes the file
    on the GEE path).
    """
    raw = None
    p = now_dir / PASSES_JSON
    if p.is_file():
        try:
            raw = json.loads(p.read_text())
        except ValueError:
            raw = None
    if raw is None:
        m = now_dir / "manifest.json"
        if m.is_file():
            try:
                raw = json.loads(m.read_text()).get("nisar_passes")
            except ValueError:
                raw = None
    out = []
    for e in raw or []:
        try:
            track = int(e.get("path") if e.get("path") is not None
                        else e.get("track"))
            acq = _utc(e["acquired"])
        except (TypeError, ValueError, KeyError, AttributeError):
            continue
        d = str(e.get("direction") or "").upper()
        look = "A" if d.startswith("A") else ("D" if d.startswith("D")
                                              else None)
        if look:
            out.append({"track": track, "look": look, "acquired": acq})
    return out


def latest_frames(bounds, days_back: float = DAYS_BACK, *,
                  get=None, now: dt.datetime | None = None) -> dict:
    """{(track, look, frame): granule} — newest CMR GCOV product per frame,
    both collections merged (full frames win a same-date tie, then the
    larger granule id). `get` is injectable for tests."""
    from pipeline.fetch import nisar as pn

    now = now or dt.datetime.now(dt.UTC)
    start = now - dt.timedelta(days=days_back)
    best: dict[tuple, tuple] = {}
    for coll in COLLECTIONS:
        for g in pn.search_gcov_domain(bounds, start, None,
                                       collection=coll, get=get):
            key = (g["track"], g["look"], g["frame"])
            score = (g["start"], g.get("coverage") == "F",
                     str(g["granule_id"]))
            if key not in best or score > best[key][0]:
                best[key] = (score, g)
    return {k: v[1] for k, v in best.items()}


def load_munis(now_dir: Path) -> list[dict]:
    """[{code, name, uf, geom}] from geojson/municipal_boundaries.geojson."""
    from shapely.geometry import shape

    p = now_dir / "geojson" / "municipal_boundaries.geojson"
    if not p.is_file():
        return []
    out = []
    for f in json.loads(p.read_text()).get("features", []):
        pr = f.get("properties") or {}
        try:
            geom = shape(f["geometry"])
        except (KeyError, TypeError, ValueError):
            continue
        out.append({"code": str(pr.get("code") or ""),
                    "name": pr.get("name"), "uf": pr.get("uf"),
                    "geom": geom})
    return out


def build(now_dir: Path, *, now: dt.datetime | None = None, get=None,
          bounds=None, days_back: float = DAYS_BACK):
    """-> (product, footprints): the nisar_next_passes.json payload plus the
    shapely footprint per pass entry (same order) for the zone/alert stamps.

    A track's L0 overpass newer than the newest CMR product re-anchors all
    its frames (GCOV has multi-day latency; the L0 RRSD pass is itself an
    observed acquisition on that track).
    """
    now = now or dt.datetime.now(dt.UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=dt.UTC)
    if bounds is None:
        from live.config import NOW_BOUNDS
        bounds = NOW_BOUNDS
    l0: dict[tuple, dt.datetime] = {}
    for p in load_recent_passes(now_dir):
        k = (p["track"], p["look"])
        if p["acquired"] <= now and (k not in l0 or p["acquired"] > l0[k]):
            l0[k] = p["acquired"]
    munis = load_munis(now_dir)
    frames = latest_frames(bounds, days_back, get=get, now=now)
    passes, fps = [], []
    for (track, look, frame), g in sorted(frames.items()):
        anchor = max(g["start"], l0.get((track, look)) or g["start"])
        fp = g.get("footprint")
        passes.append({
            "utc": _iso(next_pass(anchor, now)),
            "track": track,
            "direction": DIRECTION.get(look, str(look or "").lower()),
            "frame": frame,
            "last_observed_utc": _iso(anchor),
            "covers": covers(fp, munis),
        })
        fps.append(fp)
    order = sorted(range(len(passes)), key=lambda i: passes[i]["utc"])
    passes = [passes[i] for i in order]
    fps = [fps[i] for i in order]
    return {
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "repeat_days": REPEAT_DAYS,
        "method": METHOD,
        "passes": passes,
    }, fps


def _soonest_hit(geom, passes: list[dict], fps: list) -> dict | None:
    """The pass entry (soonest utc) whose footprint intersects `geom`."""
    best = None
    for e, fp in zip(passes, fps):
        if fp is None or not fp.intersects(geom):
            continue
        if best is None or e["utc"] < best["utc"]:
            best = e
    return best


def inject_zones(now_dir: Path, passes: list[dict], fps: list) -> int:
    """Stamp next_nisar_pass_utc/next_nisar_track on every feature of both
    priority_zones copies (null when no expected pass covers the zone)."""
    from shapely.geometry import shape

    n = 0
    for rel in ZONES:
        path = now_dir / rel
        if not path.is_file():
            continue
        try:
            fc = json.loads(path.read_text())
        except ValueError:
            continue
        for f in fc.get("features", []):
            try:
                geom = shape(f["geometry"])
            except (KeyError, TypeError, ValueError):
                continue
            hit = _soonest_hit(geom, passes, fps)
            pr = f.setdefault("properties", {})
            pr["next_nisar_pass_utc"] = hit["utc"] if hit else None
            pr["next_nisar_track"] = hit["track"] if hit else None
            n += 1
        # same compact shape tp.response_summary._write_fc emits
        _atomic_write(path, json.dumps(fc, allow_nan=False) + "\n")
    return n


def inject_alerts(now_dir: Path, passes: list[dict], fps: list,
                  munis: list[dict] | None = None) -> int:
    """Stamp next_nisar_* on alerts.json active[] — footprint over the
    alert centroid first, else over its municipality mesh."""
    from shapely.geometry import Point

    path = now_dir / "alerts.json"
    if not path.is_file():
        return 0
    doc = json.loads(path.read_text())
    if munis is None:
        munis = load_munis(now_dir)
    by_name = {(m["name"], m["uf"]): m["geom"] for m in munis if m["name"]}
    n = 0
    for item in doc.get("active") or []:
        hit = None
        c = item.get("centroid")
        if isinstance(c, (list, tuple)) and len(c) == 2:
            hit = _soonest_hit(Point(c[0], c[1]), passes, fps)
        if hit is None:
            g = by_name.get((item.get("municipality"), item.get("uf")))
            if g is not None:
                hit = _soonest_hit(g, passes, fps)
        item["next_nisar_pass_utc"] = hit["utc"] if hit else None
        item["next_nisar_track"] = hit["track"] if hit else None
        n += 1
    _atomic_write(path, json.dumps(doc, ensure_ascii=False, indent=2) + "\n")
    return n


def run(now_dir: Path, *, bounds=None, days_back: float = DAYS_BACK,
        get=None, now: dt.datetime | None = None) -> dict:
    """Write nisar_next_passes.json + stamp zones/alerts. The build itself
    raises (the workflow step is continue-on-error); the stamps are
    individually guarded so a bad geojson never costs the product."""
    now_dir = Path(now_dir)
    product, fps = build(now_dir, now=now, bounds=bounds,
                         days_back=days_back, get=get)
    out = now_dir / PRODUCT
    out.parent.mkdir(parents=True, exist_ok=True)
    passes = product["passes"]
    _atomic_write(out, json.dumps(product, ensure_ascii=False, indent=2)
                  + "\n")
    notes = []
    for tag, fn in (("zones", inject_zones), ("alerts", inject_alerts)):
        try:
            notes.append(f"{tag}={fn(now_dir, passes, fps)}")
        except Exception as exc:  # noqa: BLE001 — stamps are best-effort
            notes.append(f"{tag}=FAILED({exc!r})")
            print(f"[nisar_next] {tag} stamp failed: {exc!r}",
                  file=sys.stderr)
    print(f"[nisar_next] {len(passes)} expected passes -> {out} "
          f"({', '.join(notes)})")
    return product


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--now-dir", type=Path, default=None,
                    help="default: live.config.NOW_DIR (data/now)")
    ap.add_argument("--bounds", type=float, nargs=4, default=None,
                    metavar=("W", "S", "E", "N"),
                    help="default: live.config.NOW_BOUNDS")
    ap.add_argument("--days-back", type=int, default=DAYS_BACK,
                    help="CMR search window ending now (default %(default)s)")
    args = ap.parse_args(argv)
    from live.config import NOW_BOUNDS, NOW_DIR

    run(args.now_dir or Path(NOW_DIR),
        bounds=tuple(args.bounds) if args.bounds else NOW_BOUNDS,
        days_back=args.days_back)
    return 0


if __name__ == "__main__":
    sys.exit(main())
