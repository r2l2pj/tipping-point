"""Offline tests for ml.local pipeline modules — labels/split/models/scoring/
serve/table. Synthetic data only; no network, no real data files.

sources_dynamic / sources_static entry points are faked where a stage would
otherwise hit the network (sample_3a random_land_points, build_table_*).
"""
from __future__ import annotations

import datetime as dt
import json

import numpy as np
import pandas as pd
import pytest

from ml.local import labels as L
from ml.local import models as M
from ml.local import scoring as SC
from ml.local import settings as S
from ml.local import split as SP
from ml.local import table as T


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "DATA_DIR", tmp_path)
    monkeypatch.setattr(S, "MODEL_DIR", tmp_path / "models")
    return tmp_path


def _glc_feat(eid, lon, lat, ms=None, acc="exact", trig="rain", title="t"):
    return {
        "properties": {
            "event_id": eid, "event_date": ms, "location_accuracy": acc,
            "landslide_trigger": trig, "landslide_category": "landslide",
            "event_title": title, "latitude": lat, "longitude": lon,
            "country_name": "x",
        },
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
    }


def _inv_rows(spec):
    """spec: list of (lon, lat, source, event_id, date, acc_km, trigger)."""
    return pd.DataFrame(
        [{"point_id": f"p{i}", "lon": lo, "lat": la, "source": src,
          "event_id": ev, "date": pd.Timestamp(d) if d is not None else pd.NaT,
          "date_precision": "day" if d is not None else "none",
          "acc_km": acc, "trigger": trig, "title": ""}
         for i, (lo, la, src, ev, d, acc, trig) in enumerate(spec)],
        columns=L.COLUMNS)


# ----------------------------------------------------------------------- labels
def test_glc_frame_parses_fields():
    ms = int(pd.Timestamp("2020-03-14T12:34:00Z").value / 1e6)
    df = L.glc_frame([
        _glc_feat(101, -51.0, -29.3, ms, acc="1km", trig="Downpour", title="A"),
        _glc_feat(102, -50.5, -29.0, None, acc="bogus", trig="earthquake"),
        _glc_feat(103, None, None, ms),                     # missing coords -> skipped
        _glc_feat(104, -51.2, -29.4, ms, acc="exact"),      # no geometry coords
        {"properties": {"event_id": 105, "longitude": -52.0, "latitude": -28.0},
         "geometry": None},                                # geometry fallback -> props
    ])
    assert list(df.columns) == L.COLUMNS
    assert len(df) == 4
    r = df[df.point_id == "glc_101"].iloc[0]
    assert r.date == pd.Timestamp("2020-03-14")      # normalised to day
    assert r.acc_km == 1.0 and r.trigger == "downpour" and r.date_precision == "day"
    assert r.event_id is None                        # events assigned later
    undated = df[df.point_id == "glc_102"].iloc[0]
    assert pd.isna(undated.date) and undated.date_precision == "none"
    assert pd.isna(undated.acc_km)                   # unknown accuracy -> NaN
    fb = df[df.point_id == "glc_105"].iloc[0]
    assert fb.lon == -52.0 and fb.lat == -28.0


def test_filter_glc():
    df = _inv_rows([
        (-51.0, -29.3, "glc", None, "2020-01-10", 0.0, "rain"),
        (-51.1, -29.4, "glc", None, "2020-01-11", 6.0, "rain"),      # acc > 5
        (-51.2, -29.5, "glc", None, "2014-01-01", 1.0, "rain"),      # too old
        (-51.3, -29.6, "glc", None, "2020-01-12", 2.0, "earthquake"),  # not rain
        (-10.0, 0.0, "glc", None, "2020-01-13", 0.0, "rain"),        # outside bbox
        (-51.4, -29.7, "glc", None, "2020-01-14", np.nan, "rain"),   # unknown acc
    ])
    keep = L.filter_glc(df, since="2015-01-01", rain_only=True,
                        bbox=(-52.0, -30.0, -50.0, -28.0))
    assert keep["point_id"].tolist() == ["p0"]
    assert len(L.filter_glc(df)) == 4  # acc<=5 only (0,1,2km + outside-bbox one)


def test_in_bbox():
    m = L.in_bbox(np.array([-51.0, -52.0, -50.6]), np.array([-29.0, -29.0, -29.0]),
                  (-51.5, -29.5, -50.5, -28.5))
    assert m.tolist() == [True, False, True]


def test_chord_km_roundtrip():
    for km in (0.5, 10.0, 500.0):
        assert np.isclose(L.chord_to_km(L.km_to_chord(km)), km)


def test_min_dist_km():
    d = L.min_dist_km(np.array([-51.0, -51.0]), np.array([-29.0, -28.0]),
                      np.array([-51.0]), np.array([-29.0]))
    assert d[0] == pytest.approx(0.0, abs=1e-6)
    assert d[1] == pytest.approx(111.195, abs=1.5)  # ~1 deg lat
    assert np.isinf(L.min_dist_km(np.array([0.0]), np.array([0.0]),
                                  np.array([]), np.array([]))).all()


def test_dedupe_grid():
    df = _inv_rows([
        (-51.001, -29.301, "glc", None, "2020-01-10", 0.0, "rain"),
        (-51.0015, -29.3015, "glc", None, "2020-01-10", 0.0, "rain"),  # same 90m cell+date
        (-51.0015, -29.3015, "glc", None, "2020-01-11", 0.0, "rain"),  # same cell, other date
        (-51.02, -29.32, "glc", None, "2020-01-10", 0.0, "rain"),      # other cell
    ])
    out = L.dedupe_grid(df)
    assert len(out) == 3 and out.iloc[0].point_id == "p0"  # first wins


def test_cluster_events():
    df = _inv_rows([
        (-51.00, -29.00, "glc", None, "2020-01-10", 0.0, "rain"),  # A
        (-51.20, -29.00, "glc", None, "2020-01-11", 0.0, "rain"),  # B ~20 km, +1 d -> same
        (-51.50, -29.00, "glc", None, "2020-01-12", 0.0, "rain"),  # C ~30 km from B -> transitive same
        (-54.00, -29.00, "glc", None, "2020-01-12", 0.0, "rain"),  # D >200 km -> own
        (-51.00, -29.00, "glc", None, "2020-02-20", 0.0, "rain"),  # E same place, +41 d -> own
        (-51.00, -29.00, "glc", None, None, 0.0, "rain"),          # F undated -> own
        (-51.00, -29.00, "glc", "kept_ev", "2020-01-10", 0.0, "rain"),  # G keeps given id
    ])
    ids = L.cluster_events(df)
    assert ids[0] == ids[1] == ids[2]                  # transitive A-B-C
    assert ids[3] != ids[0] and ids[4] != ids[0] and ids[4] != ids[3]
    assert ids[5].startswith("glc_undated_")
    assert ids[6] == "kept_ev"
    assert ids[0].startswith("glc_202001")             # event named by first date


def test_report_region():
    names = L.report_region(
        np.array([-51.0, -75.0, 85.0, 170.0]),
        np.array([-29.0, 6.0, 27.0, 60.0]))
    assert names.tolist() == ["south_southeast_brazil", "south_america",
                              "asia", "other"]


