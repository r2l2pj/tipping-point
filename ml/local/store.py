"""SQLite store for the ML training data (stdlib ``sqlite3``, no new deps).

``python -m ml.local db`` (after ``table``) writes ``settings.SQLITE_PATH``
(default ``data/ml/tipping_point_ml.sqlite``) from the stage CSVs:

landslides   one row per inventory point (GLC/COOLR global, RS 2024,
             WA 2025): point_id PK, lon, lat, source, event_id, date, ...
samples      one row per model row: sample_id PK, task ('3a' | '3b' |
             '3b_horizon'), point_id, event_id, lon, lat, date, issue_date,
             lead_days, label, label_<h> (horizon rows), neg_type, split
             ('train' | 'val' | 'test' | 'buffer'), region
features     sample_id PK + one column per feature (terrain, landcover, soil,
             climate, rain, SMAP, doy) — NULL where a source had no data
split_units  the frozen split decision: task, unit_id, n_pos, split
meta         key/value: build time, settings snapshot, feature groups, counts

Views ``v_3a``, ``v_3b``, ``v_3b_horizon`` join samples + features, e.g.::

    sqlite3 data/ml/tipping_point_ml.sqlite \\
      "SELECT split, label, count(*) FROM v_3b GROUP BY 1, 2"

The CSVs under ML_DATA_DIR stay the pipeline's working files; this file is
the single queryable copy for EDA, review and the team. Rebuilt atomically.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import sqlite3
from pathlib import Path

import pandas as pd

from . import settings as S

log = logging.getLogger(__name__)

SAMPLE_COLS = ["task", "point_id", "event_id", "source", "lon", "lat", "date", "issue_date",
               "lead_days", "label", "neg_type", "split", "region", "row_id", "date_precision",
               "acc_km", "trigger"]
ID_COLS = set(SAMPLE_COLS) | {"title", "sample_id"}


def _read_csv(path: Path) -> pd.DataFrame | None:
    return pd.read_csv(path) if path.exists() else None


def _samples_features(tab: pd.DataFrame, task: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    tab = tab.reset_index(drop=True).copy()
    tab["task"] = task
    tab["sample_id"] = [f"{task}:{i}" for i in range(len(tab))]
    label_h = [c for c in tab.columns if c.startswith("label_")]
    keep = ["sample_id"] + [c for c in SAMPLE_COLS if c in tab.columns] + label_h
    samples = tab[keep]
    feat_cols = [c for c in tab.columns
                 if c not in ID_COLS and c not in label_h and pd.api.types.is_numeric_dtype(tab[c])]
    return samples, tab[["sample_id"] + feat_cols]


def build_frames(data_dir: Path | None = None, model_dir: Path | None = None) -> dict[str, pd.DataFrame]:
    """All store tables as DataFrames (missing stage outputs are skipped)."""
    data_dir = data_dir or S.DATA_DIR
    model_dir = model_dir or S.MODEL_DIR
    tables = data_dir / "tables"
    frames: dict[str, pd.DataFrame] = {}
    inv = _read_csv(data_dir / "labels" / "inventory.csv")
    if inv is None:
        raise FileNotFoundError(f"{data_dir / 'labels' / 'inventory.csv'} missing — run `labels` first")
    frames["landslides"] = inv.drop_duplicates("point_id")

    samples, feats = [], []
    for name, task in (("table_3a", "3a"), ("table_3b", "3b"), ("horizon_3b_features", "3b_horizon")):
        tab = _read_csv(tables / f"{name}.csv")
        if tab is None:
            log.warning("store: %s.csv missing — skipped", name)
            continue
        s, f = _samples_features(tab, task)
        samples.append(s)
        feats.append(f)
    if not samples:
        raise FileNotFoundError(f"no feature tables in {tables} — run `table` first")
    frames["samples"] = pd.concat(samples, ignore_index=True)
    frames["features"] = pd.concat(feats, ignore_index=True)

    units = []
    for fname, task, uid in (("split_3a_blocks.csv", "3a", "block"),
                             ("split_3b_events.csv", "3b", "event_id")):
        u = _read_csv(model_dir / fname)
        if u is not None:
            units.append(pd.DataFrame({"task": task, "unit_id": u[uid].astype(str),
                                       "n_pos": u["n_pos"], "split": u["split"]}))
    frames["split_units"] = (pd.concat(units, ignore_index=True) if units else
                             pd.DataFrame(columns=["task", "unit_id", "n_pos", "split"]))
    counts = (frames["samples"].groupby(["task", "split", "label"], dropna=False).size()
              .reset_index(name="n").astype({"label": "Int64"}).to_dict("records"))
    meta = {
        "built_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "ml_data_dir": str(data_dir),
        "region_train_3a": S.REGION_TRAIN, "region_trigger_3b": S.REGION_TRIGGER,
        "trigger_test_events": S.TRIGGER_TEST_EVENTS, "horizons": S.HORIZONS,
        "feature_groups_3a": S.FEATURE_GROUPS_3A, "feature_groups_3b": S.FEATURE_GROUPS_3B,
        "seed": S.SEED, "neg_ratio_3a": S.NEG_RATIO_3A, "neg_dates_3b": S.NEG_DATES_3B,
        "block_deg": S.BLOCK_DEG, "buffer_km": S.BUFFER_KM, "split_fracs": S.SPLIT_FRACS,
        "counts": counts,
    }
    frames["meta"] = pd.DataFrame({"key": list(meta),
                                   "value": [json.dumps(v, default=str) for v in meta.values()]})
    return frames


DDL = """
CREATE UNIQUE INDEX IF NOT EXISTS ix_landslides_pid ON landslides(point_id);
CREATE INDEX IF NOT EXISTS ix_landslides_event ON landslides(event_id);
CREATE UNIQUE INDEX IF NOT EXISTS ix_samples_id ON samples(sample_id);
CREATE INDEX IF NOT EXISTS ix_samples_task_split ON samples(task, split);
CREATE UNIQUE INDEX IF NOT EXISTS ix_features_id ON features(sample_id);
CREATE VIEW IF NOT EXISTS v_3a AS
  SELECT s.*, f.* FROM samples s JOIN features f USING (sample_id) WHERE s.task = '3a';
