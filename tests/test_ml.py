"""Insider-signal model on synthetic data: features without look-ahead, walk-forward, drift, scoring, API."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

pd = pytest.importorskip("pandas")
np = pytest.importorskip("numpy")
pytest.importorskip("sklearn")

from fastapi.testclient import TestClient  # noqa: E402
from test_api import lake  # noqa: E402, F401 - shared synthetic lake fixture

from disclosure_atlas.api import create_app  # noqa: E402
from disclosure_atlas.ml import DISCLAIMER, drift, labels, model, scoring, validation  # noqa: E402
from disclosure_atlas.ml.features import FEATURES, aggregate_events, price_features  # noqa: E402
from disclosure_atlas.reference.prices import PriceStore, SyntheticProvider  # noqa: E402


def _trades(**overrides) -> pd.DataFrame:
    row = {"isin": "SE0000000001", "issuer_name": "Example AB", "issuer_lei": None, "jurisdiction": "SE",
           "nature": "buy", "pdmr_id": "p_a", "pdmr_role": "board_member", "transaction_date": "2024-03-01",
           "published_at": "2024-03-04T09:00:00Z", "amount_eur": 10_000.0}
    rows = overrides.pop("rows", [{}])
    frame = pd.DataFrame([row | r for r in rows])
    frame["published_at"] = pd.to_datetime(frame["published_at"], utc=True)
    return frame


def test_events_aggregate_lines_by_local_publication_day():
    trades = _trades(rows=[
        {},
        {"pdmr_id": "p_b", "pdmr_role": "ceo", "amount_eur": 5_000.0, "transaction_date": "2024-02-27"},
        # 23:30 UTC on 4 March is already 5 March in Stockholm: a separate event.
        {"published_at": "2024-03-04T23:30:00Z"},
        {"nature": "sell"},
    ])
    events = aggregate_events(trades).set_index("event_id")
    first = events.loc["SE0000000001:buy:2024-03-04"]
    assert first["n_insiders"] == 2 and first["n_lines"] == 2
    assert first["role_seniority"] == 0  # the CEO outranks the board member
    assert first["filing_lag_days"] == 6 and first["amount_eur"] == 15_000.0
    assert np.isclose(first["log_amount_eur"], np.log10(15_001))
    assert set(events.index) == {"SE0000000001:buy:2024-03-04", "SE0000000001:buy:2024-03-05",
                                 "SE0000000001:sell:2024-03-04"}
    assert events.loc["SE0000000001:sell:2024-03-04", "is_buy"] == 0


def test_cluster_counts_only_filings_already_public():
    trades = _trades(rows=[
        {"pdmr_id": "p_a", "published_at": "2024-03-01T09:00:00Z"},
        {"pdmr_id": "p_b", "published_at": "2024-03-10T09:00:00Z"},
        {"pdmr_id": "p_c", "published_at": "2024-03-20T09:00:00Z"},
        {"pdmr_id": "p_d", "published_at": "2024-05-01T09:00:00Z"},  # outside the 30-day window
    ])
    clusters = aggregate_events(trades).set_index("publication_date")["cluster_insiders_30d"]
    assert clusters.tolist() == [1, 2, 3, 1]


def test_price_features_use_only_sessions_before_publication():
    dates = pd.bdate_range("2024-01-01", periods=80)
    close = pd.Series(100.0, index=dates)
    close.iloc[-1] = 1_000.0  # a jump on the publication day must not leak into the features
    prices = pd.DataFrame({"isin": "SE0000000001", "date": dates, "close": close.to_numpy(), "volume": 10.0})
    prices.loc[58, "close"] = 80.0  # 20 sessions before the last pre-publication session
    events = pd.DataFrame({"isin": ["SE0000000001", "SE0000000002"],
                           "publication_date": [dates[-1], dates[-1]]})
    out = price_features(events, prices)
    assert np.isclose(out.loc[0, "momentum_20d"], 100 / 80 - 1)
    assert np.isclose(out.loc[0, "log_traded_value_60d"], np.log10(1 + 1_000))
    assert out.loc[1].isna().all()  # no prices for that share


def _synthetic(n_per_year: int = 900, years=range(2016, 2025), seed: int = 0) -> pd.DataFrame:
    """Events with a planted signal: CEO and CFO buys in small firms tend to be followed by positive returns."""
    rng = np.random.default_rng(seed)
    frames = []
    for year in years:
        dates = pd.Timestamp(year=year, month=1, day=1) + pd.to_timedelta(rng.integers(0, 365, n_per_year), "D")
        f = pd.DataFrame({
            "publication_date": dates.astype("datetime64[us]"),
            "is_buy": rng.integers(0, 2, n_per_year), "role_seniority": rng.integers(0, 6, n_per_year),
            "log_amount_eur": rng.normal(4.5, 1, n_per_year), "n_insiders": rng.integers(1, 3, n_per_year),
            "cluster_insiders_30d": rng.integers(1, 4, n_per_year), "filing_lag_days": rng.integers(0, 4, n_per_year),
            "log_traded_value_60d": rng.normal(6, 1.2, n_per_year), "momentum_20d": rng.normal(0, 0.1, n_per_year),
        })
        logit = 1.2 * f["is_buy"] * (f["role_seniority"] <= 1) - 0.4 * (f["log_traded_value_60d"] - 6) - 0.3
        f["outcome"] = rng.normal(0.02 * logit, 0.05)
        f["label"] = (rng.random(n_per_year) < 1 / (1 + np.exp(-logit))).astype(int)
        frames.append(f)
    data = pd.concat(frames, ignore_index=True)
    data["isin"] = "SE0000000001"
    data["event_id"] = [f"e{i:05d}" for i in range(len(data))]
    data["known_at"] = data["publication_date"] + pd.Timedelta(days=labels.EMBARGO_DAYS)
    return data.sort_values("publication_date").reset_index(drop=True)


def test_labels_join_ws6_outcomes_and_set_the_embargo():
    events = aggregate_events(_trades(rows=[{}, {"nature": "sell"}, {"published_at": "2024-04-02T09:00:00Z"}]))
    outcomes = pd.DataFrame({"isin": "SE0000000001", "nature": ["buy", "sell", "buy"],
                             "pub_date": pd.to_datetime(["2024-03-04", "2024-03-04", "2024-04-02"]),
                             labels.OUTCOME: [0.03, -0.01, np.nan]})
    data = labels.attach_outcomes(events, outcomes)
    assert data["label"].tolist() == [1, 0]  # the event without an outcome is dropped
    assert (data["known_at"] - data["publication_date"]).dt.days.eq(labels.EMBARGO_DAYS).all()


def test_walk_forward_trains_only_on_outcomes_known_before_each_fold():
    data = _synthetic()
    seen = []

    def spy(X, y):
        seen.append(len(X))
        return model.CANDIDATES["prior"](X, y)

    predictions = validation.walk_forward(data, {"prior": spy}, min_train=500)
    for year, fold in predictions.groupby("fold"):
        start = pd.Timestamp(year=year, month=1, day=1)
        assert (fold["train_until"] < start - pd.Timedelta(days=labels.EMBARGO_DAYS - 1)).all()
        assert (fold["publication_date"].dt.year == year).all()
    assert seen == sorted(seen) and len(seen) == predictions["fold"].nunique()


def test_model_learns_a_planted_signal_and_beats_the_baselines():
    predictions = validation.walk_forward(_synthetic(), min_train=500)
    summary = validation.summarize(predictions)
    pooled = summary["pooled"]
    assert pooled["gradient_boosting"]["auc"] > 0.62
    assert pooled["gradient_boosting"]["auc"] > pooled["direction_prior"]["auc"] + 0.05
    assert pooled["gradient_boosting"]["brier_skill"] > 0.03
    assert pooled["gradient_boosting"]["ece"] < 0.05  # calibrated on later data than the trees saw
    assert pooled["prior"]["brier_skill"] == 0
    assert set(summary["by_fold"]["logistic"]) == set(summary["folds"])
    table = validation.calibration_table(predictions, "p_gradient_boosting")
    assert len(table) == 10 and table["n"].sum() == len(predictions)


def test_drift_is_stable_on_the_same_distribution_and_flags_a_shift():
    data = _synthetic()
    reference = drift.profile(data[data["publication_date"].dt.year < 2022])
    same = drift.report(reference, data[data["publication_date"].dt.year >= 2022])
    assert same["status"] == "stable"
    shifted = data.assign(log_traded_value_60d=data["log_traded_value_60d"] + 1.5, momentum_20d=np.nan)
    report = drift.report(reference, shifted)
    assert report["features"]["log_traded_value_60d"]["status"] == "significant"
    assert report["features"]["momentum_20d"] == {"psi": None, "status": "insufficient_data", "missing_share": 1.0,
                                                  "reference_missing_share": 0.0}
    assert report["status"] == "significant"
    assert drift.report(reference, data.head(10))["status"] == "insufficient_data"
    json.dumps(reference)  # stored with the model as JSON


@pytest.fixture
def scored_lake(lake):  # noqa: F811
    """The API's synthetic lake, scored by a model trained on synthetic events, with cached synthetic prices."""
    isin = lake.connect().execute("SELECT any_value(isin) FROM insider_transactions").fetchone()[0]
    PriceStore(lake, SyntheticProvider()).get(isin, datetime.now(UTC).date() - timedelta(days=200))
    data = _synthetic()
    fitted = model.fit_gradient_boosting(data[FEATURES], data["label"].to_numpy())
    info = {"name": "insider-signal", "version": "7", "run_id": "synthetic", "trained_through": "2024-12-31",
            "metrics": {"auc": 0.7}, "baseline_auc": 0.6}
    meta = scoring.score(lake, champion=(fitted, info, drift.profile(data[FEATURES])))
    return lake, meta


