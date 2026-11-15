"""Markdown TEST tables from ml/models/metrics_ml.json (for model-metrics.md).

    python -m ml.local.report [path/to/metrics_ml.json]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from . import settings as S

COLS = ("n", "n_pos", "base_rate", "precision", "recall", "f1", "accuracy", "pr_auc", "roc_auc",
        "pr_auc_raw", "roc_auc_raw")
EVENT = {"south_southeast_brazil": "RS 2024", "north_america": "WA 2025"}


def _f(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


def _table(rows: list[tuple[str, dict | None]], first: str = "model") -> list[str]:
    out = [f"| {first} | " + " | ".join(COLS) + " | conf (tp/fp/fn/tn) |",
           "|" + "---|" * (len(COLS) + 2)]
    for name, m in rows:
        if not m:
            out.append(f"| {name} | " + " | ".join("—" for _ in COLS) + " | — |")
            continue
        conf = "/".join(_f(m.get(k)) for k in ("tp", "fp", "fn", "tn"))
        out.append(f"| {name} | " + " | ".join(_f(m.get(c)) for c in COLS) + f" | {conf} |")
    return out


def render(met: dict) -> str:
    L: list[str] = []
    a, b = met.get("3a", {}), met.get("3b", {})
    L += ["### 3a T_ML susceptibility — TEST (50 km blocks)", ""]
    L += _table([(k, (v or {}).get("test")) for k, v in (a.get("models") or {}).items()])
    imp = (a.get("importance") or {}).get("permutation_pr_auc_drop_test")
    if imp:
        L += ["", "Group permutation PR-AUC drop (TEST): "
              + ", ".join(f"{g} {v:.3f}" for g, v in imp.items())]
    L += ["", "### 3b trigger, daily target-day rows — TEST (RS 2024 + WA 2025)", ""]
    L += _table([(k, (v or {}).get("test")) for k, v in (b.get("daily") or {}).items()])
    nt = ((b.get("daily") or {}).get("xgb_all") or {}).get("test_by_neg_type") or {}
    if nt:
        L += ["", "xgb_all by negative type (positives vs one negative type):", ""]
        L += _table(list(nt.items()), first="negatives")
    imp = (b.get("importance") or {})
    if imp.get("permutation_pr_auc_drop_test"):
        L += ["", "Group permutation PR-AUC drop (TEST): " + ", ".join(
            f"{g} {v:.3f}" for g, v in imp["permutation_pr_auc_drop_test"].items())]
    if imp.get("gain_share"):
        L += ["Group gain share (XGB, TRAIN): " + ", ".join(
            f"{g} {v:.3f}" for g, v in imp["gain_share"].items())]
    for h, d in (b.get("horizons") or {}).items():
        if not isinstance(d, dict):
            continue
        for src in ("gfs", "perfect"):
            blk = d.get(src)
            tag = "NOAA GFS 00Z (operational)" if src == "gfs" else "perfect forecast (upper bound)"
            low = " — LOW FORECAST SKILL" if h == "7d" else ""
            L += ["", f"### 3b P(landslide within {h}) — TEST, {tag}{low}", ""]
            if not isinstance(blk, dict) or "reason" in blk:
                L.append(f"not available: {(blk or {}).get('reason')}")
                continue
            L += _table([(k, v.get("test")) for k, v in blk.items()])
            ev_rows = []
            for k in ("xgb_rain_soil", "direct_rain", "xgb_all", "rf_all", "rule_tpi"):
                b = blk.get(k) or {}
                if b.get("by_event"):
                    ev_rows += [(f"{k} · {ev}", m) for ev, m in b["by_event"].items()]
                else:
                    ev_rows += [(f"{k} · {EVENT.get(r, r)}", m)
                                for r, m in (b.get("by_region") or {}).items()]
            if ev_rows:
                L += ["", "By TEST event:", ""] + _table(ev_rows, first="model · event")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    path = Path(argv[0]) if argv else S.MODEL_DIR / "metrics_ml.json"
    sys.stdout.write(render(json.loads(path.read_text())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# --------------------------------------------------------------------------- v2
V2_MODELS = ("xgb_rain_soil", "direct_rain", "xgb_all", "rf_all", "rule_tpi", "climatology")
V2_COLS = ("n", "n_pos", "precision", "recall", "f1", "accuracy", "pr_auc", "roc_auc", "pr_auc_raw")


def _row(name: str, m: dict | None) -> str:
    if not m:
        return f"| {name} | " + " | ".join("—" for _ in V2_COLS) + " |"
    return f"| {name} | " + " | ".join(_f(m.get(c)) for c in V2_COLS) + " |"


def render_v2(met: dict) -> str:
    """Pooled TEST per horizon (GFS + perfect), then per-event PR-AUC / ROC grids."""
    b = met.get("3b", {})
    L = []
    rows_info = b.get("horizon_rows") or {}
    for h, d in (b.get("horizons") or {}).items():
        for src in ("gfs", "perfect"):
            blk = (d or {}).get(src) or {}
            tag = "NOAA GFS 00Z (operational; rows with a GFS run only)" if src == "gfs" \
                else "perfect forecast = observed IMERG (upper bound; all events)"
            ri = (rows_info.get(h) or {}).get(src) or {}
            L += ["", f"#### {h} — pooled TEST, {tag}",
                  f"rows {ri.get('n', '—')} (dropped without a GFS run: {ri.get('n_dropped_no_gfs_run', 0)})", "",
                  "| model | " + " | ".join(V2_COLS) + " |", "|" + "---|" * (len(V2_COLS) + 1)]
            L += [_row(m, (blk.get(m) or {}).get("test")) for m in V2_MODELS]
    for metric in ("pr_auc", "roc_auc"):
        for src in ("gfs", "perfect"):
            for h, d in (b.get("horizons") or {}).items():
                blk = (d or {}).get(src) or {}
                evs = sorted({e for m in V2_MODELS for e in ((blk.get(m) or {}).get("by_event") or {})})
                if not evs:
                    continue
                L += ["", f"#### {h} — {metric} per TEST event ({src})", "",
                      "| model | " + " | ".join(evs) + " |", "|" + "---|" * (len(evs) + 1)]
                for m in V2_MODELS:
                    be = (blk.get(m) or {}).get("by_event") or {}
                    L.append(f"| {m} | " + " | ".join(
                        _f((be.get(e) or {}).get(metric)) for e in evs) + " |")
                npos = {e: ((blk.get("xgb_all") or {}).get("by_event") or {}).get(e, {}).get("n_pos") for e in evs}
                L.append("| n_pos | " + " | ".join(str(npos[e]) for e in evs) + " |")
    return "\n".join(L) + "\n"
