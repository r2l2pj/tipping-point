"""Rows (positives + negatives + split) and feature tables for 3a and 3b.

Stages (see ``ml.local.__main__``)::

    labels  -> DATA_DIR/labels/inventory.csv
    sample  -> DATA_DIR/tables/rows_3a.csv, rows_3b.csv, horizon_3b.csv
               ml/models/split_3a_blocks.csv, ml/models/split_3b_events.csv
    table   -> DATA_DIR/tables/table_3a.csv, table_3b.csv, horizon_3b_features.csv
               ml/models/split_summary.json

3a rows: GLC (<= 5 km, South+Southeast Brazil, any date) + RS 2024 sample;
negatives 3:1 random land points > 5 km from any known landslide (seed 42).

3b rows (target-day rows for the daily trigger model): dated rain-triggered GLC
points worldwide (<= 5 km) since SMAP start + RS 2024 sample (fixed 2024-05-01)
+ WA 2025 dated reports (both TEST-only). Negatives:
(a) the same point on NEG_DATES_3B no-event dates shared by its event (PLAN 3b),
(b) one random land point 5-50 km away on the positive's day ("where" signal).

3b horizon rows (evaluation of P within 24 h..14 d, VAL + TEST only): for a
positive on day L and horizon H, issue days D = L-j, j = 0..H-1 (label 1); for a
negative date N, issue day D = N (label 0, window free of events by construction).
"""
from __future__ import annotations

import datetime as dt
import logging

import numpy as np
import pandas as pd

from . import labels as L
from . import settings as S
from . import split as SP

log = logging.getLogger(__name__)

NEG_SPACE_RADIUS_KM = (5.0, 50.0)


def tables_dir():
    d = S.DATA_DIR / "tables"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ------------------------------------------------------------------------ labels
def build_labels(*, smoke: bool = False, force: bool = False) -> pd.DataFrame:
    """GLC/COOLR (global) + RS 2024 + WA 2025 -> inventory.csv (RS fixed at RS2024_DATE)."""

    glc = L.fetch_glc(S.REGION_TRIGGER, force=force)  # global GLC/COOLR (~15k features)
    rs = L.dedupe_grid(L.load_rs2024())
    try:
        wa = L.dedupe_grid(L.fetch_wa2025(force=force))
    except Exception as exc:  # noqa: BLE001 - optional TEST event, never blocks the run
        log.warning("WA 2025 reports unavailable (%s) — TEST = RS 2024 only", exc)
        wa = L.wa2025_frame([])
    if smoke:
        rs = rs[L.in_bbox(rs["lon"], rs["lat"], S.SMOKE_BBOX)]
        rs = rs.sample(n=min(len(rs), 60), random_state=S.SEED)
    # Fixed event date, independent of rain (no circular peak-rain dating).
    rs["date"] = pd.Timestamp(S.RS2024_DATE)
    rs["date_precision"] = "event-window"
    # Extra held-out TEST events (test_events.yaml). An enabled event that fails to
    # load stops the stage: silently shrinking TEST would change the claim.
    extra = [L.load_event_inventory(ev) for ev in S.EXTRA_TEST_EVENTS]
    inv = L.finalize(pd.concat([glc, rs, wa, *extra], ignore_index=True))
    path = S.path("labels", "inventory.csv")
    inv.to_csv(path, index=False)
    log.info("inventory: %d points (%d GLC, %d RS 2024, %d WA 2025, %d extra TEST %s) -> %s",
             len(inv), (inv.source == "glc").sum(), (inv.source == "rs2024").sum(),
             (inv.source == "wa2025").sum(), (inv.source == "event").sum(),
             inv.loc[inv.source == "event", "event_id"].value_counts().to_dict(), path)
    return inv


def drop_test_leaks(glc: pd.DataFrame, inv: pd.DataFrame, tag: str) -> pd.DataFrame:
    """Remove GLC points that may be a held-out TEST event (S.LEAK_DAYS / S.LEAK_KM);
    the removed rows are written to ml/models/test_event_leaks_<tag>.csv."""
    test_pts = inv[inv.event_id.isin(S.TRIGGER_TEST_EVENTS)]
    mask, rep = L.leak_mask(glc, test_pts, S.EVENT_WINDOWS)
    SP.save_table(rep, S.MODEL_DIR / f"test_event_leaks_{tag}.csv")
    log.info("%s: %d GLC points removed as possible TEST-event leaks %s", tag, int(mask.sum()),
             rep.test_event.value_counts().to_dict() if len(rep) else {})
    return glc[~mask]