def test_load_rs2024_synthetic(tmp_path, monkeypatch):
    gj = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {},
         "geometry": {"type": "MultiPoint", "coordinates": [[-51.0, -29.0, 100],
                                                          [-51.1, -29.1]]}},
        {"type": "Feature", "properties": {},
         "geometry": {"type": "Point", "coordinates": [-51.2, -29.2]}}]}
    path = tmp_path / "cat.geojson"
    path.write_text(json.dumps(gj))
    monkeypatch.setattr(S, "RS2024_CATALOG", path)
    df = L.load_rs2024(path)
    assert len(df) == 3
    assert (df.event_id == S.RS2024_EVENT_ID).all()
    assert df.date.isna().all() and (df.date_precision == "event-window").all()
    assert (df.point_id == [f"rs2024_{i}" for i in range(3)]).all()


def test_assign_peak_day():
    df = _inv_rows([
        (-51.0, -29.0, "rs2024", "rs_2024", None, 0.0, "rain"),  # peaks day idx 2
        (-51.1, -29.1, "rs2024", "rs_2024", None, 0.0, "rain"),  # tie -> earliest
        (-51.2, -29.2, "rs2024", "rs_2024", None, 0.0, "rain"),  # all NaN -> NaT
    ])
    win = (dt.date(2024, 4, 27), dt.date(2024, 5, 1))

    def daily(lon, lat, dates):
        d = np.asarray(dates, dtype="datetime64[D]")
        j = (d - np.datetime64("2024-04-27")).astype(int)
        r = np.where(j == 2, 50.0, 1.0)              # point pattern shared, use lon
        r = np.where(np.asarray(lon) == -51.1, np.where(j == 1, 9.0, r), r)
        r = np.where(np.asarray(lon) == -51.1, np.where(j == 2, 9.0, r), r)
        r = np.where(np.asarray(lon) == -51.2, np.nan, r)
        return r

    out = L.assign_peak_day(df, daily, window=win)
    assert out.date.iloc[0] == pd.Timestamp("2024-04-29")
    assert out.date.iloc[1] == pd.Timestamp("2024-04-28")  # tie (9 == 9) -> earliest
    assert pd.isna(out.date.iloc[2])


# ------------------------------------------------------------------------ split
def _units(items):
    return pd.DataFrame({"unit_id": [u for u, _ in items],
                         "n_pos": [n for _, n in items]})


def test_greedy_assign_fractions_by_pos_count():
    units = _units([(f"u{i}", 10) for i in range(10)] + [("z0", 0), ("z1", 0)])
    out = SP.greedy_assign(units, seed=1)
    assert len(out) == 12 and set(out) <= set(units.unit_id)
    pos = units.assign(split=units.unit_id.map(out)).groupby("split").n_pos.sum()
    # 100 positives -> 60/20/20 +- one unit granularity
    assert abs(pos["train"] - 60) <= 10
    assert abs(pos["val"] - 20) <= 10
    assert abs(pos["test"] - 20) <= 10
    assert out["z0"] in SP.SPLITS and out["z1"] in SP.SPLITS  # zero-pos drawn


def test_greedy_assign_forced_counted():
    units = _units([("big", 100)] + [(f"u{i}", 10) for i in range(5)])
    out = SP.greedy_assign(units, seed=0, forced={"big": "test"})
    assert out["big"] == "test"
    # forced 100 pos already overshoots the 20% share of the free 50 -> nothing
    # else may go to test; free units split ~30/10 train/val
    free = units.assign(split=units.unit_id.map(out))
    free = free[free.unit_id != "big"].groupby("split").n_pos.sum()
    assert free.get("test", 0) == 0
    assert free["train"] + free["val"] == 50


def test_split_blocks_3a_merges_small_events():
    pos = _inv_rows([
        (-51.10, -29.10, "glc", "evA", "2020-01-10", 0.0, "rain"),  # block (-103,-59)
        (-51.60, -29.10, "glc", "evA", "2020-01-10", 0.0, "rain"),  # block (-104,-59) -> merge
        (-50.90, -29.55, "glc", "evB", "2021-01-10", 0.0, "rain"),  # own block/event
    ])
    blocks = SP.split_blocks_3a(pos, bbox=S.SMOKE_BBOX, size=0.5)
    assert {"block", "block_x", "block_y", "unit_id", "n_pos", "split"} <= set(blocks.columns)
    a = blocks[blocks.n_pos > 0]
    assert len(a) == 3
    u_a = a[a.block.isin(["-104_-59", "-103_-59"])]
    assert u_a.unit_id.nunique() == 1 and u_a["split"].nunique() == 1  # merged
    assert a[a.block == "-102_-60"].unit_id.iloc[0] != u_a.unit_id.iloc[0]
    assert blocks["split"].isin(SP.SPLITS).all()


def test_split_blocks_3a_large_event_not_merged():
    # 4-pt event across 2 blocks with merge_max_points=3 -> no merge
    pos = _inv_rows([
        (-51.05, -29.05, "glc", "evC", "2020-01-10", 0.0, "rain"),
        (-51.10, -29.05, "glc", "evC", "2020-01-10", 0.0, "rain"),
        (-51.60, -29.05, "glc", "evC", "2020-01-10", 0.0, "rain"),
        (-51.65, -29.05, "glc", "evC", "2020-01-10", 0.0, "rain"),
    ])
    blocks = SP.split_blocks_3a(pos, bbox=S.SMOKE_BBOX, size=0.5, merge_max_points=3)
    hit = blocks[blocks.n_pos > 0]
    assert hit.unit_id.nunique() == 2


def test_rows_split_3a_buffer():
    blocks = pd.DataFrame({
        "block": ["0_0", "1_0", "0_1"], "block_x": [0, 1, 0], "block_y": [0, 0, 1],
        "unit_id": ["0_0", "1_0", "0_1"], "n_pos": [0, 0, 0],
        "split": ["train", "test", "val"]})
    # lat 0 -> km_x = 0.5*111.32 = 55.66; row at fx=0.98 is ~1.1 km from test block
    lon = np.array([0.49, 0.25, 0.75, 0.49, 1.25])
    lat = np.array([0.25, 0.25, 0.25, 0.05, 0.25])
    out = SP.rows_split_3a(lon, lat, blocks, size=0.5, buffer_km=10.0)
    # 0: train block, near test -> buffer. 1: deep inside train -> train.
    # 2: inside test block -> test (never buffered down). 3: near val -> buffer.
    # 4: lon 1.25 outside known blocks -> defaults train, no held-out neighbour in table
    assert out[0] == "buffer"
    assert out[1] == "train"
    assert out[2] == "test"
    assert out[3] == "buffer"


def test_buffer_rows():
    # train point ~5 km from a test point -> buffer; test point never buffered
    lon = np.array([0.0, 0.045, 2.0, 2.05])
    lat = np.array([0.0, 0.0, 0.0, 0.0])
    out = SP.buffer_rows(lon, lat,
                         np.array(["train", "test", "train", "train"], dtype=object),
                         buffer_km=10.0)
    assert out.tolist() == ["buffer", "test", "train", "train"]
    # val point near train point buffers it too
    out2 = SP.buffer_rows(np.array([0.0, 0.045]), np.array([0.0, 0.0]),
                          np.array(["train", "val"], dtype=object), buffer_km=10.0)
    assert out2.tolist() == ["buffer", "val"]


def test_split_events_3b_forces_rs2024_test():
    pos = _inv_rows([
        (-51.0, -29.0, "rs2024", "rs_2024", "2024-05-01", 0.0, "rain"),
        (-51.1, -29.1, "rs2024", "rs_2024", "2024-05-02", 0.0, "rain"),
        (85.0, 27.0, "glc", "glc_a", "2020-01-10", 1.0, "rain"),
        (-75.0, 6.0, "glc", "glc_b", "2020-02-10", 1.0, "rain"),
    ])
    ev = SP.split_events_3b(pos, seed=0)
    row = ev[ev.event_id == "rs_2024"].iloc[0]
    assert row["split"] == "test" and row["forced"] and row.n_pos == 2
    assert set(ev["split"]) <= set(SP.SPLITS)
    assert (~ev[ev.event_id != "rs_2024"]["forced"]).all()


