"""Train, validate and register the insider-signal model on the Swedish sample of WS6 (P8).

One command, from the repository root, after the WS6 study has run (it leaves its event table and prices in
research/insider_event_study/cache/):

    python research/signals/train.py --lake lake

--lake PATH      lake root (default: ./lake): gold trades, and where MLflow keeps its store (lake/ml/)
--ws6-cache DIR  WS6 cache with events.parquet and prices.parquet (default: research/insider_event_study/cache)
--no-register    validate only; do not log to MLflow or register a model

Features come from gold through disclosure_atlas.ml (the same code that scores new events), labels from the
WS6 outcomes. Writes aggregated metrics to results/ and figures to figures/ next to this file; event-level
predictions stay in cache/ (git-ignored). Research output, not investment advice.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "research" / "insider_event_study"))

from events import load_trades  # noqa: E402

from disclosure_atlas.ml import drift, labels, model, validation  # noqa: E402
from disclosure_atlas.ml.features import FEATURES, aggregate_events, build_features  # noqa: E402

RESULTS, FIGURES, CACHE = HERE / "results", HERE / "figures", HERE / "cache"


def log(message: str) -> None:
    print(message, flush=True)


def build_dataset(lake: Path, ws6_cache: Path) -> pd.DataFrame:
    """Gold trades (WS6 selection) -> events and features -> WS6 outcomes as labels."""
    trades = load_trades(lake)  # WS6's selection: open-market share lines, plus untyped pre-2018 lines
    trades = trades.assign(
        jurisdiction="SE",
        published_at=trades["published_local"].dt.tz_localize("Europe/Stockholm", ambiguous="NaT",
                                                               nonexistent="shift_forward").dt.tz_convert("UTC"),
    ).dropna(subset=["published_at"])
    events = aggregate_events(trades)
    outcomes = pd.read_parquet(ws6_cache / "events.parquet")
    # Prices: WS6's Yahoo cache, keyed by ticker; unadjusted closes, as the lake's price cache serves them.
    tickers = outcomes.dropna(subset=["ticker"]).drop_duplicates("isin")[["isin", "ticker"]]
    bars = pd.read_parquet(ws6_cache / "prices.parquet", columns=["ticker", "date", "raw_close", "volume"])
    prices = bars.merge(tickers, on="ticker").rename(columns={"raw_close": "close"})
    data = labels.attach_outcomes(build_features(events, prices), outcomes)
    log(f"dataset: {len(trades)} lines -> {len(events)} events -> {len(data)} labeled "
        f"({data['publication_date'].min().date()} .. {data['publication_date'].max().date()})")
    return data


def importance(data: pd.DataFrame, year: int) -> pd.DataFrame:
    """Permutation importance (drop in AUC) of the model trained for the last fold, on that fold's events."""
    from sklearn.inspection import permutation_importance

    train = data[data["known_at"] < pd.Timestamp(year=year, month=1, day=1)]
    test = data[data["publication_date"].dt.year == year]
    fitted = model.fit_gradient_boosting(train[FEATURES], train["label"].to_numpy())
    result = permutation_importance(fitted, test[FEATURES], test["label"].to_numpy(), scoring="roc_auc",
                                    n_repeats=10, random_state=0)
    return (pd.DataFrame({"feature": FEATURES, "auc_drop": result.importances_mean, "std": result.importances_std})
            .sort_values("auc_drop", ascending=False))


def bootstrap_auc(predictions: pd.DataFrame, column: str, n: int = 1000, seed: int = 0) -> tuple[float, float]:
    """95% interval of the pooled AUC, resampling test years' events by publication month (events of one month
    share market conditions, so an event-level bootstrap would be too narrow)."""
    from sklearn.metrics import roc_auc_score

    rng = np.random.default_rng(seed)
    months = predictions["publication_date"].dt.to_period("M")
    groups = [g.index.to_numpy() for _, g in predictions.groupby(months)]
    draws = []
    for _ in range(n):
        idx = np.concatenate([groups[i] for i in rng.integers(0, len(groups), len(groups))])
        draws.append(roc_auc_score(predictions.loc[idx, "label"], predictions.loc[idx, column]))
    return float(np.quantile(draws, 0.025)), float(np.quantile(draws, 0.975))


