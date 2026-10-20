"""Feature drift: population stability index (PSI) of each feature against its training distribution.

The training profile stores, per feature, decile edges, the share of present values in each bin and the share of
missing values. For a new batch, over the present values,

    PSI = sum over bins of (actual - expected) * ln(actual / expected)

with shares floored at 1e-4. Conventional reading: below 0.1 stable, 0.1 to 0.25 moderate shift, above 0.25 a
significant shift that calls for a look and probably retraining. Missing values are reported apart, next to
their training share: training events all have prices (labels need them), while a scored event whose share is not
in the price cache yet has none, and folding that gap into the PSI would flag every batch.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .features import FEATURES

MODERATE, SIGNIFICANT = 0.1, 0.25
FLOOR = 1e-4
MIN_ROWS = 50
"""Below this many scored events the PSI is too noisy to report."""


def profile(frame: pd.DataFrame, bins: int = 10) -> dict[str, Any]:
    """Bin edges and shares of each feature (JSON-serializable, stored with the model)."""
    features = {}
    for name in FEATURES:
        values = frame[name].to_numpy(dtype=float)
        present = values[~np.isnan(values)]
        edges = np.unique(np.quantile(present, np.linspace(0, 1, bins + 1)[1:-1])) if len(present) else np.array([])
        features[name] = {"edges": edges.tolist(), "shares": _shares(values, edges).tolist(),
                          "missing_share": float(np.isnan(values).mean()) if len(values) else 0.0}
    return {"rows": len(frame), "features": features}


def _shares(values: np.ndarray, edges: np.ndarray) -> np.ndarray:
    present = values[~np.isnan(values)]
    counts = np.bincount(np.searchsorted(edges, present, side="right"), minlength=len(edges) + 1)
    return counts / max(len(present), 1)


def psi(expected: np.ndarray, actual: np.ndarray) -> float:
    e, a = np.maximum(expected, FLOOR), np.maximum(actual, FLOOR)
    return float(np.sum((a - e) * np.log(a / e)))


def status(value: float | None) -> str:
    if value is None:
        return "insufficient_data"
    return "significant" if value > SIGNIFICANT else "moderate" if value > MODERATE else "stable"


def report(reference: dict[str, Any], frame: pd.DataFrame) -> dict[str, Any]:
    """PSI and status per feature for a batch, plus the worst status overall."""
    features = {}
    enough = len(frame) >= MIN_ROWS
    for name, ref in reference["features"].items():
        values = frame[name].to_numpy(dtype=float)
        present = int((~np.isnan(values)).sum())
        value = None
        if present >= MIN_ROWS:
            value = psi(np.asarray(ref["shares"]), _shares(values, np.asarray(ref["edges"])))
        features[name] = {"psi": None if value is None else round(value, 4), "status": status(value),
                          "missing_share": round(float(np.isnan(values).mean()), 4) if len(values) else None,
                          "reference_missing_share": round(ref.get("missing_share", 0.0), 4)}
    order = ["insufficient_data", "stable", "moderate", "significant"]
    worst = max((f["status"] for f in features.values()), key=order.index) if enough else "insufficient_data"
    return {"rows": len(frame), "reference_rows": reference["rows"], "status": worst, "features": features}
