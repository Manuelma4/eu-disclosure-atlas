"""Public response models. They define the OpenAPI document and the generated TypeScript client."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class Transaction(BaseModel):
    id: str = Field(description="Stable record id")
    source: str = Field(description="Connector, e.g. se_fi_pdmr")
    country: str = Field(description="ISO 3166-1 alpha-2 country of the publishing authority")
    published_at: datetime | None = Field(description="Publication by the authority (UTC)")
    first_seen_at: datetime = Field(description="When the Atlas first ingested the record (UTC)")
    transaction_date: date | None
    issuer_name: str
    issuer_lei: str | None
    person_id: str = Field(description="Pseudonymous id of the manager, stable within the issuer")
    role: str = Field(description="ceo, cfo, chair, executive, board_member, other or unknown")
    role_raw: str | None
    notifier_name: str | None = Field(description="Only for legal entities (e.g. a holding company)")
    closely_associated: bool
    nature: str = Field(description="buy, sell, subscription, grant, exercise, ...")
    direction: int = Field(description="+1 holding increases, -1 decreases, 0 neutral")
    instrument_type: str
    instrument_name: str | None
    isin: str | None
    volume: float | None
    price: float | None
    currency: str | None
    amount: float | None = Field(description="volume × price in the transaction currency")
    amount_eur: float | None = Field(description="amount converted with the ECB reference rate of the day")
    venue: str | None
    open_market: bool = Field(description="Buy or sell of shares on a trading venue")
    is_amendment: bool
    filing_lag_days: int | None = Field(description="Calendar days between transaction and publication")


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None = Field(description="Pass as ?cursor= to get the next page")
    snapshot: str = Field(description="Data version the page was read from")


class CountryStat(BaseModel):
    country: str
    transactions: int
    issuers: int
    late_share: float | None = Field(description="Share published more than 5 calendar days after the trade")


class Overview(BaseModel):
    snapshot: str
    transactions: int
    issuers: int
    countries: int
    sources: int
    last_published_at: datetime | None
    last_ingested_at: datetime | None
    open_market_buys_30d: int
    open_market_sells_30d: int
    buy_eur_30d: float | None
    sell_eur_30d: float | None
    late_share_365d: float | None
    by_country: list[CountryStat]
    largest_buys_30d: list[Transaction]
    latest: list[Transaction]


class IssuerSummary(BaseModel):
    lei: str
    name: str
    country: str
    transactions: int
    last_published_at: datetime | None
    net_open_market_eur_365d: float | None


class MonthPoint(BaseModel):
    month: date
    buy_eur: float | None
    sell_eur: float | None
    buys: int
    sells: int


class FlowPoint(BaseModel):
    period: date = Field(description="First day of the bucket")
    buy_eur: float | None
    sell_eur: float | None
    buys: int
    sells: int


class RoleStat(BaseModel):
    role: str
    transactions: int
    people: int


class IssuerDetail(IssuerSummary):
    distinct_buyers_90d: int
    distinct_sellers_90d: int
    monthly: list[MonthPoint]
    roles: list[RoleStat]
    recent: list[Transaction]


class SourceStatus(BaseModel):
    name: str
    country: str
    dataset: str
    authority: str
    homepage: str
    records: int
    last_published_at: datetime | None
    last_ingested_at: datetime | None


class Health(BaseModel):
    status: str
    snapshot: str
    records: int
