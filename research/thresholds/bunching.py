"""Bunching estimators (Saez, 2010; Chetty et al., 2011; Kleven, 2016) with cluster bootstrap.

Two building blocks:

- ``excess_mass``: the classic estimator on a binned distribution. A flexible polynomial is fitted to the
  bin counts outside an excluded window next to the threshold and extrapolated into it; the excess mass is
  the actual minus the counterfactual count in the window, normalised by the average counterfactual count per
  window bin (Kleven's ``b``). We fit the polynomial to log counts, because the distributions here (distance
  from a threshold, annual amounts) decay roughly geometrically and counts must stay positive.
- ``difference_in_bunching``: the same estimate at a "treated" threshold minus a counterfactual built from
  "control" thresholds where the incentive is absent (here, the 0.1% steps above 0.5% where disclosure is
  already public), extrapolated with a polynomial in the threshold level. This removes the bunching that the
  reporting mechanics create at every threshold.

Standard errors come from a bootstrap that resamples clusters (position holders, issuers) with replacement,
so the dependence between notifications of one holder is respected.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np


def excess_mass(counts: np.ndarray, window: int = 1, degree: int = 2, side: str = "left") -> dict[str, float]:
    """Kleven-style excess mass in the first ``window`` bins (``side='left'``) of ``counts``.

    ``counts[k]`` is the number of observations at distance k bins from the threshold. The counterfactual is a
    polynomial of ``degree`` in k fitted to log(counts + 0.5) on bins k >= window and extrapolated to k < window.
    Returns the excess mass ``B`` (in observations), the normalised ``b`` (B over the mean counterfactual count
    per window bin) and the share of observations in the window.
    """
    counts = np.asarray(counts, dtype=float)
    if side == "right":
        counts = counts[::-1]
    k = np.arange(len(counts))
    fit = k >= window
    if counts.sum() == 0 or fit.sum() <= degree:
        return {"B": np.nan, "b": np.nan, "share": np.nan, "n": float(counts.sum())}
    coef = np.polyfit(k[fit], np.log(counts[fit] + 0.5), degree)
    cf = np.exp(np.polyval(coef, k)) - 0.5
    in_window = ~fit
    big_b = float((counts[in_window] - cf[in_window]).sum())
    share = float(counts[in_window].sum() / counts.sum())
    return {"B": big_b, "b": big_b / float(cf[in_window].mean()), "share": share, "n": float(counts.sum())}


def difference_in_bunching(
    levels: np.ndarray,
    counts: np.ndarray,
    treated: float,
    *,
    window: int = 1,
    degree: int = 2,
    trend_degree: int = 1,
    stat: str = "b",
    min_n: int = 30,
) -> dict[str, float]:
    """Bunching at the ``treated`` threshold minus its extrapolation from the other thresholds.

    ``levels`` (T,) are threshold levels and ``counts`` (T, K) the binned distances from each threshold. The
    statistic (``b`` or ``share``) is computed at every threshold with at least ``min_n`` observations; a
    polynomial of ``trend_degree`` in the level, fitted on the control thresholds and weighted by their
    observation counts, gives the counterfactual at the treated level.
    """
    levels = np.asarray(levels, dtype=float)
    stats = np.array([excess_mass(c, window, degree)[stat] for c in counts])
    n = counts.sum(axis=1)
    is_treated = np.isclose(levels, treated)
    control = ~is_treated & (n >= min_n) & np.isfinite(stats)
    actual = float(stats[is_treated][0]) if is_treated.any() else np.nan
    if control.sum() <= trend_degree or not np.isfinite(actual):
        return {"actual": actual, "counterfactual": np.nan, "difference": np.nan}
    coef = np.polyfit(levels[control], stats[control], trend_degree, w=np.sqrt(n[control]))
    counterfactual = float(np.polyval(coef, treated))
    return {"actual": actual, "counterfactual": counterfactual, "difference": actual - counterfactual}


def cluster_bootstrap(
    cluster_counts: np.ndarray, estimator: Callable[[np.ndarray], dict[str, float]], reps: int = 999, seed: int = 7
) -> dict[str, np.ndarray]:
    """Re-run ``estimator`` on counts summed over clusters drawn with replacement.

    ``cluster_counts`` has shape (clusters, ...); the estimator receives the summed array (...). Returns the
    bootstrap draws of every statistic the estimator returns.
    """
    rng = np.random.default_rng(seed)
    n_clusters = cluster_counts.shape[0]
    flat = cluster_counts.reshape(n_clusters, -1)
    draws: dict[str, list[float]] = {}
    for _ in range(reps):
        weights = rng.multinomial(n_clusters, np.full(n_clusters, 1 / n_clusters))
        result = estimator((weights @ flat).reshape(cluster_counts.shape[1:]))
        for key, value in result.items():
            draws.setdefault(key, []).append(value)
    return {key: np.asarray(values, dtype=float) for key, values in draws.items()}


def summarize(point: float, draws: np.ndarray) -> dict[str, float]:
    """Point estimate with bootstrap standard error, 95% percentile interval and two-sided p-value for zero."""
    draws = draws[np.isfinite(draws)]
    if len(draws) < 20 or not np.isfinite(point):
        return {"estimate": point, "se": np.nan, "ci_low": np.nan, "ci_high": np.nan, "p": np.nan}
    # p-value from the bootstrap distribution centred on the estimate (null: zero).
    centred = draws - draws.mean()
    p = float(min(1.0, (np.abs(centred) >= abs(point)).mean() + 1 / len(draws)))
    low, high = np.percentile(draws, [2.5, 97.5])
    return {"estimate": point, "se": float(draws.std(ddof=1)), "ci_low": float(low), "ci_high": float(high), "p": p}
