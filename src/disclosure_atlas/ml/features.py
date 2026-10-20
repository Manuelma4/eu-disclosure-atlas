"""Events and features from the gold layer, using only information public at publication.

An event is one issuer share (ISIN), one direction (buy or sell) and one publication day in the authority's time
zone, as in the WS6 event study: several lines of one filing, and several insiders filing on the same day, become
one event. Every feature is computed from filings published up to that day and from prices of earlier sessions,
so nothing in a feature row was unknown to an outsider at publication (no look-ahead).
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

FEATURES = [
    "is_buy",
    "role_seniority",
    "log_amount_eur",
    "n_insiders",
    "cluster_insiders_30d",
    "filing_lag_days",
    "log_traded_value_60d",
    "momentum_20d",
]
"""Model inputs, in order. Missing values stay NaN: the gradient-boosting model handles them natively."""

ROLE_SENIORITY = {"ceo": 0, "cfo": 1, "chair": 2, "executive": 3, "board_member": 4}
"""Lower is more senior; other and unknown roles get 5. An event takes its most senior insider."""

CLUSTER_DAYS = 30
MOMENTUM_SESSIONS = 20
SIZE_SESSIONS = 60
MAX_STALE_DAYS = 10
"""A price feature is missing when the stock's last session before publication is older than this."""

TIMEZONES = {"FI": "Europe/Helsinki", "IE": "Europe/Dublin", "PT": "Europe/Lisbon"}
DEFAULT_TIMEZONE = "Europe/Stockholm"  # CET/CEST: every other covered market

TRADES_SQL = """
SELECT record_id, source, jurisdiction, isin, issuer_name, issuer_lei, nature, pdmr_id, pdmr_role,
       published_at, transaction_date, amount_eur
FROM insider_transactions
WHERE is_open_market AND isin IS NOT NULL AND published_at IS NOT NULL AND volume > 0 AND price > 0
  AND published_at >= ?
"""


def load_trades(con: duckdb.DuckDBPyConnection, since: pd.Timestamp | None = None) -> pd.DataFrame:
    """Open-market share buys and sells from gold (one row per transaction line), published since ``since``."""
    start = (since or pd.Timestamp("1900-01-01", tz="UTC")).to_pydatetime()
    trades = con.execute(TRADES_SQL, [start]).df()
    trades["published_at"] = pd.to_datetime(trades["published_at"], utc=True)
    return trades


def aggregate_events(trades: pd.DataFrame) -> pd.DataFrame:
    """One row per (isin, nature, local publication day), with the raw ingredients of the features.

    ``trades`` needs isin, issuer_name, jurisdiction, nature, pdmr_id, pdmr_role, published_at (UTC),
    transaction_date and amount_eur; issuer_lei is optional.
    """
    t = trades.copy()
    if "issuer_lei" not in t:
        t["issuer_lei"] = None
    local = pd.Series(pd.NaT, index=t.index, dtype="datetime64[us]")
    for country, rows in t.groupby("jurisdiction").groups.items():
        tz = TIMEZONES.get(str(country), DEFAULT_TIMEZONE)
        local.loc[rows] = t.loc[rows, "published_at"].dt.tz_convert(tz).dt.tz_localize(None).astype("datetime64[us]")
    t["publication_date"] = local.dt.normalize()
    t["seniority"] = t["pdmr_role"].map(ROLE_SENIORITY).fillna(5).astype(int)
    t["lag"] = (t["publication_date"] - pd.to_datetime(t["transaction_date"])).dt.days.clip(lower=0)
    events = t.groupby(["isin", "nature", "publication_date"], as_index=False).agg(
        issuer_name=("issuer_name", "first"),
        issuer_lei=("issuer_lei", "first"),
        country=("jurisdiction", "first"),
        published_at=("published_at", "min"),
        amount_eur=("amount_eur", lambda s: s.sum(min_count=1)),
        n_lines=("pdmr_id", "size"),
        n_insiders=("pdmr_id", "nunique"),
        pdmr_ids=("pdmr_id", lambda s: frozenset(s)),
        role_seniority=("seniority", "min"),
        filing_lag_days=("lag", "max"),
    )
    events["event_id"] = (events["isin"] + ":" + events["nature"] + ":"
                          + events["publication_date"].dt.strftime("%Y-%m-%d"))
    events["is_buy"] = (events["nature"] == "buy").astype(int)
    events["log_amount_eur"] = np.log10(1 + events["amount_eur"].clip(lower=0))
    events["cluster_insiders_30d"] = cluster_insiders(events)
    return events.drop(columns="pdmr_ids").sort_values(["publication_date", "event_id"]).reset_index(drop=True)


