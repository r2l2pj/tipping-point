"""Copy each case's forecast lead time (L22/L28b) into data/metrics.json.

Source of truth: data/<case>/forecast_lead_time.json (written by
.herdr/gfs_replay_run.py). Only the `cases.<case>.forecast_lead_time` block
is replaced; every other metrics key is left untouched (verified scalars are
never rewritten here).

    PYTHONPATH=.:pipeline .venv/bin/python tools/sync_forecast_lead_metrics.py [--data-root data]
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
KEYS = ("earliest_critical_issue", "would_have_warned_h", "lead_h_median", "lead_h_iqr",
        "n_warned", "n_issue_days", "skipped_issue_days", "skipped_reason", "anchor_utc",
        "alignment", "basis")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-root", type=Path, default=REPO / "data")
    args = ap.parse_args(argv)
    path = args.data_root / "metrics.json"
    metrics = json.loads(path.read_text())
    cases = metrics.get("cases", metrics)
    for case in ("rs_2024", "wa_2025"):
        src = args.data_root / case / "forecast_lead_time.json"
        if not src.exists() or case not in cases:
            print(f"{case}: skipped ({'no ' + src.name if not src.exists() else 'not in metrics'})")
            continue
        lt = json.loads(src.read_text())
        block = {"forecast_source": "noaa-gfs", **{k: lt[k] for k in KEYS if k in lt}}
        cases[case]["forecast_lead_time"] = block
        print(f"{case}: median {block.get('lead_h_median')} h, n_warned "
              f"{block.get('n_warned')}/{block.get('n_issue_days')}")
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(metrics, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
