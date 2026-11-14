"""Required coverage: block-CV disjointness, XGBoost monotonicity, metrics."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ml import config as cfg
from ml.dataset import block_ids, iter_block_folds
from ml.evaluate import manual_tpi_score, metric_bundle, threshold_at_fpr
from ml.train import build_classifier


def synthetic_table(n_blocks=(8, 4), per_block=30, seed=0):
    """Cells spread over a lon/lat grid of 0.25° blocks, two t values each."""
    rng = np.random.default_rng(seed)
    rows = []
    for bi in range(n_blocks[0]):
        for bj in range(n_blocks[1]):
            lon0, lat0 = cfg.AOI_BBOX[0] + bi * 0.25, cfg.AOI_BBOX[1] + bj * 0.25
            for i in range(per_block):
                lon = lon0 + rng.uniform(0.01, 0.24)
                lat = lat0 + rng.uniform(0.01, 0.24)
                rain = rng.uniform(0, 200)
                slope = rng.uniform(0, 40)
                sm = rng.uniform(0.2, 1.0)
                y = int(rain * (slope / 40) * sm > 30)
                for t in ("2024-04-30", "2024-05-02"):
                    rows.append({
                        "cell_id": len(rows), "lon": lon, "lat": lat, "t_utc": t,
                        "label": y, "set": "pos" if y else "N1",
                        "slope_mean": slope, "slope_max": slope * 1.2,
                        "relief_500m": rng.uniform(0, 100), "curv": rng.normal(),
                        "aspect_sin": rng.uniform(-1, 1), "aspect_cos": rng.uniform(-1, 1),
                        "elev": rng.uniform(0, 800), "hand_proxy": rng.uniform(0, 30),
                        "rain_24h": rain, "rain_ante_2d": rng.uniform(0, 100),
                        "rain_7d": rain + rng.uniform(0, 100),
                        "rain_rel": rain / 150, "sm_profile_wetness": sm,
                    })
    return pd.DataFrame(rows)


def test_block_folds_are_disjoint():
    df = synthetic_table()
    df["block_id"] = block_ids(df["lon"], df["lat"])
    seen_test = set()
    for tr, te in iter_block_folds(df, n_splits=5):
        train_blocks = set(df["block_id"].iloc[tr])
        test_blocks = set(df["block_id"].iloc[te])
        assert train_blocks.isdisjoint(test_blocks)
        # stronger: no cell on both sides either
        assert set(df["cell_id"].iloc[tr]).isdisjoint(df["cell_id"].iloc[te])
        assert not seen_test & set(df["cell_id"].iloc[te])
        seen_test |= set(df["cell_id"].iloc[te])
    assert seen_test == set(df["cell_id"])


def test_xgboost_monotone_in_rain():
    """predict_proba is non-decreasing in rain_24h on a synthetic sweep."""
    df = synthetic_table(n_blocks=(4, 3), per_block=40, seed=1)
    cols = ["rain_24h", "rain_7d", "sm_profile_wetness", "slope_mean", "slope_max"]
    model = build_classifier(cols, n_estimators=60, early_stopping_rounds=None)
    model.fit(df[cols], df["label"])

    sweep = pd.DataFrame(
        {c: np.full(50, df[c].median()) for c in cols}
    )
    sweep["rain_24h"] = np.linspace(0, 250, 50)
    sweep["rain_7d"] = sweep["rain_24h"] + 20  # also constrained +1
    p = model.predict_proba(sweep)[:, 1]
    assert np.all(np.diff(p) >= -1e-9)


def test_metrics_on_toy():
    # perfect ranking
    m = metric_bundle([0, 0, 1, 1], [0.1, 0.2, 0.8, 0.9])
    assert m["roc_auc"] == pytest.approx(1.0)
    assert m["pr_auc"] == pytest.approx(1.0)
    assert m["tpr_at_fpr05"] == pytest.approx(1.0)
    # imperfect ranking: 7 of 9 pairs concordant; at FPR 5% the interpolated
    # TPR is 2/3 (thresholds 0.9/0.7 already give TPR 2/3 at FPR 0)
    y, s = [0, 0, 0, 1, 1, 1], [0.1, 0.5, 0.6, 0.4, 0.7, 0.9]
    m = metric_bundle(y, s)
    assert m["roc_auc"] == pytest.approx(7 / 9)
    assert m["pr_auc"] == pytest.approx(13 / 15)
    assert m["tpr_at_fpr05"] == pytest.approx(2 / 3)
    # lowest threshold keeping FPR ≤ 5% (0.6 would already produce an FP)
    thr = threshold_at_fpr(y, s)
    assert thr == pytest.approx(0.7)


def test_manual_tpi_is_rain_soil_slope_geomean():
    df = pd.DataFrame({
        "rain_24h": [0.0, 300.0, 0.0, 100.0],
        "rain_ante_2d": [0.0, 300.0, 0.0, 100.0],
        "sm_profile_wetness": [0.9, 0.9, 0.9, 0.9],
        "slope_max": [30.0, 30.0, 30.0, 30.0],
    })
    s = manual_tpi_score(df, percentiles=(50.0, 200.0, 400.0))
    assert s[0] == pytest.approx(0.0)          # no rain → 0
    assert s[1] == pytest.approx(1.0)          # ≥p99 rain, wet, steep → 1
    assert s[1] > s[2]                         # rain is the trigger
    # geometric mean: rain3 == p95 -> r=0.5, s=t=1 -> ~0.5^(1/3) (not 0.5;
    # abs tol covers the config's rounded 0.3333 weights)
    assert s[3] == pytest.approx(0.5 ** (1 / 3), abs=1e-3)
    s3 = manual_tpi_score(df, percentiles=(50.0, 200.0, 400.0),
                          weights=(1 / 3, 1 / 3, 1 / 3))
    assert s3[3] == pytest.approx(0.5 ** (1 / 3))
    # weights honoured: w_R=1 -> score == r
    s2 = manual_tpi_score(df, percentiles=(50.0, 200.0, 400.0),
                          weights=(1.0, 0.0, 0.0))
    assert s2[3] == pytest.approx(0.5)
