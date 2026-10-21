"""Offline tests on synthetic data (no network, no real names)."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

import bunching
import pdmr
import shorts
from calendars import business_day_lag, easter, holidays


def test_easter_dates():
    assert easter(2024) == date(2024, 3, 31)
    assert easter(2025) == date(2025, 4, 20)
    assert easter(2026) == date(2026, 4, 5)


def test_swedish_midsummer_eve_is_a_friday():
    assert date(2025, 6, 20) in holidays("SE", 2025)
    assert date(2026, 6, 19) in holidays("SE", 2026)


def test_business_day_lag_counts_weekends_and_holidays():
    trade = pd.Series(pd.to_datetime(["2025-03-07", "2025-03-07", "2025-04-17", "2025-03-08", "2025-03-07"]))
    published = pd.Series(pd.to_datetime(["2025-03-12", "2025-03-13", "2025-04-24", "2025-03-09", "2025-03-07"]))
    lag = business_day_lag(trade, published, "SE")
    # Friday -> Wednesday is T+3 (on time), Thursday is T+4 (late); Easter (18 and 21 April) is skipped;
    # a Sunday publication rolls to Monday; same-day publication is 0.
    assert lag.tolist() == [3, 4, 3, 1, 0]
    assert business_day_lag(
        pd.Series(pd.to_datetime(["2025-07-11"])), pd.Series(pd.to_datetime(["2025-07-15"])), "FR"
    ).tolist() == [1]  # 14 July


def geometric(n0: float, ratio: float = 0.6, k: int = 10) -> np.ndarray:
    return n0 * ratio ** np.arange(k)


def test_excess_mass_recovers_a_spike():
    counts = geometric(1000)
    counts[0] += 300
    result = bunching.excess_mass(np.round(counts), window=1, degree=1)
    assert result["B"] == pytest.approx(300, rel=0.05)
    assert result["b"] == pytest.approx(0.3, rel=0.08)


def test_difference_in_bunching_nets_out_the_common_shape():
    levels = np.arange(0.5, 1.51, 0.1)
    counts = np.array([geometric(1000 + 100 * i) for i in range(len(levels))])
    flat = bunching.difference_in_bunching(levels, counts, 0.5, stat="share")
    assert flat["difference"] == pytest.approx(0, abs=1e-6)
    counts[0, 0] += 200
    spike = bunching.difference_in_bunching(levels, counts, 0.5, stat="share")
    assert spike["difference"] == pytest.approx(1200 / 2685 - 1000 / 2485, abs=0.002)


def test_cluster_bootstrap_is_reproducible_and_centred():
    rng = np.random.default_rng(0)
    clusters = rng.poisson(5, size=(50, 10)).astype(float)
    draws = bunching.cluster_bootstrap(clusters, lambda c: {"mean": c.mean()}, reps=200, seed=3)
    again = bunching.cluster_bootstrap(clusters, lambda c: {"mean": c.mean()}, reps=200, seed=3)
    assert np.array_equal(draws["mean"], again["mean"])
    summary = bunching.summarize(clusters.sum(axis=0).mean(), draws["mean"])
    assert summary["ci_low"] < summary["estimate"] < summary["ci_high"]


def positions(rows: list[tuple[str, str, float | None, bool]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "jurisdiction": "DE",
                "holder_name": "Fund A LLP",
                "isin": "DE0000000001",
                "issuer_name": "Issuer AG",
                "position_date": pd.Timestamp(d),
                "published_at": pd.NaT,
                "position_pct": v,
                "below_threshold": b,
            }
            for d, _, v, b in rows
        ]
    )


def test_crossings_classify_direction_level_and_distance():
    rows = [
        ("2020-01-02", "", 0.52, False),
        ("2020-01-10", "", 0.61, False),
        ("2020-01-20", "", 0.58, False),
        ("2020-02-01", "", 0.49, True),
        ("2020-03-01", "", 0.50, False),
        ("2020-04-01", "", 0.31, True),
    ]
    out = shorts.crossings(positions(rows))
    assert out["direction"].tolist() == ["up", "up", "down", "down", "up", "down"]
    assert out["level"].tolist() == [50, 60, 60, 50, 50, 50]
    assert out["k"].tolist() == [2, 1, 1, 0, 0, 18]
    assert out["in_band"].tolist() == [True, True, True, True, True, False]
    assert out["reentry_from_known"].tolist() == [False, False, False, False, True, False]


def test_crossings_exit_without_value_and_multi_band_jump():
    rows = [("2021-01-04", "", 0.73, False), ("2021-02-01", "", None, True)]
    out = shorts.crossings(positions(rows))
    assert out["direction"].tolist() == ["up", "exit_no_value"]
    assert out.loc[0, "level"] == 70 and out.loc[0, "k"] == 3


def units(counts: dict[tuple[str, int], int]) -> pd.DataFrame:
    """Synthetic person-issuer-years: `counts[(class, year)]` units, spread over 20 issuers."""
    amount = {"< EUR 5k": 1_000, "EUR 5k-20k": 10_000, "EUR 20k-50k": 30_000, ">= EUR 50k": 80_000}
    rows = [
        {"country": "SE", "issuer": f"I{i % 20}", "year": y, "total_eur": amount[c]}
        for (c, y), n in counts.items()
        for i in range(n)
    ]
    frame = pd.DataFrame(rows)
    frame["size_class"] = pd.cut(frame["total_eur"], pdmr.CLASSES, labels=pdmr.CLASS_LABELS, right=False)
    return frame


def test_listing_act_did_recovers_a_halving():
    years = [2022, 2023, 2024, 2025]
    counts = {}
    for y in years:
        for c in pdmr.CLASS_LABELS:
            n = 200
            if c == "EUR 5k-20k" and y == 2025:
                n = 100
            counts[(c, y)] = n
    table = pdmr.listing_act_did(units(counts), "SE", years, {"post_20k": [2025]}, reps=50)
    row = table[(table["term"] == "post_20k") & (table["class"] == "EUR 5k-20k")].iloc[0]
    assert row["estimate"] == pytest.approx(-0.5, abs=1e-6)
    placebo = table[(table["term"] == "post_20k") & (table["class"] == "EUR 20k-50k")].iloc[0]
    assert placebo["estimate"] == pytest.approx(0, abs=1e-6)


def test_person_years_cut_at_september_and_aggregate_without_netting():
    lines = pd.DataFrame(
        {
            "country": "SE",
            "issuer": "I1",
            "notifier_id": "p_x",
            "pdmr_id": "p_x",
            "record_id": ["a", "b", "c"],
            "transaction_date": pd.to_datetime(["2025-02-01", "2025-03-01", "2025-11-01"]),
            "gross_eur": [12_000.0, 9_000.0, 50_000.0],
            "role_group": "CEO/CFO",
            "closely_associated": False,
            "market_trade": True,
        }
    )
    ytd = pdmr.person_years(lines)
    assert ytd["total_eur"].tolist() == [21_000.0]
    assert ytd["size_class"].astype(str).tolist() == ["EUR 20k-50k"]
    full = pdmr.person_years(lines, full_year=True)
    assert full["total_eur"].tolist() == [71_000.0]
