"""Judge figure (report #8): is the ML trigger better than the simple things?

Per held-out TEST event: ROC-AUC of the served trigger (xgb_rain_soil) vs the
TPI > 0.6 rule vs climatology (static + day-of-year XGB), at 24 h and 72 h.
ROC is used because it is base-rate free, so events with 8 or 3,000 positives
sit on one axis. Source = NOAA GFS 00Z where the archive covers the event;
Maria 2017 / Kerala 2018 predate it and use the perfect forecast (hatched).
Palette: reference categorical slots 1-3 (blue / orange / aqua), validated.

    python -m ml.local.figures   -> ml/models/figures/v2_vs_simple.png (+ .csv table view)
"""
from __future__ import annotations

import csv
import json

from . import settings as S

SERIES = (("xgb_rain_soil", "ML trigger (served)", "#2a78d6"),
          ("rule_tpi", "TPI > 0.6 rule", "#eb6834"),
          ("climatology", "Climatology (season + place)", "#1baf7a"))
HORIZONS = ("24h", "72h")
MIN_POS = 8
SURFACE, INK, INK2, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9"


def collect(met: dict) -> list[dict]:
    rows = []
    hz = (met.get("3b") or {}).get("horizons") or {}
    for h in HORIZONS:
        for src in ("gfs", "perfect"):
            blk = (hz.get(h) or {}).get(src) or {}
            for model, _, _ in SERIES:
                for ev, m in ((blk.get(model) or {}).get("by_event") or {}).items():
                    rows.append({"horizon": h, "source": src, "model": model, "event": ev,
                                 "n_pos": m.get("n_pos"), "roc_auc": m.get("roc_auc"),
                                 "pr_auc": m.get("pr_auc"), "base_rate": m.get("base_rate")})
    return rows


def pick(rows: list[dict], h: str) -> tuple[list[str], dict]:
    """Per event: GFS if available with positives, else perfect (flagged)."""
    out: dict = {}
    for r in rows:
        if r["horizon"] != h or r["roc_auc"] is None or (r["n_pos"] or 0) < MIN_POS:
            continue
        key = (r["event"], r["model"])
        if r["source"] == "gfs" or key not in out:
            out[key] = r
    events = sorted({e for e, _ in out},
                    key=lambda e: -max((out.get((e, m)) or {}).get("n_pos") or 0 for m, _, _ in SERIES))
    return events, out


def main() -> int:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    met = json.loads((S.MODEL_DIR / "metrics_ml.json").read_text())
    rows = collect(met)
    outdir = S.MODEL_DIR / "figures"
    outdir.mkdir(parents=True, exist_ok=True)
    with open(outdir / "v2_vs_simple.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["horizon"])
        w.writeheader()
        w.writerows(rows)

    plt.rcParams.update({"font.size": 10, "axes.edgecolor": MUTED, "axes.labelcolor": INK2,
                         "xtick.color": INK2, "ytick.color": INK2, "text.color": INK})
    fig, axes = plt.subplots(len(HORIZONS), 1, figsize=(11, 7.2), facecolor=SURFACE)
    for ax, h in zip(axes, HORIZONS, strict=True):
        events, sel = pick(rows, h)
        x = np.arange(len(events))
        bw = 0.26
        ax.set_facecolor(SURFACE)
        for i, (model, label, color) in enumerate(SERIES):
            vals = [(sel.get((e, model)) or {}).get("roc_auc") for e in events]
            hatch = ["//" if (sel.get((e, model)) or {}).get("source") == "perfect" else "" for e in events]
            for j, v in enumerate(vals):
                if v is None:
                    continue
                ax.bar(x[j] + (i - 1) * (bw + 0.02), v - 0.5, bottom=0.5, width=bw, color=color,
                       hatch=hatch[j], edgecolor=SURFACE, linewidth=0.8,
                       label=label if j == 0 else None)
        ax.axhline(0.5, color=MUTED, linewidth=1)
        ax.text(len(events) - 0.5, 0.505, "chance", color=MUTED, fontsize=8, ha="right", va="bottom")
        ax.set_ylim(0.3, 1.0)
        ax.set_yticks([0.3, 0.5, 0.7, 0.9, 1.0])
        ax.yaxis.grid(True, color=GRID, linewidth=0.6)
        ax.set_axisbelow(True)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
        npos = {e: max((sel.get((e, m)) or {}).get("n_pos") or 0 for m, _, _ in SERIES) for e in events}
        src = {e: (sel.get((e, "xgb_rain_soil")) or {}).get("source") for e in events}
        ax.set_xticks(x)
        ax.set_xticklabels([f"{e}\nn={npos[e]}" + ("\nperfect fc" if src[e] == "perfect" else "")
                            for e in events], fontsize=8)
        ax.set_ylabel(f"ROC-AUC · {h}")
    axes[0].legend(loc="lower left", frameon=False, ncol=3, fontsize=9)
    fig.suptitle("Held-out landslide events (v2 TEST): served ML trigger vs simple baselines",
                 x=0.01, ha="left", fontsize=12, color=INK)
    fig.text(0.01, 0.005, "ROC-AUC per event (base-rate free). NOAA GFS 00Z forecast; hatched = "
             "perfect forecast (event predates the GFS archive). Negatives: same points on "
             "event-free dates. Climatology knows only season + place.", fontsize=7.5, color=INK2)
    fig.tight_layout(rect=(0, 0.03, 1, 0.95))
    out = outdir / "v2_vs_simple.png"
    fig.savefig(out, dpi=160, facecolor=SURFACE)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
