"""Offline tests for ml.local.combine — synthetic scores, no data files."""
from __future__ import annotations

import numpy as np
import pytest

from ml.local import combine as C
from ml.local import scoring as SC


def test_weight_grid_simplex():
    g2 = C.weight_grid(2, step=0.5)
    assert g2 == [(0.0, 1.0), (0.5, 0.5), (1.0, 0.0)]
    g3 = C.weight_grid(3, step=0.05)
    assert all(abs(sum(w) - 1.0) < 1e-9 for w in g3)
    assert len(g3) == 231  # compositions of 20 into 3 parts
    assert all(len(w) == 3 and min(w) >= 0.0 for w in g3)


def test_vote_count_and_nan():
    scores = {"a": np.array([0.9, 0.1, 0.8, np.nan]),
              "b": np.array([0.2, 0.7, 0.9, 0.5])}
    thr = {"a": 0.5, "b": 0.5}
    v = C.vote_count(scores, thr, ("a", "b"))
    assert v.tolist()[:3] == [1.0, 1.0, 2.0]
    assert np.isnan(v[3])


def test_vote_rules_metrics():
    # 4 rows: (a,b) votes -> and needs 2, or needs 1
    y = np.array([1, 1, 0, 0])
    votes = np.array([2.0, 1.0, 1.0, 0.0])
    m_and = SC.metrics(y, votes, 2.0)
    assert (m_and["tp"], m_and["fp"], m_and["fn"], m_and["tn"]) == (1, 0, 1, 2)
    m_or = SC.metrics(y, votes, 1.0)
    assert (m_or["tp"], m_or["fp"], m_or["fn"], m_or["tn"]) == (2, 1, 0, 1)
    # trio majority needs 2
    votes3 = np.array([3.0, 2.0, 1.0, 0.0])
    m_maj = SC.metrics(y, votes3, 2.0)
    assert (m_maj["tp"], m_maj["fp"], m_maj["fn"], m_maj["tn"]) == (2, 0, 0, 2)


def test_fit_blend_prefers_the_good_member():
    rng = np.random.default_rng(0)
    n = 400
    y = np.concatenate([np.ones(n // 2, int), np.zeros(n // 2, int)])
    good = np.concatenate([rng.uniform(0.6, 1.0, n // 2), rng.uniform(0.0, 0.4, n // 2)])
    noise = np.concatenate([rng.uniform(0.3, 0.7, n // 2), rng.uniform(0.3, 0.7, n // 2)])
    bl = C.fit_blend({"a": good, "b": noise}, y, ("a", "b"), step=0.1)
    assert abs(sum(bl["weights"].values()) - 1.0) < 1e-9
    assert bl["weights"]["a"] >= 0.9
    assert bl["val_f1"] == pytest.approx(1.0, abs=1e-6)
    # blend score on the same rows separates perfectly at the chosen threshold
    s = bl["weights"]["a"] * good + bl["weights"]["b"] * noise
    m = SC.metrics(y, s, bl["threshold"])
    assert m["f1"] == pytest.approx(1.0, abs=1e-6)


def _ref(metrics):
    return {"3b": {"horizons": {"24h": {"gfs": {"xgb_all": {"test": metrics}}}}}}


def test_assert_matches_passes_and_fails():
    y = np.array([1, 1, 0, 0])
    p = np.array([0.9, 0.8, 0.4, 0.1])
    got = SC.metrics(y, p, 0.5) | {"pr_auc_raw": 0.99, "roc_auc_raw": 0.98}
    C.assert_matches(_ref(dict(got)), "24h", "gfs", "xgb_all", got)
    bad = dict(got, f1=got["f1"] + 0.5)
    with pytest.raises(AssertionError, match="xgb_all"):
        C.assert_matches(_ref(dict(got)), "24h", "gfs", "xgb_all", bad)
