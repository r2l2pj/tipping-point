"""ML v3 — curated event-level split, operational season negatives (L41, conductor-approved).

Split (.crew/research/ml-v3-split.md): TEST = RS 2024, WA 2025, Helene 2024, Petrópolis 2022;
VAL = São Sebastião 2023, Rwanda 2023, Himachal 2023; TRAIN = Maria 2017, Kerala 2018,
Emilia 2023, Angra 2022 + filtered GLC (location <= 1 km, rain trigger, day-dated) + any
extra event inventories enabled in test_events.d with ``proposed_split: train``.

Rows: <= SITES sites per event (all GLC sites), every issue day D within the onset's day
of year +- SEASON days, in the event year and the two years before (operational: the
model is scored on every day of the wet season, not on hand-picked dates).
``label_H = 1`` when [D, D+H-1] overlaps the event's label window; event-year issue days
from the onset to the window end + 3 d are dropped (early-warning rule: the model never
sees "it is already pouring"). So horizons are 48h / 72h / 7d (a 24h window issued before
the onset cannot contain it).

Model: one XGBoost per horizon on direct rain features only (antecedent observed IMERG +
forecast window; observed IMERG = perfect forecast in TRAIN/VAL; site statics are constant
within a site's season rows, and the extra inventories have no texture sample), event-
balanced weights (each dense event total 1, GLC group total GLC_TOTAL, single reports x0.25).
Isotonic calibration + F1 threshold on POOLED VAL. Dynamic SMAP is dropped (not cached
for season rows; worth ~0 per the audit). Baselines on the same rows: untrained
``fc_max72_rel`` (forecast max 3-day rain / local P99 3-day; threshold by VAL F1) and the
TPI > 0.6 rule (only on rows with cached SMAP: the run-up window of each event).
TEST is scored once, after VAL choices are frozen.

    ML_EVENT_ONSETS=... python -m ml.local.v3   -> ml/models/metrics_v3.json (+ md on stdout)
"""
from __future__ import annotations

import argparse
import json
import logging

import numpy as np
import pandas as pd

from . import models as M
from . import scoring as SC
from . import settings as S
from .trainer import _calibrated, _load_table, _rain

log = logging.getLogger(__name__)
SPLIT = {"rs_2024": "test", "wa_2025": "test", "helene_2024": "test", "petropolis_2022": "test",
         "saosebastiao_2023": "val", "rwanda_2023": "val", "himachal_2023": "val",
         "maria_2017": "train", "kerala_2018": "train", "emilia_2023": "train",
         "angra_2022": "train"}
V3_HORIZONS = ("48h", "72h", "7d")
SITES = 300
SEASON = 45
YEARS_BACK = (1, 2)
GLC_TOTAL = 2.0
RAINY = {"rain", "downpour", "continuous_rain", "tropical_cyclone", "monsoon"}
MIN_POS = 20
N_BOOT = 1000
KEYS = ("n", "n_pos", "base_rate", "threshold", "accuracy", "precision", "recall", "f1",
        "pr_auc", "roc_auc", "tp", "fp", "fn", "tn")