def test_summary_counts():
    rows = pd.DataFrame({
        "lon": [-51.0, -51.1, -10.0], "lat": [-29.0, -29.1, 0.0],
        "date": pd.to_datetime(["2020-01-01", "2020-01-02", None]),
        "label": [1, 0, 1], "event_id": ["e1", "e1", "e2"],
        "region": ["x", "x", "y"], "split": ["train", "train", "test"]})
    s = SP.summary(rows)
    assert s["train"]["n_rows"] == 2 and s["train"]["n_pos"] == 1
    assert s["test"]["n_events"] == 1 and s["test"]["date_min"] is None


# ----------------------------------------------------------------------- models
def test_prepare_X_categorical():
    df = pd.DataFrame({"landcover": [5, 10, np.nan], "slope": [1.0, 2.0, 3.0],
                       "label": [0, 1, 0]})
    Xxgb = M.prepare_X(df, ["landcover", "slope"])
    assert str(Xxgb["landcover"].dtype) == "category" and Xxgb["slope"].dtype == "float64"
    Xrf = M.prepare_X(df, ["landcover", "slope"], for_rf=True)
    assert Xrf["landcover"].dtype == "float64"


def test_fit_calibrator_isotonic_branch():
    rng = np.random.default_rng(0)
    y = np.concatenate([np.zeros(60), np.ones(40)])
    p = np.clip(y + rng.normal(0, 0.15, 100), 0, 1)
    cal = M.fit_calibrator(p, y)
    assert cal.kind == "isotonic"                       # >= MIN_ISOTONIC_POS positives
    out = cal(np.array([0.0, 0.25, 0.5, 0.75, 1.0]))
    assert np.all(np.diff(out) >= -1e-9) and out.min() >= 0 and out.max() <= 1


def test_fit_calibrator_platt_branch():
    rng = np.random.default_rng(1)
    y = np.concatenate([np.zeros(90), np.ones(10)])
    p = np.clip(y * 0.8 + 0.1 + rng.normal(0, 0.05, 100), 1e-4, 1 - 1e-4)
    cal = M.fit_calibrator(p, y)
    assert cal.kind == "platt"
    out = cal(np.linspace(0.01, 0.99, 25))
    assert np.all(np.diff(out) > 0)  # logistic in p is strictly increasing here
    d = cal.to_dict()
    assert M.Calibrator.from_dict(d)(0.5) == pytest.approx(cal(0.5))


def test_fit_calibrator_single_class_identity():
    cal = M.fit_calibrator(np.array([0.2, 0.4]), np.array([0, 0]))
    assert cal.kind == "identity"
    assert np.allclose(cal(np.array([0.2, 0.4])), [0.2, 0.4])


def test_best_f1_threshold():
    y = np.concatenate([np.zeros(50), np.ones(50)])
    p = np.concatenate([np.full(50, 0.1), np.full(50, 0.9)])
    thr = M.best_f1_threshold(p, y)
    assert 0.1 < thr <= 0.9                                # separates perfectly
    assert M.best_f1_threshold(p, np.zeros(100, dtype=int)) == 0.5


def _tiny_xgb(cols, seed=0):
    """Small real XGBClassifier on synthetic separable data."""
    from xgboost import XGBClassifier

    rng = np.random.default_rng(seed)
    n = 60
    rain = rng.uniform(0, 100, n)
    X = pd.DataFrame({
        "rain_24h": rain, "rain_72h": rain * 2 + rng.normal(0, 5, n),
        "rain_7d": rain * 5 + rng.normal(0, 10, n),
        "rain_24h_rel": rain / 50.0, "rain_72h_rel": rain / 30.0,
        "slope": rng.uniform(0, 40, n), "landcover": rng.integers(1, 17, n).astype(float),
        "doy_sin": rng.uniform(-1, 1, n), "doy_cos": rng.uniform(-1, 1, n),
    })
    y = (rain > 50).astype(int)
    m = XGBClassifier(n_estimators=8, max_depth=2, learning_rate=0.5,
                      enable_categorical=True, random_state=seed)
    m.fit(M.prepare_X(X.assign(label=y), cols), y)
    return m, cols


def test_xgb_fit_predict_roundtrip(tmp_path):
    cols = ["rain_24h", "slope", "landcover", "doy_sin", "doy_cos"]
    model, cols = _tiny_xgb(cols)
    train = pd.DataFrame({"rain_24h": [80.0, 5.0], "slope": [20.0, 20.0],
                          "landcover": [5.0, 5.0], "doy_sin": [0.0, 0.0],
                          "doy_cos": [1.0, 1.0], "label": [1, 0]})
    p = M.predict(model, train, cols)
    assert p[0] > p[1]                                   # more rain -> higher p
    assert len(M.predict(model, train.iloc[:0], cols)) == 0
    path = M.save_xgb(model, tmp_path / "m.json", {"features": cols})
    m2, meta = M.load_xgb(path)
    assert meta["features"] == cols
    assert np.allclose(M.predict(m2, train, cols), p)


# ---------------------------------------------------------------------- scoring
def test_metrics_counts_and_nan():
    y = np.array([0, 0, 1, 1, 1])
    p = np.array([0.1, 0.8, 0.9, np.nan, 0.3])
    m = SC.metrics(y, p, thr=0.5)
    assert m["n"] == 4 and m["n_dropped_nan"] == 1
    assert (m["tn"], m["fp"], m["fn"], m["tp"]) == (1, 1, 1, 1)
    assert m["precision"] == 0.5 and m["recall"] == 0.5
    assert m["f1"] == 0.5 and m["accuracy"] == 0.5
    assert m["roc_auc"] is not None
    empty = SC.metrics(np.array([]), np.array([]), 0.5)
    assert empty["n"] == 0 and empty["precision"] is None


def test_slope_rule():
    out = SC.slope_rule(np.array([0.0, 15.0, 22.5, 30.0, 45.0]))
    assert np.allclose(out, [0.0, 0.0, 0.5, 1.0, 1.0])


class FakeRain:
    """daily() returns the day-of-year float; constant p99/clim."""

    def daily(self, lon, lat, dates):
        d = pd.to_datetime(np.asarray(dates).ravel())
        v = d.day_of_year.to_numpy(dtype=float)
        return np.broadcast_to(v, np.atleast_1d(lon).shape).astype(float)

    def p99_daily(self, lon, lat):
        return np.full(len(np.atleast_1d(lon)), 50.0)

    def p99_3day(self, lon, lat):
        return np.full(len(np.atleast_1d(lon)), 60.0)

    def pctl_3day(self, lon, lat, doy, **kw):
        n = len(np.atleast_1d(lon))
        return (np.full(n, 10.0), np.full(n, 20.0), np.full(n, 40.0))


