"""tools/prediction_lift.py — pure pieces (hermetic)."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
import prediction_lift as pl


def test_area_shares_and_lift():
    tpi = np.array([[0.5, 0.7], [0.1, np.nan]])
    km2 = np.ones((2, 2))
    a = pl.area_shares(tpi, km2, np.ones((2, 2), bool))
    assert a["domain_km2"] == 3.0
    assert a["alto_plus_share_pct"] == round(100 * 2 / 3, 2)
    assert a["critico_share_pct"] == round(100 / 3, 2)
    assert pl.lift(50.0, 25.0) == 2.0
    assert pl.lift(10.0, 0.0) is None and pl.lift(None, 5.0) is None