def load_inventory() -> pd.DataFrame:
    path = S.DATA_DIR / "labels" / "inventory.csv"
    if not path.exists():
        raise FileNotFoundError(f"{path} missing — run `python -m ml.local labels` first")
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    return df


# ----------------------------------------------------------------- 3a sampling
def sample_3a(inv: pd.DataFrame, *, smoke: bool = False, seed: int = S.SEED) -> pd.DataFrame:
    from . import sources_static as ST

    region = S.SMOKE_BBOX if smoke else S.REGION_TRAIN
    rng = np.random.default_rng(seed)
    glc = inv[(inv.source == "glc") & inv.acc_km.le(S.GLC_MAX_ACC_KM)
              & L.in_bbox(inv.lon, inv.lat, region)]
    glc = drop_test_leaks(glc, inv, "3a")
    parts = [glc]
    # off since L32 (RS is a 3b TEST event); smoke keeps a few RS points only to have
    # any 3a positives in its 1° box (plumbing test, never reported)
    if "rs2024" in S.T_ML_POSITIVE_SOURCES or smoke:
        rs = inv[(inv.source == "rs2024") & L.in_bbox(inv.lon, inv.lat, region)]
        cap = 60 if smoke else S.RS2024_MAX_POINTS
        parts.append(rs.sample(n=min(len(rs), cap), random_state=seed))
    pos = pd.concat(parts, ignore_index=True).assign(label=1, neg_type="")

    known_lon, known_lat = inv["lon"].to_numpy(), inv["lat"].to_numpy()
    want = S.NEG_RATIO_3A * len(pos)
    got_lon, got_lat = [], []
    tries = 0
    while sum(map(len, got_lon)) < want and tries < 50:
        lon, lat = ST.random_land_points(max(2 * want, 100), region, rng)
        far = L.min_dist_km(lon, lat, known_lon, known_lat) > S.NEG_MIN_DIST_KM
        got_lon.append(lon[far])
        got_lat.append(lat[far])
        tries += 1
    nlon = np.concatenate(got_lon)[:want]
    nlat = np.concatenate(got_lat)[:want]
    neg = pd.DataFrame({
        "point_id": [f"neg3a_{i}" for i in range(len(nlon))], "lon": nlon, "lat": nlat,
        "source": "random", "event_id": "", "date": pd.NaT, "date_precision": "none",
        "acc_km": 0.0, "trigger": "", "title": "", "label": 0, "neg_type": "random_far",
    })
    rows = pd.concat([pos, neg], ignore_index=True)

    blocks = SP.split_blocks_3a(pos, region)
    SP.save_table(blocks, S.MODEL_DIR / "split_3a_blocks.csv")
    rows["split"] = SP.rows_split_3a(rows["lon"], rows["lat"], blocks)
    rows["region"] = L.report_region(rows["lon"].to_numpy(), rows["lat"].to_numpy())
    rows["row_id"] = np.arange(len(rows))
    rows.to_csv(tables_dir() / "rows_3a.csv", index=False)
    log.info("3a rows: %d pos / %d neg; split %s", len(pos), len(neg),
             rows["split"].value_counts().to_dict())
    return rows


# ----------------------------------------------------------------- 3b sampling
def _date_pool(start: dt.date, end: dt.date, n: int, rng) -> np.ndarray:
    days = (end - start).days + 1
    pick = rng.choice(days, size=min(n, days), replace=False)
    return np.sort(np.datetime64(start) + pick.astype("timedelta64[D]"))


def last_data_day() -> dt.date:
    """Latest day with IMERG + SMAP for training rows (POWER/IMERG lag ~ days)."""
    return dt.date.today() - dt.timedelta(days=10)