def test_rain_series_offsets_perfect():
    rain = FakeRain()
    lon = np.array([-51.0, -51.1])
    lat = np.array([-29.0, -29.1])
    issue = np.array([np.datetime64("2020-03-10"), np.datetime64("2020-03-11")])
    r = SC.rain_series(rain, lon, lat, issue, 3)          # H=3 -> 7+3 cols
    assert r.shape == (2, 10)
    assert r[0, SC.OFF] == pd.Timestamp("2020-03-10").day_of_year   # col 7 = day D
    assert r[0, SC.OFF - 1] == pd.Timestamp("2020-03-09").day_of_year
    assert r[0, SC.OFF + 2] == pd.Timestamp("2020-03-12").day_of_year
    assert r[0, 0] == pd.Timestamp("2020-03-03").day_of_year        # D-7
    assert r[1, SC.OFF] == pd.Timestamp("2020-03-11").day_of_year


def test_rain_series_gfs_override_and_errors():
    rain = FakeRain()
    lon = np.array([-51.0])
    lat = np.array([-29.0])
    issue = np.array([np.datetime64("2020-03-10")])
    gfs = lambda l, a, i, H: np.full((len(np.atleast_1d(l)), H), 999.0)
    r = SC.rain_series(rain, lon, lat, issue, 3, source="gfs", gfs_daily=gfs)
    assert r[0, SC.OFF:] == pytest.approx(999.0)          # forecast cols replaced
    assert (r[0, :SC.OFF] < 400).all()                    # observed kept (doy)
    with pytest.raises(ValueError):
        SC.rain_series(rain, lon, lat, issue, 3, source="gfs")
    with pytest.raises(ValueError):
        SC.rain_series(rain, lon, lat, issue, 3, source="bogus")


def test_rain_feats_at_offsets():
    n, cols = 4, 12                                     # OFF=7 -> days D-7..D+4
    r = np.tile(np.arange(cols, dtype=float), (n, 1))   # value = column index
    p99d = np.full(n, 2.0)
    p99_3 = np.full(n, 3.0)
    f = SC.rain_feats_at(r, 0, p99d, p99_3)             # k=0 -> target D
    assert np.allclose(f["rain_24h"], 7.0)              # column OFF
    assert np.allclose(f["rain_72h"], 5 + 6 + 7)
    assert np.allclose(f["rain_7d"], sum(range(1, 8)))
    assert np.allclose(f["rain_24h_rel"], 3.5) and np.allclose(f["rain_72h_rel"], 6.0)
    f1 = SC.rain_feats_at(r, 2, p99d, p99_3)            # k=2 -> target D+2
    assert np.allclose(f1["rain_24h"], 9.0)


def _hz_rows(issue_days, n_per=1, split="test"):
    return pd.DataFrame({
        "lon": np.repeat(-51.0, len(issue_days) * n_per),
        "lat": np.repeat(-29.0, len(issue_days) * n_per),
        "issue_date": pd.to_datetime(np.repeat(issue_days, n_per)),
        "slope": np.full(len(issue_days) * n_per, 25.0),
        "sm_rootzone_wetness": np.full(len(issue_days) * n_per, 0.5),
        "split": split})


def test_horizon_scores_tiny_xgb_monotone_in_H():
    cols = ["rain_24h", "rain_72h", "slope", "doy_sin", "doy_cos"]
    model, cols = _tiny_xgb(cols)
    hz = _hz_rows(["2020-03-10"])
    entries = {"xgb": {"kind": "xgb", "model": model, "cols": cols}}
    p1 = SC.horizon_scores(hz, 1, entries, FakeRain())["xgb"]
    p3 = SC.horizon_scores(hz, 3, entries, FakeRain())["xgb"]
    assert p3[0] >= p1[0] - 1e-9                        # 1-prod shrinks with more days
    # FakeRain daily = dayofyear -> doy(2020-03-10)=70 rain -> p1 high
    assert p1[0] > 0.4


def test_horizon_scores_tpi_and_nowcast():
    from live import tpi as TPI

    hz = _hz_rows(["2020-03-10"])
    # rain_72h at k: cols c-2..c where c=7+k; daily=doy -> r72 = doy*3
    r72 = pd.Timestamp("2020-03-10").day_of_year * 3.0    # ~210 >> p99=40 -> R=1
    p90, p95, p99 = 10.0, 20.0, 40.0
    expected = TPI.rain_score(np.array([r72]), np.array([p90]), np.array([p95]),
                              np.array([p99])) ** (1 / 3) \
        * TPI.soil_score(np.array([0.5])) ** (1 / 3) \
        * TPI.terrain_slide(np.array([25.0])) ** (1 / 3)
    entries = {"tpi": {"kind": "tpi"},
               "now": {"kind": "nowcast",
                       "model": _ConstModel(0.5), "cols": []}}
    out = SC.horizon_scores(hz, 2, entries, FakeRain())
    assert out["tpi"] == pytest.approx(expected[0], abs=1e-9)
    assert out["now"] == pytest.approx(0.5)             # single-day p, not compounded


def test_horizon_scores_gfs_source_changes_xgb():
    cols = ["rain_24h", "rain_72h", "slope", "doy_sin", "doy_cos"]
    model, cols = _tiny_xgb(cols)
    hz = _hz_rows(["2020-03-10"])
    entries = {"xgb": {"kind": "xgb", "model": model, "cols": cols}}
    per = SC.horizon_scores(hz, 1, entries, FakeRain())["xgb"][0]
    gfs = lambda l, a, i, H: np.full((len(np.atleast_1d(l)), H), 0.0)
    dry = SC.horizon_scores(hz, 1, entries, FakeRain(),
                            source="gfs", gfs_daily=gfs)["xgb"][0]
    assert dry < per                                    # GFS zero-rain -> lower P


class _ConstModel:
    def __init__(self, p):
        self.p = p

    def predict_proba(self, X):
        return np.tile([1 - self.p, self.p], (len(X), 1))


def test_horizon_scores_independence_product():
    hz = _hz_rows(["2020-03-10"])
    entries = {"c": {"kind": "xgb", "model": _ConstModel(0.1), "cols": []}}
    out = SC.horizon_scores(hz, 3, entries, FakeRain())
    assert out["c"][0] == pytest.approx(1 - 0.9**3)


