"""Daily share prices behind a pluggable provider, mapped from ISIN to a listing and cached in the lake.

lake/reference/prices/<provider>/<ISIN>.parquet   daily bars, the resolved ticker and currency in the metadata

Providers:
- ``yfinance`` (development): resolves an ISIN through Yahoo Finance search, preferring the listing on the
  issuer's home market, then downloads daily bars. Yahoo's terms allow personal use only, so production
  must plug in a licensed provider (same interface).
- ``synthetic``: deterministic random walk seeded by the ISIN, for tests, demos and end-to-end runs.
- ``none``: no prices; the API answers with empty bars.

Selected with ``ATLAS_PRICE_PROVIDER`` (default: ``yfinance`` when installed, otherwise ``none``).
"""

from __future__ import annotations

import hashlib
import math
import os
import random
import threading
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Protocol

import pyarrow as pa
import pyarrow.parquet as pq

from ..lake import Lake

SCHEMA = pa.schema(
    [
        ("date", pa.date32()),
        ("open", pa.float64()),
        ("high", pa.float64()),
        ("low", pa.float64()),
        ("close", pa.float64()),
        ("volume", pa.float64()),
    ]
)

# Yahoo Finance suffix of each country's main exchange, keyed by the ISIN country prefix.
HOME_SUFFIX = {
    "SE": ".ST", "FI": ".HE", "DK": ".CO", "NO": ".OL", "IS": ".IC", "FR": ".PA", "DE": ".DE", "NL": ".AS",
    "BE": ".BR", "ES": ".MC", "IT": ".MI", "PT": ".LS", "IE": ".IR", "AT": ".VI", "PL": ".WA", "GB": ".L",
    "CH": ".SW", "LU": ".PA",
}


@dataclass(frozen=True)
class Listing:
    ticker: str
    currency: str | None
    exchange: str | None = None


@dataclass(frozen=True)
class Bar:
    date: date
    open: float | None
    high: float | None
    low: float | None
    close: float
    volume: float | None


@dataclass(frozen=True)
class PriceHistory:
    isin: str
    listing: Listing | None
    provider: str
    fetched_at: datetime | None
    bars: list[Bar]


class PriceProvider(Protocol):
    name: str

    def resolve(self, isin: str) -> Listing | None:
        """Map an ISIN to the listing whose prices to use (ticker and trading currency)."""
        ...

    def history(self, listing: Listing, start: date, end: date) -> tuple[Listing, list[Bar]]:
        """Daily bars from start to end inclusive, oldest first, and the listing completed (e.g. currency)."""
        ...


def pick_listing(isin: str, quotes: list[dict[str, object]]) -> Listing | None:
    """Choose among search results: equities only, the issuer's home market first."""
    equities = [q for q in quotes if q.get("quoteType") == "EQUITY" and q.get("symbol")]
    if not equities:
        return None
    suffix = HOME_SUFFIX.get(isin[:2].upper())
    home = [q for q in equities if suffix and str(q["symbol"]).upper().endswith(suffix)]
    chosen = (home or equities)[0]
    return Listing(ticker=str(chosen["symbol"]), currency=None, exchange=str(chosen.get("exchange") or "") or None)


class YFinanceProvider:
    """Yahoo Finance through the yfinance package (development only, see the module docstring)."""

    name = "yfinance"

    def __init__(self) -> None:
        import yfinance  # noqa: F401  (fail early when the extra is missing)

        self._lock = threading.Lock()  # yfinance shares a session; keep requests sequential and polite
        self._last = 0.0

    def _polite(self) -> None:
        wait = 1.0 - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def resolve(self, isin: str) -> Listing | None:
        import yfinance as yf

        with self._lock:
            self._polite()
            quotes = yf.Search(isin, max_results=8, news_count=0).quotes
        return pick_listing(isin, quotes)

    def history(self, listing: Listing, start: date, end: date) -> tuple[Listing, list[Bar]]:
        import yfinance as yf

        with self._lock:
            self._polite()
            ticker = yf.Ticker(listing.ticker)
            frame = ticker.history(start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
                                   auto_adjust=False, actions=False)
            currency = (ticker.history_metadata or {}).get("currency")
        bars = []
        for index, row in frame.iterrows():
            close = row.get("Close")
            if close is None or math.isnan(close):
                continue
            bars.append(Bar(date=index.date(), open=_num(row.get("Open")), high=_num(row.get("High")),
                            low=_num(row.get("Low")), close=float(close), volume=_num(row.get("Volume"))))
        return Listing(listing.ticker, currency or listing.currency, listing.exchange), bars