def sample_3b(inv: pd.DataFrame, *, smoke: bool = False, seed: int = S.SEED) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    since = pd.Timestamp(S.SMAP_START)
    glc = inv[(inv.source == "glc") & inv.acc_km.le(S.GLC_MAX_ACC_KM)
              & inv.trigger.isin(S.GLC_RAIN_TRIGGERS) & inv.date.ge(since)]
    if S.REGION_TRIGGER is not None:
        glc = glc[L.in_bbox(glc.lon, glc.lat, S.REGION_TRIGGER)]
    glc = drop_test_leaks(glc, inv, "3b")
    rs = inv[(inv.source == "rs2024") & inv.date.notna()]
    wa = inv[(inv.source == "wa2025") & inv.date.notna()]
    extra = inv[(inv.source == "event") & inv.event_id.isin(S.TRIGGER_TEST_EVENTS)
                & inv.date.notna()]
    if smoke:
        # tiny but real: 16 random GLC points worldwide + a few RS points + all WA
        glc = glc.sample(n=min(len(glc), S.SMOKE_GLC_N), random_state=seed)
        rs = rs.sample(n=min(len(rs), 30), random_state=seed)
    else:
        rs = rs.sample(n=min(len(rs), S.RS2024_MAX_POINTS), random_state=seed)
    pos = pd.concat([glc, rs, wa, extra], ignore_index=True).assign(label=1, neg_type="")
    pos["date"] = pd.to_datetime(pos["date"]).dt.normalize()

    events = SP.split_events_3b(pos, seed=seed)
    SP.save_table(events, S.MODEL_DIR / "split_3b_events.csv")
    ev_split = dict(zip(events.event_id, events.split, strict=True))

    # (a) same point on NEG_DATES_3B no-event dates shared by the whole event
    end = last_data_day()
    pool = _date_pool(S.SMAP_START, end, S.NEG_DATE_POOL, rng)
    neg_rows = []
    for ev, grp in pos.groupby("event_id"):
        ev_days = grp["date"].to_numpy().astype("datetime64[D]")
        if ev in S.EVENT_WINDOWS:
            w0, w1 = S.EVENT_WINDOWS[ev]
            ev_days = np.concatenate([ev_days, np.arange(
                np.datetime64(w0), np.datetime64(w1) + np.timedelta64(1, "D"))])
        gap = np.min(np.abs(pool[:, None] - ev_days[None, :]).astype(int), axis=1)
        ok = pool[gap >= S.NEG_DATE_GAP_DAYS]
        dates = rng.choice(ok, size=min(S.NEG_DATES_3B, len(ok)), replace=False)
        for d in dates:
            g = grp.copy()
            g["date"] = pd.Timestamp(d)
            g["label"] = 0
            g["neg_type"] = "same_point_other_date"
            neg_rows.append(g)
    neg_a = pd.concat(neg_rows, ignore_index=True) if neg_rows else pos.iloc[:0]

    # (b) one random point 5-50 km away on the positive's day (land check at table time)
    r_km = rng.uniform(*NEG_SPACE_RADIUS_KM, size=len(pos))
    theta = rng.uniform(0, 2 * np.pi, size=len(pos))
    dlat = r_km * np.sin(theta) / 110.57
    dlon = r_km * np.cos(theta) / (111.32 * np.cos(np.radians(pos["lat"].to_numpy())))
    neg_b = pos.copy()
    neg_b["lon"] = (pos["lon"].to_numpy() + dlon + 180.0) % 360.0 - 180.0  # antimeridian
    neg_b["lat"] = pos["lat"].to_numpy() + dlat
    far = L.min_dist_km(neg_b.lon.to_numpy(), neg_b.lat.to_numpy(),
                        inv.lon.to_numpy(), inv.lat.to_numpy()) > S.NEG_MIN_DIST_KM
    neg_b = neg_b[far].assign(label=0, neg_type="elsewhere_same_day")
    neg_b["point_id"] = neg_b["point_id"] + "_nb"

    rows = pd.concat([pos, neg_a, neg_b], ignore_index=True)
    rows["split"] = rows["event_id"].map(ev_split)
    rows["split"] = SP.buffer_rows(rows["lon"].to_numpy(), rows["lat"].to_numpy(),
                                   rows["split"].to_numpy())
    rows["region"] = L.report_region(rows["lon"].to_numpy(), rows["lat"].to_numpy())
    rows["row_id"] = np.arange(len(rows))
    rows.to_csv(tables_dir() / "rows_3b.csv", index=False)
    log.info("3b rows: %d pos, %d same-point neg, %d elsewhere neg; split %s",
             len(pos), len(neg_a), len(neg_b), rows["split"].value_counts().to_dict())

    hz = horizon_rows(rows)
    hz.to_csv(tables_dir() / "horizon_3b.csv", index=False)
    return rows


LOOKBACK_DAYS = 20  # issue days L-20 .. L per positive (>= the 14 d horizon)


