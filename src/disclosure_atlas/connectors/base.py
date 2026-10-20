"""Connector contract. One connector = one authority x one dataset, in one module.

Adding a source means adding a module in this package that defines a ``Connector`` subclass
decorated with ``@register``; discovery is automatic, so parallel work never edits a shared list.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, ClassVar

from ..http import PoliteClient
from ..models import Dataset, InsiderTransaction, NetShortPosition
from ..privacy import pseudonym

Record = InsiderTransaction | NetShortPosition


@dataclass(frozen=True)
class RawDocument:
    """Exactly what an authority returned for one request; stored unchanged in bronze."""

    source: str
    url: str
    params: dict[str, Any]
    content: bytes
    content_type: str | None
    fetched_at: datetime
    suffix: str = ".bin"
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


@dataclass(frozen=True)
class ParseContext:
    pseudonym_key: bytes

    def person_id(self, name: str | None, *, scope: str) -> str:
        return pseudonym(name, scope=scope, key=self.pseudonym_key)


class Connector(ABC):
    name: ClassVar[str]
    dataset: ClassVar[Dataset]
    jurisdiction: ClassVar[str]
    authority: ClassVar[str]
    homepage: ClassVar[str]
    parser_version: ClassVar[int] = 1

    def __init__(self, http: PoliteClient | None = None) -> None:
        self.http = http or PoliteClient()

    @property
    def parser_id(self) -> str:
        return f"{self.name}@{self.parser_version}"

    @abstractmethod
    def fetch(self, start: date, end: date) -> Iterator[RawDocument]:
        """Yield raw documents covering publications between start and end (inclusive)."""

    @abstractmethod
    def parse(self, doc: RawDocument, ctx: ParseContext) -> Iterator[Record]:
        """Turn one raw document into normalized records. Must be pure: no network access."""


_REGISTRY: dict[str, type[Connector]] = {}


def register(cls: type[Connector]) -> type[Connector]:
    if cls.name in _REGISTRY and _REGISTRY[cls.name] is not cls:
        raise ValueError(f"duplicate connector name: {cls.name}")
    _REGISTRY[cls.name] = cls
    return cls


def date_windows(start: date, end: date, days: int) -> Iterator[tuple[date, date]]:
    """Consecutive inclusive windows of at most ``days`` days."""
    cursor = start
    while cursor <= end:
        window_end = min(cursor + timedelta(days=days - 1), end)
        yield cursor, window_end
        cursor = window_end + timedelta(days=1)
