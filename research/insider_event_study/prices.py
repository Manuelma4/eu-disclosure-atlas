"""Daily prices from Yahoo Finance (yfinance), cached as one Parquet file in the study's cache folder.

`adj_close` (split- and dividend-adjusted) drives returns. `raw_close` undoes the split adjustment so a
filing's transaction price can be compared with the market price on the trade date (a mapping sanity check).
"""

from __future__ import annotations

import time
from pathlib import Path

import pandas as pd

BENCHMARK = "^OMXSPI"  # OMX Stockholm All-Share (price index)
CHUNK = 40


def _tidy(raw: pd.DataFrame, symbol: str) -> pd.DataFrame:
    frame = raw.dropna(subset=["Adj Close"]).copy()
    if frame.empty:
        return pd.DataFrame()
    splits = frame.get("Stock Splits", pd.Series(0.0, index=frame.index)).fillna(0.0).replace(0.0, 1.0)
    # yfinance's Close is split-adjusted: multiply by every split ratio after each date to recover raw prices.
    later_splits = splits[::-1].cumprod()[::-1].shift(-1, fill_value=1.0)
    return pd.DataFrame(
        {
            "ticker": symbol,
            "date": frame.index.tz_localize(None).normalize(),
            "adj_close": frame["Adj Close"].to_numpy(),
            "raw_close": (frame["Close"] * later_splits).to_numpy(),
            "volume": frame["Volume"].to_numpy(),
        }
    )


def download(symbols: list[str], start: str, end: str | None = None, log=print) -> pd.DataFrame:
    import yfinance as yf

    frames = []
    for i in range(0, len(symbols), CHUNK):
        chunk = symbols[i : i + CHUNK]
        raw = yf.download(chunk, start=start, end=end, auto_adjust=False, actions=True, group_by="ticker",
                          progress=False, threads=True)
        for symbol in chunk:
            if raw.empty or symbol not in raw.columns.get_level_values(0):
                continue
            tidy = _tidy(raw[symbol], symbol)
            if not tidy.empty:
                frames.append(tidy)
        log(f"  prices {min(i + CHUNK, len(symbols))}/{len(symbols)}")
        time.sleep(1.0)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def load_prices(symbols: list[str], cache_path: Path, start: str, refresh: bool = False, log=print) -> pd.DataFrame:
    """Prices for `symbols` plus the benchmark; downloads only symbols missing from the cache."""
    wanted = sorted(set(symbols) | {BENCHMARK})
    cached = pd.read_parquet(cache_path) if cache_path.exists() and not refresh else pd.DataFrame()
    have = set(cached["ticker"]) if not cached.empty else set()
    todo = [s for s in wanted if s not in have]
    if todo:
        log(f"prices: downloading {len(todo)} tickers from Yahoo Finance")
        fresh = download(todo, start=start, log=log)
        # Remember tickers without data so re-runs do not ask again.
        empty = sorted(set(todo) - (set(fresh["ticker"]) if not fresh.empty else set()))
        stub = pd.DataFrame({"ticker": empty, "date": pd.NaT, "adj_close": float("nan"),
                             "raw_close": float("nan"), "volume": float("nan")})
        cached = pd.concat([c for c in (cached, fresh, stub) if not c.empty], ignore_index=True)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cached.to_parquet(cache_path, index=False)
    out = cached[cached["ticker"].isin(wanted)].dropna(subset=["date"])
    return out.sort_values(["ticker", "date"]).reset_index(drop=True)
