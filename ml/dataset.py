"""Feature table I/O and spatial block cross-validation.

Reads the exported GEE feature table (CSV or parquet), enforces dtypes,
derives ``block_id`` on the 0.25° grid, and yields GroupKFold train/test
splits that never leak a block across folds (spec §2.4, §5.1).
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.model_selection import GroupKFold

from . import config as cfg

REQUIRED_COLS = ["cell_id", "lon", "lat", "t_utc", "label"]


def block_ids(lon, lat, block_size: float = cfg.BLOCK_SIZE_DEG) -> np.ndarray:
    """Integer block id on the 0.25° grid: floor(lon/size), floor(lat/size)."""
    bx = np.floor(np.asarray(lon, dtype=np.float64) / block_size).astype(np.int64)
    by = np.floor(np.asarray(lat, dtype=np.float64) / block_size).astype(np.int64)
    return bx * 1_000_000 + by


def load_features(path: str | Path) -> pd.DataFrame:
    """Read a feature table exported by gee_features (csv or parquet)."""
    path = Path(path)
    if path.suffix in (".parquet", ".pq"):
        df = pd.read_parquet(path)
    elif path.suffix == ".csv":
        df = pd.read_csv(path)
    else:
        raise ValueError(f"unsupported feature table format: {path}")

    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"feature table missing columns: {missing}")

    df["t_utc"] = pd.to_datetime(df["t_utc"], utc=True)
    df["label"] = df["label"].astype(np.int8)
    if "set" not in df.columns:
        df["set"] = np.where(df["label"] == 1, "pos", "N1")
    if "block_id" not in df.columns:
        df["block_id"] = block_ids(df["lon"], df["lat"])
    df["block_id"] = df["block_id"].astype(np.int64)
    return df


def write_features(df: pd.DataFrame, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix in (".parquet", ".pq"):
        df.to_parquet(path, index=False)
    elif path.suffix == ".csv":
        df.to_csv(path, index=False)
    else:
        raise ValueError(f"unsupported output format: {path}")
    return path


def feature_cols(df: pd.DataFrame) -> list[str]:
    """Feature columns present in the table, in spec order."""
    return [c for c in cfg.FEATURES if c in df.columns]


def prepare_X(df: pd.DataFrame, cols: list[str] | None = None) -> pd.DataFrame:
    """Feature matrix with categoricals cast for ``enable_categorical``."""
    cols = cols or feature_cols(df)
    X = df[cols].copy()
    for c in cfg.CATEGORICAL_FEATURES:
        if c in X.columns:
            X[c] = X[c].astype("category")
    return X


def iter_block_folds(
    df: pd.DataFrame, n_splits: int = cfg.N_FOLDS
) -> Iterator[tuple[np.ndarray, np.ndarray]]:
    """Yield (train_positional_idx, test_positional_idx) over 0.25° blocks.

    ``sklearn.model_selection.GroupKFold`` on ``block_id`` — no block (and
    therefore no cell) appears in both sides of a split.
    """
    groups = df["block_id"].to_numpy()
    gkf = GroupKFold(n_splits=n_splits)
    yield from gkf.split(df, df["label"].to_numpy(), groups)


def inner_block_split(
    df: pd.DataFrame, val_frac: float = 0.2, seed: int = 0
) -> tuple[np.ndarray, np.ndarray]:
    """Split rows into (fit, val) positional indices by whole blocks.

    Used for early stopping — the validation blocks are drawn from the
    training side only, never from the test fold.
    """
    blocks = df["block_id"].unique()
    rng = np.random.default_rng(seed)
    rng.shuffle(blocks)
    n_val = max(1, round(len(blocks) * val_frac))
    val_blocks = set(blocks[:n_val].tolist())
    is_val = df["block_id"].isin(val_blocks).to_numpy()
    pos = np.arange(len(df))
    return pos[~is_val], pos[is_val]
