"""Model fitting, calibration and thresholds — VAL only, never TEST.

* XGBoost: early stopping on VAL, monotone (+1) on rain/soil features.
* RandomForest(200, probability) — PLAN Nível 3 (doc's smileRandomForest).
* Calibration on VAL: isotonic when VAL has >= 30 positives, else Platt.
* Threshold: maximises F1 on calibrated VAL scores.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import precision_recall_curve
from xgboost import XGBClassifier

from . import settings as S

MIN_ISOTONIC_POS = 30


def prepare_X(df: pd.DataFrame, cols: list[str], *, for_rf: bool = False) -> pd.DataFrame:
    X = df[cols].copy()
    for c in S.CATEGORICAL:
        if c in X:
            if for_rf:
                X[c] = pd.to_numeric(X[c], errors="coerce")
            else:
                X[c] = pd.Categorical(pd.to_numeric(X[c], errors="coerce").round(),
                                      categories=list(range(18)))
    return X.astype({c: "float64" for c in X.columns if c not in S.CATEGORICAL or for_rf})


def fit_xgb(train: pd.DataFrame, val: pd.DataFrame, cols: list[str], *,
            pos_weight: float | None = None, seed: int = S.SEED,
            sample_weight=None, **over) -> XGBClassifier:
    params = dict(S.XGB_PARAMS)
    params["monotone_constraints"] = {k: v for k, v in S.MONOTONE.items() if k in cols}
    params["random_state"] = seed
    if pos_weight:
        params["scale_pos_weight"] = pos_weight
    params.update(over)
    has_val = len(val) > 0 and val["label"].nunique() == 2
    if not has_val:
        params.pop("early_stopping_rounds", None)
    model = XGBClassifier(**params)
    eval_set = [(prepare_X(val, cols), val["label"].to_numpy())] if has_val else None
    model.fit(prepare_X(train, cols), train["label"].to_numpy(), eval_set=eval_set, verbose=False,
              sample_weight=sample_weight)
    return model


def fit_rf(train: pd.DataFrame, cols: list[str], *, balanced: bool = False,
           seed: int = S.SEED) -> RandomForestClassifier:
    rf = RandomForestClassifier(**S.RF_PARAMS, random_state=seed,
                                class_weight="balanced" if balanced else None)
    rf.fit(prepare_X(train, cols, for_rf=True), train["label"].to_numpy())
    return rf


def predict(model, df: pd.DataFrame, cols: list[str]) -> np.ndarray:
    for_rf = isinstance(model, RandomForestClassifier)
    if len(df) == 0:
        return np.zeros(0)
    return model.predict_proba(prepare_X(df, cols, for_rf=for_rf))[:, 1]


# ------------------------------------------------------------------ calibration
@dataclass
class Calibrator:
    kind: str = "identity"
    x: list = field(default_factory=list)   # isotonic knots
    y: list = field(default_factory=list)
    a: float = 1.0                           # Platt on logit
    b: float = 0.0

    def __call__(self, p) -> np.ndarray:
        p = np.asarray(p, dtype=float)
        if self.kind == "isotonic":
            return np.interp(p, self.x, self.y)
        if self.kind == "platt":
            z = np.log(np.clip(p, 1e-6, 1 - 1e-6) / np.clip(1 - p, 1e-6, 1))
            return 1.0 / (1.0 + np.exp(-(self.a * z + self.b)))
        return p

    def to_dict(self) -> dict:
        return {"kind": self.kind, "x": list(map(float, self.x)), "y": list(map(float, self.y)),
                "a": float(self.a), "b": float(self.b)}

    @classmethod
    def from_dict(cls, d: dict) -> Calibrator:
        return cls(**d)


def fit_calibrator(p_val, y_val) -> Calibrator:
    p_val = np.asarray(p_val, dtype=float)
    y_val = np.asarray(y_val, dtype=int)
    if len(np.unique(y_val)) < 2:
        return Calibrator()
    if y_val.sum() >= MIN_ISOTONIC_POS:
        iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(p_val, y_val)
        return Calibrator("isotonic", list(iso.X_thresholds_), list(iso.y_thresholds_))
    z = np.log(np.clip(p_val, 1e-6, 1 - 1e-6) / np.clip(1 - p_val, 1e-6, 1)).reshape(-1, 1)
    lr = LogisticRegression().fit(z, y_val)
    return Calibrator("platt", a=float(lr.coef_[0, 0]), b=float(lr.intercept_[0]))


def best_f1_threshold(p, y) -> float:
    p = np.asarray(p, dtype=float)
    y = np.asarray(y, dtype=int)
    if len(np.unique(y)) < 2:
        return 0.5
    prec, rec, thr = precision_recall_curve(y, p)
    f1 = 2 * prec * rec / np.clip(prec + rec, 1e-12, None)
    i = int(np.nanargmax(f1[:-1])) if len(thr) else 0
    return float(thr[i]) if len(thr) else 0.5


# ----------------------------------------------------------------- persistence
def save_xgb(model: XGBClassifier, path: Path, meta: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    model.save_model(path)
    path.with_suffix(".meta.json").write_text(json.dumps(meta, indent=2, default=str))
    return path


def load_xgb(path: Path) -> tuple[XGBClassifier, dict]:
    m = XGBClassifier()
    m.load_model(path)
    meta = json.loads(Path(path).with_suffix(".meta.json").read_text())
    return m, meta