# ---------------------------------------------------------------------- sites
def sites(seed: int = S.SEED) -> pd.DataFrame:
    """One row per site: lon, lat, event_id, split, w0, w1 (label window), onset, kind."""
    t = _load_table("table_3b")
    pos = t[t.label == 1].copy()
    inv = pd.read_csv(S.DATA_DIR / "labels" / "inventory.csv")
    glc = inv[inv.source == "glc"]
    good = set(glc[(glc.acc_km <= 1) & glc.trigger.isin(RAINY) & (glc.date_precision == "day")].event_id)
    rng = np.random.default_rng(seed)
    parts = []
    for ev, g in pos.groupby("event_id"):
        if ev in SPLIT:
            g = g.drop_duplicates(["lon", "lat"])
            if len(g) > SITES:
                g = g.iloc[rng.choice(len(g), SITES, replace=False)]
            w0, w1 = S.LABEL_WINDOWS.get(ev, (None, None))
            d = pd.Timestamp(S.EVENT_DAYS.get(ev, g.date.iloc[0]))
            w0 = pd.Timestamp(w0) if w0 else d
            w1 = pd.Timestamp(w1) if w1 else d
            on = pd.Timestamp(S.EVENT_ONSETS.get(ev, w0))
            kind, split = "dense", SPLIT[ev]
            g = g.assign(w0=w0, w1=w1, onset=on)
            if ev == "wa_2025":   # per-report dates
                g = g.assign(w0=pd.to_datetime(g.date), w1=pd.to_datetime(g.date),
                             onset=pd.to_datetime(g.date))
        elif ev in good and ev not in EXTRA_GLC_DUPES and (g.split.isin(["train", "val"])).all():
            kind, split = "glc", "train"
            dd = pd.to_datetime(g.date)
            g = g.assign(w0=dd, w1=dd, onset=dd)
        else:
            continue
        parts.append(g.assign(kind=kind, split=split,
                              n_event_sites=len(g))[["lon", "lat", "event_id", "split", "kind",
                                                     "w0", "w1", "onset", "n_event_sites"]
                                                    + [c for c in S.SOIL_TEXTURE + ["slope"] if c in g]])
    parts += extra_sites(rng)
    s = pd.concat(parts, ignore_index=True)
    log.info("v3 sites: %s", s.groupby(["split", "kind"]).size().to_dict())
    return s


EXTRA_TRAIN = ("kyushu_2017", "japan_2018", "uganda_2020", "rwanda_2018", "bayarea_2017",
               "santaynez_2023")
EXTRA_GLC_DUPES = {"glc_11008", "glc_11009", "glc_14590", "glc_11325"}  # same slides (worker leak checks)


def extra_sites(rng) -> list[pd.DataFrame]:
    """Extra TRAIN inventories from test_events.d (enabled: false there; v3 reads them
    directly). POWER rain is fetched for their cells (cached afterwards)."""
    from . import labels as L
    from . import sources_dynamic as D
    from .events import load_registry
    reg = load_registry(S.TEST_EVENTS_FILE)
    out = []
    for ev_id in EXTRA_TRAIN:
        if ev_id not in reg:
            continue
        ev = reg[ev_id]
        try:
            pts = L.load_event_inventory(ev)
        except Exception as exc:  # noqa: BLE001 - an unreadable extra inventory is skipped, logged
            log.warning("v3 extra %s skipped: %s", ev_id, exc)
            continue
        pts = pts.drop_duplicates(["lon", "lat"])
        if len(pts) > SITES:
            pts = pts.iloc[rng.choice(len(pts), SITES, replace=False)]
        w0, w1 = (pd.Timestamp(d) for d in ev.trigger_window)
        D.fetch_power_points(pts.lon.to_numpy(), pts.lat.to_numpy())
        out.append(pd.DataFrame({"lon": pts.lon.to_numpy(), "lat": pts.lat.to_numpy(),
                                 "event_id": ev_id, "split": "train", "kind": "dense",
                                 "w0": w0, "w1": w1, "onset": w0, "n_event_sites": len(pts)}))
        log.info("v3 extra %s: %d sites, window %s..%s", ev_id, len(pts), w0.date(), w1.date())
    return out


