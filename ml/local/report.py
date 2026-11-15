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
            for k in ("xgb_all", "rf_all", "rule_tpi"):
                for r, m in ((blk.get(k) or {}).get("by_region") or {}).items():
                    ev_rows.append((f"{k} · {EVENT.get(r, r)}", m))
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
