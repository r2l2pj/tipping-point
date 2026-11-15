"""NISAR HH time series over the Uruguay River at Uruguaiana ("the river's dance").

Author: Jhonatan (team) — original dance.py; adapted into the repo by lead-ml (paths, colours,
honesty labels, outputs). Method unchanged:
  * reads the published HH dB COGs (60 m, UTM 21S), crops a fixed 30 x 30 km window,
  * maps open water per pass: HH below the per-scene Otsu threshold, clamped to [-20, -13] dB
    (falls back to -15 dB outside that range), opening + 3 ha minimum mapping unit,
  * counts water inside a 3 km corridor around the persistent river, on pixels valid in ALL passes,
  * "new water" = water minus the persistent river / water present in >= 7 of 9 passes,
  * compares new-water km² with the ANA Uruguaiana gauge (77150000) daily median stage.
Mixed tracks (010D / 082D) look at the river from different geometries; NISAR = candidates.

    PYTHONPATH=.:pipeline python tools/nisar_river_dance.py
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
CASE = REPO / "data" / "rs_uruguai_2026"
COG = CASE / "cog" / "rs_uruguai_2026_nisar_hh_"
BOX = (476000, 6700000, 506000, 6730000)  # x0, y0, x1, y1 UTM 21S
PASSES = [("2026-09-18", "20260918", "010D"), ("2026-09-23", "20260923_082D", "082D"),
          ("2026-09-30", "20260930", "010D"), ("2026-10-05", "20261005_082D", "082D"),
          ("2026-10-12", "20261012", "010D"), ("2026-10-17", "20261017_082D", "082D"),
          ("2026-10-24", "20261024", "010D"), ("2026-10-29", "20261029_082D", "082D"),
          ("2026-11-05", "20261105", "010D")]
SLIDE = ("2026-09-30", "2026-10-05", "2026-10-12", "2026-10-17", "2026-10-24", "2026-11-05")
PX_KM2 = 0.0036  # 60 m pixel
INK, PAPER, MUTED = "#0F1E2A", "#F5F2EC", "#9aa4b2"
RED, AMBER, EDGE = "#ff1f1f", "#f59e0b", "#33475a"
CREDIT = "NASA NISAR (provisional GCOV) · HH · gauge: ANA 77150000 Uruguaiana · analysis: Jhonatan / Tipping Point"


def compute() -> dict:
    import rasterio
    from rasterio.windows import from_bounds
    from scipy import ndimage
    from skimage.filters import threshold_otsu

    gauge = json.loads((CASE / "series" / "forecast_proof.json").read_text())["truth"]["uruguaiana"]
    stage, fs = gauge["daily_median_m"], gauge["flood_stage_m"]
    imgs = []
    for _, f, _ in PASSES:
        with rasterio.open(f"{COG}{f}_60m.tif") as s:
            win = from_bounds(*BOX, s.transform)
            a = s.read(1, window=win).astype(np.float64)
            if s.nodata is not None:
                a[a == s.nodata] = np.nan
            imgs.append(a)
            wtr, wcrs = s.window_transform(win), s.crs
    stack = np.stack(imgs)
    common = np.all(np.isfinite(stack), axis=0)
    # river corridor: persistent dark water (< -15 dB in >= 7 of 9 passes), largest body, buffered 3 km
    pers = (np.nan_to_num(stack, nan=0) < -15).sum(0) >= 7
    lab, n = ndimage.label(pers)
    sz = ndimage.sum(pers, lab, range(1, n + 1))
    river = lab == (1 + int(np.argmax(sz)))
    corr = (ndimage.distance_transform_edt(~river) * 60 <= 3000) & common
    rows, waters = [], []
    for (d, _, trk), a in zip(PASSES, stack, strict=True):
        v = a[corr]
        try:
            otsu = float(threshold_otsu(np.clip(v, -30, 5)))
        except ValueError:
            otsu = float("nan")
        thr = otsu if -20 <= otsu <= -13 else -15.0
        w = ndimage.binary_opening((a < thr) & corr, iterations=1)
        lab2, n2 = ndimage.label(w)
        sizes = ndimage.sum(w, lab2, range(1, n2 + 1))
        w = np.isin(lab2, 1 + np.where(sizes * 0.36 >= 3)[0])  # MMU 3 ha (60 m px = 0.36 ha)
        waters.append(w)
        rows.append({"date": d, "track": trk, "otsu_raw_db": round(otsu, 2), "threshold_db": round(thr, 2),
                     "threshold_fallback": thr != otsu, "water_km2_corridor": round(float(w.sum() * PX_KM2), 2),
                     "valid_pct": round(100 * float(np.isfinite(a).mean()), 1), "stage_m": stage.get(d),
                     "above_flood_stage": bool(stage.get(d) is not None and stage[d] >= fs)})
    base = river | (np.stack(waters).sum(0) >= 7)
    fp = floodplain(stack.shape[1:], wtr, wcrs)
    for r, w in zip(rows, waters, strict=True):
        nwm = w & ~base
        r["new_water_km2"] = round(float(nwm.sum() * PX_KM2), 2)
        r["new_water_floodplain_km2"] = round(float((nwm & fp).sum() * PX_KM2), 2)
        r["new_water_outside_floodplain_pct"] = round(100 * float((nwm & ~fp).sum() / max(nwm.sum(), 1)), 1)
    st = np.array([r["stage_m"] for r in rows], float)
    nw = np.array([r["new_water_km2"] for r in rows], float)
    r_p = float(np.corrcoef(st, nw)[0, 1])
    nwf = np.array([r["new_water_floodplain_km2"] for r in rows], float)
    r_fp = float(np.corrcoef(st, nwf)[0, 1])
    by_track = {}
    for trk in ("010D", "082D"):
        m = np.array([r["track"] == trk for r in rows])
        by_track[trk] = {"n": int(m.sum()), "pearson": round(float(np.corrcoef(st[m], nw[m])[0, 1]), 3)}
    all_fb = all(r["threshold_fallback"] for r in rows)
    note = ("water = HH < -15 dB (scene threshold fell back to -15 dB on all passes)" if all_fb else
            "water = HH below the per-scene Otsu threshold, clamped to [-20, -13] dB")
    return {"stack": stack, "common": common, "waters": waters, "base": base, "doc": {
        "what": "NISAR HH new-water area in a 3 km corridor of the Uruguay River at Uruguaiana vs the gauge stage",
        "box_utm21s": list(BOX), "common_km2": round(float(common.sum() * PX_KM2), 1),
        "corridor_km2": round(float(corr.sum() * PX_KM2), 1), "river_km2": round(float(river.sum() * PX_KM2), 1),
        "flood_stage_m": fs, "pearson_stage_newwater": round(r_p, 3), "n": len(rows), "pearson_by_track": by_track,
        "pearson_stage_newwater_floodplain_only": round(r_fp, 3),
        "floodplain": "HAND <= 5 m (static-v1 hand_90m, nearest onto the 60 m UTM window)",
        "gauge": "ANA 77150000 Uruguaiana, daily median stage on the pass date (forecast_proof.json)",
        "new_water_reference": ("water of the pass minus the persistent river and any pixel that is water in >= 7 of "
                                "the 9 passes; inside a 3 km corridor around the river; pixels valid in all passes"),
        "threshold_note": note,
        "caption": ("The river's dance: 9 NISAR HH passes (Sep 18 - Nov 5 2026, mixed tracks 010D and 082D), "
                    f"30 x 30 km around Uruguaiana. New water (red) tracks the gauge: Pearson r = {r_p:.2f} "
                    f"(n = {len(rows)}). {note}. NISAR water = candidates (provisional GCOV)."),
        "credit": CREDIT, "author": "Jhonatan (method), adapted by lead-ml", "passes": rows}}


def floodplain(shape, tr, crs) -> np.ndarray:
    import sys

    import rasterio
    from rasterio.warp import Resampling, reproject
    sys.path.insert(0, str(REPO / "tools"))
    from t_flood_eval import RELEASE
    hand = np.full(shape, np.nan, np.float32)
    with rasterio.open(f"/vsicurl/{RELEASE}/hand_90m.tif") as src:
        reproject(rasterio.band(src, 1), hand, dst_transform=tr, dst_crs=crs, resampling=Resampling.nearest)
    return hand <= 5.0


def _gray(a: np.ndarray) -> np.ndarray:
    g = np.clip((a + 25.0) / 25.0, 0, 1)          # fixed -25..0 dB, as every NISAR frame in the app
    rgb = np.stack([g, g, g], -1)
    rgb[~np.isfinite(a)] = [0.06, 0.12, 0.16]
    return rgb


def _frame(ax, a, w, base, common, r, fs) -> None:
    rgb = _gray(a)
    rgb[w & ~base & common] = [1.0, 31 / 255, 31 / 255]
    ax.imshow(rgb, interpolation="nearest")
    ax.set_xticks([])
    ax.set_yticks([])
    over = r["above_flood_stage"]
    for sp in ax.spines.values():
        sp.set_color(AMBER if over else EDGE)
        sp.set_linewidth(5 if over else 1)
    ax.set_title(f"{r['date'][5:].replace('-', '/')}  ·  {r['track']}", color=PAPER, fontsize=17,
                 fontweight="bold", pad=8)
    ax.text(0.5, -0.06, f"river {r['stage_m']:.2f} m" + ("\nabove flood stage" if over else "\n "),
            transform=ax.transAxes, ha="center", va="top", color=AMBER if over else PAPER, fontsize=15,
            fontweight="bold")


def strip(res: dict, out: Path, dates=None, size=(27, 5.0), title=True) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    d = res["doc"]
    idx = [i for i, r in enumerate(d["passes"]) if dates is None or r["date"] in dates]
    nrow = 2 if dates is not None else 1
    fig, ax = plt.subplots(nrow, -(-len(idx) // nrow), figsize=size, facecolor=INK)
    ax = np.ravel(ax)
    for x, i in zip(ax, idx, strict=True):
        _frame(x, res["stack"][i], res["waters"][i], res["base"], res["common"], d["passes"][i], d["flood_stage_m"])
    if title:
        fig.suptitle("The river's dance — NISAR radar, Uruguay River at Uruguaiana (red = new water)",
                     color=PAPER, fontsize=22 if dates else 20, y=0.97)
    fig.text(0.5, 0.015, f"{d['threshold_note']} · mixed tracks 010D / 082D · {CREDIT}",
             ha="center", color=MUTED, fontsize=11)
    if dates is not None:
        plt.subplots_adjust(wspace=0.04, hspace=0.32, left=0.2, right=0.8, top=0.9, bottom=0.1)
    else:
        plt.subplots_adjust(wspace=0.06, left=0.005, right=0.995, top=0.80 if title else 0.86, bottom=0.22)
    fig.savefig(out, dpi=110 if dates is None else 100, facecolor=INK)
    plt.close(fig)


def chart(res: dict, out: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    d = res["doc"]
    stage = json.loads((CASE / "series" / "forecast_proof.json").read_text())["truth"]["uruguaiana"]["daily_median_m"]
    days = [k for k in sorted(stage) if "2026-09-10" <= k <= "2026-11-10"]
    idx = {k: i for i, k in enumerate(days)}
    fig, a1 = plt.subplots(figsize=(14, 5.2), facecolor=INK)
    a1.set_facecolor(INK)
    a2 = a1.twinx()
    a2.set_facecolor(INK)
    a2.patch.set_facecolor(INK)
    px = [idx[r["date"]] for r in d["passes"]]
    nw = [r["new_water_km2"] for r in d["passes"]]
    a2.bar(px, nw, width=1.6, color=RED, alpha=0.85)
    for p, v in zip(px, nw, strict=True):
        a2.text(p, v + max(nw) * 0.02, f"{v:.0f}", ha="center", color=RED, fontsize=12, fontweight="bold")
    a2.set_ylabel("NISAR new water (km²)", color=RED, fontsize=14)
    a2.tick_params(colors=RED, labelsize=12)
    a1.plot(range(len(days)), [stage[k] for k in days], color=PAPER, lw=3, label="River stage (m)")
    a1.axhline(d["flood_stage_m"], color=AMBER, lw=1.5, ls="--")
    a1.text(1, d["flood_stage_m"] + 0.12, "flood stage 8.5 m", color=AMBER, fontsize=14)
    a1.set_ylabel("stage, Uruguaiana gauge (m)", color=PAPER, fontsize=14)
    a1.tick_params(colors=PAPER, labelsize=12)
    a1.set_zorder(a2.get_zorder() + 1)
    a1.patch.set_visible(False)
    ticks = [k for k in days if k.endswith(("-01", "-15"))]
    a1.set_xticks([idx[k] for k in ticks], [k[5:].replace("-", "/") for k in ticks])
    a1.set_title(f"River stage vs NISAR new water — Pearson r = {d['pearson_stage_newwater']:.2f} (n = {d['n']}, "
                 "mixed tracks 010D / 082D)", color=PAPER, fontsize=15)
    for s in list(a1.spines.values()) + list(a2.spines.values()):
        s.set_color(EDGE)
    fig.text(0.5, 0.01, d["threshold_note"] + " · " + CREDIT, ha="center", color=MUTED, fontsize=9)
    plt.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(out, dpi=110, facecolor=INK)
    plt.close(fig)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--img-dirs", type=Path, nargs="+",
                    default=[REPO / "app/docs/img", REPO.parent.parent / ".crew/deck/img"])
    ap.add_argument("--json", type=Path, default=CASE / "series" / "river_dance.json")
    a = ap.parse_args(argv)
    res = compute()
    a.json.write_text(json.dumps(res["doc"], indent=1) + "\n")
    for d in a.img_dirs:
        d.mkdir(parents=True, exist_ok=True)
        strip(res, d / "dance_strip.png")
        strip(res, d / "dance_slide.png", dates=SLIDE, size=(19.2, 10.8))
        chart(res, d / "dance_chart.png")
    doc = res["doc"]
    print(json.dumps({k: doc[k] for k in ("pearson_stage_newwater", "pearson_stage_newwater_floodplain_only",
                                          "n", "pearson_by_track", "threshold_note",
                                          "common_km2", "corridor_km2")}))
    for r in doc["passes"]:
        print(r["date"], r["track"], r["stage_m"], r["new_water_km2"], r["new_water_floodplain_km2"],
              r["new_water_outside_floodplain_pct"], r["otsu_raw_db"], r["threshold_db"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
