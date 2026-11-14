"""Validation: block-CV metrics, baselines, importance → metrics.json.

Reports the two honest scores from spec §2.4/§5.3:
  * event-window AUC (positives vs N1 spatial negatives) — the headline
  * all-negatives AUC (adds N2 temporal negatives)

Baselines on the same folds: slope-only and the doc's manual TPI_slide = R×S×T
(gee/01_tpi_index.js). Thresholds are set at a fixed FPR on the OOF scores.
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score, roc_curve

from . import config as cfg
from .dataset import iter_block_folds, load_features, prepare_X

log = logging.getLogger(__name__)


def tpr_at_fpr(y_true, y_score, fpr_target: float = cfg.FPR_TARGET) -> float:
    fpr, tpr, _ = roc_curve(y_true, y_score)
    return float(np.interp(fpr_target, fpr, tpr))


def metric_bundle(y_true, y_score, fpr_target: float = cfg.FPR_TARGET) -> dict:
    y_true = np.asarray(y_true)
    y_score = np.asarray(y_score)
    return {
        "roc_auc": float(roc_auc_score(y_true, y_score)),
        "pr_auc": float(average_precision_score(y_true, y_score)),
        "tpr_at_fpr05": tpr_at_fpr(y_true, y_score, fpr_target),
    }


def _summary(per_fold: list[dict]) -> dict:
    out = {}
    for k in per_fold[0]:
        vals = np.array([f[k] for f in per_fold], dtype=float)
        out[k] = {"mean": float(vals.mean()), "sd": float(vals.std())}
    return out


# --- Baselines ---------------------------------------------------------------
def slope_only_score(df: pd.DataFrame) -> np.ndarray:
    col = "slope_max" if "slope_max" in df.columns else "slope_mean"
    return df[col].to_numpy(dtype=float)


def _unit_scale(x, lo, hi):
    return np.clip((np.asarray(x, dtype=float) - lo) / (hi - lo), 0.0, 1.0)


def manual_tpi_score(
    df: pd.DataFrame, percentiles: tuple[float, float, float] | None = None
) -> np.ndarray:
    """Manual TPI_slide = R×S×T (gee/01_tpi_index.js).

    R: piecewise ramp of 3-day rain against climatological p90/p95/p99.
    S: SMAP wetness unitScale(0.5, 0.9).  T: slope unitScale(15°, 30°).

    The feature table has no rain climatology columns, so p90/p95/p99 of
    rain_3d ≈ rain_24h + rain_ante_2d are estimated from the given rows —
    pass per-training-fold rows so the baseline sees no test data.
    """
    rain3 = df["rain_24h"].to_numpy(dtype=float) + df["rain_ante_2d"].to_numpy(dtype=float)
    p90, p95, p99 = percentiles if percentiles else np.nanquantile(rain3, [0.9, 0.95, 0.99])
    # Same sequential-overwrite semantics as the JS .where() chain.
    R = np.zeros_like(rain3)
    R = np.where(rain3 >= p90, (rain3 - p90) / max(p95 - p90, 1e-9) * 0.5, R)
    R = np.where(rain3 >= p95, (rain3 - p95) / max(p99 - p95, 1e-9) * 0.5 + 0.5, R)
    R = np.where(rain3 >= p99, 1.0, R).clip(0, 1)
    S = _unit_scale(df["sm_profile_wetness"], 0.5, 0.9)
    T = _unit_scale(df["slope_max"], 15.0, 30.0)
    return R * S * T


# --- Fold evaluation ----------------------------------------------------------
def evaluate_cv(
    df: pd.DataFrame, oof: pd.Series, n_splits: int = cfg.N_FOLDS
) -> dict:
    """Per-fold metrics for model + baselines, event-window and all-negatives.

    ``oof`` must be aligned to ``df.index`` (from train.cross_validate).
    """
    per_fold = {"model": [], "slope_only": [], "tpi_manual": []}
    per_fold_all = {"model": [], "slope_only": [], "tpi_manual": []}

    for k, (tr, te) in enumerate(iter_block_folds(df, n_splits)):
        test, train = df.iloc[te], df.iloc[tr]
        pct = np.nanquantile(
            train["rain_24h"] + train["rain_ante_2d"], [0.9, 0.95, 0.99]
        )
        scores = {
            "model": oof.iloc[te].to_numpy(),
            "slope_only": slope_only_score(test),
            "tpi_manual": manual_tpi_score(test, tuple(pct)),
        }
        ev = test["set"].isin(["pos", "N1"]).to_numpy()
        for name, s in scores.items():
            y = test["label"].to_numpy()
            per_fold[name].append(metric_bundle(y[ev], np.asarray(s)[ev]))
            per_fold_all[name].append(metric_bundle(y, s))
        log.info("fold %d evaluated (%d rows, %d event-window)", k, len(te), ev.sum())

    return {
        "event_window": {n: _summary(f) for n, f in per_fold.items()},
        "all_negatives": {n: _summary(f) for n, f in per_fold_all.items()},
    }


def threshold_at_fpr(y_true, y_score, fpr_target: float = cfg.FPR_TARGET) -> float:
    """Lowest score threshold that keeps FPR ≤ ``fpr_target`` (max TPR under the cap)."""
    fpr, _, thr = roc_curve(y_true, y_score)
    mask = (fpr <= fpr_target) & np.isfinite(thr)
    if not mask.any():
        return float(np.min(thr[np.isfinite(thr)]))
    return float(np.min(thr[mask]))


def importance(model, X_sample: pd.DataFrame, with_shap: bool = True) -> dict:
    """XGBoost gain importance plus mean |SHAP| when shap is available."""
    booster = model.get_booster()
    gain = booster.get_score(importance_type="gain")
    out = {"gain": {k: float(v) for k, v in gain.items()}}
    if with_shap:
        try:
            import shap

            vals = shap.TreeExplainer(model).shap_values(X_sample)
            out["mean_abs_shap"] = {
                c: float(v) for c, v in zip(X_sample.columns, np.abs(vals).mean(axis=0))
            }
        except Exception as e:  # noqa: BLE001 — shap is best-effort; gain always reported
            log.warning("SHAP importances unavailable: %s", e)
    return out


# --- metrics.json ---------------------------------------------------------------
def update_metrics_json(
    section: dict, event_id: str = cfg.EVENT_ID, path: str | Path | None = None
) -> Path:
    """Merge ``section`` under ``landslide_ml`` in data/<event>/metrics.json."""
    path = Path(path) if path else cfg.DATA_DIR / event_id / "metrics.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    metrics = {"event": event_id}
    if path.exists():
        metrics = json.loads(path.read_text())
    metrics.setdefault("landslide_ml", {}).update(section)
    path.write_text(json.dumps(metrics, indent=2) + "\n")
    log.info("wrote %s", path)
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--features", required=True, help="feature table (csv/parquet)")
    ap.add_argument("--model", default=str(cfg.MODEL_DIR / "slide_xgb.json"))
    ap.add_argument("--folds", type=int, default=cfg.N_FOLDS)
    ap.add_argument("--event", default=cfg.EVENT_ID)
    ap.add_argument("--metrics-out", default=None)
    ap.add_argument("--no-shap", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    from xgboost import XGBClassifier

    from .train import cross_validate

    df = load_features(args.features)
    oof, _ = cross_validate(df, n_splits=args.folds)
    cv = evaluate_cv(df, oof, n_splits=args.folds)

    ev = df["set"].isin(["pos", "N1"]).to_numpy()
    thr = threshold_at_fpr(df["label"].to_numpy()[ev], oof.to_numpy()[ev])

    model = XGBClassifier()
    model.load_model(args.model)
    imp = importance(model, prepare_X(df).sample(min(2000, len(df)), random_state=0),
                     with_shap=not args.no_shap)

    metrics = {
        "cv": {
            "event_window": cv["event_window"]["model"],
            "all_negatives": cv["all_negatives"]["model"],
        },
        "baselines": {
            "slope_only": cv["event_window"]["slope_only"],
            "tpi_manual": cv["event_window"]["tpi_manual"],
        },
        "threshold": thr,
        "fpr_target": cfg.FPR_TARGET,
        "importance": imp,
        "data_versions": {
            "imerg": cfg.COL_IMERG,
            "smap": cfg.COL_SMAP,
            "dem": cfg.IMG_NASADEM,
            "lc": cfg.COL_MODIS_LC,
            "labels_ufrgs": cfg.ZENODO_UFRGS,
            "labels_cemaden": cfg.ZENODO_CEMADEN,
        },
    }
    update_metrics_json(metrics, event_id=args.event, path=args.metrics_out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