def horizon_rows(rows: pd.DataFrame, horizons=S.HORIZONS,
                 lookback: int = LOOKBACK_DAYS) -> pd.DataFrame:
    """One shared set of issue-day rows for VAL + TEST, labels per horizon.

    Positive point with event window [L0, L1] (``S.LABEL_WINDOWS``, else the
    point's own day L0 = L1 = L) -> issue days D = L1 - j, j = 0..lookback;
    ``label_<h>`` = 1 when [D, D+H-1] overlaps [L0, L1]. Its
    same-point negative dates N -> issue day D = N, every label 0 (event-free by
    the 30-day gap). Same rows for every horizon, so the base rate grows with
    the window exactly as it does operationally.
    """
    keep = rows[rows["split"].isin(["val", "test"])]
    pos = keep[keep.label == 1].copy()
    d = pd.to_datetime(pos["date"])
    w0 = d.copy()
    w1 = d.copy()
    for ev, (a, b) in S.LABEL_WINDOWS.items():
        m = (pos["event_id"] == ev).to_numpy()
        w0[m] = pd.Timestamp(a)
        w1[m] = pd.Timestamp(b)
    pos["win_start"], pos["win_end"] = w0.dt.normalize(), w1.dt.normalize()
    out = []
    for j in range(lookback + 1):
        g = pos.copy()
        g["issue_date"] = g["win_end"] - pd.Timedelta(days=j)
        g["lead_days"] = (g["win_start"] - g["issue_date"]).dt.days.clip(lower=0)
        out.append(g)
    neg = keep[(keep.label == 0) & (keep.neg_type == "same_point_other_date")].copy()
    neg["issue_date"] = neg["date"]
    neg["lead_days"] = -1
    out.append(neg)
    hz = pd.concat(out, ignore_index=True)
    hz["issue_date"] = pd.to_datetime(hz["issue_date"]).dt.normalize()
    pos_row = hz["lead_days"] >= 0
    end = hz["issue_date"]
    for h, H in horizons.items():
        overlap = (end <= hz["win_end"]) & (end + pd.Timedelta(days=H - 1) >= hz["win_start"])
        hz[f"label_{h}"] = (pos_row & overlap.fillna(False)).astype(int)
    return hz.drop(columns=["win_start", "win_end"])


def load_rows(name: str) -> pd.DataFrame:
    path = tables_dir() / f"{name}.csv"
    if not path.exists():
        raise FileNotFoundError(f"{path} missing — run `python -m ml.local sample` first")
    df = pd.read_csv(path, keep_default_na=True)
    for c in ("date", "issue_date"):
        if c in df:
            df[c] = pd.to_datetime(df[c], errors="coerce")
    return df


# ----------------------------------------------------------------- data needs
def point_needs() -> dict:
    """Everything `fetch-points` must download for the sampled rows."""
    r3b = load_rows("rows_3b")
    hz = load_rows("horizon_3b")
    smap_lon = np.concatenate([r3b.lon, hz.lon])
    smap_lat = np.concatenate([r3b.lat, hz.lat])
    smap_dates = np.concatenate([r3b.date.values, hz.issue_date.values]).astype("datetime64[D]")
    return {
        "power_lon": r3b.lon.to_numpy(), "power_lat": r3b.lat.to_numpy(),
        "smap_lon": smap_lon, "smap_lat": smap_lat, "smap_dates": smap_dates,
        "static_lon": r3b.lon.to_numpy(), "static_lat": r3b.lat.to_numpy(),
        "gfs_jobs": gfs_jobs(hz),
    }


def _bbox_of(lon, lat, pad=0.0, snap=0.25):
    """Padded bbox snapped outward to the GFS 0.25° grid (stable cache key)."""
    return (float(np.floor((np.min(lon) - pad) / snap) * snap),
            float(np.floor((np.min(lat) - pad) / snap) * snap),
            float(np.ceil((np.max(lon) + pad) / snap) * snap),
            float(np.ceil((np.max(lat) + pad) / snap) * snap))


def gfs_jobs(hz: pd.DataFrame) -> list[dict]:
    """One GFS subset per TEST event: its own small bbox + its issue dates.

    One union bbox would span RS -> WA (a continent of 0.25° cells per
    bucket); per-event boxes keep the cache to a few MB per issue day.
    ``fetch-points`` downloads these and ``trainer`` reads them back with the
    same boxes (``gfs_reader``), so the cache keys always match.
    """
    test = hz[hz.split == "test"]
    out = []
    for ev, g in test.groupby("event_id"):
        out.append({"event_id": ev, "bbox": _bbox_of(g.lon, g.lat, pad=0.5),
                    "issue_dates": np.unique(g.issue_date.values.astype("datetime64[D]"))})
    return out