def figures(predictions: pd.DataFrame, summary: dict) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    FIGURES.mkdir(exist_ok=True)
    fig, (left, right) = plt.subplots(1, 2, figsize=(10, 4))
    for name, style in (("gradient_boosting", "o-"), ("logistic", "s--"), ("direction_prior", "^:")):
        if name != "direction_prior":  # two distinct values per year: deciles of it are meaningless
            table = validation.calibration_table(predictions, f"p_{name}")
            left.plot(table["predicted"], table["observed"], style, label=name.replace("_", " "), ms=4)
        folds = summary["by_fold"][name]
        right.plot(list(folds), [folds[y]["auc"] for y in folds], style, label=name.replace("_", " "), ms=4)
    lo, hi = left.get_xlim()
    left.plot([lo, hi], [lo, hi], color="grey", lw=0.8)
    left.set(title="Reliability (out of sample, deciles)", xlabel="Predicted P(CAR > 0)", ylabel="Observed share")
    right.axhline(0.5, color="grey", lw=0.8)
    right.set(title="AUC by test year (walk-forward)", xlabel="Test year", ylabel="ROC AUC")
    for ax in (left, right):
        ax.legend(fontsize=8)
        ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(FIGURES / "walk_forward.png", dpi=130)
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lake", type=Path, default=Path("lake"))
    parser.add_argument("--ws6-cache", type=Path, default=ROOT / "research" / "insider_event_study" / "cache")
    parser.add_argument("--no-register", action="store_true")
    args = parser.parse_args(argv)

    data = build_dataset(args.lake, args.ws6_cache)
    predictions = validation.walk_forward(data, first_test_year=2019)
    summary = validation.summarize(predictions)
    for name, values in summary["pooled"].items():
        values["auc_ci_low"], values["auc_ci_high"] = bootstrap_auc(predictions, f"p_{name}")
        log(f"{name:>18}: AUC {values['auc']:.4f} [{values['auc_ci_low']:.3f}, {values['auc_ci_high']:.3f}]  "
            f"Brier {values['brier']:.4f}  skill {values['brier_skill']:+.4f}  ECE {values['ece']:.4f}  "
            f"top-decile CAR {values['top_decile_mean_outcome']:+.4f}")

    RESULTS.mkdir(exist_ok=True)
    CACHE.mkdir(exist_ok=True)
    rows = [{"candidate": name, "fold": year, **values}
            for name, folds in summary["by_fold"].items() for year, values in folds.items()]
    pd.DataFrame(rows).round(5).to_csv(RESULTS / "walk_forward_by_year.csv", index=False)
    pooled = pd.DataFrame(summary["pooled"]).T.rename_axis("candidate").reset_index()
    pooled.round(5).to_csv(RESULTS / "walk_forward_pooled.csv", index=False)
    validation.calibration_table(predictions, "p_gradient_boosting").round(5).to_csv(RESULTS / "calibration.csv",
                                                                                     index=False)
    ranked = importance(data, summary["folds"][-1])
    ranked.round(5).to_csv(RESULTS / "permutation_importance.csv", index=False)
    log(ranked.to_string(index=False))
    figures(predictions, summary)
    predictions.to_parquet(CACHE / "walk_forward_predictions.parquet", index=False)

    # Final model on every labeled event, then a drift check of the last fold against the earlier years.
    final = model.fit_gradient_boosting(data[FEATURES], data["label"].to_numpy())
    reference = drift.profile(data[FEATURES])
    last = summary["folds"][-1]
    shift = drift.report(drift.profile(data[data["publication_date"].dt.year < last]),
                         data[data["publication_date"].dt.year == last])
    coverage = {
        "labeled_events": len(data),
        "buys": int(data["is_buy"].sum()),
        "sells": int((1 - data["is_buy"]).sum()),
        "first_publication": str(data["publication_date"].min().date()),
        "last_publication": str(data["publication_date"].max().date()),
        "base_rate": round(float(data["label"].mean()), 4),
        "missing_share": data[FEATURES].isna().mean().round(4).to_dict(),
        "folds": summary["folds"],
        "drift_last_fold_vs_history": shift,
    }
    (RESULTS / "dataset.json").write_text(json.dumps(coverage, indent=2) + "\n")
    if not args.no_register:
        from disclosure_atlas.ml.registry import log_training

        registered = log_training(
            args.lake, final, data, summary, reference,
            params={"label": labels.OUTCOME, "embargo_days": labels.EMBARGO_DAYS,
                    "calibration_share": model.CALIBRATION_SHARE, **{f"gbm.{k}": v for k, v in model.PARAMS.items()}},
            tags={"sample": "se_fi_pdmr", "validation": "walk-forward, yearly, expanding"},
            artifacts={"walk_forward": FIGURES / "walk_forward.png", "pooled": RESULTS / "walk_forward_pooled.csv"},
        )
        log(f"registered {registered}")
    log(f"results -> {RESULTS}")
    return 0


if __name__ == "__main__":
    np.seterr(all="ignore")
    sys.exit(main())