def test_scoring_writes_scores_and_metadata(scored_lake):
    store, meta = scored_lake
    assert meta["status"] == "ok" and meta["rows"] == 2  # one open-market buy and one sale
    assert meta["drift"]["status"] == "insufficient_data" and meta["disclaimer"] == DISCLAIMER
    frame = pd.read_parquet(store.root / scoring.SIGNALS)
    assert frame["score"].between(0, 1).all() and frame["momentum_20d"].notna().all()
    assert "pdmr_id" not in frame.columns  # events carry no person identifiers


def test_signals_endpoint_returns_scores_with_disclaimer(scored_lake):
    store, _ = scored_lake
    client = TestClient(create_app(str(store.root)))
    body = client.get("/v1/signals").json()
    assert body["disclaimer"] == DISCLAIMER and body["model"]["version"] == "7"
    assert len(body["items"]) == 2 and {i["nature"] for i in body["items"]} == {"buy", "sell"}
    assert set(body["items"][0]["features"]) == set(FEATURES)
    assert [i["nature"] for i in client.get("/v1/signals?nature=sell").json()["items"]] == ["sell"]
    assert client.get("/v1/signals?min_score=1").json()["items"] == []
    assert client.get("/v1/signals?country=FR").json()["items"] == []
    assert client.get("/v1/signals?min_score=2").status_code == 422


