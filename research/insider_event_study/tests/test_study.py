"""Offline tests on synthetic data (no network, no real names)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import eventstudy as es
from events import aggregate_events
from prices import _tidy
from tickers import figi_candidates


def test_figi_candidates_share_class():
    assert figi_candidates("VOLVB", "VOLVO AB-B SHS")[0] == "VOLV-B.ST"
    assert figi_candidates("ABC", "ABC AB")[-1] == "ABC.ST"


def test_tidy_undoes_split_adjustment():
    idx = pd.to_datetime(["2020-01-02", "2020-01-03", "2020-01-06"])
    raw = pd.DataFrame({"Adj Close": [50.0, 51.0, 52.0], "Close": [50.0, 51.0, 52.0],
                        "Volume": [1, 1, 1], "Stock Splits": [0.0, 0.0, 2.0]}, index=idx)
    out = _tidy(raw, "X.ST")
    assert out["raw_close"].tolist() == [100.0, 102.0, 52.0]


def test_day_zero_respects_close():
    dates = pd.bdate_range("2021-03-01", periods=10)
    published = pd.Series(pd.to_datetime(["2021-03-01 09:00", "2021-03-01 18:00", "2021-03-06 10:00"]))
    assert es.day_zero(published, dates).tolist() == [0, 1, 5]  # Saturday -> Monday


def _panel(n_days=400, n_stocks=2, seed=0):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2018-01-01", periods=n_days)
    market = rng.normal(0, 0.01, n_days)
    stocks = 0.0001 + 1.2 * market[:, None] + rng.normal(0, 0.01, (n_days, n_stocks))
    return es.Panel(dates=dates, tickers=[f"S{j}.ST" for j in range(n_stocks)], stocks=stocks, market=market,
                    traded_value=np.ones((n_days, n_stocks)), has_price=np.ones((n_days, n_stocks), bool))


def test_market_model_recovers_beta():
    p = _panel()
    alpha, beta, sigma, n = es.market_model(p.stocks[:, 0], p.market)
    assert beta == pytest.approx(1.2, abs=0.1)
    assert sigma == pytest.approx(0.01, abs=0.002)


def test_abnormal_returns_detects_planted_jump():
    p = _panel()
    p.stocks[300, 0] += 0.10  # +10% abnormal on day 0
    events = pd.DataFrame({"ticker": ["S0.ST", "S1.ST", "MISSING.ST"], "day0": [300, 300, 300]})
    cars, paths = es.abnormal_returns(events, p)
    assert cars.loc[0, "[0,+5]"] == pytest.approx(0.10, abs=0.05)
    assert abs(cars.loc[1, "[0,+5]"]) < 0.06
    assert np.isnan(cars.loc[2, "[0,+5]"])
    assert paths.shape == (3, es.PATH[1] - es.PATH[0] + 1)


def test_windows_past_end_of_data_are_missing():
    p = _panel()
    cars, _ = es.abnormal_returns(pd.DataFrame({"ticker": ["S0.ST"], "day0": [380]}), p)
    assert np.isfinite(cars.loc[0, "[0,+5]"]) and np.isnan(cars.loc[0, "[0,+60]"])


def test_flag_clusters_counts_distinct_insiders_in_trailing_window():
    events = pd.DataFrame({
        "isin": ["SE1"] * 4, "nature": ["buy"] * 4, "day0": [10, 12, 30, 31],
        "pdmr_ids": [["a"], ["b"], ["c"], ["c"]],
    })
    assert es.flag_clusters(events).tolist() == [False, True, False, False]


def test_benjamini_hochberg():
    q = es.benjamini_hochberg(pd.Series([0.01, 0.04, 0.03, np.nan]))
    assert q.round(4).tolist()[:3] == [0.03, 0.04, 0.04] and np.isnan(q.iloc[3])


def test_summarize_t_statistics():
    car = pd.Series([0.01, 0.02, 0.03, 0.04])
    out = es.summarize(car, pd.Series([0.01] * 4), 21)
    assert out["n"] == 4 and out["mean"] == pytest.approx(0.025) and out["t"] > 3


def test_calendar_time_holds_stocks_for_horizon():
    p = _panel()
    frame = es.calendar_time(pd.DataFrame({"ticker": ["S0.ST", "S1.ST"], "day0": [100, 105]}), p, horizon=10)
    later = es.calendar_time(pd.DataFrame({"ticker": ["S0.ST"], "day0": [100]}), p, horizon=10, entry=1)
    assert later.index[0] == p.dates[101] and len(later) == 10
    assert frame.index[0] == p.dates[100] and len(frame) == 15 and frame["n"].max() == 2


def test_aggregate_events_one_row_per_issuer_day_direction():
    trades = pd.DataFrame({
        "record_id": ["r1", "r2", "r3"], "isin": ["SE1", "SE1", "SE1"], "issuer_name": ["Issuer AB"] * 3,
        "nature": ["buy", "buy", "sell"], "inferred_share": [False] * 3, "pdmr_id": ["p1", "p2", "p1"],
        "role_group": ["Other", "CEO/CFO", "Other"], "venue": ["NASDAQ STOCKHOLM AB"] * 3,
        "published_local": pd.to_datetime(["2022-05-02 08:00", "2022-05-02 15:00", "2022-05-02 15:00"]),
        "transaction_date": pd.to_datetime(["2022-04-29"] * 3),
        "volume": [100.0, 300.0, 50.0], "price": [10.0, 20.0, 15.0],
        "amount_eur": [1_000.0, 30_000.0, np.nan], "filing_lag_days": [3, 3, 3],
    })
    ev = aggregate_events(trades)
    buy = ev[ev["nature"] == "buy"].iloc[0]
    assert len(ev) == 2 and buy["n_insiders"] == 2 and buy["role_group"] == "CEO/CFO"
    assert buy["vwap"] == pytest.approx(17.5) and buy["size_bucket"] == "EUR 25k-100k"
    assert np.isnan(ev[ev["nature"] == "sell"].iloc[0]["amount_eur"])


def test_market_adjusted_ignores_estimated_alpha():
    p = _panel()
    p.stocks[50:280, 0] += 0.004  # a strong pre-event run-up inflates the market-model alpha
    events = pd.DataFrame({"ticker": ["S0.ST"], "day0": [300]})
    mm, _ = es.abnormal_returns(events, p, model="market_model")
    ma, _ = es.abnormal_returns(events, p, model="market_adjusted")
    assert mm.loc[0, "alpha"] > 0.003 and ma.loc[0, "alpha"] == 0.0
    assert mm.loc[0, "[0,+60]"] < ma.loc[0, "[0,+60]"] - 0.15  # 61 days x ~0.4% of extrapolated alpha


def test_universe_return_is_cross_sectional_mean():
    p = _panel()
    assert es.universe_return(p)[5] == pytest.approx(p.stocks[5].mean())
