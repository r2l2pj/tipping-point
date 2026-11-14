"""Train the LHASA-style XGBoost slide-trigger model.

Block-CV with early stopping on an inner block split (never the test fold),
then a final fit on all data → ``models/slide_xgb.json`` (spec §5.2, §5.4).

    uv run python -m ml.train --features features.parquet --out ml/models/slide_xgb.json
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd
from xgboost import XGBClassifier

from . import config as cfg
from .dataset import (
    feature_cols,
    inner_block_split,
    iter_block_folds,
    load_features,
    prepare_X,
)

log = logging.getLogger(__name__)


def build_classifier(feature_names: list[str], **overrides) -> XGBClassifier:
    """XGBClassifier per spec §5.2, monotone constraints limited to present features."""
    params = dict(cfg.XGB_PARAMS)
    params["monotone_constraints"] = {
        k: v for k, v in cfg.MONOTONE_CONSTRAINTS.items() if k in feature_names
    }
    params.update(overrides)
    return XGBClassifier(**params)


def _fit_es(
    model: XGBClassifier, X_fit, y_fit, X_val, y_val
) -> XGBClassifier:
    model.fit(X_fit, y_fit, eval_set=[(X_val, y_val)], verbose=False)
    return model


def fit_fold(
    train_df: pd.DataFrame, cols: list[str], seed: int = 0
) -> tuple[XGBClassifier, int]:
    """Fit one fold: inner block split for early stopping."""
    fit_idx, val_idx = inner_block_split(train_df, seed=seed)
    X = prepare_X(train_df, cols)
    y = train_df["label"].to_numpy()
    model = build_classifier(cols)
    _fit_es(model, X.iloc[fit_idx], y[fit_idx], X.iloc[val_idx], y[val_idx])
    best_it = getattr(model, "best_iteration", None)
    return model, model.n_estimators if best_it is None else int(best_it)


def cross_validate(
    df: pd.DataFrame, n_splits: int = cfg.N_FOLDS, seed: int = 0
) -> tuple[pd.Series, list[dict]]:
    """Out-of-fold predictions + per-fold fitted models.

    Returns (oof_score Series aligned to df.index, fold info dicts).
    """
    cols = feature_cols(df)
    oof = pd.Series(np.nan, index=df.index, dtype=np.float64)
    folds = []
    for k, (tr, te) in enumerate(iter_block_folds(df, n_splits)):
        model, best = fit_fold(df.iloc[tr], cols, seed=seed + k)
        oof.iloc[te] = model.predict_proba(prepare_X(df.iloc[te], cols))[:, 1]
        folds.append({"fold": k, "model": model, "best_iteration": best,
                      "n_train": len(tr), "n_test": len(te)})
        log.info("fold %d: %d train / %d test, best_iteration=%d", k, len(tr), len(te), best)
    return oof, folds


def train_final(
    df: pd.DataFrame, out_path: str | Path, seed: int = 0
) -> tuple[XGBClassifier, dict]:
    """Final fit on all rows (inner block holdout for early stopping) → JSON."""
    cols = feature_cols(df)
    fit_idx, val_idx = inner_block_split(df, seed=seed)
    X = prepare_X(df, cols)
    y = df["label"].to_numpy()
    model = build_classifier(cols)
    _fit_es(model, X.iloc[fit_idx], y[fit_idx], X.iloc[val_idx], y[val_idx])

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    model.save_model(out_path)
    best_it = getattr(model, "best_iteration", None)
    meta = {
        "feature_names": cols,
        "best_iteration": model.n_estimators if best_it is None else int(best_it),
        "n_rows": len(df),
        "monotone_constraints": {
            k: v for k, v in cfg.MONOTONE_CONSTRAINTS.items() if k in cols
        },
    }
    meta_path = out_path.with_suffix(".meta.json")
    meta_path.write_text(json.dumps(meta, indent=2))
    log.info("saved %s (+ %s)", out_path, meta_path)
    return model, meta


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--features", required=True, help="feature table (csv/parquet)")
    ap.add_argument("--out", default=str(cfg.MODEL_DIR / "slide_xgb.json"))
    ap.add_argument("--folds", type=int, default=cfg.N_FOLDS)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    df = load_features(args.features)
    log.info("features: %d rows, %d blocks, %d positives",
             len(df), df["block_id"].nunique(), int(df["label"].sum()))

    oof, _ = cross_validate(df, n_splits=args.folds, seed=args.seed)
    log.info("OOF coverage: %.1f%%", 100 * oof.notna().mean())
    train_final(df, args.out, seed=args.seed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
