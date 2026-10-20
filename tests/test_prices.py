from datetime import UTC, date, datetime, timedelta

import duckdb
import pytest
from conftest import EXAMPLE_ISIN, EXAMPLE_LEI
from fastapi.testclient import TestClient
from test_api import lake  # noqa: F401  (shared fixture: synthetic Swedish filings)

from disclosure_atlas.api import create_app
from disclosure_atlas.reference.prices import (
    Bar,
    Listing,
    NoPrices,
    PriceStore,
    SyntheticProvider,
    pick_listing,
    provider_from_env,
)


class CountingProvider(SyntheticProvider):
    name = "counting"

    def __init__(self, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    def resolve(self, isin: str) -> Listing | None:
        self.calls += 1
        if self.fail:
            raise ConnectionError("provider down")
        return super().resolve(isin)


def test_pick_listing_prefers_the_home_market():
    quotes = [
        {"symbol": "EXR.F", "quoteType": "EQUITY", "exchange": "FRA"},
        {"symbol": "EXR-B.ST", "quoteType": "EQUITY", "exchange": "STO"},
        {"symbol": "EXR-B.ST", "quoteType": "OPTION"},
    ]
    assert pick_listing("SE0000000001", quotes) == Listing("EXR-B.ST", None, "STO")
    assert pick_listing("NL0000000001", quotes).ticker == "EXR.F"
    assert pick_listing("SE0000000001", [{"symbol": "X", "quoteType": "ETF"}]) is None


def test_synthetic_prices_are_deterministic_business_days():
    provider = SyntheticProvider()
    listing = provider.resolve(EXAMPLE_ISIN)
    assert listing.currency == "SEK"
    _, first = provider.history(listing, date(2026, 1, 1), date(2026, 3, 31))
    _, again = provider.history(listing, date(2026, 1, 1), date(2026, 3, 31))
    assert first == again and len(first) > 55
    assert all(b.date.weekday() < 5 and b.low <= b.close <= b.high for b in first)


def test_store_caches_under_the_lake_reference_area(tmp_path):
    from disclosure_atlas.lake import Lake

    provider = CountingProvider()
    store = PriceStore(Lake(tmp_path), provider)
    start = date.today() - timedelta(days=60)
    history = store.get(EXAMPLE_ISIN.lower(), start)
    assert history.listing.ticker.startswith("SYN-") and history.bars
    assert all(b.date >= start for b in history.bars)
    assert store.path(EXAMPLE_ISIN) == tmp_path / "reference" / "prices" / "counting" / f"{EXAMPLE_ISIN}.parquet"
    assert store.path(EXAMPLE_ISIN).exists()
    store.get(EXAMPLE_ISIN, start)
    assert provider.calls == 1  # second read served from the cache

    # A fresh store reads the cached file; when it is stale and the provider fails, stale data is served.
    failing = CountingProvider(fail=True)
    stale = PriceStore(Lake(tmp_path), failing, max_age=timedelta(0))
    stale.folder = store.folder
    assert stale.get(EXAMPLE_ISIN, start).bars == history.bars
    with pytest.raises(ConnectionError):
        stale.get("SE0000000002", start)


def test_provider_from_env(monkeypatch):
    monkeypatch.setenv("ATLAS_PRICE_PROVIDER", "synthetic")
    assert isinstance(provider_from_env(), SyntheticProvider)
    monkeypatch.setenv("ATLAS_PRICE_PROVIDER", "none")
    assert isinstance(provider_from_env(), NoPrices)


@pytest.fixture
def client(lake):  # noqa: F811
    store = PriceStore(lake, SyntheticProvider())
    return TestClient(create_app(str(lake.root), price_store=store))


def test_issuer_prices_endpoint(client):
    body = client.get(f"/v1/issuers/{EXAMPLE_LEI.lower()}/prices", params={"days": 90}).json()
    assert body["isin"] == EXAMPLE_ISIN and body["provider"] == "synthetic" and body["currency"] == "SEK"
    assert body["bars"] and body["bars"][0]["date"] >= (date.today() - timedelta(days=90)).isoformat()
    assert [(d["buys"], d["sells"]) for d in body["insider"]] == [(1, 1)]
    assert body["insider"][0]["buy_eur"] == pytest.approx(16912.5)
    missing = client.get("/v1/issuers/549300XXXXXXXXXXXX00/prices")
    assert missing.status_code == 404 and missing.json()["status"] == 404
    assert client.get(f"/v1/issuers/{EXAMPLE_LEI}/prices", params={"days": 5}).status_code == 422


def test_prices_without_provider_return_empty_bars(lake):  # noqa: F811
    client = TestClient(create_app(str(lake.root), price_store=PriceStore(lake, NoPrices())))
    body = client.get(f"/v1/issuers/{EXAMPLE_LEI}/prices").json()
    assert body["bars"] == [] and body["ticker"] is None and body["provider"] == "none"


def test_parquet_export_supports_range_requests(client, lake, tmp_path):  # noqa: F811
    listing = {f["dataset"]: f for f in client.get("/v1/exports").json()}
    assert listing["insider_transactions"]["rows"] == 3 and listing["net_short_positions"]["rows"] == 0
    full = client.get("/v1/exports/insider_transactions.parquet")
    assert full.status_code == 200 and full.headers["content-type"] == "application/vnd.apache.parquet"
    assert full.headers["accept-ranges"] == "bytes" and full.content[:4] == b"PAR1"
    tail = client.get("/v1/exports/insider_transactions.parquet", headers={"Range": "bytes=-8"})
    assert tail.status_code == 206 and tail.content == full.content[-8:]
    assert client.head("/v1/exports/insider_transactions.parquet").status_code == 200
    assert client.get("/v1/exports/secret.parquet").status_code == 422

    path = tmp_path / "gold.parquet"
    path.write_bytes(full.content)
    rows = duckdb.sql(f"SELECT count(*), max(amount_eur) FROM '{path}'").fetchone()
    assert rows[0] == 3 and rows[1] == pytest.approx(16912.5)
    assert len(list((lake.root / "exports").iterdir())) == 1


def test_bar_shape_matches_api_model():
    from disclosure_atlas.api.prices import PriceBar

    bar = Bar(date=datetime.now(UTC).date(), open=1.0, high=2.0, low=0.5, close=1.5, volume=None)
    assert PriceBar(**bar.__dict__).close == 1.5


@pytest.mark.network
def test_yfinance_resolves_a_swedish_isin(tmp_path):
    from disclosure_atlas.lake import Lake
    from disclosure_atlas.reference.prices import YFinanceProvider

    store = PriceStore(Lake(tmp_path), YFinanceProvider())
    history = store.get("SE0000115446", date.today() - timedelta(days=30))  # a large Stockholm-listed issuer
    assert history.listing.ticker.endswith(".ST") and history.listing.currency == "SEK"
    assert len(history.bars) > 10
