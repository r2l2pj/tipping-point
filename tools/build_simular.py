"""Convert the GEE `simular` CSV export into simular.json for Telegram /simular.

Input: simular_rs_2024.csv from gee/08_simular_table.js — one row per town,
one column per day-band (0_TPI_slide, 1_TPI_slide, ...; band 0 = first day of
the window, 10 days before the 2024-05-02 event). Values are the max TPI_slide
within a 2 km buffer of each town.

Output: {"<town>": [{"date": "dd/mm/YYYY", "label": "<classe>"}, ...]} keeping
only class changes — the bot replays them as the alert sequence.

Usage: uv run python tools/build_simular.py [csv] [-o simular.json]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import pandas as pd
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
_cfg = yaml.safe_load((REPO_ROOT / "config.yaml").read_text())
EVENT = _cfg["cases"]["rs_2024"]
CUTS = _cfg["thresholds"]["tpi_classes"]              # [0.2, 0.4, 0.6]

START = dt.date.fromisoformat(str(EVENT["event_date"])) - dt.timedelta(
    days=EVENT["window_before_days"])
LABELS = ["Baixo", "Moderado", "Alto", "Crítico"]
TOWNS = ["Encantado", "Muçum", "Roca Sales", "Lajeado", "Canoas", "Porto Alegre"]


def tpi_class(v: float) -> int:
    return sum(v > c for c in CUTS)


def build(csv_path: Path) -> dict:
    df = pd.read_csv(csv_path)
    band_cols = sorted(
        (c for c in df.columns if c.endswith("_TPI_slide")),
        key=lambda c: int(c.split("_")[0]))
    if not band_cols:
        sys.exit(f"no *_TPI_slide band columns in {csv_path}")
    out = {}
    for _, row in df.iterrows():
        steps, last = [], None
        for col in band_cols:
            day = START + dt.timedelta(days=int(col.split("_")[0]))
            c = tpi_class(row[col])
            if c != last:  # only class changes, like the real bot would send
                steps.append({"date": day.strftime("%d/%m/%Y"), "label": LABELS[c]})
            last = c
        out[row["name"]] = steps
    missing = [t for t in TOWNS if t not in out]
    if missing:
        print(f"warning: towns missing from CSV: {missing}", file=sys.stderr)
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("csv", nargs="?", default="simular_rs_2024.csv", type=Path)
    p.add_argument("-o", "--out", default="simular.json", type=Path)
    args = p.parse_args()
    out = build(args.csv)
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"wrote {args.out} ({len(out)} towns)")


if __name__ == "__main__":
    main()
