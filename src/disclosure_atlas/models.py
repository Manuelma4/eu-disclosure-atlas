"""Unified record schemas shared by every connector.

The insider schema follows the EU-wide notification template of Commission Implementing
Regulation (EU) 2016/523 (MAR Article 19), so national sources map onto the same fields.
The short-position schema follows the public disclosure fields of the Short Selling
Regulation (EU) No 236/2012, Article 6.

Natural persons never appear by name: they are pseudonymous ids (see ``privacy.py``).
"""

from __future__ import annotations

import types
import typing
from datetime import date, datetime
from enum import Enum, StrEnum

import pyarrow as pa
from pydantic import BaseModel, ConfigDict


class Dataset(StrEnum):
    INSIDER_TRANSACTIONS = "insider_transactions"
    NET_SHORT_POSITIONS = "net_short_positions"


class Role(StrEnum):
    """Most senior position of the person discharging managerial responsibilities (PDMR)."""

    CEO = "ceo"
    CFO = "cfo"
    CHAIR = "chair"
    EXECUTIVE = "executive"
    BOARD_MEMBER = "board_member"
    OTHER = "other"
    UNKNOWN = "unknown"


class Nature(StrEnum):
    """Normalized nature of the transaction (template field 4b)."""

    BUY = "buy"
    SELL = "sell"
    SUBSCRIPTION = "subscription"
    GRANT = "grant"
    EXERCISE = "exercise"
    CONVERSION = "conversion"
    EXCHANGE = "exchange"
    GIFT = "gift"
    INHERITANCE = "inheritance"
    PLEDGE = "pledge"
    LENDING = "lending"
    INTERNAL_TRANSFER = "internal_transfer"
    MERGER = "merger"
    OTHER = "other"


class InstrumentType(StrEnum):
    SHARE = "share"
    OPTION = "option"
    WARRANT = "warrant"
    SUBSCRIPTION_RIGHT = "subscription_right"
    CONVERTIBLE = "convertible"
    BOND = "bond"
    SWAP = "swap"
    OTHER = "other"


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, use_enum_values=True)


class InsiderTransaction(_Record):
    """One managers' transaction (MAR Article 19), one row per transaction line."""

    record_id: str
    source: str
    jurisdiction: str
    source_url: str | None = None
    published_at: datetime | None = None
    transaction_date: date | None = None
    issuer_name: str
    issuer_lei: str | None = None
    pdmr_id: str
    pdmr_role: Role = Role.UNKNOWN
    pdmr_role_raw: str | None = None
    notifier_id: str
    notifier_name: str | None = None
    notifier_is_legal_entity: bool = False
    closely_associated: bool = False
    is_amendment: bool = False
    amendment_details: str | None = None
    linked_to_share_option_programme: bool | None = None
    nature: Nature
    nature_raw: str
    direction: int = 0
    instrument_type: InstrumentType = InstrumentType.OTHER
    instrument_type_raw: str | None = None
    instrument_name: str | None = None
    isin: str | None = None
    volume: float | None = None
    price: float | None = None
    currency: str | None = None
    amount: float | None = None
    venue: str | None = None
    on_venue: bool | None = None
    is_superseded: bool = False
    status_raw: str | None = None
    fetched_at: datetime
    parser: str


class NetShortPosition(_Record):
    """One public net short position (SSR Article 6: >= 0.5% of issued share capital)."""

    record_id: str
    source: str
    jurisdiction: str
    source_url: str | None = None
    published_at: datetime | None = None
    position_date: date
    holder_name: str  # a pseudonym ("p_...") when the holder is a natural person
    holder_is_natural_person: bool = False
    holder_lei: str | None = None
    issuer_name: str
    issuer_lei: str | None = None
    isin: str | None = None
    position_pct: float | None  # None when the source only says "below 0.5%"
    below_threshold: bool = False  # the notification that the position fell below 0.5%
    is_superseded: bool = False
    fetched_at: datetime
    parser: str


RECORD_TYPES: dict[Dataset, type[_Record]] = {
    Dataset.INSIDER_TRANSACTIONS: InsiderTransaction,
    Dataset.NET_SHORT_POSITIONS: NetShortPosition,
}

_ARROW_TYPES: dict[type, pa.DataType] = {
    str: pa.string(),
    int: pa.int64(),
    float: pa.float64(),
    bool: pa.bool_(),
    date: pa.date32(),
    datetime: pa.timestamp("us", tz="UTC"),
}


def _arrow_type(annotation: object) -> pa.DataType:
    if typing.get_origin(annotation) in (typing.Union, types.UnionType):
        (inner,) = [a for a in typing.get_args(annotation) if a is not type(None)]
        return _arrow_type(inner)
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        return pa.string()
    return _ARROW_TYPES[annotation]  # type: ignore[index]


def arrow_schema(model: type[_Record]) -> pa.Schema:
    """Arrow schema derived from a record model, so Parquet types never drift from the model."""
    return pa.schema(
        [pa.field(name, _arrow_type(field.annotation)) for name, field in model.model_fields.items()]
    )