def season_rows(s: pd.DataFrame) -> pd.DataFrame:
    """Every issue day onset-doy +- SEASON in the event year and YEARS_BACK earlier years."""
    offs = np.arange(-SEASON, SEASON + 1)
    out = []
    for yb in (0, *YEARS_BACK):
        base = s.onset - pd.DateOffset(years=yb)
        for o in offs:
            r = s.copy()
            r["issue_date"] = (base + pd.Timedelta(days=int(o))).dt.normalize()
            r["year_back"] = yb
            out.append(r)
    rows = pd.concat(out, ignore_index=True)
    ev_year = rows.year_back == 0
    drop = ev_year & (rows.issue_date >= rows.onset) & (rows.issue_date <= rows.w1 + pd.Timedelta(days=3))
    rows = rows[~drop].reset_index(drop=True)
    for h, H in S.HORIZONS.items():
        end = rows.issue_date + pd.Timedelta(days=H - 1)
        rows[f"label_{h}"] = (ev_year[~drop].to_numpy()
                              & (rows.issue_date <= rows.w1).to_numpy()
                              & (end >= rows.w0).to_numpy()).astype(int)
    # event-balanced weights
    n_ev = rows.groupby("event_id").size()
    rows["w_event"] = np.where(rows.kind == "glc", np.where(rows.n_event_sites >= 3, 1.0, 0.25), 1.0)
    glc_ev = rows[rows.kind == "glc"].groupby("event_id").w_event.first()
    glc_scale = GLC_TOTAL / glc_ev.sum() if len(glc_ev) else 0.0
    rows["weight"] = rows.w_event / rows.event_id.map(n_ev).to_numpy()
    rows.loc[rows.kind == "glc", "weight"] *= glc_scale
    rows["weight"] *= len(rows) / rows.weight.sum()
    rows["label"] = 0
    return rows


def feats(rows: pd.DataFrame, H: int, rain, source="perfect", gfs=None) -> pd.DataFrame:
    rows = rows.reset_index(drop=True)
    lon, lat = rows.lon.to_numpy(), rows.lat.to_numpy()
    r = SC.rain_series(rain, lon, lat, rows.issue_date.to_numpy(), H, source=source, gfs_daily=gfs)
    f = SC.direct_feats(r, H, rain.p99_daily(lon, lat), rain.p99_3day(lon, lat))
    f = f.replace([np.inf, -np.inf], np.nan)
    return pd.concat([rows.drop(columns=[c for c in S.DIRECT_RAIN if c in rows]), f], axis=1)


# -------------------------------------------------------------------- metrics
def _m(y, p, thr) -> dict:
    m = SC.metrics(y, p, thr)
    return {k: m.get(k) for k in KEYS}


def _boot(df: pd.DataFrame, p: np.ndarray, thr: float, seed=S.SEED) -> dict:
    """Event-bootstrap 90 % CI of pooled metrics (resample events with replacement)."""
    ev = df.event_id.to_numpy()
    y = df.y.to_numpy()
    evs = np.unique(ev)
    idx = {e: np.flatnonzero(ev == e) for e in evs}
    rng = np.random.default_rng(seed)
    acc: dict[str, list] = {k: [] for k in ("pr_auc", "roc_auc", "precision", "recall", "f1", "base_rate")}
    for _ in range(N_BOOT):
        take = np.concatenate([idx[e] for e in rng.choice(evs, len(evs), replace=True)])
        if len(np.unique(y[take])) < 2:
            continue
        m = SC.metrics(y[take], p[take], thr)
        for k, lst in acc.items():
            if m.get(k) is not None:
                lst.append(m[k])
    return {k: [float(np.percentile(v, 5)), float(np.percentile(v, 95))] if v else None
            for k, v in acc.items()}


def _alarm_days(df: pd.DataFrame, p: np.ndarray, thr: float) -> dict:
    """Alarms on negative season rows: per site per (2*SEASON+1)-day season."""
    neg = df.y.to_numpy() == 0
    d = df[neg].assign(a=(np.nan_to_num(p[neg], nan=-1) >= thr).astype(int))
    per = d.groupby(["event_id", "lon", "lat", "year_back"]).a.agg(["sum", "size"])
    per = per[per["size"] > 0]
    return {"per_site_season_mean": float(per["sum"].mean()) if len(per) else None,
            "season_days": 2 * SEASON + 1,
            "share_negative_rows_alarmed": float(d.a.mean()) if len(d) else None}


