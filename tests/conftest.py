"""Shared fixtures. All data here is synthetic: no real person appears in the test suite."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from disclosure_atlas.connectors.base import ParseContext, RawDocument
from disclosure_atlas.normalize import _alnum_to_digits

FI_HEADER = [
    "Publication date", "Issuer", "LEI-code", "Notifier", "Person discharging managerial responsibilities",
    "Position", "Closely associated", "Amendment", "Details of amendment", "Initial notification",
    "Linked to share option programme", "Nature of transaction", "Intrument type", "Instrument name", "ISIN",
    "Transaction date", "Volume", "Unit", "Price", "Currency", "Trading venue", "Status",
]
NBSP = "\xa0"


def make_lei(prefix18: str) -> str:
    check = 98 - int(_alnum_to_digits(prefix18 + "00")) % 97
    return f"{prefix18}{check:02d}"


def make_isin(prefix11: str) -> str:
    digits = _alnum_to_digits(prefix11)
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch) * (2 if i % 2 == 0 else 1)
        total += n - 9 if n > 9 else n
    return f"{prefix11}{(10 - total % 10) % 10}"


EXAMPLE_LEI = make_lei("549300EXAMPLE00001")
EXAMPLE_ISIN = make_isin("SE000EXAMPL")


def fi_row(**overrides: str) -> dict[str, str]:
    row = {
        "Publication date": "02/10/2026 23:24:55",
        "Issuer": "Example Robotics AB (publ)",
        "LEI-code": EXAMPLE_LEI,
        "Notifier": "Anna Example",
        "Person discharging managerial responsibilities": "Anna Example",
        "Position": NBSP.join(["Chief", "Executive", "Officer", "(CEO)/Managing", "Directory"]),
        "Closely associated": "",
        "Amendment": "",
        "Details of amendment": "",
        "Initial notification": "Yes",
        "Linked to share option programme": "",
        "Nature of transaction": "Acquisition",
        "Intrument type": "Share",
        "Instrument name": "Example Robotics AB (publ)",
        "ISIN": EXAMPLE_ISIN,
        "Transaction date": "01/10/2026 00:00:00",
        "Volume": "2750.0",
        "Unit": "Quantity",
        "Price": "61.50",
        "Currency": "SEK",
        "Trading venue": "NASDAQ STOCKHOLM AB",
        "Status": "Current",
    }
    row.update(overrides)
    return row


def fi_csv(rows: list[dict[str, str]]) -> bytes:
    """Mimic the FI export: UTF-16LE without BOM, ';'-separated, trailing ';', CRLF."""
    lines = [";".join(FI_HEADER) + ";"] + [";".join(r[h] for h in FI_HEADER) + ";" for r in rows]
    return ("\r\n".join(lines) + "\r\n").encode("utf-16-le")


def fi_doc(rows: list[dict[str, str]], fetched_at: datetime | None = None) -> RawDocument:
    return RawDocument(
        source="se_fi_pdmr",
        url="https://marknadssok.fi.se/publiceringsklient/en-GB/Search/Search",
        params={},
        content=fi_csv(rows),
        content_type="text/csv",
        fetched_at=fetched_at or datetime(2026, 10, 3, 6, 0, tzinfo=UTC),
        suffix=".csv",
    )


@pytest.fixture
def ctx() -> ParseContext:
    return ParseContext(pseudonym_key=b"test-key")


@pytest.fixture(autouse=True)
def _no_env_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ATLAS_PSEUDONYM_KEY", raising=False)
