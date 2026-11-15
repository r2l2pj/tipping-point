"""C1 acceptance: every case blob in data/metrics.json carries each required
Mode 3 / impact / ML / lead-time key, as a value or as null + a reason.

Key list: doc v3 'Modo 3. Validação' + impact + predictive levels (tp4
extract) and data/SCHEMA.md 'data/metrics.json'. Reads the repo file
read-only.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

METRICS = Path(__file__).resolve().parents[2] / "data" / "metrics.json"
CASES = ("rs_2024", "wa_2025")   # replay cases; the live node lives in now/summary.json

REQUIRED = (
    # Mode 3 (doc step 7)
    "iou", "f1", "cloud_pct", "hand_validation",
    # impact layer
    "people_critical", "night_light_drop_pct", "gpw_year", "flood_source",
    # predictive level 1 (lead time)
    "lead_time_mean_h", "lead_time_median_h", "warned_before_pct",
    # level 2 flag + level 3 ML
    "forecast_source", "rf_auc", "rule_auc", "feature_importance",
)
# Required too, but written by the lead-analysis unit (in flight at audit time).
LEAD_OWNED = ("landslide_hit_pct", "people_flooded", "flooded_km2",
              "forecast_lead_time")


def _cases() -> dict:
    return json.loads(METRICS.read_text())["cases"]


def _check(case: str, keys) -> list[str]:
    blob = _cases().get(case)
    assert blob is not None, f"case node {case!r} missing"
    reasons = blob.get("_null_reasons") or {}
    bad = []
    for key in keys:
        if key not in blob:
            bad.append(f"{key}: MISSING")
        elif blob[key] is None and not str(reasons.get(key, "")).strip():
            bad.append(f"{key}: null without _null_reasons entry")
    return bad


@pytest.mark.parametrize("case", CASES)
def test_required_keys_present_or_null_with_reason(case):
    assert _check(case, REQUIRED) == []


@pytest.mark.parametrize("case", CASES)
def test_every_null_has_reason(case):
    blob = _cases()[case]
    reasons = blob.get("_null_reasons") or {}
    missing = [k for k, v in blob.items()
               if v is None and not str(reasons.get(k, "")).strip()]
    assert missing == []


@pytest.mark.parametrize("case", CASES)
def test_lead_owned_keys(case):
    assert _check(case, LEAD_OWNED) == []
