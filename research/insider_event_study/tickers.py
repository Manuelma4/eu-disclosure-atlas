"""ISIN -> Yahoo Finance ticker, for Stockholm listings (suffix `.ST`).

1. Yahoo search by ISIN (via yfinance), keeping Stockholm results.
2. Fallback: OpenFIGI (https://www.openfigi.com/api), keeping the Stockholm listing (exchCode `SS`) and
   turning the Bloomberg ticker into Yahoo candidates (`VOLVB` with name "VOLVO AB-B SHS" -> `VOLV-B.ST`).
   Candidates are accepted only if Yahoo returns prices for them.

The shared reference package for identifiers belongs to WS5; this module is deliberately local to the study.
Results are cached in data/isin_tickers.csv (identifiers only), so re-runs make no requests for known ISINs.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterable
from pathlib import Path

import httpx
import pandas as pd
from disclosure_atlas.http import USER_AGENT

OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"
OPENFIGI_BATCH = 10  # jobs per request without an API key
OPENFIGI_INTERVAL = 2.6  # 25 requests per minute without an API key
YAHOO_INTERVAL = 1.0
CACHE_COLUMNS = ["isin", "yahoo_ticker", "method", "figi_type"]


def figi_candidates(ticker: str, name: str) -> list[str]:
    """Yahoo candidates for a Bloomberg Stockholm ticker; share classes become `-A`, `-B`, ..."""
    base = ticker.replace(" ", "").replace("/", "-").upper()
    out: list[str] = []
    share_class = re.search(r"-([A-D])\b", name.upper())
    if share_class and base.endswith(share_class.group(1)) and len(base) > 1:
        out.append(f"{base[:-1]}-{share_class.group(1)}.ST")
    if len(base) > 1 and base[-1] in "ABCD" and not out:
        out.append(f"{base[:-1]}-{base[-1]}.ST")
    out.append(f"{base}.ST")
    return list(dict.fromkeys(out))


def yahoo_search(isin: str) -> str | None:
    import yfinance as yf

    try:
        quotes = yf.Search(isin, max_results=8).quotes
    except Exception:  # network or parsing errors: fall back to OpenFIGI
        return None
    for quote in quotes:
        symbol = quote.get("symbol") or ""
        if symbol.endswith(".ST") and quote.get("quoteType") == "EQUITY":
            return symbol
    return None


def openfigi(isins: list[str], client: httpx.Client) -> dict[str, list[dict]]:
    """All listings per ISIN (any exchange), so unmapped ISINs can still be classified by security type."""
    jobs = [{"idType": "ID_ISIN", "idValue": isin} for isin in isins]
    for attempt in range(5):
        response = client.post(OPENFIGI_URL, json=jobs)
        if response.status_code == 429:
            time.sleep(10 * (attempt + 1))
            continue
        response.raise_for_status()
        return {isin: item.get("data", []) for isin, item in zip(isins, response.json(), strict=True)}
    return {}


def figi_type(listings: list[dict]) -> str:
    """'Common Stock', another OpenFIGI security type, or 'not found'."""
    if not listings:
        return "not found"
    types = [x.get("securityType") or "unknown" for x in listings]
    return "Common Stock" if "Common Stock" in types else types[0]


def has_prices(symbol: str) -> bool:
    import yfinance as yf

    try:
        return not yf.Ticker(symbol).history(period="max", auto_adjust=False).empty
    except Exception:
        return False


def _save(cache: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cache.sort_values("isin").to_csv(path, index=False)


def map_isins(isins: Iterable[str], cache_path: Path, log=print) -> pd.DataFrame:
    """Return the cache, extended with any ISIN not seen before; `yahoo_ticker` is empty when unmapped.

    Stage 1 (Yahoo search) and stage 2 (OpenFIGI, also recording `figi_type`) save as they go, so an
    interrupted run resumes where it stopped.
    """
    cache = (pd.read_csv(cache_path, dtype=str).fillna("") if cache_path.exists()
             else pd.DataFrame(columns=CACHE_COLUMNS))
    for column in CACHE_COLUMNS:
        if column not in cache:
            cache[column] = ""
    todo = sorted(set(isins) - set(cache["isin"]))
    if todo:
        log(f"tickers: Yahoo search for {len(todo)} new ISINs")
    rows = []
    for i, isin in enumerate(todo, 1):
        symbol = yahoo_search(isin)
        rows.append({"isin": isin, "yahoo_ticker": symbol or "", "method": "yahoo_search" if symbol else "",
                     "figi_type": ""})
        time.sleep(YAHOO_INTERVAL)
        if i % 50 == 0 or i == len(todo):
            cache = pd.concat([cache, pd.DataFrame(rows)], ignore_index=True)
            rows = []
            _save(cache, cache_path)
            log(f"  yahoo search {i}/{len(todo)}")
    cache = cache.set_index("isin")
    unmatched = (cache["yahoo_ticker"] == "") & (cache["figi_type"] == "")
    pending = list(cache.index[unmatched])
    if pending:
        log(f"tickers: OpenFIGI for {len(pending)} ISINs without a Yahoo match")
    with httpx.Client(timeout=60, headers={"User-Agent": USER_AGENT}) as client:
        for start in range(0, len(pending), OPENFIGI_BATCH):
            batch = pending[start : start + OPENFIGI_BATCH]
            for isin, listings in openfigi(batch, client).items():
                cache.at[isin, "figi_type"] = figi_type(listings)
                cache.at[isin, "method"] = "none"
                stockholm = [x for x in listings
                             if x.get("exchCode") == "SS" and x.get("securityType") == "Common Stock"]
                for listing in stockholm:
                    for candidate in figi_candidates(listing.get("ticker", ""), listing.get("name", "")):
                        if has_prices(candidate):
                            cache.at[isin, "yahoo_ticker"], cache.at[isin, "method"] = candidate, "openfigi"
                            break
                        time.sleep(YAHOO_INTERVAL)
                    if cache.at[isin, "yahoo_ticker"]:
                        break
            time.sleep(OPENFIGI_INTERVAL)
            if (start // OPENFIGI_BATCH) % 10 == 9:
                _save(cache.reset_index(), cache_path)
    cache = cache.reset_index()
    _save(cache, cache_path)
    return cache
