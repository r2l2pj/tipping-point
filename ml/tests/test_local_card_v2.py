"""ml_card_v2: v1 card shape + per-event by_event, test_events, generalise (numbers)."""
from ml.local import export as E


def _m(n_pos, pr):
    return {"n": 100, "n_pos": n_pos, "pr_auc": pr, "roc_auc": 0.8, "extra": 1}


def _met():
    blk = {"xgb_rain_soil": {"test": _m(60, 0.5),
                             "by_event": {"rs_2024": _m(40, 0.6), "helene_2024": _m(20, 0.3)},
                             "loeo": {"rs_2024": _m(20, 0.3), "helene_2024": _m(40, 0.6)}}}
    return {"3a": {"models": {"xgb_all": {"test": _m(10, 0.4)}}},
            "3b": {"horizons": {"24h": {"gfs": blk, "perfect": blk}}},
            "by_lead": {"definition": "d", "scopes": {"pooled": {"1": {"both": {"x": _m(5, 0.2)}}},
                                                      "rs_2024": {"1": {}}}}}


def test_card_v2_shape():
    c = E.ml_card_v2(_met())
    assert c["version"] == "v2" and c["t_ml_excludes_rs"] is True
    be = c["trigger"]["24h"]["gfs"]["xgb_rain_soil"]["by_event"]
    assert set(be) == {"rs_2024", "helene_2024"} and "extra" not in be["rs_2024"]
    ids = [e["id"] for e in c["test_events"]]
    assert ids == ["helene_2024", "rs_2024"]
    assert c["test_events"][1]["n_pos_24h"] == 40
    g = c["generalise"]["gfs"]["24h"]["xgb_rain_soil"]
    assert g["events_n"] == 2 and abs(g["event_median"] - 0.45) < 1e-9 and g["worst_event"] == "helene_2024"
    assert g["loeo_min"] == 0.3 and g["loeo_max"] == 0.6
    assert set(c["by_lead"]["scopes"]) == {"pooled", "rs_2024"}
    assert "extra" not in c["by_lead"]["scopes"]["pooled"]["1"]["both"]["x"]


def test_by_lead_onset_anchor(monkeypatch):
    import datetime as dt

    import pandas as pd

    from ml.local import by_lead as BL
    from ml.local import settings as S
    monkeypatch.setitem(S.EVENT_DAYS, "rs_2024", dt.date(2024, 5, 1))
    monkeypatch.setattr(S, "EVENT_ONSETS", {"rs_2024": dt.date(2024, 4, 29)})
    df = pd.DataFrame({"date": pd.to_datetime(["2024-05-01", "2025-12-10"]),
                       "event_id": ["rs_2024", "wa_2025"]})
    assert list(BL.event_day(df).dt.day) == [1, 10]
    assert list(BL.event_day(df, "onset").dt.strftime("%m-%d")) == ["04-29", "12-10"]
