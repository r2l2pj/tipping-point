"""Now history: keep the last KEEP Now runs as playable snapshots.

Each Now run (every 3 h) overwrites data/now/. This step, run after the
manifest is finalized and before the fallback publish, copies the run's
map frames into

    data/now/history/<YYYYMMDDTHHMMSSZ>/manifest.json   (paths relative to this dir)
    data/now/history/<YYYYMMDDTHHMMSSZ>/png/...          (layer PNGs, same layout)
    data/now/history/<YYYYMMDDTHHMMSSZ>/summary.json     (when present)

and rewrites data/now/history/index.json (newest first). Older runs are
deleted, so history is rolling: KEEP = 8 runs = 24 h at the 3-hourly cadence.

The fallback branch is one orphan snapshot per run, so earlier runs must be
restored from it before this step (now.yml: `git archive` of
origin/fallback data/now/history). COGs, GeoJSON and the response node are
NOT kept (size); history manifests carry PNG layers only, `cog` dropped.

    python -m live.now_history --now-dir data/now [--keep 8]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
from pathlib import Path

import numpy as np

KEEP = 8
KEEP_COG = 32             # runs 9..32 keep only cog/ + manifest (NISAR agreement leads, ~4 d)
COG_SCALE = 250           # uint8 TPI: value / 250; 255 = no data
SCHEMA = "tipping-point/now-history@1"
NOTE = ("Past Now runs (every 3 h), newest first, rolling: the last {keep} runs "
        "(~{hours} h). Map frames (PNG) + summary + TPI_flood d0..d3 COGs (for NISAR agreement leads); "
        "other COGs, GeoJSON and the response layers are not kept. Each run's layer dates are as issued then.")


def run_id(updated_at: str) -> str:
    """'2026-11-15T17:10:47.575597Z' -> '20261115T171047Z' (UTC)."""
    t = dt.datetime.fromisoformat(updated_at)
    return t.astimezone(dt.UTC).strftime("%Y%m%dT%H%M%SZ")


def _iso(rid: str) -> str:
    return dt.datetime.strptime(rid, "%Y%m%dT%H%M%SZ").strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_json(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def snapshot(now_dir: Path, hist: Path) -> dict | None:
    """Copy the current run into hist/<run_id>/; returns its index row."""
    mpath = now_dir / "manifest.json"
    if not mpath.is_file():
        print("now_history: no data/now/manifest.json - nothing to keep")
        return None
    m = json.loads(mpath.read_text())
    if not m.get("updated_at"):
        print("now_history: manifest has no updated_at - skipped")
        return None
    rid = run_id(m["updated_at"])
    dest = hist / rid
    tmp = hist / f".{rid}.tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)

    layers = []
    for lay in m.get("layers", []):
        png = lay.get("png")
        if not png or not (now_dir / png).is_file():
            continue
        (tmp / png).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(now_dir / png, tmp / png)
        layers.append({k: v for k, v in lay.items() if k != "cog"})

    cogs = _flood_cogs(now_dir, tmp)

    summary = None
    if (now_dir / "summary.json").is_file():
        shutil.copy2(now_dir / "summary.json", tmp / "summary.json")
        summary = json.loads((now_dir / "summary.json").read_text())

    hm = {k: v for k, v in m.items() if k not in ("layers", "products", "response")}
    hm.update(schema=m.get("schema"), history=True, run=_iso(rid), layers=layers,
              flood_cogs=cogs,
              products={"summary": "summary.json"} if summary is not None else {},
              _history_note="snapshot of a past Now run: PNG layers + summary only "
                            "(COGs, GeoJSON and the response node are not kept)")
    _write_json(tmp / "manifest.json", hm)       # manifest last inside the run dir
    shutil.rmtree(dest, ignore_errors=True)
    os.replace(tmp, dest)

    dd = m.get("data_dates", {})
    crit = (summary or {}).get("critical_km2") or {}
    alert = (summary or {}).get("alert_km2") or {}
    return {"run": _iso(rid), "id": rid,
            "manifest": f"now/history/{rid}/manifest.json",
            "imerg": dd.get("imerg"), "gfs_issued": dd.get("gfs_issued"),
            "smap": dd.get("smap"), "mode": m.get("mode"),
            "layers": len(layers),
            "critical_km2_any": crit.get("any"), "alert_km2_any": alert.get("any")}


def _flood_cogs(now_dir: Path, dst: Path) -> list[str]:
    """TPI_flood d0..d3 of this run as small COGs (cog/tpi_flood_d<k>.tif, float32
    rounded to 0.001) from places_tpi.npz, so later NISAR passes can score the
    forecasts issued 1–3 days before them (L61 agreement leads)."""
    npz = now_dir / "places_tpi.npz"
    if not npz.is_file():
        return []
    import rasterio
    from rasterio.transform import from_bounds

    z = np.load(npz)
    w, s, e, n = (float(x) for x in z["bounds"])
    k_, h, wd = z["flood"].shape
    tr = from_bounds(w, s, e, n, wd, h)
    (dst / "cog").mkdir(parents=True, exist_ok=True)
    out = []
    for k in range(k_):
        f = z["flood"][k].astype(np.float32)
        a = np.where(np.isfinite(f), np.clip(np.round(f * COG_SCALE), 0, 250), 255).astype(np.uint8)
        prof = {"driver": "GTiff", "height": h, "width": wd, "count": 1, "dtype": "uint8",
                "crs": "EPSG:4326", "transform": tr, "nodata": 255, "compress": "deflate",
                "tiled": True, "blockxsize": 256, "blockysize": 256}
        name = f"cog/tpi_flood_d{k}.tif"
        with rasterio.open(dst / name, "w", **prof) as d:
            d.write(a, 1)
            d.set_band_description(1, str(z["dates"][k]))
            d.scales = (1 / COG_SCALE,)      # TPI = value * scale; 255 = no data
            d.update_tags(tpi_scale=str(1 / COG_SCALE), nodata="255")
        out.append(name)
    return out


def _row_from_dir(d: Path) -> dict | None:
    """Index row for a restored run dir (rebuild from its own manifest)."""
    try:
        m = json.loads((d / "manifest.json").read_text())
    except (OSError, ValueError):
        return None
    s = {}
    if (d / "summary.json").is_file():
        try:
            s = json.loads((d / "summary.json").read_text())
        except ValueError:
            s = {}
    dd = m.get("data_dates", {})
    return {"run": _iso(d.name), "id": d.name,
            "manifest": f"now/history/{d.name}/manifest.json",
            "imerg": dd.get("imerg"), "gfs_issued": dd.get("gfs_issued"),
            "smap": dd.get("smap"), "mode": m.get("mode"),
            "layers": len(m.get("layers", [])),
            "critical_km2_any": (s.get("critical_km2") or {}).get("any"),
            "alert_km2_any": (s.get("alert_km2") or {}).get("any")}


def _is_run_dir(d: Path) -> bool:
    try:
        dt.datetime.strptime(d.name, "%Y%m%dT%H%M%SZ")
    except ValueError:
        return False
    return d.is_dir()


def update(now_dir: Path, keep: int = KEEP) -> dict:
    """Snapshot the current run, roll the history to `keep` runs, write index."""
    hist = now_dir / "history"
    hist.mkdir(parents=True, exist_ok=True)
    for junk in hist.glob(".*.tmp"):
        shutil.rmtree(junk, ignore_errors=True)
    current = snapshot(now_dir, hist)

    runs = sorted((d for d in hist.iterdir() if _is_run_dir(d)),
                  key=lambda d: d.name, reverse=True)
    keep_cog = max(keep, KEEP_COG)
    for old in runs[keep_cog:]:
        shutil.rmtree(old)
        print(f"now_history: dropped {old.name}")
    archive = []
    for old in runs[keep:keep_cog]:             # archive-only: TPI COGs + manifest
        for sub in ("png", "response"):
            shutil.rmtree(old / sub, ignore_errors=True)
        try:
            am = json.loads((old / "manifest.json").read_text())
        except (OSError, ValueError):
            continue
        if am.get("flood_cogs"):
            archive.append({"run": _iso(old.name), "id": old.name,
                            "manifest": f"now/history/{old.name}/manifest.json",
                            "flood_cogs": [f"now/history/{old.name}/{c}" for c in am["flood_cogs"]]})
    rows = []
    for d in runs[:keep]:
        row = current if current and current["id"] == d.name else _row_from_dir(d)
        if row is None:                       # unreadable restore: drop it
            shutil.rmtree(d, ignore_errors=True)
            continue
        rows.append(row)
    index = {"schema": SCHEMA, "keep": keep, "cadence_h": 3,
             "updated_at": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "note": NOTE.format(keep=keep, hours=keep * 3),
             "paths_relative_to": "data/ (same as the root manifest); a run's own "
                                  "manifest paths resolve under its run directory",
             "runs": rows,
             "archive": archive,
             "archive_note": ("runs 9..32 (~4 d): TPI_flood d0..d3 COGs only (uint8, TPI = value/250, "
                              "255 = no data) for NISAR agreement leads; not playable")}
    _write_json(hist / "index.json", index)
    # point the live manifest at the index (after the snapshot, so run
    # snapshots never list it); manifest rewritten last
    mpath = now_dir / "manifest.json"
    if rows and mpath.is_file():
        m = json.loads(mpath.read_text())
        m.setdefault("products", {})["history"] = "history/index.json"
        _write_json(mpath, m)
    print(f"now_history: {len(rows)} runs kept (newest {rows[0]['id'] if rows else '-'})")
    return index


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--now-dir", type=Path, default=Path("data/now"))
    ap.add_argument("--keep", type=int, default=KEEP)
    args = ap.parse_args(argv)
    update(args.now_dir, args.keep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