def _block(df: pd.DataFrame, scores: dict, cal: dict, boot: bool = False) -> dict:
    y = df.y.to_numpy()
    ev = df.event_id.astype(str).to_numpy()
    out = {}
    for name, raw in scores.items():
        c = cal[name]
        p = c["cal"](raw)
        per = {e: _m(y[ev == e], p[ev == e], c["thr"]) for e in sorted(set(ev[y == 1]))}
        big = [v for v in per.values() if (v["n_pos"] or 0) >= MIN_POS and v["pr_auc"] is not None]
        ew = {k: (float(np.mean([v[k] for v in big])) if big else None)
              for k in ("accuracy", "precision", "recall", "f1", "pr_auc", "roc_auc", "base_rate")}
        ew["events_n"] = len(big)
        r = {"pooled": _m(y, p, c["thr"]), "event_weighted": ew, "by_event": per,
             "alarm_days": _alarm_days(df, p, c["thr"])}
        if boot:
            r["pooled_ci90_event_bootstrap"] = _boot(df, p, c["thr"])
        out[name] = r
    return out


# ------------------------------------------------------------------------ run
def run() -> dict:
    rain = _rain()
    st = sites()
    rows = season_rows(st)
    log.info("v3 rows: %s", rows.groupby("split").size().to_dict())
    hz = _load_table("horizon_3b_features")
    smap = (hz[["lon", "lat", "issue_date", "sm_rootzone_wetness"]]
            .dropna().drop_duplicates(["lon", "lat", "issue_date"]))
    from .table import gfs_jobs, gfs_reader, load_rows
    gfs = gfs_reader(gfs_jobs(load_rows("horizon_3b")))
    cols = list(S.DIRECT_RAIN)   # rain only: extra inventories have no texture sample
    res = {"definition": __doc__.split("\n\n")[1].strip(), "split": SPLIT,
           "sites": st.groupby(["split", "kind"]).size().to_dict().__repr__(),
           "rows": rows.groupby("split").size().to_dict(),
           "events": st.groupby("event_id").agg(split=("split", "first"), kind=("kind", "first"),
                                                 sites=("lon", "size")).query("kind == 'dense'")
                       .to_dict("index"),
           "glc_events": int(st[st.kind == "glc"].event_id.nunique()),
           "cols": cols, "horizons": {}}
    for h in V3_HORIZONS:
        H = S.HORIZONS[h]
        d = feats(rows, H, rain)
        d["label"] = d[f"label_{h}"].to_numpy()
        d["y"] = d["label"]
        tr, va, te = (d[d.split == k].reset_index(drop=True) for k in ("train", "val", "test"))
        model = M.fit_xgb(tr, va, cols, sample_weight=tr.weight.to_numpy())
        def sc(df, model=model):
            return {"v3_direct": M.predict(model, df, cols),
                    "rule_fc_max72_rel": df["fc_max72_rel"].to_numpy(dtype=float)}
        vs = sc(va)
        cal = {}
        for n, p in vs.items():
            ok = np.isfinite(p)
            cal[n] = _calibrated({}, p[ok], va.y.to_numpy()[ok])
        hres = {"train_rows": {"n": len(tr), "n_pos": int(tr.y.sum()),
                               "best_iteration": getattr(model, "best_iteration", None)},
                "val_thresholds": {n: c["thr"] for n, c in cal.items()},
                "perfect": {"train": _block(tr, sc(tr), cal), "val": _block(va, vs, cal, boot=True),
                            "test": _block(te, sc(te), cal)}}
        # GFS: VAL + TEST rows with an archived, cached, finite forecast (option a)
        hres["gfs"] = {}
        for k, part in (("val", va), ("test", te)):
            ok = SC.source_mask(part, "gfs", gfs, H)
            g = part[ok].reset_index(drop=True)
            if g.y.sum() == 0:
                hres["gfs"][k] = {"reason": "no GFS-covered positives"}
                continue
            gf = feats(g.drop(columns=S.DIRECT_RAIN), H, rain, "gfs", gfs)
            gf["y"] = g.y.to_numpy()
            hres["gfs"][k] = {"rows": {"n": len(gf), "n_pos": int(gf.y.sum())},
                              **_block(gf, sc(gf), cal, boot=(k == "val"))}
        # TPI > 0.6 rule: only where SMAP is cached (run-up rows of each event)
        tpi = {}
        for k, part in (("val", va), ("test", te)):
            m = part.merge(smap, on=["lon", "lat", "issue_date"], how="inner")
            if m.y.sum() == 0 or "slope" not in m:
                tpi[k] = {"reason": "no SMAP-covered positives"}
                continue
            tp = SC.horizon_scores(m, H, {"rule_tpi": {"kind": "tpi", "model": None, "cols": []}},
                                   rain, source="perfect")["rule_tpi"]
            same = sc(m)
            cal_t = {**cal, "rule_tpi": {"cal": M.Calibrator(), "thr": S.TPI_RULE_THRESHOLD}}
            tpi[k] = {"rows": {"n": len(m), "n_pos": int(m.y.sum())},
                      **_block(m, {**same, "rule_tpi": tp}, cal_t)}
        hres["tpi_subset_perfect"] = tpi
        res["horizons"][h] = hres
        log.info("v3 %s done", h)
    return res