# ------------------------------------------------------------------------ serve
def test_trigger_model_predict_grid(tmp_path):
    from ml.local.serve import TriggerModel

    cols = ["rain_24h", "rain_72h", "rain_7d", "rain_24h_rel", "rain_72h_rel",
            "slope", "landcover", "doy_sin", "doy_cos"]
    model, cols = _tiny_xgb(cols)
    meta = {"features": cols,
            "horizons": {"24h": 1, "48h": 2, "72h": 3, "7d": 7, "14d": 14}}
    path = M.save_xgb(model, tmp_path / "p_trigger_xgb.json", meta)
    tm = TriggerModel.load(path)
    assert tm.horizons["14d"] == 14

    h, w = 3, 4
    ones = np.full((h, w), 1.0)
    static = {"elevation": ones * 300, "slope": ones * 20, "aspect_sin": ones * 0,
              "aspect_cos": ones * 1, "curvature": ones * 0, "relief": ones * 50,
              "landcover": ones * 5, "ndvi": ones * 0.7}
    soil = {"sm_surface": ones * 0.5, "sm_rootzone": ones * 0.5,
            "sm_profile_wetness": ones * 0.5}
    texture = {"clay": ones * 0.3, "sand": ones * 0.4, "porosity": ones * 0.5}
    rain_obs = np.full((7, h, w), 5.0, dtype=np.float32)
    rain_fc = np.full((16, h, w), 5.0, dtype=np.float32)
    out = tm.predict(dt.date(2024, 5, 1), static=static, soil=soil,
                     texture=texture, rain_obs=rain_obs, rain_fc=rain_fc,
                     p99_daily=ones * 50, p99_3day=ones * 60)
    keys = [f"p_trigger_{h}" for h in ("24h", "48h", "72h", "7d", "14d")]
    assert set(out) == set(keys)
    for k in keys:
        assert out[k].shape == (h, w) and out[k].dtype == np.float32
        assert np.isfinite(out[k]).all()
        assert ((out[k] >= 0) & (out[k] <= 1)).all()
    # P_H = 1 - prod(1-p_k): non-decreasing in H before per-horizon calibration
    stack = np.stack([out[k] for k in keys])
    assert (np.diff(stack, axis=0) >= -1e-6).all()
    # rain_fc day values vary -> higher rain -> higher 24h
    out_wet = tm.predict(dt.date(2024, 5, 1), static=static, soil=soil,
                         texture=texture, rain_obs=rain_obs,
                         rain_fc=np.full((16, h, w), 90.0, dtype=np.float32),
                         p99_daily=ones * 50, p99_3day=ones * 60)
    assert (out_wet["p_trigger_24h"] > out["p_trigger_24h"]).all()
    # missing elevation -> NaN
    static2 = {**static, "elevation": np.where(np.arange(h * w).reshape(h, w) == 5,
                                              np.nan, static["elevation"])}
    out2 = tm.predict(dt.date(2024, 5, 1), static=static2, soil=soil,
                      texture=texture, rain_obs=rain_obs, rain_fc=rain_fc,
                      p99_daily=ones * 50, p99_3day=ones * 60)
    assert np.isnan(out2["p_trigger_14d"]).sum() == 1
    with pytest.raises(ValueError):
        tm.predict(dt.date(2024, 5, 1), static=static, soil=soil, texture=texture,
                   rain_obs=rain_obs, rain_fc=np.full((3, h, w), 5.0),
                   p99_daily=ones * 50, p99_3day=ones * 60)


# ------------------------------------------------------------------------ table
def test_doy_features_and_date_pool():
    d = T.doy_features(pd.to_datetime(["2020-01-01", "2020-07-02"]))
    ang0 = 2 * np.pi * 1 / 365.25
    assert d["doy_sin"].iloc[0] == pytest.approx(np.sin(ang0))
    assert d["doy_cos"].iloc[0] == pytest.approx(np.cos(ang0))
    rng = np.random.default_rng(0)
    pool = T._date_pool(dt.date(2020, 1, 1), dt.date(2020, 1, 31), 10, rng)
    assert len(pool) == 10 and len(np.unique(pool)) == 10
    assert (pool >= np.datetime64("2020-01-01")).all()
    assert (pool <= np.datetime64("2020-01-31")).all()
    assert np.all(np.diff(pool.astype("datetime64[D]").astype(int)) > 0)


def test_horizon_rows():
    rows = pd.DataFrame({
        "lon": [-51.0, -51.1, -51.2], "lat": [-29.0, -29.1, -29.2],
        "date": pd.to_datetime(["2024-05-01", "2020-01-10", "2024-05-01"]),
        "issue_date": [pd.NaT, pd.NaT, pd.NaT], "label": [1, 0, 1],
        "event_id": ["e1", "e1", "e2"],
        "neg_type": ["", "same_point_other_date", ""],
        "split": ["test", "test", "train"],
        "point_id": ["a", "b", "c"], "source": ["x"] * 3,
        "acc_km": [0.0] * 3, "trigger": ["rain"] * 3,
        "date_precision": ["day"] * 3, "title": [""] * 3})
    hz = T.horizon_rows(rows, horizons={"24h": 1, "72h": 3}, lookback=5)
    # one shared set: pos e1 x (j=0..5) + neg b once; train pos e2 excluded
    assert "horizon" not in hz.columns and "H" not in hz.columns
    assert {"label_24h", "label_72h"} <= set(hz.columns)
    pos = hz[hz.point_id == "a"]
    assert len(pos) == 6
    assert sorted(pos.lead_days) == [0, 1, 2, 3, 4, 5]
    assert set(pd.to_datetime(pos.issue_date).dt.date) == {
        dt.date(2024, 5, 1) - dt.timedelta(days=j) for j in range(6)}
    # label_<h> = 1 iff the event day is inside [D, D+H-1] i.e. lead_days < H
    assert pos["label_24h"].tolist() == [1, 0, 0, 0, 0, 0]
    assert pos["label_72h"].tolist() == [1, 1, 1, 0, 0, 0]
    neg = hz[hz.label == 0]
    assert len(neg) == 1 and neg.issue_date.iloc[0] == neg.date.iloc[0]
    assert neg.lead_days.iloc[0] == -1
    assert neg["label_24h"].iloc[0] == 0 and neg["label_72h"].iloc[0] == 0


def test_point_needs_and_load_rows(data_dir):
    tables = T.tables_dir()
    pd.DataFrame({"lon": [-51.0, -51.1], "lat": [-29.0, -29.1],
                  "date": ["2020-01-10", "2020-01-11"], "split": ["test", "val"],
                  "issue_date": ["", ""], "label": [1, 0],
                  "event_id": ["e1", "e1"], "neg_type": ["", "x"]}).to_csv(
        tables / "rows_3b.csv", index=False)
    pd.DataFrame({"lon": [-51.2], "lat": [-29.2],
                  "issue_date": ["2020-01-09"], "split": ["test"],
                  "date": ["2020-01-10"], "label": [1],
                  "event_id": ["e1"], "neg_type": [""]}).to_csv(
        tables / "horizon_3b.csv", index=False)
    needs = T.point_needs()
    assert needs["power_lon"].tolist() == [-51.0, -51.1]
    assert len(needs["smap_lon"]) == 3                  # rows + horizon rows
    (job,) = needs["gfs_jobs"]
    assert job["event_id"] == "e1"
    assert job["issue_dates"].tolist() == [np.datetime64("2020-01-09")]
    w, so, e, n = job["bbox"]
    assert w <= -51.2 - 0.5 and e >= -51.2 + 0.5 and so <= -29.7 and n >= -28.7
    assert all(abs(v * 4 - round(v * 4)) < 1e-9 for v in job["bbox"])  # snapped to 0.25°
    with pytest.raises(FileNotFoundError):
        T.load_rows("missing")


def test_sample_3a_faked_static(data_dir, monkeypatch):
    from ml.local import sources_static as ST

    inv = _inv_rows([
        (-51.0, -29.3, "glc", "e1", "2020-01-10", 1.0, "rain"),
        (-51.2, -29.4, "glc", "e2", "2020-01-11", 0.5, "rain"),
        (-51.05, -29.35, "rs2024", "rs_2024", "2024-05-01", 0.0, "rain"),
    ])
    # deterministic land points >5 km from all positives
    monkeypatch.setattr(
        ST, "random_land_points",
        lambda n, bbox, rng: (np.linspace(-51.45, -50.6, n),
                              np.linspace(-29.55, -29.55, n)))
    rows = T.sample_3a(inv, smoke=True, seed=0)
    assert rows.label.sum() == 3
    assert len(rows[rows.label == 0]) == S.NEG_RATIO_3A * 3
    assert (rows[rows.label == 0].neg_type == "random_far").all()
    assert rows["split"].isin(list(SP.SPLITS) + ["buffer"]).all()
    assert (S.MODEL_DIR / "split_3a_blocks.csv").exists()
    assert (data_dir / "tables" / "rows_3a.csv").exists()
    r2 = T.load_rows("rows_3a")
    assert len(r2) == len(rows)