def gfs_reader(jobs: list[dict]):
    """gfs_daily(lon, lat, issue, H) over several per-event boxes (first box containing the point)."""
    from . import sources_dynamic as D

    def read(lon, lat, issue, lead_days):
        lon = np.asarray(lon, float)
        lat = np.asarray(lat, float)
        issue = np.asarray(issue).astype("datetime64[D]")
        out = np.full((len(lon), lead_days), np.nan)
        todo = np.ones(len(lon), bool)
        for job in jobs:
            m = todo & L.in_bbox(lon, lat, job["bbox"])
            if m.any():
                out[m] = D.gfs_daily(lon[m], lat[m], issue[m], lead_days, bbox=job["bbox"])
                todo &= ~m
        return out
    return read


# -------------------------------------------------------------------- features
def doy_features(dates) -> pd.DataFrame:
    doy = pd.to_datetime(dates).day_of_year.to_numpy()
    ang = 2 * np.pi * doy / 365.25
    return pd.DataFrame({"doy_sin": np.sin(ang), "doy_cos": np.cos(ang)})


def build_table_3a() -> pd.DataFrame:
    from . import sources_dynamic as D
    from . import sources_static as ST

    rows = load_rows("rows_3a")
    lon, lat = rows.lon.to_numpy(), rows.lat.to_numpy()
    feats = [ST.sample_static(lon, lat).reset_index(drop=True),
             D.climate(lon, lat).reset_index(drop=True),
             D.soil_texture(lon, lat).reset_index(drop=True)]
    tab = pd.concat([rows.reset_index(drop=True)] + feats, axis=1)
    tab = tab.loc[:, ~tab.columns.duplicated()]
    tab.to_csv(tables_dir() / "table_3a.csv", index=False)
    return tab


def build_table_3b() -> tuple[pd.DataFrame, pd.DataFrame]:
    from . import sources_dynamic as D
    from . import sources_static as ST

    rows = load_rows("rows_3b")
    rain = D.PowerRain.load()
    smap = D.SmapPoints.load()
    lon, lat = rows.lon.to_numpy(), rows.lat.to_numpy()
    dates = rows.date.values.astype("datetime64[D]")
    static = ST.sample_static_points(lon, lat).reset_index(drop=True)
    tab = pd.concat([
        rows.reset_index(drop=True),
        D.rain_features(lon, lat, dates, imerg=rain, clim=rain).reset_index(drop=True),
        smap.sample(lon, lat, dates).reset_index(drop=True),
        D.soil_texture(lon, lat).reset_index(drop=True),
        static,
        doy_features(dates),
    ], axis=1)
    tab = tab.loc[:, ~tab.columns.duplicated()]
    # elsewhere-negatives that fell in water / no DEM are not valid land rows
    bad = (tab.neg_type == "elsewhere_same_day") & tab["elevation"].isna()
    tab = tab[~bad].reset_index(drop=True)
    tab.to_csv(tables_dir() / "table_3b.csv", index=False)

    hz = load_rows("horizon_3b")
    hz_static = ST.sample_static_points(hz.lon.to_numpy(), hz.lat.to_numpy()).reset_index(drop=True)
    hz_smap = smap.sample(hz.lon.to_numpy(), hz.lat.to_numpy(),
                          hz.issue_date.values.astype("datetime64[D]")).reset_index(drop=True)
    hz_tex = D.soil_texture(hz.lon.to_numpy(), hz.lat.to_numpy()).reset_index(drop=True)
    hzt = pd.concat([hz.reset_index(drop=True), hz_static, hz_smap, hz_tex], axis=1)
    hzt = hzt.loc[:, ~hzt.columns.duplicated()]
    hzt.to_csv(tables_dir() / "horizon_3b_features.csv", index=False)

    summ = {"3a": SP.summary(load_rows("table_3a")) if (tables_dir() / "table_3a.csv").exists() else None,
            "3b": SP.summary(tab)}
    import json
    summary_path = S.MODEL_DIR / "split_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summ, indent=2))
    return tab, hzt