def cluster_insiders(events: pd.DataFrame, days: int = CLUSTER_DAYS) -> pd.Series:
    """Distinct insiders of the issuer who traded in the same direction in the trailing ``days`` calendar days,
    publication day included (only filings already public)."""
    out = pd.Series(0, index=events.index, dtype=int)
    window = pd.Timedelta(days=days - 1)
    for _, group in events.sort_values("publication_date").groupby(["isin", "nature"]):
        dates = group["publication_date"].to_numpy()
        ids = group["pdmr_ids"].to_list()
        for k, idx in enumerate(group.index):
            first = np.searchsorted(dates, dates[k] - window, side="left")
            out[idx] = len(frozenset().union(*ids[first : k + 1]))
    return out


def price_features(events: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
    """Firm size and momentum per event from daily prices of sessions strictly before the publication day.

    ``prices`` has isin, date, close and volume (unadjusted, as the lake's price cache stores them). Returns
    ``log_traded_value_60d`` (log10 of the median daily close x volume over the last 60 sessions, a liquidity and
    size proxy in the trading currency) and ``momentum_20d`` (the return over the last 20 sessions).
    """
    columns = ["log_traded_value_60d", "momentum_20d"]
    if prices.empty or events.empty:
        return pd.DataFrame(np.nan, index=events.index, columns=columns)
    p = prices[["isin", "date", "close", "volume"]].dropna(subset=["close"]).copy()
    p["date"] = pd.to_datetime(p["date"]).astype("datetime64[us]")
    p = p.sort_values(["isin", "date"])
    grouped = p.groupby("isin", sort=False)
    p["momentum_20d"] = p["close"] / grouped["close"].shift(MOMENTUM_SESSIONS) - 1
    value = p["close"] * p["volume"]
    p["log_traded_value_60d"] = np.log10(
        1 + value.groupby(p["isin"], sort=False).transform(lambda s: s.rolling(SIZE_SESSIONS, min_periods=20).median())
    )
    left = events[["isin", "publication_date"]].reset_index().rename(columns={"publication_date": "date"})
    left["date"] = left["date"].astype("datetime64[us]")
    merged = pd.merge_asof(
        left.sort_values("date"), p[["isin", "date", *columns]].sort_values("date"), on="date", by="isin",
        allow_exact_matches=False, tolerance=pd.Timedelta(days=MAX_STALE_DAYS), direction="backward",
    )
    return merged.set_index("index")[columns].reindex(events.index)


def build_features(events: pd.DataFrame, prices: pd.DataFrame | None = None) -> pd.DataFrame:
    """Events with every column of FEATURES (price features NaN without prices)."""
    out = events.copy()
    priced = price_features(out, prices if prices is not None else pd.DataFrame())
    for column in priced:
        out[column] = priced[column]
    out[FEATURES] = out[FEATURES].astype(float)
    return out


def cached_prices(lake_root: Path, isins: list[str], since: pd.Timestamp) -> pd.DataFrame:
    """Daily bars already in the lake's price cache (lake/reference/prices/<provider>/<ISIN>.parquet).

    Read-only: scoring never calls a price provider, so a missing ISIN just leaves its price features NaN.
    """
    folder = Path(lake_root) / "reference" / "prices"
    frames = []
    for isin in sorted(set(isins)):
        # Prefer a real provider's cache over synthetic prices when both exist.
        paths = sorted(folder.glob(f"*/{isin}.parquet"), key=lambda p: p.parent.name in ("synthetic", "none"))
        if paths:
            bars = pq.read_table(paths[0], columns=["date", "close", "volume"]).to_pandas()
            frames.append(bars.assign(isin=isin))
    if not frames:
        return pd.DataFrame(columns=["isin", "date", "close", "volume"])
    prices = pd.concat(frames, ignore_index=True)
    prices["date"] = pd.to_datetime(prices["date"])
    return prices[prices["date"] >= since - timedelta(days=2 * SIZE_SESSIONS)]