class SyntheticProvider:
    """Deterministic prices for tests and demos: a business-day random walk seeded by the ISIN."""

    name = "synthetic"

    def resolve(self, isin: str) -> Listing | None:
        return Listing(ticker=f"SYN-{isin[-6:]}", currency=_home_ccy(isin))

    def history(self, listing: Listing, start: date, end: date) -> tuple[Listing, list[Bar]]:
        seed = int(hashlib.sha256(listing.ticker.encode()).hexdigest()[:12], 16)
        rng = random.Random(seed)
        origin = date(2020, 1, 1)
        price = 20 + seed % 180
        bars: list[Bar] = []
        day = origin
        while day <= end:
            if day.weekday() < 5:
                drift = rng.gauss(0.0002, 0.018)
                open_ = price
                price = max(0.5, price * math.exp(drift))
                spread = abs(rng.gauss(0, 0.008))
                if day >= start:
                    bars.append(Bar(date=day, open=round(open_, 2), high=round(max(open_, price) * (1 + spread), 2),
                                    low=round(min(open_, price) * (1 - spread), 2), close=round(price, 2),
                                    volume=float(rng.randint(20_000, 900_000))))
            day += timedelta(days=1)
        return listing, bars


class NoPrices:
    name = "none"

    def resolve(self, isin: str) -> Listing | None:
        return None

    def history(self, listing: Listing, start: date, end: date) -> tuple[Listing, list[Bar]]:
        return listing, []


def _home_ccy(isin: str) -> str:
    return {"SE": "SEK", "DK": "DKK", "NO": "NOK", "IS": "ISK", "PL": "PLN", "GB": "GBP", "CH": "CHF"}.get(
        isin[:2].upper(), "EUR"
    )


def _num(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else number


def provider_from_env() -> PriceProvider:
    choice = os.environ.get("ATLAS_PRICE_PROVIDER", "").strip().lower()
    if choice == "synthetic":
        return SyntheticProvider()
    if choice == "none":
        return NoPrices()
    try:
        return YFinanceProvider()
    except ImportError:
        if choice == "yfinance":
            raise
        return NoPrices()


class PriceStore:
    """Read-through cache of daily bars per ISIN under lake/reference/prices/<provider>/."""

    def __init__(self, lake: Lake, provider: PriceProvider, max_age: timedelta = timedelta(hours=12),
                 history_days: int = 5 * 366) -> None:
        self.provider = provider
        self.folder = lake.root / "reference" / "prices" / provider.name
        self.max_age = max_age
        self.history_days = history_days
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def _lock(self, isin: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(isin, threading.Lock())

    def path(self, isin: str) -> Path:
        return self.folder / f"{isin}.parquet"

    def get(self, isin: str, start: date, end: date | None = None) -> PriceHistory:
        isin = isin.upper()
        end = end or date.today()
        with self._lock(isin):
            cached = self._read(isin)
            if cached is None or datetime.now(UTC) - (cached.fetched_at or datetime.min.replace(tzinfo=UTC)) > (
                self.max_age
            ):
                try:
                    cached = self._refresh(isin)
                except Exception:  # noqa: BLE001  (a provider outage must not break the page: serve stale)
                    if cached is None:
                        raise
        bars = [b for b in cached.bars if start <= b.date <= end]
        return PriceHistory(isin=isin, listing=cached.listing, provider=cached.provider,
                            fetched_at=cached.fetched_at, bars=bars)

    def _refresh(self, isin: str) -> PriceHistory:
        listing = self.provider.resolve(isin)
        today = date.today()
        bars: list[Bar] = []
        if listing:
            listing, bars = self.provider.history(listing, today - timedelta(days=self.history_days), today)
        fetched_at = datetime.now(UTC)
        meta = {
            "provider": self.provider.name,
            "fetched_at": fetched_at.isoformat(),
            "ticker": listing.ticker if listing else "",
            "currency": (listing.currency or "") if listing else "",
            "exchange": (listing.exchange or "") if listing else "",
        }
        table = pa.Table.from_pylist([b.__dict__ for b in bars], schema=SCHEMA)
        table = table.replace_schema_metadata({f"atlas.{k}": v for k, v in meta.items()})
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = self.path(isin).with_suffix(".tmp")
        pq.write_table(table, tmp, compression="zstd")
        tmp.replace(self.path(isin))
        return PriceHistory(isin=isin, listing=listing, provider=self.provider.name, fetched_at=fetched_at, bars=bars)

    def _read(self, isin: str) -> PriceHistory | None:
        path = self.path(isin)
        if not path.exists():
            return None
        table = pq.read_table(path)
        meta = {k.decode().removeprefix("atlas."): v.decode() for k, v in (table.schema.metadata or {}).items()}
        listing = (
            Listing(ticker=meta["ticker"], currency=meta.get("currency") or None, exchange=meta.get("exchange") or None)
            if meta.get("ticker")
            else None
        )
        fetched = meta.get("fetched_at")
        return PriceHistory(
            isin=isin,
            listing=listing,
            provider=meta.get("provider", self.provider.name),
            fetched_at=datetime.fromisoformat(fetched) if fetched else None,
            bars=[Bar(**row) for row in table.to_pylist()],
        )
