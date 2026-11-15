"""early_warning: in-event rows dropped, positives strictly before onset, negative sets."""
import datetime as dt

import pandas as pd

from ml.local import early_warning as EW
from ml.local import settings as S


def _rows():
    d = pd.to_datetime
    return pd.DataFrame({
        "event_id": ["rs_2024"] * 5 + ["rs_2024", "rs_2024"],
        "label": [1, 1, 1, 1, 1, 0, 0],
        "date": d(["2024-05-01"] * 5 + ["2023-05-10", "2023-11-01"]),
        "issue_date": d(["2024-04-30", "2024-04-29", "2024-04-28", "2024-04-27", "2024-04-15",
                         "2023-05-10", "2023-11-01"]),
        "label_48h": [1, 1, 1, 0, 0, 0, 0],
        "lon": 0.0, "lat": 0.0,
    })


def test_ew_rows(monkeypatch):
    monkeypatch.setitem(S.EVENT_DAYS, "rs_2024", dt.date(2024, 5, 1))
    monkeypatch.setattr(S, "EVENT_ONSETS", {"rs_2024": dt.date(2024, 4, 29)})
    hz = _rows()
    assert list(EW.in_event(hz)) == [True, True, False, False, False, False, False]
    el = pd.DataFrame({"event_id": ["rs_2024"] * 2, "label": [0, 0],
                       "date": pd.to_datetime(["2024-05-01"] * 2),
                       "issue_date": pd.to_datetime(["2024-04-28", "2024-04-30"]),
                       "label_48h": [0, 0], "lon": 1.0, "lat": 1.0})
    r = EW.ew_rows(hz, "48h", el)
    got = r.groupby("neg_set").size().to_dict()
    # Apr 28 positive (k=1); Apr 27 + Apr 15 pre-event; May 10 same season kept, Nov dropped;
    # elsewhere Apr 28 kept, Apr 30 (after onset) dropped
    assert got == {"positive": 1, "pre_event": 2, "same_season": 1, "elsewhere": 1}
    assert r.y.sum() == 1


def test_source_mask_gfs_finite_and_positive_events():
    """Review 04: rows need a finite GFS window; events left without positives drop out."""
    import numpy as np

    from ml.local import scoring as SC
    df = pd.DataFrame({"issue_date": pd.to_datetime(["2020-01-01", "2022-02-14", "2022-02-15",
                                                     "2023-05-01", "2023-05-02"]),
                       "lon": 0.0, "lat": 0.0, "label": [1, 1, 0, 1, 0],
                       "event_id": ["a", "b", "b", "c", "c"]})

    def gd(lon, lat, iss, H):
        v = {np.datetime64("2022-02-14"): np.nan}
        return np.array([[v.get(np.datetime64(d, "D"), 1.0)] * H for d in iss])

    assert SC.source_mask(df, "perfect").all()
    assert list(SC.source_mask(df, "gfs", gd, 2)) == [False, False, False, True, True]
