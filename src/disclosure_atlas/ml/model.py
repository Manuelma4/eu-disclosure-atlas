"""The calibrated gradient-boosting classifier and the naive baselines it must beat.

Every candidate is a function ``fit(X, y) -> estimator with predict_proba``. ``X`` holds FEATURES in time order
(oldest first): the gradient-boosting model keeps the most recent ``CALIBRATION_SHARE`` of its training rows
apart to fit a Platt (sigmoid) calibration, so the probabilities are calibrated on data the trees did not see,
and later in time than it, as at prediction time.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.frozen import FrozenEstimator
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .features import FEATURES

CALIBRATION_SHARE = 0.2
PARAMS: dict[str, Any] = {
    "learning_rate": 0.04,
    "max_iter": 250,
    "max_leaf_nodes": 15,
    "min_samples_leaf": 200,
    "l2_regularization": 1.0,
    "early_stopping": False,
    "random_state": 0,
}
"""Shallow, strongly regularized trees: the signal is weak and the sample is a few thousand events per year."""

SKOPS_TRUSTED_TYPES = [
    "sklearn.calibration._CalibratedClassifier",
    "sklearn.calibration._SigmoidCalibration",
    "sklearn.ensemble._hist_gradient_boosting.predictor.TreePredictor",
]
"""Types MLflow's skops serializer must be told to trust; only models this project trained are ever loaded."""

Fit = Callable[[pd.DataFrame, np.ndarray], Any]


def fit_gradient_boosting(X: pd.DataFrame, y: np.ndarray, params: dict[str, Any] | None = None) -> Any:
    """Histogram gradient boosting on the older rows, sigmoid calibration on the newest ``CALIBRATION_SHARE``."""
    X = X[FEATURES]
    split = int(len(X) * (1 - CALIBRATION_SHARE))
    trees = HistGradientBoostingClassifier(**(PARAMS | (params or {})))
    trees.fit(X.iloc[:split], y[:split])
    calibrated = CalibratedClassifierCV(FrozenEstimator(trees), method="sigmoid")
    return calibrated.fit(X.iloc[split:], y[split:])


class _ColumnRate:
    """Base rate per value of one column (e.g. buys vs sells), falling back to the overall rate."""

    def __init__(self, column: str) -> None:
        self.column = column

    def fit(self, X: pd.DataFrame, y: np.ndarray) -> _ColumnRate:
        frame = pd.DataFrame({"key": X[self.column].to_numpy(), "y": y})
        self.overall_ = float(np.mean(y))
        self.rates_ = frame.groupby("key")["y"].mean().to_dict()
        return self

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        p = X[self.column].map(self.rates_).fillna(self.overall_).to_numpy(dtype=float)
        return np.column_stack([1 - p, p])


def _prior(X: pd.DataFrame, y: np.ndarray) -> Any:
    return DummyClassifier(strategy="prior").fit(X[FEATURES], y)


def _direction(X: pd.DataFrame, y: np.ndarray) -> Any:
    return _ColumnRate("is_buy").fit(X, y)


def _logistic(X: pd.DataFrame, y: np.ndarray) -> Any:
    model = make_pipeline(SimpleImputer(strategy="median", add_indicator=True), StandardScaler(),
                          LogisticRegression(C=1.0, max_iter=2000))
    return _Columns(model.fit(X[FEATURES], y))


class _Columns:
    """Select FEATURES before predicting (baselines receive the whole event frame)."""

    def __init__(self, model: Any) -> None:
        self.model = model

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        return self.model.predict_proba(X[FEATURES])


def _boosting(X: pd.DataFrame, y: np.ndarray) -> Any:
    return _Columns(fit_gradient_boosting(X, y))


CANDIDATES: dict[str, Fit] = {
    "prior": _prior,
    "direction_prior": _direction,
    "logistic": _logistic,
    "gradient_boosting": _boosting,
}
"""The model (gradient_boosting) and its baselines: the training base rate, the base rate of buys and sells,
and a regularized logistic regression on the same features."""
MODEL = "gradient_boosting"