def test_signals_endpoint_without_a_model(tmp_path):
    body = TestClient(create_app(str(tmp_path / "lake"))).get("/v1/signals").json()
    assert body == {"disclaimer": DISCLAIMER, "model": None, "scored_at": None, "snapshot": None, "drift": None,
                    "items": []}


def test_mlflow_registry_round_trip(tmp_path, monkeypatch):
    pytest.importorskip("mlflow")
    pytest.importorskip("skops")
    from disclosure_atlas.ml import registry

    monkeypatch.delenv("ATLAS_MLFLOW_TRACKING_URI", raising=False)
    data = _synthetic(n_per_year=300, years=range(2018, 2023))
    assert registry.load_champion(tmp_path) is None
    summary = validation.summarize(validation.walk_forward(data, min_train=300))
    fitted = model.fit_gradient_boosting(data[FEATURES], data["label"].to_numpy())
    registered = registry.log_training(tmp_path, fitted, data, summary, drift.profile(data[FEATURES]),
                                       params={"label": labels.OUTCOME})
    assert registered["version"] == "1" and (tmp_path / "ml" / "mlflow.db").exists()
    loaded, info, reference = registry.load_champion(tmp_path)
    assert info["version"] == "1" and info["trained_through"] == registered["trained_through"]
    assert 0 < info["metrics"]["auc"] <= 1 and set(reference["features"]) == set(FEATURES)
    sample = data[FEATURES].tail(20)
    assert np.allclose(loaded.predict_proba(sample), fitted.predict_proba(sample))


def test_dagster_asset_scores_nothing_without_a_champion(tmp_path, monkeypatch):
    pytest.importorskip("mlflow")
    dg = pytest.importorskip("dagster")
    from disclosure_atlas.orchestration.definitions import LakeResource
    from disclosure_atlas.orchestration.signals import signal_definitions

    monkeypatch.delenv("ATLAS_MLFLOW_TRACKING_URI", raising=False)
    defs = dg.Definitions.merge(signal_definitions(LakeResource),
                                dg.Definitions(resources={"lake": LakeResource(root=str(tmp_path / "lake"))}))
    result = defs.resolve_job_def("insider_signals_job").execute_in_process(instance=dg.DagsterInstance.ephemeral())
    assert result.success
    (event,) = result.get_asset_materialization_events()
    assert event.materialization.metadata["status"].value == "no_model"
    (check,) = result.get_asset_check_evaluations()
    assert check.check_name == "feature_drift" and check.passed