def test_sample_3b_and_horizon_files(data_dir, monkeypatch):
    monkeypatch.setattr(S, "NEG_DATES_3B", 2)
    monkeypatch.setattr(S, "NEG_DATE_POOL", 90)
    monkeypatch.setattr(S, "NEG_DATE_GAP_DAYS", 10)
    inv = _inv_rows([
        (-51.0, -29.3, "rs2024", "rs_2024", "2024-05-01", 0.0, "rain"),
        (-51.1, -29.35, "rs2024", "rs_2024", "2024-05-02", 0.0, "rain"),
        (-51.2, -29.4, "rs2024", "rs_2024", "2024-05-03", 0.0, "rain"),
        (-51.3, -29.45, "rs2024", "rs_2024", "2024-05-04", 0.0, "rain"),
        (-51.4, -29.5, "rs2024", "rs_2024", "2024-05-05", 0.0, "rain"),
        (85.0, 27.0, "glc", "glc_e1", "2020-01-10", 1.0, "rain"),
        (-75.0, 6.0, "glc", "glc_e2", "2020-02-10", 1.0, "rain"),
        (-51.5, -29.55, "glc", "glc_e3", "2020-03-10", 1.0, "rain"),
    ])
    rows = T.sample_3b(inv, smoke=True, seed=0)
    assert (rows[rows.event_id == "rs_2024"]["split"] == "test").all()
    ev = pd.read_csv(S.MODEL_DIR / "split_3b_events.csv")
    assert ev[ev.event_id == "rs_2024"]["split"].iloc[0] == "test"
    types = set(rows.neg_type)
    assert "same_point_other_date" in types and "elsewhere_same_day" in types
    assert rows["split"].isin(list(SP.SPLITS) + ["buffer"]).all()
    hz = T.load_rows("horizon_3b")
    assert {"label_24h", "label_14d", "issue_date", "lead_days"} <= set(hz.columns)
    assert "horizon" not in hz.columns
    # same_point_other_date negatives are >= NEG_DATE_GAP_DAYS from event days
    for ev_id, grp in rows[rows.neg_type == "same_point_other_date"].groupby("event_id"):
        pos_days = rows[(rows.event_id == ev_id) & (rows.label == 1)]["date"]
        for d in grp.date:
            assert (pos_days - d).abs().dt.days.min() >= S.NEG_DATE_GAP_DAYS


def test_build_table_3a_faked_sources(data_dir, monkeypatch):
    from ml.local import sources_dynamic as D
    from ml.local import sources_static as ST

    tables = T.tables_dir()
    pd.DataFrame({"lon": [-51.0], "lat": [-29.0], "split": ["train"],
                  "label": [1], "event_id": ["e1"], "date": ["2020-01-10"],
                  "point_id": ["a"], "source": ["glc"], "neg_type": [""],
                  "region": ["south_southeast_brazil"],
                  "acc_km": [1.0], "trigger": ["rain"],
                  "date_precision": ["day"], "title": [""]}).to_csv(
        tables / "rows_3a.csv", index=False)
    monkeypatch.setattr(ST, "sample_static",
                        lambda lon, lat: pd.DataFrame({"elevation": [100.0] * len(lon),
                                                       "slope": [10.0] * len(lon),
                                                       "landcover": [5.0] * len(lon),
                                                       "ndvi": [0.6] * len(lon)}))
    monkeypatch.setattr(D, "climate",
                        lambda lon, lat: pd.DataFrame({"rain_mean": [4.0] * len(lon),
                                                       "soil_mean": [0.4] * len(lon)}))
    monkeypatch.setattr(D, "soil_texture",
                        lambda lon, lat: pd.DataFrame({"clay": [0.3] * len(lon),
                                                       "sand": [0.4] * len(lon),
                                                       "porosity": [0.5] * len(lon)}))
    tab = T.build_table_3a()
    assert {"elevation", "rain_mean", "clay", "label", "split"} <= set(tab.columns)
    assert tab["elevation"].iloc[0] == 100.0
    assert len(tab.columns) == len(set(tab.columns))  # duplicates dropped


def test_build_table_3b_drops_water_negs(data_dir, monkeypatch):
    from ml.local import sources_dynamic as D
    from ml.local import sources_static as ST

    tables = T.tables_dir()
    pd.DataFrame({
        "lon": [-51.0, -51.05], "lat": [-29.0, -29.05],
        "split": ["test", "test"], "label": [1, 0],
        "event_id": ["e1", "e1"], "date": ["2020-01-10", "2020-02-10"],
        "issue_date": ["", ""], "point_id": ["a", "b"],
        "source": ["glc", "random"], "neg_type": ["", "elsewhere_same_day"],
        "region": ["x", "x"], "acc_km": [0.0, 0.0], "trigger": ["rain", ""],
        "date_precision": ["day", "none"], "title": ["", ""]}).to_csv(
        tables / "rows_3b.csv", index=False)
    pd.DataFrame({
        "lon": [-51.0], "lat": [-29.0], "issue_date": ["2020-01-09"],
        "date": ["2020-01-10"], "split": ["test"], "label": [1],
        "event_id": ["e1"], "neg_type": [""], "point_id": ["a"],
        "source": ["glc"], "region": ["x"], "acc_km": [0.0],
        "trigger": ["rain"], "date_precision": ["day"], "title": [""]}).to_csv(
        tables / "horizon_3b.csv", index=False)

    class _Rain:
        def daily(self, lon, lat, dates):
            return np.zeros(len(np.atleast_1d(lon)))

        def p99_daily(self, lon, lat):
            return np.ones(len(np.atleast_1d(lon)))

        def p99_3day(self, lon, lat):
            return np.ones(len(np.atleast_1d(lon)))

        def pctl_3day(self, lon, lat, doy, **kw):
            n = len(np.atleast_1d(lon))
            return np.ones(n), np.ones(n), np.ones(n)

    class _Smap:
        def sample(self, lon, lat, dates):
            return pd.DataFrame({"sm_surface": np.zeros(len(lon)),
                                 "sm_rootzone": np.zeros(len(lon)),
                                 "sm_profile_wetness": np.zeros(len(lon))})

    monkeypatch.setattr(D.PowerRain, "load", classmethod(lambda cls: _Rain()))
    monkeypatch.setattr(D.SmapPoints, "load", classmethod(lambda cls: _Smap()))
    monkeypatch.setattr(D, "rain_features",
                        lambda lon, lat, dates, imerg=None, clim=None:
                        pd.DataFrame({"rain_24h": np.zeros(len(lon)),
                                      "rain_72h": np.zeros(len(lon))}))
    monkeypatch.setattr(D, "soil_texture",
                        lambda lon, lat: pd.DataFrame({"clay": np.zeros(len(lon))}))
    monkeypatch.setattr(
        ST, "sample_static_points",
        lambda lon, lat, keep_dem=False: pd.DataFrame({
            "lon": lon, "lat": lat,
            "elevation": np.where(np.asarray(lon) == -51.05, np.nan, 100.0),
            "slope": np.ones(len(lon)) * 10.0}))
    tab, hzt = T.build_table_3b()
    assert len(tab) == 1                    # water-neg row dropped
    assert tab.point_id.iloc[0] == "a"
    assert {"sm_surface", "rain_24h", "elevation", "doy_sin"} <= set(tab.columns)
    assert len(hzt) == 1 and "elevation" in hzt.columns
    assert (S.MODEL_DIR / "split_summary.json").exists()


