"""L61 (3) EXPERIMENTAL — blend the TPI soil term S with NISAR SME2 where a pass exists. NOT SERVED.

S (served) = unit_scale(SMAP L4 root-zone wetness, 0.5, 0.9) at ~9 km (1 km published series).
NISAR L3 SME2 (provisional) is ~0-5 cm volumetric soil moisture at 200 m, on pass days only.
Earlier check (tools/nisar_sme2_layers.py): SME2 swath means do not track SMAP in absolute terms
and only a minority of pixels are flagged 'recommended'. So SME2 is used for PATTERN only:

  1. SME2 -> case grid (average), cells with SME2 = swath.
  2. Quantile-map SME2 onto the distribution of S_smap over the same swath cells that day
     (removes SME2's absolute bias, keeps its 200 m spatial ranking).
  3. S_blend = 0.5 * S_smap + 0.5 * S_sme2_matched inside the swath; S_smap elsewhere.
  4. TPI_flood_sme2 = cbrt(R * S_blend * T) with the case's own R and T for that day.

Outputs (staged, never published): ~/.cache/lead-ml/l61/<case>/<case>_s_sme2_blend_<date>.tif,
<case>_tpi_flood_sme2_<date>.tif and layers.json (kind 'experimental'). If lead-analysis's NISAR
water mask exists for the same day, both TPIs are scored with tools/nisar_agreement.py.

    PYTHONPATH=.:pipeline python tools/sme2_s_blend.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
for p in (str(REPO), str(REPO / "pipeline"), str(REPO / "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

ALPHA = 0.5
TRACK = {"wa_2025": "172D", "rs_uruguai_2026": "010D"}
DATES = {"wa_2025": ("20251204", "20251216"), "rs_uruguai_2026": ("20260930", "20261012")}


def quantile_map(x: np.ndarray, target: np.ndarray) -> np.ndarray:
    """Map x onto the empirical distribution of target (both 1-D, finite)."""
    ranks = np.argsort(np.argsort(x, kind="stable"), kind="stable") / max(x.size - 1, 1)
    return np.quantile(target, ranks)


def blend(s_smap: np.ndarray, sme2: np.ndarray, alpha: float = ALPHA) -> tuple[np.ndarray, np.ndarray]:
    """(S_blend, swath mask); outside the swath S_blend = S_smap."""
    sw = np.isfinite(sme2) & np.isfinite(s_smap)
    out = s_smap.copy()
    if sw.sum() > 10:
        m = quantile_map(sme2[sw], s_smap[sw])
        out[sw] = alpha * s_smap[sw] + (1 - alpha) * m
    return out, sw


def _to_grid(path: Path, band: int, tr, shape, resampling="average") -> np.ndarray:
    import rasterio
    from rasterio.warp import Resampling, reproject
    a = np.full(shape, np.nan, np.float32)
    with rasterio.open(path) as src:
        src_a = src.read(band).astype(np.float32)
        if src.nodata is not None:
            src_a[src_a == src.nodata] = np.nan
        src_a[~np.isfinite(src_a)] = np.nan
        reproject(src_a, a, src_transform=src.transform, src_crs=src.crs, dst_transform=tr,
                  dst_crs="EPSG:4326", src_nodata=np.nan, dst_nodata=np.nan,
                  resampling=getattr(Resampling, resampling))
    return a.astype(np.float64)


def case_inputs(data: Path, case: str, ds: str) -> dict:
    """R, S_smap, T, published TPI, grid for one day."""
    import rasterio
    c = data / case / "cog"
    if case == "wa_2025":
        with rasterio.open(c / "wa_2025_tpi_flood_20251210.tif") as s:
            tr, shape, names = s.transform, s.shape, list(s.descriptions)
            pub = s.read(names.index(ds) + 1).astype(np.float64)
        rn = list(rasterio.open(c / "wa_2025_r_series_20251210.tif").descriptions)
        r = _to_grid(c / "wa_2025_r_series_20251210.tif", rn.index(ds) + 1, tr, shape, "bilinear")
        s_ = _to_grid(c / "wa_2025_s_series_20251210.tif", rn.index(ds) + 1, tr, shape, "bilinear")
        t = rasterio.open(c / "wa_2025_t_flood_20251210.tif").read(1).astype(np.float64)
    else:
        with rasterio.open(c / f"rs_uruguai_2026_tpi_flood_obs_{ds}.tif") as s:
            tr, shape = s.transform, s.shape
            b = {n: s.read(i + 1).astype(np.float64) for i, n in enumerate(s.descriptions)}
        pub, r, s_, t = b["tpi"], b["r"], b["s"], b["t"]
    s_[~np.isfinite(s_)] = np.nan
    return {"tr": tr, "shape": shape, "pub": pub, "r": np.clip(r, 0, 1), "s": np.clip(s_, 0, 1), "t": t}


def _write(path: Path, a: np.ndarray, tr) -> None:
    import rasterio
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", height=a.shape[0], width=a.shape[1], count=1,
                       dtype="float32", crs="EPSG:4326", transform=tr, nodata=np.nan,
                       compress="deflate", tiled=True) as d:
        d.write(a.astype(np.float32), 1)


def main(argv=None) -> int:
    import nisar_agreement as na
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", type=Path, default=REPO / "data")
    ap.add_argument("--masks", type=Path, default=Path.home() / ".cache/lead-analysis/l61")
    ap.add_argument("--stage", type=Path, default=Path.home() / ".cache/lead-ml/l61")
    ap.add_argument("--out", type=Path, default=REPO / "ml/models/sme2_s_blend.json")
    a = ap.parse_args(argv)
    res = {"status": "EXPERIMENTAL — not served, not published", "alpha": ALPHA, "days": []}
    for case, days in DATES.items():
        layers = []
        for ds in days:
            inp = case_inputs(a.data, case, ds)
            sme2 = _to_grid(a.data / case / "cog" / f"{case}_nisar_sme2_{ds}.tif", 1, inp["tr"], inp["shape"])
            sb, sw = blend(inp["s"], sme2)
            valid = np.isfinite(inp["pub"])
            tpi_v2 = np.where(valid, np.cbrt(inp["r"] * inp["s"] * inp["t"]), np.nan)
            tpi_b = np.where(valid, np.cbrt(inp["r"] * sb * inp["t"]), np.nan)
            both = valid & sw & np.isfinite(tpi_v2)
            rebuild = np.abs(tpi_v2[valid] - inp["pub"][valid])
            row = {"case": case, "date": ds, "track": TRACK[case], "swath_cells": int(sw.sum()),
                   "swath_share_pct": round(100 * float(sw[valid].mean()), 1),
                   "v2_rebuild_mean_abs_err": round(float(np.nanmean(rebuild)), 4),
                   "s_smap_mean_swath": round(float(np.nanmean(inp["s"][sw])), 3),
                   "s_blend_mean_swath": round(float(np.nanmean(sb[sw])), 3),
                   "spearman_sme2_vs_smap": None,
                   "tpi_mean_abs_change_swath": round(float(np.nanmean(np.abs(tpi_b - tpi_v2)[both])), 4),
                   "critical_cells_v2": int((tpi_v2[both] > 0.6).sum()),
                   "critical_cells_blend": int((tpi_b[both] > 0.6).sum())}
            if sw.sum() > 10:
                x, y = sme2[sw], inp["s"][sw]
                rx, ry = np.argsort(np.argsort(x)), np.argsort(np.argsort(y))
                row["spearman_sme2_vs_smap"] = round(float(np.corrcoef(rx, ry)[0, 1]), 3)
            mp = a.masks / case / f"water_{ds}.npz"
            if mp.exists():
                w = na.load_water(mp)
                if w["water"].shape == inp["shape"]:
                    row["agreement_v2"] = na.agreement_day(w, tpi_v2, {}, inp["t"], None)
                    row["agreement_blend"] = na.agreement_day(w, tpi_b, {}, inp["t"], None)
            else:
                row["agreement"] = f"no NISAR water mask for {ds} in {mp.parent}"
            stage = a.stage / case
            _write(stage / f"{case}_s_sme2_blend_{ds}.tif", np.where(valid, sb, np.nan), inp["tr"])
            _write(stage / f"{case}_tpi_flood_sme2_{ds}.tif", tpi_b, inp["tr"])
            for lid, f, label in ((f"s_sme2_blend_{ds}_{TRACK[case]}", f"{case}_s_sme2_blend_{ds}.tif",
                                   "Soil term blended with NISAR SME2 (experimental)"),
                                  (f"tpi_flood_sme2_{ds}_{TRACK[case]}", f"{case}_tpi_flood_sme2_{ds}.tif",
                                   "Flood index with NISAR SME2 soil (experimental)")):
                layers.append({"id": lid, "date": f"{ds[:4]}-{ds[4:6]}-{ds[6:]}", "kind": "experimental",
                               "cog": f"{case}/cog/{f}", "track": TRACK[case], "served": False,
                               "label": label,
                               "note": "EXPERIMENTAL, not served: NISAR SME2 (provisional) quantile-mapped onto "
                                       "SMAP-based S inside the swath and averaged 50/50; pattern only, SME2 "
                                       "absolute values do not track SMAP."})
            res["days"].append(row)
            print(json.dumps({k: v for k, v in row.items() if not k.startswith("agreement")}), flush=True)
        (a.stage / case).mkdir(parents=True, exist_ok=True)
        (a.stage / case / "layers.json").write_text(json.dumps({"layers": layers}, indent=1))
    a.out.write_text(json.dumps(res, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