def report_md(res: dict) -> str:
    f = lambda v: "—" if v is None else f"{v:.3f}"
    L = [f"rows {res['rows']}; dense events {len(res['events'])}, GLC events {res['glc_events']}"]
    for h, hr in res["horizons"].items():
        L += [f"\n#### {h} (train rows {hr['train_rows']}, VAL thr {hr['val_thresholds']})", "",
              "| source · split · model | base | acc | P | R | F1 | PR-AUC | ROC | event-wtd P / R / PR (n) | alarms/site-season |",
              "|---|---|---|---|---|---|---|---|---|---|"]
        blocks = [("perfect", k, hr["perfect"][k]) for k in ("train", "val", "test")]
        blocks += [("gfs", k, v) for k, v in hr["gfs"].items() if "reason" not in v]
        blocks += [("tpi-subset", k, v) for k, v in hr["tpi_subset_perfect"].items() if "reason" not in v]
        for src, k, b in blocks:
            for n, r in b.items():
                if n == "rows":
                    continue
                p, e = r["pooled"], r["event_weighted"]
                L.append(f"| {src} · {k} · {n} | {f(p['base_rate'])} | {f(p['accuracy'])} | {f(p['precision'])} | "
                         f"{f(p['recall'])} | {f(p['f1'])} | {f(p['pr_auc'])} | {f(p['roc_auc'])} | "
                         f"{f(e['precision'])} / {f(e['recall'])} / {f(e['pr_auc'])} ({e['events_n']}) | "
                         f"{f(r['alarm_days']['per_site_season_mean'])} |")
                if "pooled_ci90_event_bootstrap" in r:
                    ci = r["pooled_ci90_event_bootstrap"]
                    L.append(f"|   ↳ VAL 90% event-bootstrap CI | {ci['base_rate']} | | {ci['precision']} | "
                             f"{ci['recall']} | {ci['f1']} | {ci['pr_auc']} | {ci['roc_auc']} | | |")
        evs = sorted({e for b in [hr["perfect"]["val"], hr["perfect"]["test"]] for r in b.values() for e in r["by_event"]})
        L += ["", "| per event (perfect) · model | " + " | ".join(evs) + " |", "|---|" + "---|" * len(evs)]
        def cell(m):
            return (f"{f(m.get('precision'))} / {f(m.get('recall'))} / {f(m.get('pr_auc'))}, "
                    f"{f(m.get('base_rate'))}") if m else ""
        for k in ("val", "test"):
            for n, r in hr["perfect"][k].items():
                L.append(f"| {k} · {n} (P@thr / R / PR, base) | "
                         + " | ".join(cell(r["by_event"].get(e)) for e in evs) + " |")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--md", action="store_true")
    a = ap.parse_args(argv)
    path = S.MODEL_DIR / "metrics_v3.json"
    if not a.md:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
        path.write_text(json.dumps(run(), indent=1, default=str))
    print(report_md(json.loads(path.read_text())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