# --------------------------------------------------------- 3b holdout + GFS per event
def test_gfs_jobs_one_box_per_test_event_and_reader(monkeypatch):
    hz = pd.DataFrame({
        "lon": [-51.0, -51.3, -122.0, -60.0], "lat": [-29.0, -29.4, 47.5, -10.0],
        "issue_date": pd.to_datetime(["2024-05-01", "2024-04-30", "2025-12-09", "2020-01-01"]),
        "split": ["test", "test", "test", "val"],
        "event_id": ["rs_2024", "rs_2024", "wa_2025", "glc_x"]})
    jobs = T.gfs_jobs(hz)
    assert [j["event_id"] for j in jobs] == ["rs_2024", "wa_2025"]   # VAL never fetched
    rs, wa = jobs
    assert rs["bbox"][2] - rs["bbox"][0] < 3 and wa["bbox"][0] < -122 < wa["bbox"][2]
    calls = []

    def fake_gfs_daily(lon, lat, issue, lead_days, bbox):
        calls.append(bbox)
        return np.full((len(lon), lead_days), 10.0 if bbox == rs["bbox"] else 20.0)

    from ml.local import sources_dynamic as D
    monkeypatch.setattr(D, "gfs_daily", fake_gfs_daily)
    out = T.gfs_reader(jobs)(hz.lon, hz.lat, hz.issue_date.values, 3)
    assert out.shape == (4, 3)
    assert out[:2].tolist() == [[10.0] * 3] * 2 and out[2].tolist() == [20.0] * 3
    assert np.isnan(out[3]).all()                      # outside every box -> NaN
    assert calls == [rs["bbox"], wa["bbox"]]           # same boxes as the fetch


def test_wa2025_frame_window_and_minimal_fields():
    ms = lambda d: int(pd.Timestamp(d, tz="UTC").timestamp() * 1000)
    feats = [
        {"geometry": {"coordinates": [-122.0, 47.6]}, "properties": {"event_date": ms("2025-12-11")}},
        {"geometry": {"coordinates": [-122.1, 47.7]}, "properties": {"event_date": ms("2025-01-10")}},
        {"geometry": {"coordinates": [-122.2, 47.8]}, "properties": {"event_date": None}},
    ]
    df = L.wa2025_frame(feats)
    assert len(df) == 1 and df.event_id.iloc[0] == "wa_2025"
    assert str(df.date.iloc[0].date()) == "2025-12-11" and df.source.iloc[0] == "wa2025"


def test_fetch_wa2025_requests_only_date_field(data_dir):
    seen = {}

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return {"features": [{"geometry": {"coordinates": [-122.0, 47.6]},
                                  "properties": {"event_date": 1765411200000, "email": "x"}}]}

    def get(url, params, timeout):
        seen.update(params)
        return R()

    df = L.fetch_wa2025(get=get, force=True)
    assert seen["outFields"] == "event_date"            # never names/emails
    cached = (S.DATA_DIR / "labels" / "wa2025_reports.geojson").read_text()
    assert "email" not in cached and len(df) == 1


def test_store_roundtrip(data_dir, tmp_path):
    from ml.local import store

    (S.DATA_DIR / "labels").mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"point_id": ["p1", "p2"], "lon": [-51.0, -51.1], "lat": [-29.0, -29.1],
                  "source": ["glc", "rs2024"], "event_id": ["e1", "rs_2024"],
                  "date": ["2020-01-10", "2024-05-01"], "date_precision": ["day", "event-window"],
                  "acc_km": [1.0, 0.0], "trigger": ["rain", "rain"], "title": ["a", "b"]}
                 ).to_csv(S.DATA_DIR / "labels" / "inventory.csv", index=False)
    t = T.tables_dir()
    pd.DataFrame({"point_id": ["p1", "p1", "p2"], "event_id": ["e1", "e1", "rs_2024"],
                  "lon": [-51.0, -51.0, -51.1], "lat": [-29.0, -29.0, -29.1],
                  "date": ["2020-01-10", "2020-03-01", "2024-05-01"], "label": [1, 0, 1],
                  "neg_type": ["", "same_point_other_date", ""], "split": ["train", "train", "test"],
                  "region": ["x", "x", "x"], "row_id": [0, 1, 2], "title": ["a", "a", "b"],
                  "rain_24h": [50.0, 1.0, 90.0], "slope": [20.0, 20.0, 25.0]}
                 ).to_csv(t / "table_3b.csv", index=False)
    pd.DataFrame({"point_id": ["p2"], "event_id": ["rs_2024"], "lon": [-51.1], "lat": [-29.1],
                  "date": ["2024-05-01"], "issue_date": ["2024-04-30"], "lead_days": [1],
                  "label": [1], "label_24h": [0], "label_48h": [1], "split": ["test"],
                  "slope": [25.0]}).to_csv(t / "horizon_3b_features.csv", index=False)
    path = store.write_store(tmp_path / "ml.sqlite")
    import sqlite3
    con = sqlite3.connect(path)
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master")}
    assert {"landslides", "samples", "features", "split_units", "meta", "v_3b"} <= names
    assert con.execute("SELECT count(*) FROM landslides").fetchone()[0] == 2
    assert con.execute("SELECT split, count(*) FROM samples WHERE task='3b' GROUP BY 1"
                       ).fetchall() == [("test", 1), ("train", 2)]
    fcols = [r[1] for r in con.execute("PRAGMA table_info(features)")]
    assert "rain_24h" in fcols and "label" not in fcols and "lon" not in fcols
    con.close()
    v = store.read_task("3b", path)
    assert len(v) == 3 and v.rain_24h.tolist() == [50.0, 1.0, 90.0]
    h = store.read_task("3b_horizon", path)
    assert h.label_48h.tolist() == [1] and h.lead_days.tolist() == [1]


def test_by_neg_type_splits_negatives():
    from ml.local.trainer import by_neg_type
    df = pd.DataFrame({"label": [1, 1, 0, 0, 0, 0],
                       "neg_type": ["", "", "same_point_other_date", "same_point_other_date",
                                    "elsewhere_same_day", "elsewhere_same_day"]})
    p = np.array([0.9, 0.8, 0.1, 0.85, 0.2, 0.3])
    out = by_neg_type(df, p, 0.5)
    assert set(out) == {"same_point_other_date", "elsewhere_same_day"}
    assert out["same_point_other_date"]["fp"] == 1 and out["elsewhere_same_day"]["fp"] == 0
    assert out["elsewhere_same_day"]["n"] == 4


def test_horizon_rows_rs_window_labels():
    rows = pd.DataFrame({"lon": [-51.0, -51.0], "lat": [-29.0, -29.0],
                         "date": pd.to_datetime(["2024-05-01", "2024-03-01"]),
                         "label": [1, 0], "split": ["test", "test"],
                         "event_id": ["rs_2024", "rs_2024"],
                         "neg_type": ["", "same_point_other_date"]})
    hz = T.horizon_rows(rows, horizons={"24h": 1, "48h": 2}, lookback=5)
    pos = hz[hz.label == 1].set_index(hz[hz.label == 1].issue_date.dt.strftime("%m-%d"))
    # issue days May 2 .. Apr 27 (window end - j), window Apr 29 - May 2
    assert list(pos.index) == ["05-02", "05-01", "04-30", "04-29", "04-28", "04-27"]
    assert pos.label_24h.tolist() == [1, 1, 1, 1, 0, 0]
    assert pos.label_48h.tolist() == [1, 1, 1, 1, 1, 0]
    assert pos.lead_days.tolist() == [0, 0, 0, 0, 1, 2]
    neg = hz[hz.label == 0]
    assert neg.label_24h.tolist() == [0] and neg.label_48h.tolist() == [0]
    assert "win_start" not in hz.columns


