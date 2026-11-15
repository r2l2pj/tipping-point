"""TRAIN / VAL / TEST split, decided before any model is fit.

Rules (conductor 2026-11-15, PLAN Nível 3):
  * TEST is never touched until the final evaluation; VAL is only for early
    stopping, threshold choice and calibration; TRAIN is the rest.
  * No cell and no event day in more than one split.
  * Buffer: a row closer than ``BUFFER_KM`` to a row of a *more held-out*
    split (train < val < test) is dropped ("buffer"), so neighbours don't leak.

3a (susceptibility, static): unit = ~50 km block (``BLOCK_DEG``); blocks that
share a small GLC event are merged into one unit; the RS 2024 inventory is
split by block (one storm covering ~10 blocks).

3b (trigger, dynamic): unit = event (``labels.cluster_events``). Every event in
``TRIGGER_TEST_EVENTS`` (RS 2024, WA 2025) are forced to TEST — trained elsewhere,
tested on RS 2024 / WA 2025. The rest go to TRAIN/VAL/TEST by positive count.

Negatives inherit the split of the point/event they were drawn for (3b) or of
their block (3a). Assignment files are written by ``save_*`` and re-read by
every later stage, so the split cannot drift between runs.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import settings as S
from .labels import km_to_chord, unit_xyz

SPLITS = ("train", "val", "test")
RANK = {"train": 0, "val": 1, "test": 2}


# ------------------------------------------------------------------ assignment
def greedy_assign(units: pd.DataFrame, fracs: dict[str, float] = S.SPLIT_FRACS,
                  seed: int = S.SEED, forced: dict | None = None) -> dict:
    """Assign ``units`` (columns unit_id, n_pos) to splits by positive count.

    Random order (seeded), each unit to the split furthest below its target
    share. ``forced`` maps unit_id -> split and is applied first (counted).
    Units with n_pos == 0 are drawn at random with ``fracs`` probabilities.
    """
    forced = dict(forced or {})
    rng = np.random.default_rng(seed)
    out: dict = {}
    got = {s: 0.0 for s in SPLITS}
    n_pos = units.set_index("unit_id")["n_pos"]
    for uid, sp in forced.items():
        out[uid] = sp
        got[sp] += float(n_pos.get(uid, 0.0))
    free = units[~units["unit_id"].isin(list(forced))]
    pos = free[free["n_pos"] > 0].sample(frac=1.0, random_state=seed)
    total = float(pos["n_pos"].sum())
    for uid, n in zip(pos["unit_id"], pos["n_pos"], strict=True):
        deficit = {s: fracs[s] * total - got[s] for s in SPLITS}
        best = max(SPLITS, key=lambda s: (deficit[s], -RANK[s]))
        out[uid] = best
        got[best] += float(n)
    zero = free[free["n_pos"] <= 0]["unit_id"].to_numpy()
    if len(zero):
        draws = rng.choice(list(SPLITS), size=len(zero), p=[fracs[s] for s in SPLITS])
        out.update(dict(zip(zero, draws, strict=True)))
    return out


# --------------------------------------------------------------------- 3a blocks
def block_xy(lon, lat, size: float = S.BLOCK_DEG) -> tuple[np.ndarray, np.ndarray]:
    return (np.floor(np.asarray(lon, dtype=float) / size).astype(np.int64),
            np.floor(np.asarray(lat, dtype=float) / size).astype(np.int64))


def block_key(bx, by) -> np.ndarray:
    return np.char.add(np.char.add(np.asarray(bx).astype(str), "_"), np.asarray(by).astype(str))


def split_blocks_3a(pos: pd.DataFrame, bbox=S.REGION_TRAIN, *, size: float = S.BLOCK_DEG,
                    seed: int = S.SEED, merge_max_points: int = 200,
                    fracs: dict[str, float] = S.SPLIT_FRACS) -> pd.DataFrame:
    """Block table for the 3a region: block_x, block_y, unit_id, n_pos, split.

    Blocks touched by the same event with <= ``merge_max_points`` points are
    merged into one unit (an event never straddles two splits).
    """
    w, s, e, n = bbox
    xs = np.arange(int(np.floor(w / size)), int(np.ceil(e / size)))
    ys = np.arange(int(np.floor(s / size)), int(np.ceil(n / size)))
    gx, gy = np.meshgrid(xs, ys, indexing="ij")
    blocks = pd.DataFrame({"block_x": gx.ravel(), "block_y": gy.ravel()})
    blocks["block"] = block_key(blocks["block_x"], blocks["block_y"])

    bx, by = block_xy(pos["lon"], pos["lat"], size)
    pb = pd.DataFrame({"block": block_key(bx, by), "event_id": pos["event_id"].to_numpy()})
    parent = {b: b for b in blocks["block"]}
    parent.update({b: b for b in pb["block"]})

    def find(b):
        while parent[b] != b:
            parent[b] = parent[parent[b]]
            b = parent[b]
        return b

    sizes = pb.groupby("event_id").size()
    for ev, grp in pb.groupby("event_id"):
        if sizes[ev] > merge_max_points:
            continue
        bl = grp["block"].unique()
        for other in bl[1:]:
            ra, rb = find(bl[0]), find(other)
            if ra != rb:
                parent[rb] = ra
    allb = pd.DataFrame({"block": list(parent)})
    allb["unit_id"] = [find(b) for b in allb["block"]]
    counts = pb.groupby("block").size()
    allb["n_pos_block"] = allb["block"].map(counts).fillna(0).astype(int)
    units = allb.groupby("unit_id", as_index=False)["n_pos_block"].sum().rename(
        columns={"n_pos_block": "n_pos"})
    assign = greedy_assign(units, fracs=fracs, seed=seed)
    allb["split"] = allb["unit_id"].map(assign)
    xy = allb["block"].str.split("_", expand=True).astype(np.int64)
    allb["block_x"], allb["block_y"] = xy[0], xy[1]
    return allb[["block", "block_x", "block_y", "unit_id", "n_pos_block", "split"]].rename(
        columns={"n_pos_block": "n_pos"})


def rows_split_3a(lon, lat, blocks: pd.DataFrame, *, size: float = S.BLOCK_DEG,
                  buffer_km: float = S.BUFFER_KM) -> np.ndarray:
    """Split per row from the block table, 'buffer' near a more held-out block."""
    lon = np.asarray(lon, dtype=float)
    lat = np.asarray(lat, dtype=float)
    lut = dict(zip(blocks["block"], blocks["split"], strict=True))
    bx, by = block_xy(lon, lat, size)
    own = np.array([lut.get(k, "train") for k in block_key(bx, by)], dtype=object)
    fx = lon / size - bx  # position inside the block, 0..1
    fy = lat / size - by
    km_x = size * 111.32 * np.cos(np.radians(lat))
    km_y = size * 110.57
    out = own.copy()
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            nb = np.array([lut.get(k, "train") for k in block_key(bx + dx, by + dy)], dtype=object)
            gap_x = np.where(dx == -1, fx, np.where(dx == 1, 1 - fx, 0.0)) * km_x
            gap_y = np.where(dy == -1, fy, np.where(dy == 1, 1 - fy, 0.0)) * km_y
            dist = np.hypot(gap_x, gap_y)
            more = np.array([RANK[b] > RANK[a] for a, b in zip(own, nb, strict=True)])
            out[(dist < buffer_km) & more] = "buffer"
    return out


# --------------------------------------------------------------------- 3b events
def split_events_3b(pos: pd.DataFrame, *, seed: int = S.SEED,
                    test_events=S.TRIGGER_TEST_EVENTS) -> pd.DataFrame:
    """Event table: event_id, n_pos, split (RS 2024 forced to TEST)."""
    units = pos.groupby("event_id", as_index=False).size().rename(
        columns={"event_id": "unit_id", "size": "n_pos"})
    forced = {ev: "test" for ev in test_events if ev in set(units["unit_id"])}
    assign = greedy_assign(units, seed=seed, forced=forced)
    out = units.rename(columns={"unit_id": "event_id"})
    out["split"] = out["event_id"].map(assign)
    out["forced"] = out["event_id"].isin(list(forced))
    return out


def buffer_rows(lon, lat, split: np.ndarray, *, buffer_km: float = S.BUFFER_KM) -> np.ndarray:
    """Mark rows within ``buffer_km`` of any row of a more held-out split."""
    from scipy.spatial import cKDTree

    split = np.asarray(split, dtype=object).copy()
    xyz = unit_xyz(lon, lat)
    r = km_to_chord(buffer_km)
    for held in ("test", "val"):
        ref = split == held
        if not ref.any():
            continue
        tree = cKDTree(xyz[ref])
        lower = np.array([RANK.get(s, 9) < RANK[held] for s in split])
        if not lower.any():
            continue
        d, _ = tree.query(xyz[lower], k=1, distance_upper_bound=r)
        hit = np.isfinite(d)
        idx = np.flatnonzero(lower)[hit]
        split[idx] = "buffer"
    return split


# ------------------------------------------------------------------- persistence
def save_table(df: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return path


def load_table(path: Path) -> pd.DataFrame:
    if not Path(path).exists():
        raise FileNotFoundError(f"{path} missing — run `python -m ml.local split` first")
    return pd.read_csv(path)


def summary(rows: pd.DataFrame) -> dict:
    """Per split: n events, positives, negatives, date range, region bbox."""
    out = {}
    for sp, g in rows.groupby("split"):
        d = pd.to_datetime(g["date"], errors="coerce") if "date" in g else pd.Series(dtype="datetime64[ns]")
        out[sp] = {
            "n_rows": len(g),
            "n_events": int(g.loc[g["label"] == 1, "event_id"].nunique()) if "event_id" in g else None,
            "n_pos": int((g["label"] == 1).sum()),
            "n_neg": int((g["label"] == 0).sum()),
            "date_min": None if d.isna().all() else str(d.min().date()),
            "date_max": None if d.isna().all() else str(d.max().date()),
            "bbox": [round(float(g["lon"].min()), 3), round(float(g["lat"].min()), 3),
                     round(float(g["lon"].max()), 3), round(float(g["lat"].max()), 3)],
            "regions": g.loc[g["label"] == 1, "region"].value_counts().to_dict()
            if "region" in g else {},
        }
    return out


def write_summary(rows: pd.DataFrame, path: Path) -> dict:
    s = summary(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(s, indent=2))
    return s