CREATE VIEW IF NOT EXISTS v_3b AS
  SELECT s.*, f.* FROM samples s JOIN features f USING (sample_id) WHERE s.task = '3b';
CREATE VIEW IF NOT EXISTS v_3b_horizon AS
  SELECT s.*, f.* FROM samples s JOIN features f USING (sample_id) WHERE s.task = '3b_horizon';
"""


def write_store(path: Path | None = None, **kw) -> Path:
    """Build every table and swap the SQLite file in atomically."""
    path = Path(path or S.SQLITE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = build_frames(**kw)
    tmp = path.with_suffix(".sqlite.tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    try:
        for name, df in frames.items():
            df.to_sql(name, con, index=False)
        con.executescript(DDL)
        con.commit()
    finally:
        con.close()
    tmp.replace(path)
    log.info("store: %s (%s)", path,
             ", ".join(f"{k} {len(v)}" for k, v in frames.items() if k != "meta"))
    return path


def read_task(task: str, path: Path | None = None) -> pd.DataFrame:
    """samples JOIN features for one task ('3a' | '3b' | '3b_horizon')."""
    con = sqlite3.connect(Path(path or S.SQLITE_PATH))
    try:
        df = pd.read_sql_query(
            "SELECT s.*, f.* FROM samples s JOIN features f USING (sample_id) "
            "WHERE s.task = ? ORDER BY s.rowid", con, params=(task,))
    finally:
        con.close()
    df = df.loc[:, ~df.columns.duplicated()]
    for c in ("date", "issue_date"):
        if c in df:
            df[c] = pd.to_datetime(df[c], errors="coerce")
    return df