def test_trigger_model_monotone_with_crossing_calibrators(tmp_path):
    from ml.local.serve import TriggerModel

    cols = ["rain_24h", "rain_72h", "rain_7d", "rain_24h_rel", "rain_72h_rel"]
    model, cols = _tiny_xgb(cols)
    up = M.Calibrator(kind="isotonic", x=[0.0, 1.0], y=[0.5, 0.9]).to_dict()     # 48h: high
    down = M.Calibrator(kind="isotonic", x=[0.0, 1.0], y=[0.0, 0.1]).to_dict()   # 72h: low
    meta = {"features": cols, "horizons": {"24h": 1, "48h": 2, "72h": 3},
            "horizon_calibrators": {"48h": up, "72h": down}}
    tm = TriggerModel.load(M.save_xgb(model, tmp_path / "p.json", meta))
    ones = np.ones((2, 2))
    out = tm.predict(dt.date(2024, 5, 1), static={"elevation": ones}, soil={}, texture={},
                     rain_obs=np.full((7, 2, 2), 5.0), rain_fc=np.full((3, 2, 2), 5.0),
                     p99_daily=ones * 50, p99_3day=ones * 60)
    assert (out["p_trigger_72h"] >= out["p_trigger_48h"]).all()      # running max applied
    assert (out["p_trigger_48h"] >= out["p_trigger_24h"]).all()


def test_by_lead_horizon_mapping():
    from ml.local.by_lead import horizon_for_lead
    got = {k: horizon_for_lead(k) for k in range(14)}
    assert got[0] == "24h" and got[1] == "48h" and got[2] == "72h"
    assert all(got[k] == "7d" for k in range(3, 7))
    assert all(got[k] == "14d" for k in range(7, 14))
    import pytest
    with pytest.raises(ValueError):
        horizon_for_lead(14)


# ---------------------------------------------------------------- TEST events
def _ev(**kw):
    from ml.local.events import TestEvent
    d = {"event_id": "x_2024", "name": "X", "date": "2024-05-01",
         "trigger_window": ["2024-04-30", "2024-05-02"], "format": "geojson", "source": "x.geojson"}
    d.update(kw)
    return TestEvent.from_dict(d)


def test_test_event_validation():
    import pytest
    ev = _ev()
    assert ev.inventory_window == (dt.date(2024, 4, 23), dt.date(2024, 5, 9))
    with pytest.raises(ValueError):
        _ev(date="2024-06-01")                       # D outside trigger window
    with pytest.raises(ValueError):
        _ev(dating="per_point")                      # per_point needs date_field
    with pytest.raises(ValueError):
        _ev(format="kmz")


def test_registry_file_parses_and_select(monkeypatch):
    from ml.local import settings as S
    from ml.local.events import select
    reg = S.TEST_EVENT_REGISTRY
    assert {"helene_2024", "maria_2017"} <= set(reg)
    assert select(reg, "none") == []
    assert [e.event_id for e in select(reg, "maria_2017")] == ["maria_2017"]
    import pytest
    with pytest.raises(KeyError):
        select(reg, "nope")


def test_load_event_inventory_geojson_polygons_and_dates(tmp_path, monkeypatch):
    import json
    from ml.local import labels as L
    from ml.local import settings as S
    monkeypatch.setattr(S, "DATA_DIR", tmp_path)
    poly = {"type": "Polygon", "coordinates": [[[-50, -29], [-49.99, -29], [-49.99, -28.99],
                                                [-50, -28.99], [-50, -29]]]}
    feats = [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-51.0, -29.5]},
         "properties": {"d": "2024-05-01", "who": "secret@example.com"}},
        {"type": "Feature", "geometry": poly, "properties": {"d": "2024-05-02"}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-51.1, -29.6]},
         "properties": {"d": "2023-01-01"}},          # outside inventory window -> dropped
    ]
    p = tmp_path / "inv.geojson"
    p.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    df = L.load_event_inventory(_ev(source=str(p), dating="per_point", date_field="d"))
    assert len(df) == 2 and set(df.source) == {"event"} and set(df.event_id) == {"x_2024"}
    assert -50 < df.lon.iloc[1] < -49.99                # polygon -> point inside it
    assert "who" not in df.columns and list(df.columns) == L.COLUMNS
    fixed = L.load_event_inventory(_ev(source=str(p)))
    assert len(fixed) == 3 and (fixed.date == pd.Timestamp("2024-05-01")).all()


def test_load_event_inventory_arcgis_requests_only_date_field(tmp_path, monkeypatch):
    from ml.local import labels as L
    from ml.local import settings as S
    monkeypatch.setattr(S, "DATA_DIR", tmp_path)
    seen = []

    class R:
        def __init__(self, j): self.j = j
        def raise_for_status(self): pass
        def json(self): return self.j

    def get(url, params=None, timeout=None):
        seen.append(params)
        off = params["resultOffset"]
        feats = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [-80 - off - i, 35]},
                  "properties": {}} for i in range(2)]
        return R({"features": feats, "properties": {"exceededTransferLimit": off == 0}})

    df = L.load_event_inventory(_ev(format="arcgis", source="https://x/query"), get=get)
    assert len(df) == 4 and len(seen) == 2
    assert all(p["outFields"] == "" for p in seen)


def test_resolve_source_uses_content_disposition_name(tmp_path, monkeypatch):
    """Extension-less API URLs (Dataverse /api/access/dataset/:persistentId/) take
    the real file name from Content-Disposition so the .zip suffix survives."""
    from ml.local import labels as L
    from ml.local import settings as S
    monkeypatch.setattr(S, "DATA_DIR", tmp_path)
    payload = b"zip-bytes"

    class R:
        headers = {"Content-Disposition": 'attachment; filename="real_name.zip"'}
        content = payload
        def raise_for_status(self): pass

    ev = _ev(source="https://x/api/access/dataset/:persistentId/?persistentId=doi:y")
    p = L._resolve_source(ev, get=lambda url, timeout=None: R())
    assert p.name == "real_name.zip" and p.read_bytes() == payload
    # A URL whose basename already has an extension ignores Content-Disposition.
    p2 = L._resolve_source(_ev(source="https://x/files/inv.geojson"),
                           get=lambda url, timeout=None: R())
    assert p2.name == "inv.geojson"
    # No header at all -> URL name kept.
    class R2:
        headers = {}
        content = payload
        def raise_for_status(self): pass
    p3 = L._resolve_source(_ev(source="https://x/other/noext"),
                           get=lambda url, timeout=None: R2())
    assert p3.name == "noext"


def test_leak_mask_days_and_km():
    from ml.local import labels as L
    glc = pd.DataFrame({"point_id": ["a", "b", "c", "d"], "event_id": ["e1", "e2", "e3", "e4"],
                        "lon": [-51.0, -51.0, -40.0, -51.0], "lat": [-29.5, -29.5, -10.0, -29.5],
                        "date": pd.to_datetime(["2024-05-03", "2024-05-10", "2024-05-01", "2023-05-01"])})
    test = pd.DataFrame({"event_id": ["x"], "lon": [-51.2], "lat": [-29.4],
                         "date": [pd.Timestamp("2024-05-01")]})
    m, rep = L.leak_mask(glc, test, {"x": (dt.date(2024, 4, 30), dt.date(2024, 5, 2))}, days=3, km=100)
    assert m.tolist() == [True, False, False, False]   # b too late, c too far, d other year
    assert rep.glc_point_id.tolist() == ["a"]
