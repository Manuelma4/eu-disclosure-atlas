"""GET /v1/issuers/{lei}/prices: daily share prices with the issuer's insider trading per day."""

from __future__ import annotations

import datetime as dt
from http import HTTPStatus
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from ..reference.prices import PriceStore
from .readmodel import ReadModel

PRICE_CACHE_CONTROL = "public, max-age=900, s-maxage=3600, stale-while-revalidate=86400"


class PriceBar(BaseModel):
    date: dt.date
    open: float | None
    high: float | None
    low: float | None
    close: float
    volume: float | None


class InsiderDay(BaseModel):
    date: dt.date = Field(description="Transaction date")
    buys: int = Field(description="Open-market purchases of shares")
    sells: int = Field(description="Open-market sales of shares")
    buy_eur: float | None
    sell_eur: float | None
    buy_volume: float | None
    sell_volume: float | None


class PriceSeries(BaseModel):
    lei: str
    isin: str | None = Field(description="Share ISIN used (the one most often traded by the issuer's insiders)")
    ticker: str | None = Field(description="Listing the prices come from, as the provider names it")
    currency: str | None = Field(description="Trading currency of the listing")
    provider: str = Field(description="Price provider: yfinance (development), synthetic or none")
    fetched_at: dt.datetime | None = Field(description="When the provider was last queried for this ISIN")
    bars: list[PriceBar] = Field(description="Daily bars, oldest first (unadjusted)")
    insider: list[InsiderDay] = Field(description="Days with open-market insider trades in the window")


def prices_router(read_model: ReadModel, store: PriceStore) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/issuers/{lei}/prices", response_model=PriceSeries, tags=["issuers"])
    async def issuer_prices(
        lei: str,
        response: Response,
        days: Annotated[int, Query(ge=30, le=1830, description="Calendar days of history")] = 730,
        isin: Annotated[str | None, Query(description="Override the share ISIN")] = None,
    ) -> Any:
        """Daily prices of the issuer's share, plus the days on which its insiders bought or sold on the market.

        Prices come from a pluggable provider (yfinance in development) and are cached in the lake's reference
        area. They are unadjusted closing prices of the listing on the issuer's home market. Not investment advice.
        """
        lei = lei.upper()
        start = dt.date.today() - dt.timedelta(days=days)
        with read_model.snapshot() as snap:
            known = snap.one("SELECT count(*) AS n FROM insider_transactions WHERE issuer_lei = ?", [lei])
            if not known or not known["n"]:
                raise HTTPException(HTTPStatus.NOT_FOUND, f"No issuer with LEI {lei}")
            if not isin:
                row = snap.one(
                    "SELECT isin FROM insider_transactions WHERE issuer_lei = ? AND isin IS NOT NULL "
                    "AND instrument_type = 'share' GROUP BY isin ORDER BY count(*) DESC, isin LIMIT 1",
                    [lei],
                )
                isin = row["isin"] if row else None
            insider = snap.rows(
                """
                SELECT transaction_date AS date,
                    count(*) FILTER (WHERE direction > 0) AS buys,
                    count(*) FILTER (WHERE direction < 0) AS sells,
                    sum(amount_eur) FILTER (WHERE direction > 0) AS buy_eur,
                    sum(amount_eur) FILTER (WHERE direction < 0) AS sell_eur,
                    sum(volume) FILTER (WHERE direction > 0) AS buy_volume,
                    sum(volume) FILTER (WHERE direction < 0) AS sell_volume
                FROM insider_transactions
                WHERE issuer_lei = ? AND is_open_market AND transaction_date >= ?
                GROUP BY 1 ORDER BY 1
                """,
                [lei, start],
            )
        response.headers["Cache-Control"] = PRICE_CACHE_CONTROL
        if not isin:
            return PriceSeries(lei=lei, isin=None, ticker=None, currency=None, provider=store.provider.name,
                               fetched_at=None, bars=[], insider=insider)
        try:
            history = await run_in_threadpool(store.get, isin.upper(), start)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(HTTPStatus.BAD_GATEWAY, "The price provider is not reachable") from exc
        return PriceSeries(
            lei=lei,
            isin=history.isin,
            ticker=history.listing.ticker if history.listing else None,
            currency=history.listing.currency if history.listing else None,
            provider=history.provider,
            fetched_at=history.fetched_at,
            bars=[PriceBar(**b.__dict__) for b in history.bars],
            insider=insider,
        )

    return router
