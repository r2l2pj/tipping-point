"""NISAR L3 SME2 soil moisture as 'nisar_extra' case layers (provisional product).

For each downloaded SME2 granule (NISAR_L3_SME2_PROVISIONAL_V1, EASE-Grid 2.0 / EPSG:6933,
200 m): the top-level `soilMoisture` (average of the recommended algorithm retrievals,
or of all when none is recommended), water (waterbodyFraction > 0.5) masked, cropped to
the case box ->
  data/<case>/png/nisar_sme2_<YYYYMMDD>.png   (EPSG:3857 RGBA, transparent nodata)
  data/<case>/cog/<case>_nisar_sme2_<YYYYMMDD>.tif (EPSG:4326 float32, m3/m3)
and an entry merged into data/<case>/png/_index.json (append/replace by id+date, never
rebuilt). The note compares the area mean with SMAP L4 surface soil moisture (0-5 cm,
9 km, model-assimilated) at the same day, sampled on a 0.1 deg lattice inside the swath.

    PYTHONPATH=.:pipeline python tools/nisar_sme2_layers.py <granules_dir> [--no-smap]
granule files named <case-key>_<YYYYMMDD>.h5 with case-key uru | wa.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
for p in (str(REPO), str(REPO / "pipeline")):
    if p not in sys.path:
        sys.path.insert(0, p)

CASES = {"uru": ("rs_uruguai_2026", (-58.0, -30.6, -55.3, -28.2)),
         "wa": ("wa_2025", (-122.6, 47.0, -121.5, 48.5))}
STOPS = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5]
RGB = [(166, 97, 26), (223, 194, 125), (199, 233, 192), (116, 196, 118), (49, 130, 189), (8, 48, 107)]
LEGEND = {"unit": "m³/m³", "stops": [{"v": v, "color": f"#{c[0]:02x}{c[1]:02x}{c[2]:02x}",
                                      "label": f"{v:.1f}" if v < 0.5 else "≥ 0.5"}
                                     for v, c in zip(STOPS, RGB)]}
G = "science/LSAR/SME2/grids"


def read_granule(path: Path, bbox):
    import h5py
    from pyproj import Transformer
    from rasterio.transform import from_origin

    with h5py.File(path, "r") as f:
        g = f[G]
        x, y = g["xCoordinates"][:], g["yCoordinates"][:]
        tr = Transformer.from_crs(4326, 6933, always_xy=True)
        xs, ys = tr.transform([bbox[0], bbox[2], bbox[0], bbox[2]], [bbox[1], bbox[1], bbox[3], bbox[3]])
        ix = np.flatnonzero((x >= min(xs)) & (x <= max(xs)))
        iy = np.flatnonzero((y >= min(ys)) & (y <= max(ys)))
        if ix.size == 0 or iy.size == 0:
            return None
        sl = (slice(iy[0], iy[-1] + 1), slice(ix[0], ix[-1] + 1))
        sm = g["soilMoisture"][sl].astype(np.float64)
        q = g["retrievalQualityFlag"][sl]
        water = g["ancillaryData/waterbodyFraction"][sl]
    sm[(sm <= -9000) | (water > 0.5)] = np.nan
    rec = np.isfinite(sm) & ((q & 1) == 0) & (q >= 0)
    dx = float(x[1] - x[0])
    dy = float(y[1] - y[0])
    transform = from_origin(float(x[ix[0]]) - dx / 2, float(y[iy[0]]) - dy / 2 if dy < 0 else float(y[iy[-1]]) + dy / 2,
                            dx, abs(dy))
    if dy > 0:   # south-up rows -> flip to north-up
        sm, rec = sm[::-1], rec[::-1]
    return {"sm": sm, "rec": rec, "transform": transform}


def png_3857(sm, transform):
    import rasterio

    from live.render import warp_to_3857

    cls = np.digitize(np.nan_to_num(sm, nan=-1.0), STOPS).astype(np.uint8)   # 0 = nodata/<0
    cls[~np.isfinite(sm)] = 0
    c3857, _, bounds = warp_to_3857(cls, transform, src_crs="EPSG:6933")
    rgba = np.zeros((4, *c3857.shape), np.uint8)
    for k, rgb in enumerate(RGB, start=1):
        m = c3857 == k
        for i in range(3):
            rgba[i][m] = rgb[i]
        rgba[3][m] = 220
    with rasterio.MemoryFile() as mf:
        with mf.open(driver="PNG", height=c3857.shape[0], width=c3857.shape[1], count=4,
                     dtype="uint8") as dst:
            dst.write(rgba)
        return mf.read(), [round(float(b), 6) for b in bounds]


def cog_4326(sm, transform, out: Path):
    import rasterio
    from rasterio.warp import Resampling, calculate_default_transform, reproject

    h, w = sm.shape
    dst_tr, dw, dh = calculate_default_transform("EPSG:6933", "EPSG:4326", w, h,
                                                 *rasterio.transform.array_bounds(h, w, transform))
    dst = np.full((dh, dw), np.nan, np.float32)
    reproject(sm.astype(np.float32), dst, src_transform=transform, src_crs="EPSG:6933",
              dst_transform=dst_tr, dst_crs="EPSG:4326", resampling=Resampling.bilinear,
              src_nodata=np.nan, dst_nodata=np.nan)
    with rasterio.open(out, "w", driver="GTiff", height=dh, width=dw, count=1, dtype="float32",
                       crs="EPSG:4326", transform=dst_tr, nodata=np.nan, tiled=True,
                       blockxsize=256, blockysize=256, compress="deflate") as d:
        d.write(dst, 1)
        d.set_band_description(1, "soil_moisture_m3m3")


def smap_mean(sm, transform, day: dt.date) -> float | None:
    """SMAP L4 surface SM mean on a 0.1 deg lattice of valid SME2 pixels."""
    from pyproj import Transformer

    from ml.local import sources_dynamic as D
    rows, cols = np.nonzero(np.isfinite(sm))
    if rows.size == 0:
        return None
    xs, ys = transform * (cols + 0.5, rows + 0.5)
    lon, lat = Transformer.from_crs(6933, 4326, always_xy=True).transform(xs, ys)
    pts = np.unique(np.round(np.column_stack([lon, lat]) / 0.1) * 0.1, axis=0)
    dates = np.full(len(pts), np.datetime64(day))
    D.fetch_smap_points(pts[:, 0], pts[:, 1], dates)
    v = D.SmapPoints.load().sample(pts[:, 0], pts[:, 1], dates)["sm_surface"].to_numpy()
    return float(np.nanmean(v)) if np.isfinite(v).any() else None


def layer_id(ds: str, track: str | None) -> str:
    """Unique per case: nisar_sme2_<YYYYMMDD>_<track+direction> (e.g. nisar_sme2_20251204_172D)."""
    return f"nisar_sme2_{ds}_{track}" if track else f"nisar_sme2_{ds}"


def merge_index(index_path: Path, entry: dict) -> None:
    doc = json.loads(index_path.read_text()) if index_path.exists() else {"layers": []}
    # replace by (id, date) or by the same png (drops the pre-fix shared id "nisar_sme2" on rerun)
    layers = [e for e in doc.get("layers", [])
              if not ((e.get("id") == entry["id"] and e.get("date") == entry["date"])
                      or (entry.get("png") and e.get("png") == entry["png"]))]
    layers.append(entry)
    doc["layers"] = layers
    index_path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("granules", type=Path)
    ap.add_argument("--no-smap", action="store_true")
    a = ap.parse_args(argv)
    stats = []
    for h5 in sorted(a.granules.glob("*.h5")):
        key, ds = h5.stem.split("_")
        case, bbox = CASES[key]
        day = dt.datetime.strptime(ds, "%Y%m%d").date()
        g = read_granule(h5, bbox)
        if g is None or not np.isfinite(g["sm"]).any():
            print(f"{h5.name}: no valid pixels in the case box")
            continue
        cdir = REPO / "data" / case
        (cdir / "png").mkdir(parents=True, exist_ok=True)
        (cdir / "cog").mkdir(parents=True, exist_ok=True)
        png, bounds = png_3857(g["sm"], g["transform"])
        (cdir / "png" / f"nisar_sme2_{ds}.png").write_bytes(png)
        cog_4326(g["sm"], g["transform"], cdir / "cog" / f"{case}_nisar_sme2_{ds}.tif")
        valid = np.isfinite(g["sm"])
        mean = float(np.nanmean(g["sm"]))
        rec_share = float(g["rec"][valid].mean())
        smap = None if a.no_smap else smap_mean(g["sm"], g["transform"], day)
        trk = {"uru_20260918": "010D", "uru_20260930": "010D", "uru_20261012": "010D",
               "uru_20261017": "082D"}.get(h5.stem, "172D" if key == "wa" else None)
        note = (f"NISAR SME2 provisional: surface soil moisture (~0-5 cm, 200 m), swath mean {mean:.2f} m³/m³"
                + (f" vs SMAP L4 surface {smap:.2f} (9 km, same day)" if smap is not None else "")
                + f"; only {rec_share * 100:.0f} % of pixels flagged 'recommended' — the SME2 algorithms "
                  "(DSG/PMI/TSR) still disagree, read patterns, not absolute values")
        entry = {"id": layer_id(ds, trk), "date": day.isoformat(), "kind": "nisar_extra",
                 "png": f"{case}/png/nisar_sme2_{ds}.png", "cog": f"{case}/cog/{case}_nisar_sme2_{ds}.tif",
                 "bounds": bounds, "track": trk, "sensor": "NISAR (SME2, provisional)",
                 "source": "NISAR L3 SME2 provisional (ASF, NISAR_L3_SME2_PROVISIONAL_V1)",
                 "label": "Soil moisture (NISAR, provisional)", "legend": LEGEND, "note": note}
        merge_index(cdir / "png" / "_index.json", entry)
        stats.append({"case": case, "date": day.isoformat(), "track": trk, "mean": round(mean, 3),
                      "smap_surface": None if smap is None else round(smap, 3),
                      "recommended_share": round(rec_share, 3), "valid_px": int(valid.sum()),
                      "bounds": bounds})
        print(stats[-1], flush=True)
    (a.granules / "sme2_stats.json").write_text(json.dumps(stats, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
