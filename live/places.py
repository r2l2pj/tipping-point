"""'Search for city': data/now/places.json — one compact record per municipality.

For every IBGE municipality in the Now domain (geojson/municipal_boundaries.geojson,
RS/SC/PR + the SP/MS municipalities inside the bbox) the CURRENT Now run is
summarised: TPI flood/landslide max + class for d0..d3, ML P(trigger) max + p90
for 24h/48h/72h/7d, the active-alert flag/level, and people in High+ cells.

Inputs (all written earlier in the same Now job):
  places_tpi.npz          d0..d3 TPI arrays from live.run_now (not app-facing);
                          absent -> cog/tpi_{slide,flood}_{today,d1}.tif, d2/d3 null
  cog/p_trigger_<h>.tif   live.p_trigger (absent -> null)
  alerts.json             active[] (municipality, uf, label, hazard)
  static/gpw/gpw_popcount_2020_30sec.tif   GPW v4.11 2020 count (unadjusted)

Population: GPW count on its native 30" grid, each cell assigned to the
municipality containing its centre. people_high: the High+ mask (TPI > 0.4,
slide OR flood, today or D+1) is averaged onto the native GPW grid (covered
fraction) and multiplied by the cell count — the same area weighting as every
other people number.

    python -m live.places --now-dir data/now
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from live.config import LABELS, NOW_BOUNDS  # noqa: E402
from live.grid import dst_transform  # noqa: E402
from live.tpi import classify  # noqa: E402

HORIZONS = ("24h", "48h", "72h", "7d")
HIGH = 0.4
PEOPLE_METHOD = ("GPW v4.11 2020 population count (unadjusted), native 30\" grid; pop = cells whose "
                 "centre lies in the municipality; people_high = High+ (TPI > 0.4, slide or flood, "
                 "today or D+1) covered fraction of each cell x its count")


def group_max(labels: np.ndarray, values: np.ndarray, n: int) -> np.ndarray:
    """Per-label nanmax (NaN where a label has no finite value). labels 1..n, 0 = none."""
    out = np.full(n + 1, -np.inf)
    ok = (labels > 0) & np.isfinite(values)
    np.maximum.at(out, labels[ok], values[ok])
    out[~np.isfinite(out)] = np.nan
    return out


def group_quantile(labels: np.ndarray, values: np.ndarray, n: int, q: float) -> np.ndarray:
    """Per-label quantile (linear between order statistics), NaN for empty labels."""
    ok = (labels > 0) & np.isfinite(values)
    lab, val = labels[ok], values[ok]
    out = np.full(n + 1, np.nan)
    if lab.size == 0:
        return out
    order = np.lexsort((val, lab))
    lab, val = lab[order], val[order]
    starts = np.searchsorted(lab, np.arange(n + 1), side="left")
    ends = np.searchsorted(lab, np.arange(n + 1), side="right")
    for i in range(1, n + 1):
        s, e = starts[i], ends[i]
        if e > s:
            out[i] = float(np.quantile(val[s:e], q))
    return out


def _rasterize(geoms, shape, transform) -> np.ndarray:
    from rasterio.features import rasterize
    return rasterize(((g, i + 1) for i, g in enumerate(geoms)), out_shape=shape,
                     transform=transform, fill=0, dtype="int32", all_touched=False)


def _ensure_cell(labels: np.ndarray, geoms, transform) -> np.ndarray:
    """Municipalities smaller than a cell get the cell under their representative point."""
    present = np.zeros(len(geoms) + 1, bool)
    present[np.unique(labels)] = True
    inv = ~transform
    for i, g in enumerate(geoms):
        if not present[i + 1]:
            p = g.representative_point()
            c, r = inv * (p.x, p.y)
            r, c = int(r), int(c)
            if 0 <= r < labels.shape[0] and 0 <= c < labels.shape[1] and labels[r, c] == 0:
                labels[r, c] = i + 1
    return labels


def _load_tpi(now_dir: Path):
    """(slide[4,H,W] float, flood[4,H,W], dates[4] str|None, transform)."""
    npz = now_dir / "places_tpi.npz"
    if npz.exists():
        with np.load(npz) as z:
            slide, flood = z["slide"].astype(np.float32), z["flood"].astype(np.float32)
            dates = [str(d) for d in z["dates"]]
            bounds = tuple(float(b) for b in z["bounds"])
        return slide, flood, dates, dst_transform(bounds)
    import rasterio
    out = {}
    tr = None
    for hz in ("slide", "flood"):
        arrs = []
        for k in ("today", "d1"):
            with rasterio.open(now_dir / "cog" / f"tpi_{hz}_{k}.tif") as src:
                a = src.read(1).astype(np.float32)
                if src.nodata is not None and np.isfinite(src.nodata):
                    a[a == src.nodata] = np.nan
                arrs.append(a)
                tr = src.transform
        nan = np.full_like(arrs[0], np.nan)
        out[hz] = np.stack(arrs + [nan, nan])
    return out["slide"], out["flood"], [None] * 4, tr


def _p_trigger(now_dir: Path, h: str, shape, transform):
    import rasterio
    from rasterio.warp import Resampling, reproject
    p = now_dir / "cog" / f"p_trigger_{h}.tif"
    if not p.exists():
        return None
    dst = np.full(shape, np.nan, np.float32)
    with rasterio.open(p) as src:
        a = src.read(1).astype(np.float32)
        if src.nodata is not None and np.isfinite(src.nodata):
            a[a == src.nodata] = np.nan
        reproject(a, dst, src_transform=src.transform, src_crs=src.crs, src_nodata=np.nan,
                  dst_transform=transform, dst_crs="EPSG:4326", dst_nodata=np.nan,
                  resampling=Resampling.nearest)
    return dst


def _people(geoms, high: np.ndarray, transform, gpw_path: Path, n: int):
    """(pop[n+1], people_high[n+1]) on the native GPW grid, or (None, None)."""
    import rasterio
    from rasterio.warp import Resampling, reproject
    if not gpw_path.exists():
        return None, None
    with rasterio.open(gpw_path) as ds:
        w = rasterio.windows.from_bounds(*_bbox_of(geoms), ds.transform).round_offsets().round_lengths()
        w = w.intersection(rasterio.windows.Window(0, 0, ds.width, ds.height))  # clip to the raster
        cnt = ds.read(1, window=w).astype(np.float64)
        gtr = ds.window_transform(w)
        if ds.nodata is not None:
            cnt[cnt == ds.nodata] = 0
    cnt[~np.isfinite(cnt) | (cnt < 0)] = 0
    lab = _ensure_cell(_rasterize(geoms, cnt.shape, gtr), geoms, gtr)
    frac = np.zeros(cnt.shape, np.float32)
    reproject(high.astype(np.float32), frac, src_transform=transform, src_crs="EPSG:4326",
              dst_transform=gtr, dst_crs="EPSG:4326", resampling=Resampling.average)
    pop = np.bincount(lab.ravel(), weights=cnt.ravel(), minlength=n + 1)
    ph = np.bincount(lab.ravel(), weights=(cnt * np.clip(frac, 0, 1)).ravel(), minlength=n + 1)
    return pop, ph


def _bbox_of(geoms):
    b = np.array([g.bounds for g in geoms])
    return b[:, 0].min(), b[:, 1].min(), b[:, 2].max(), b[:, 3].max()


def _alerts(now_dir: Path) -> dict:
    """{(name, uf): {'level': 'Alto'|'Crítico', 'hazards': set}} from alerts.json active[]."""
    p = now_dir / "alerts.json"
    out: dict = {}
    if not p.exists():
        return out
    try:
        act = json.loads(p.read_text()).get("active") or []
    except ValueError:
        return out
    rank = {"Alto": 2, "Crítico": 3}
    for a in act:
        key = (a.get("municipality"), a.get("uf"))
        if not key[0]:
            continue
        lvl = a.get("label") or LABELS[int(a.get("class", 2))]
        cur = out.setdefault(key, {"level": lvl, "hazards": set()})
        if rank.get(lvl, 0) > rank.get(cur["level"], 0):
            cur["level"] = lvl
        cur["hazards"].add(a.get("hazard"))
    return out


def _r(x, nd):
    return None if x is None or not np.isfinite(x) else round(float(x), nd)


def build(now_dir: Path, gpw_path: Path | None = None) -> dict:
    from shapely.geometry import shape
    now_dir = Path(now_dir)
    gpw_path = Path(gpw_path) if gpw_path else now_dir / "static" / "gpw" / "gpw_popcount_2020_30sec.tif"
    fc = json.loads((now_dir / "geojson" / "municipal_boundaries.geojson").read_text())
    feats = [f for f in fc["features"] if f.get("geometry")]
    geoms = [shape(f["geometry"]).buffer(0) for f in feats]
    n = len(geoms)
    slide, flood, dates, tr = _load_tpi(now_dir)
    shape_ = slide.shape[1:]
    lab = _ensure_cell(_rasterize(geoms, shape_, tr), geoms, tr)
    flat = lab.ravel()
    mx = {hz: [group_max(flat, arr[k].ravel(), n) for k in range(4)]
          for hz, arr in (("slide", slide), ("flood", flood))}
    pt = {}
    for h in HORIZONS:
        a = _p_trigger(now_dir, h, shape_, tr)
        pt[h] = None if a is None else (group_max(flat, a.ravel(), n),
                                        group_quantile(flat, a.ravel(), n, 0.9))
    high = np.zeros(shape_, bool)
    for arr in (slide, flood):
        for k in (0, 1):
            high |= np.nan_to_num(arr[k], nan=0.0) > HIGH
    pop, ph = _people(geoms, high, tr, gpw_path, n)
    al = _alerts(now_dir)
    now_manifest = now_dir / "manifest.json"
    now_updated = None
    if now_manifest.exists():
        try:
            now_updated = json.loads(now_manifest.read_text()).get("updated_at")
        except ValueError:
            pass
    if dates[0] is None:
        try:
            d0 = dt.date.fromisoformat(json.loads((now_dir / "summary.json").read_text())
                                       ["generated_at"][:10])
            dates = [(d0 + dt.timedelta(days=k)).isoformat() for k in range(4)]
        except (OSError, ValueError, KeyError):
            pass
    places = []
    for i, (f, g) in enumerate(zip(feats, geoms, strict=True)):
        pr = f.get("properties") or {}
        j = i + 1
        rec = {"id": str(pr.get("code") or pr.get("id") or j), "name": pr.get("name"), "uf": pr.get("uf")}
        c = g.centroid
        rec["centroid"] = [round(c.x, 4), round(c.y, 4)]
        rec["bbox"] = [round(v, 4) for v in g.bounds]
        rec["pop"] = None if pop is None else int(round(pop[j]))
        for hz in ("flood", "slide"):
            vals = [mx[hz][k][j] for k in range(4)]
            rec[hz] = {"cls": [None if not np.isfinite(v) else int(classify(np.array([v]))[0]) for v in vals],
                       "max": [_r(v, 2) for v in vals]}
        rec["p_trigger"] = {h: (None if pt[h] is None or not np.isfinite(pt[h][0][j]) else
                                {"max": _r(pt[h][0][j], 3), "p90": _r(pt[h][1][j], 3)}) for h in HORIZONS}
        a = al.get((rec["name"], rec["uf"]))
        rec["alert"] = a is not None
        rec["alert_level"] = a["level"] if a else None
        hz = sorted(h for h in (a["hazards"] if a else []) if h)
        rec["alert_hazard"] = "+".join(hz) if hz else None
        rec["people_high"] = None if ph is None else int(round(ph[j]))
        places.append(rec)
    places.sort(key=lambda r: (r["uf"] or "", r["name"] or ""))
    return {"schema": "tipping-point/places@1",
            "generated_at": dt.datetime.now(dt.UTC).isoformat().replace("+00:00", "Z"),
            "now_updated_at": now_updated,
            "dates": {f"d{k}": dates[k] for k in range(4)},
            "classes": list(LABELS),
            "people_method": PEOPLE_METHOD,
            "d2_d3_note": None if (now_dir / "places_tpi.npz").exists() else
                          "d2/d3 not available in this run's published COGs (filled from the next Now run)",
            "places": places}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--now-dir", type=Path, default=REPO_ROOT / "data" / "now")
    ap.add_argument("--gpw", type=Path)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args(argv)
    out = build(a.now_dir, a.gpw)
    path = a.out or a.now_dir / "places.json"
    path.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
    print(f"[places] {len(out['places'])} municipalities -> {path} ({path.stat().st_size / 1e6:.2f} MB)")
    return 0


if __name__ == "__main__":
    _ = NOW_BOUNDS
    raise SystemExit(main())
