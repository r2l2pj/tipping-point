"""Generalisation across held-out TEST events (v2, conductor 2026-11-15).

Reads ``metrics_ml.json`` (evaluate output): per horizon / source / model, the
per-event TEST metrics (``by_event``) and the leave-one-event-out pooled metrics
(``loeo``). Reports, per model: pooled value, per-event median [min, max] and the
LOEO range — the spread is the honest answer to "does it overfit one storm?".
Pooled numbers are dominated by the 3,000-point events (RS, Maria, Kerala, Emilia),
so the per-event (macro) view comes first.

    python -m ml.local.generalise [--source gfs|perfect] [--metric pr_auc]
"""
from __future__ import annotations

import argparse
import json

import numpy as np

from . import settings as S

MODELS = ("xgb_rain_soil", "xgb_all", "rf_all", "direct_rain", "rule_tpi", "climatology")
MIN_POS_EVENT = 20   # per-event metrics on fewer positives are listed, not summarised


def summary(met: dict, source: str = "gfs", metric: str = "pr_auc") -> dict:
    out: dict = {}
    for h in S.HORIZONS:
        blk = ((met.get("3b") or {}).get("horizons") or {}).get(h, {}).get(source) or {}
        out[h] = {}
        for m in MODELS:
            e = blk.get(m)
            if not isinstance(e, dict) or "test" not in e:
                continue
            per = {ev: v.get(metric) for ev, v in (e.get("by_event") or {}).items()
                   if v.get(metric) is not None and (v.get("n_pos") or 0) >= MIN_POS_EVENT}
            lo = [v.get(metric) for v in (e.get("loeo") or {}).values() if v.get(metric) is not None]
            vals = np.array(list(per.values()), float)
            out[h][m] = {
                "pooled": e["test"].get(metric),
                "events_n": len(vals),
                "event_median": float(np.median(vals)) if len(vals) else None,
                "event_min": float(vals.min()) if len(vals) else None,
                "event_max": float(vals.max()) if len(vals) else None,
                "worst_event": min(per, key=per.get) if per else None,
                "loeo_min": float(min(lo)) if lo else None,
                "loeo_max": float(max(lo)) if lo else None,
                "per_event": per,
            }
    return out


def table_md(summ: dict, metric: str) -> str:
    f = lambda v: "—" if v is None else f"{v:.3f}"
    L = [f"| H | model | pooled {metric} | per-event median [min – max] (n events) | worst event | LOEO pooled range |",
         "|---|---|---|---|---|---|"]
    for h, ms in summ.items():
        for m, r in ms.items():
            L.append(f"| {h} | {m} | {f(r['pooled'])} | {f(r['event_median'])} [{f(r['event_min'])} – "
                     f"{f(r['event_max'])}] ({r['events_n']}) | {r['worst_event'] or '—'} | "
                     f"{f(r['loeo_min'])} – {f(r['loeo_max'])} |")
    return "\n".join(L)


def per_event_md(summ: dict, h: str, metric: str) -> str:
    ms = summ.get(h, {})
    evs = sorted({e for r in ms.values() for e in r["per_event"]})
    f = lambda v: "—" if v is None else f"{v:.2f}"
    L = [f"| model | " + " | ".join(evs) + " |", "|" + "---|" * (len(evs) + 1)]
    for m, r in ms.items():
        L.append(f"| {m} | " + " | ".join(f(r["per_event"].get(e)) for e in evs) + " |")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="gfs", choices=["gfs", "perfect"])
    ap.add_argument("--metric", default="pr_auc")
    a = ap.parse_args(argv)
    met = json.loads((S.MODEL_DIR / "metrics_ml.json").read_text())
    s = summary(met, a.source, a.metric)
    print(table_md(s, a.metric))
    for h in S.HORIZONS:
        print(f"\n{h} per event ({a.metric}, {a.source}):\n")
        print(per_event_md(s, h, a.metric))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
