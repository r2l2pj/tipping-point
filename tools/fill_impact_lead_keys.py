"""Fill the Impacto / Nível 1 keys that were null only for bookkeeping reasons.

  gpw_year, gpw_product, population_total (both cases) — the case population
      COG (cog/<case>_pop_gpw_<ref>.tif) is the GPW v4.11 population COUNT, epoch 2020 (the latest epoch; nearest to both
      events). Verified for rs_2024: the case COG equals the GPW v4.11 2020
      30-arc-sec count cell for cell (sum 5,298,955). population_total = sum of
      the case COG over the case AOI, so the done-when "people_critical <
      population of the area" is checkable.
  lead_time_mean_h (rs_2024) — cluster mode stores a 6-h histogram of
      lead_hours = anchor - first Crítico crossing. Crossings are daily 00Z
      frames and the anchor is fixed (2024-04-27 15:00Z), so every lead is
      -9 - 24 k h and each occupied 6-h bin holds exactly one value: the mean is
      exact, not an approximation. Refuses to run if a bin could hold two values.

    PYTHONPATH=.:pipeline .venv/bin/python tools/fill_impact_lead_keys.py [--data-root data]
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import numpy as np
import rasterio

REPO = Path(__file__).resolve().parents[1]
POP = {"rs_2024": "rs_2024_pop_gpw_20240502.tif", "wa_2025": "wa_2025_pop_gpw_20251210.tif"}
GPW_PRODUCT = "GPW v4.11 population count (CIESIN/NASA SEDAC), 30 arc-sec"


def population_total(path: Path) -> float:
    with rasterio.open(path) as src:
        a = src.read(1).astype("float64")
    a[(a < -1e30) | ~np.isfinite(a)] = 0.0       # GPW water/uninhabited = -3.4e38
    return float(a.sum())


def mean_from_hist(hist: list[int], from_h: float, bin_h: float, lead_values: np.ndarray) -> float:
    total, n = 0.0, 0
    for i, c in enumerate(hist):
        if not c:
            continue
        lo, hi = from_h + i * bin_h, from_h + (i + 1) * bin_h
        inside = lead_values[(lead_values >= lo) & (lead_values < hi)]
        if len(inside) != 1:
            raise SystemExit(f"bin [{lo},{hi}) holds {len(inside)} possible lead values; mean not exact")
        total += c * float(inside[0])
        n += c
    return total / n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-root", type=Path, default=REPO / "data")
    args = ap.parse_args(argv)
    path = args.data_root / "metrics.json"
    m = json.loads(path.read_text())
    for case, f in POP.items():
        blob = m["cases"][case]
        tot = population_total(args.data_root / case / "cog" / f)
        blob["gpw_year"] = 2020
        blob["gpw_product"] = GPW_PRODUCT
        blob["population_total"] = round(tot)
        blob["population_total_scope"] = "sum of the case GPW COG over the case AOI"
        blob.get("_null_reasons", {}).pop("gpw_year", None)
        pc = blob.get("people_critical")
        print(f"{case}: population_total {tot:,.0f}, people_critical {pc}")
        if pc is not None and not pc < tot:
            raise SystemExit(f"{case}: people_critical >= population_total — check inputs")
    rs = m["cases"]["rs_2024"]
    lt = json.loads((args.data_root / "rs_2024" / "lead_time_metrics.json").read_text())["metrics"]
    first = lt["event_lead_h"]                        # lead of the first daily crossing (-9 h)
    leads = first - 24.0 * np.arange(0, 30)
    mean = mean_from_hist(rs["lead_time_hist"], rs["lead_time_hist_from_h"], rs["lead_time_hist_bin_h"], leads)
    rs["lead_time_mean_h"] = round(mean, 2)
    rs["lead_time_mean_note"] = ("exact from lead_time_hist: daily 00Z crossings vs the fixed anchor "
                                 "put each occupied 6-h bin on a single lead value")
    rs.get("_null_reasons", {}).pop("lead_time_mean_h", None)
    print(f"rs_2024 lead_time_mean_h {mean:.2f} (median {rs['lead_time_median_h']})")
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(m, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
