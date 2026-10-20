"""Expanding-window walk-forward validation without look-ahead, and the metrics reported in the model card.

For each test year Y, every candidate is trained on the events whose outcome was known before 1 January of Y
(``known_at``, publication plus an embargo longer than the label horizon) and scored on the events published in
Y. Features use only information public at publication (features.py), and training never sees an outcome that
was still in the future at the start of the test year.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

from .features import FEATURES
from .model import CANDIDATES, Fit

MIN_TRAIN = 1000


def walk_forward(data: pd.DataFrame, candidates: dict[str, Fit] | None = None, first_test_year: int | None = None,
                 min_train: int = MIN_TRAIN) -> pd.DataFrame:
    """Out-of-sample predictions, one row per test event, one ``p_<candidate>`` column per candidate.

    ``data`` is the output of ``labels.attach_outcomes`` with FEATURES (oldest first).
    """
    candidates = candidates or CANDIDATES
    data = data.sort_values(["publication_date", "event_id"]).reset_index(drop=True)
    years = sorted(data["publication_date"].dt.year.unique())
    folds = []
    for year in years:
        if first_test_year is not None and year < first_test_year:
            continue
        start = pd.Timestamp(year=year, month=1, day=1)
        train = data[data["known_at"] < start]
        test = data[data["publication_date"].dt.year == year]
        if len(train) < min_train or test.empty or train["label"].nunique() < 2:
            continue
        y = train["label"].to_numpy()
        fold = test[["event_id", "publication_date", "isin", "is_buy", "label", "outcome"]].copy()
        fold["fold"] = year
        fold["train_size"] = len(train)
        fold["train_until"] = train["publication_date"].max()
        for name, fit in candidates.items():
            fold[f"p_{name}"] = fit(train[FEATURES], y).predict_proba(test[FEATURES])[:, 1]
        folds.append(fold)
    if not folds:
        raise ValueError("not enough history for one walk-forward fold")
    return pd.concat(folds, ignore_index=True)


def expected_calibration_error(y: np.ndarray, p: np.ndarray, bins: int = 10) -> float:
    """Mean absolute gap between predicted and observed rates over equal-count bins, weighted by bin size."""
    order = np.argsort(p, kind="stable")
    gaps = [abs(p[idx].mean() - y[idx].mean()) * len(idx) for idx in np.array_split(order, bins) if len(idx)]
    return float(sum(gaps) / len(y))


def _top_decile(fold: pd.DataFrame, column: str) -> pd.DataFrame:
    cut = fold[column].quantile(0.9)
    return fold[fold[column] >= cut]


def metrics(predictions: pd.DataFrame, column: str) -> dict[str, float]:
    """Discrimination, calibration and the outcome of the top-scored decile (deciles within each test year)."""
    y = predictions["label"].to_numpy()
    p = predictions[column].to_numpy().clip(1e-6, 1 - 1e-6)
    top = pd.concat([_top_decile(fold, column) for _, fold in predictions.groupby("fold")])
    out = {
        "n": len(y),
        "base_rate": float(y.mean()),
        "auc": float(roc_auc_score(y, p)) if len(set(y)) > 1 else float("nan"),
        "brier": float(brier_score_loss(y, p)),
        "log_loss": float(log_loss(y, p, labels=[0, 1])),
        "ece": expected_calibration_error(y, p),
        "top_decile_hit_rate": float(top["label"].mean()),
        "top_decile_mean_outcome": float(top["outcome"].mean()),
        "mean_outcome": float(predictions["outcome"].mean()),
    }
    for name, subset in (("buys", predictions["is_buy"] == 1), ("sells", predictions["is_buy"] == 0)):
        ys = y[subset.to_numpy()]
        out[f"auc_{name}"] = float(roc_auc_score(ys, p[subset.to_numpy()])) if len(set(ys)) > 1 else float("nan")
    return out


def summarize(predictions: pd.DataFrame) -> dict[str, Any]:
    """Pooled and per-fold metrics for every candidate; Brier skill is relative to the ``prior`` baseline."""
    names = [c.removeprefix("p_") for c in predictions.columns if c.startswith("p_")]
    pooled = {name: metrics(predictions, f"p_{name}") for name in names}
    by_fold: dict[str, dict[int, dict[str, float]]] = {name: {} for name in names}
    for year, fold in predictions.groupby("fold"):
        for name in names:
            by_fold[name][int(year)] = metrics(fold, f"p_{name}")
    if "prior" in pooled:
        for name in names:
            pooled[name]["brier_skill"] = 1 - pooled[name]["brier"] / pooled["prior"]["brier"]
            for year, fold_metrics in by_fold[name].items():
                fold_metrics["brier_skill"] = 1 - fold_metrics["brier"] / by_fold["prior"][year]["brier"]
    return {"pooled": pooled, "by_fold": by_fold, "folds": sorted(int(y) for y in predictions["fold"].unique())}


def calibration_table(predictions: pd.DataFrame, column: str, bins: int = 10) -> pd.DataFrame:
    """Predicted vs observed rate per equal-count bin of scores (for the reliability diagram)."""
    ranked = predictions[column].rank(method="first")
    bucket = pd.qcut(ranked, bins, labels=False)
    table = predictions.groupby(bucket).agg(predicted=(column, "mean"), observed=("label", "mean"),
                                            mean_outcome=("outcome", "mean"), n=("label", "size"))
    return table.rename_axis("bin").reset_index()
