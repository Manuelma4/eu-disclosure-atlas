from datetime import UTC, date, datetime

import pytest
from conftest import EXAMPLE_ISIN, EXAMPLE_LEI, NBSP

from disclosure_atlas.models import InstrumentType, Nature, Role
from disclosure_atlas.normalize import (
    clean,
    normalize_instrument,
    normalize_nature,
    normalize_role,
    parse_date,
    parse_datetime,
    parse_number,
    record_id,
    valid_isin,
    valid_lei,
)


def nb(text: str) -> str:
    """FI style: non-breaking spaces inside one role."""
    return NBSP.join(text.split(" "))


def test_clean_collapses_nbsp_and_empty():
    assert clean(f"  Other{NBSP}senior{NBSP}executive ") == "Other senior executive"
    assert clean("   ") is None
    assert clean(None) is None


@pytest.mark.parametrize(
    "text, expected",
    [("61.50", 61.5), ("2750,0", 2750.0), ("1 234,56", 1234.56), ("1,234.56", 1234.56), ("1.234,56", 1234.56),
     ("100000.0", 100000.0), ("", None), ("n/a", None)],
)
def test_parse_number(text, expected):
    assert parse_number(text) == expected


def test_parse_datetime_converts_local_time_to_utc():
    parsed = parse_datetime("02/10/2026 23:24:55", "%d/%m/%Y %H:%M:%S", "Europe/Stockholm")
    assert parsed == datetime(2026, 10, 2, 21, 24, 55, tzinfo=UTC)  # CEST = UTC+2
    winter = parse_datetime("15/01/2026 10:00:00", "%d/%m/%Y %H:%M:%S", "Europe/Stockholm")
    assert winter == datetime(2026, 1, 15, 9, 0, tzinfo=UTC)  # CET = UTC+1
    assert parse_date("30/09/2026", "%d/%m/%Y") == date(2026, 9, 30)


def test_identifier_checksums():
    assert valid_isin("US0378331005") == "US0378331005"
    assert valid_isin("us0378331005") == "US0378331005"
    assert valid_isin("US0378331006") is None
    assert valid_isin(EXAMPLE_ISIN) == EXAMPLE_ISIN
    assert valid_isin("") is None
    assert valid_lei(EXAMPLE_LEI) == EXAMPLE_LEI
    assert valid_lei(EXAMPLE_LEI[:-1] + str((int(EXAMPLE_LEI[-1]) + 1) % 10)) is None
    assert valid_lei("not-a-lei") is None


@pytest.mark.parametrize(
    "raw, expected",
    [
        (nb("Chief Executive Officer (CEO)/Managing Directory"), Role.CEO),
        (nb("Chief Financial Officer (CFO)"), Role.CFO),
        (nb("Chairman of the Board of Directors"), Role.CHAIR),
        (nb("Member of the Board of Directors"), Role.BOARD_MEMBER),
        (nb("Deputy Member of the Board of Directors"), Role.BOARD_MEMBER),
        (nb("Other senior executive"), Role.EXECUTIVE),
        (nb("Deputy CEO/Deputy Managing Director"), Role.EXECUTIVE),
        (nb("Other member of the company's administrative, management or supervisory body"), Role.OTHER),
        (nb("Employee Representative of the Board of Directors or deputy Employee Representative of the Board"
            " of Directors"), Role.BOARD_MEMBER),
        (nb("Deputy CEO/Deputy Managing Director") + ", " + nb("Chief Financial Officer (CFO)"), Role.CFO),
        (nb("Chief Executive Officer (CEO)/Managing Directory") + ", " + nb("Member of the Board of Directors"),
         Role.CEO),
        (nb("Chairman of the Board of Directors") + ", " + nb("Chief Financial Officer (CFO)"), Role.CFO),
        ("Président-directeur général", Role.CEO),
        ("Directeur général délégué", Role.EXECUTIVE),
        ("Président du conseil d'administration", Role.CHAIR),
        ("Administrateur", Role.BOARD_MEMBER),
        ("Directeur financier", Role.CFO),
        ("", Role.UNKNOWN),
    ],
)
def test_normalize_role(raw, expected):
    assert normalize_role(raw) is expected


@pytest.mark.parametrize(
    "raw, nature, direction",
    [
        ("Acquisition", Nature.BUY, 1),
        ("Disposal", Nature.SELL, -1),
        ("Subscription", Nature.SUBSCRIPTION, 1),
        ("Allotment", Nature.GRANT, 1),
        ("Exercise increase", Nature.EXERCISE, 1),
        ("Exercise decrease", Nature.EXERCISE, -1),
        ("Internal transaction – Acquisition", Nature.INTERNAL_TRANSFER, 1),
        ("Internal transaction – Disposal", Nature.INTERNAL_TRANSFER, -1),
        ("Gift received", Nature.GIFT, 1),
        ("Gift given", Nature.GIFT, -1),
        ("Inheritance received", Nature.INHERITANCE, 1),
        ("Loan granted", Nature.LENDING, 0),
        ("Pledging", Nature.PLEDGE, 0),
        ("Conversion decrease", Nature.CONVERSION, -1),
        ("Exchange increase", Nature.EXCHANGE, 1),
        ("Merger increase", Nature.MERGER, 1),
        ("Issue of instrument", Nature.OTHER, 0),
        ("Acquisition d'actions", Nature.BUY, 1),
        ("Cession", Nature.SELL, -1),
    ],
)
def test_normalize_nature(raw, nature, direction):
    assert normalize_nature(raw) == (nature, direction)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Share", InstrumentType.SHARE), ("BTA", InstrumentType.SHARE), ("Interim share", InstrumentType.SHARE),
        ("Subcription warrant", InstrumentType.WARRANT), ("Option", InstrumentType.OPTION),
        ("Call option", InstrumentType.OPTION), ("Synthetic option", InstrumentType.OPTION),
        ("Subscription right/Unit right", InstrumentType.SUBSCRIPTION_RIGHT), ("Swap", InstrumentType.SWAP),
        ("Bond", InstrumentType.BOND), ("Convertible", InstrumentType.CONVERTIBLE),
        ("Action", InstrumentType.SHARE), ("", InstrumentType.OTHER),
    ],
)
def test_normalize_instrument(raw, expected):
    assert normalize_instrument(raw) is expected


def test_record_id_is_deterministic_and_sensitive():
    assert record_id("src", 1, None, "x") == record_id("src", 1, None, "x")
    assert record_id("src", 1, None, "x") != record_id("src", 1, None, "y")
    assert len(record_id("src")) == 32
