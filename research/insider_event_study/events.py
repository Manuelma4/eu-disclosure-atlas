"""Open-market insider trades from the gold layer, aggregated into events.

An event is one issuer (ISIN), one direction (buy or sell) and one publication day: several lines of one
filing, and several insiders filing on the same day, become one event. This keeps the cross-section of
abnormal returns closer to independent (one observation per stock and day) and matches what an outsider sees.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from disclosure_atlas.lake import Lake
from disclosure_atlas.models import InstrumentType
from disclosure_atlas.normalize import normalize_instrument

ROLE_GROUPS = {"ceo": "CEO/CFO", "cfo": "CEO/CFO", "chair": "Chair/board", "board_member": "Chair/board"}
ROLE_RANK = {"CEO/CFO": 0, "Chair/board": 1, "Other": 2}
SIZE_BUCKETS = [0, 25_000, 100_000, 1_000_000, float("inf")]
SIZE_LABELS = ["< EUR 25k", "EUR 25k-100k", "EUR 100k-1m", ">= EUR 1m"]

# Open-market trades are gold `is_open_market` rows. FI's older exports leave "Instrument type" empty, so
# gold cannot tell shares from warrants there; those on-venue buys and sells are kept as `inferred_share`
# candidates and must later resolve to a Stockholm equity ticker and pass the price check (see run.py).
TRADES_SQL = """
SELECT record_id, isin, issuer_name, nature, pdmr_id, pdmr_role, closely_associated, instrument_name, venue,
       NOT is_open_market AS inferred_share,
       timezone('Europe/Stockholm', published_at) AS published_local,
       transaction_date, volume, price, currency, amount, amount_eur, filing_lag_days
FROM insider_transactions
WHERE source = 'se_fi_pdmr' AND isin IS NOT NULL AND volume > 0 AND price > 0
  AND (is_open_market
       OR (nature IN ('buy', 'sell') AND coalesce(on_venue, false) AND instrument_type_raw IS NULL))
"""


def venue_group(venue: str | None) -> str:
    """Coarse market segment from the trading venue as published."""
    v = (venue or "").upper()
    if "FIRST NORTH" in v:
        return "First North"
    if "STOCKHOLM" in v:
        return "Nasdaq Stockholm"
    if "AKTIETORGET" in v or "SPOTLIGHT" in v:
        return "Spotlight"
    if "NGM" in v or "NORDIC GROWTH" in v or "NORDIC MTF" in v:
        return "NGM"
    return "Other"


def load_trades(lake: Path | str) -> pd.DataFrame:
    """Open-market share buys and sells (gold `is_open_market`), one row per transaction line."""
    trades = Lake(lake).connect().sql(TRADES_SQL).df()
    # Drop untyped lines whose instrument name says warrant, option, right, bond...
    named = trades["instrument_name"].map(normalize_instrument)
    trades = trades[~trades["inferred_share"] | named.isin([InstrumentType.SHARE, InstrumentType.OTHER])].copy()
    trades["role_group"] = trades["pdmr_role"].map(ROLE_GROUPS).fillna("Other")
    trades["transaction_date"] = pd.to_datetime(trades["transaction_date"])
    trades["published_local"] = pd.to_datetime(trades["published_local"])
    return trades


def aggregate_events(trades: pd.DataFrame) -> pd.DataFrame:
    """One row per (isin, nature, publication day) with the most senior role and the total amount."""
    t = trades.assign(
        pub_date=trades["published_local"].dt.normalize(),
        role_rank=trades["role_group"].map(ROLE_RANK),
        pv=trades["price"] * trades["volume"],
    )
    grouped = t.groupby(["isin", "nature", "pub_date"], as_index=False)
    events = grouped.agg(
        issuer_name=("issuer_name", "first"),
        published_local=("published_local", "min"),
        first_trade=("transaction_date", "min"),
        last_trade=("transaction_date", "max"),
        amount_eur=("amount_eur", lambda s: s.sum(min_count=1)),
        volume=("volume", "sum"),
        pv=("pv", "sum"),
        n_lines=("record_id", "count"),
        n_insiders=("pdmr_id", "nunique"),
        inferred_share=("inferred_share", "all"),
        venue=("venue", lambda s: s.mode().iat[0] if s.notna().any() else None),
        pdmr_ids=("pdmr_id", lambda s: sorted(set(s))),
        role_rank=("role_rank", "min"),
        filing_lag_days=("filing_lag_days", "max"),
    )
    # Volume-weighted price, used only for the ISIN/ticker sanity check against market prices.
    events["vwap"] = events.pop("pv") / events["volume"]
    events["role_group"] = events["role_rank"].map({v: k for k, v in ROLE_RANK.items()})
    events["size_bucket"] = pd.cut(events["amount_eur"], SIZE_BUCKETS, labels=SIZE_LABELS, right=False)
    events["market"] = events["venue"].map(venue_group)
    events["direction"] = events["nature"].map({"buy": 1, "sell": -1})
    return events.drop(columns="role_rank").sort_values(["published_local", "isin"]).reset_index(drop=True)
