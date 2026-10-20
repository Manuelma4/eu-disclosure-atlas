"""Netherlands - AFM register of managers' transactions (MAR Article 19).

Source notes and quirks: docs/sources/nl_afm_pdmr.md. In short:
- The register export (XML) lists every notification since 2005 with its id, issuer, LEI, notifier, the PDMR a
  closely associated person is linked to, the position and the transaction date, but no transaction lines.
- The lines (instrument, ISIN, category, type, venue, price, quantity, currency) are on one HTML detail page per
  notification; the connector stores those pages in bronze and parses them.
- There is no publication date: windows select notifications by transaction date, reaching back
  ``LATE_FILING_DAYS`` so that notifications published after an earlier run are still picked up.
- Values are Dutch ("Verwerving", "Koop", "Gewoon aandeel"); the column "Unit" holds the currency.
"""

from __future__ import annotations

import html
import logging
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import httpx

from ..models import Dataset, InsiderTransaction, InstrumentType, Nature
from ..normalize import (
    clean,
    normalize_instrument,
    normalize_nature,
    normalize_role,
    parse_number,
    record_id,
    valid_isin,
    valid_lei,
    yes,
)
from ..privacy import is_legal_entity
from .base import Connector, ParseContext, RawDocument, register

log = logging.getLogger(__name__)

REGISTER_URL = "https://www.afm.nl/en/sector/registers/meldingenregisters/transacties-leidinggevenden-mar19-"
DETAIL_URL = f"{REGISTER_URL}/details"
EXPORT_URL = "https://www.afm.nl/export.aspx"
EXPORT_TYPE = "0ee836dc-5520-459d-bcf4-a4a689de6614"
LATE_FILING_DAYS = 14
OTC = "otc"
ACQUISITION, DISPOSAL = "verwerving", "vervreemding"
MISSING_DOCUMENT_STATUSES = {403, 404, 410}

_LABEL = re.compile(
    r'detail-list__label">\s*([^<]+?)\s*</span>\s*<span class="cc-em--detail-list__value">\s*<span>(.*?)</span>', re.S
)
_TRANSACTIONS = re.compile(r">\s*Transactions\s*</h2>(.*?)(?:>\s*Aggregated information\s*</h2>|$)", re.S)
_ROW = re.compile(r"<tr>(.*?)</tr>", re.S)
_CELL = re.compile(r'<td><span class="cc-mobile-title">(.*?)</span>(.*?)</td>', re.S)
_TAGS = re.compile(r"<[^>]+>")

# Transaction types of the register (closed vocabulary, Dutch), checked in order. The direction comes from the
# category (Verwerving = acquisition, Vervreemding = disposal) unless the nature is neutral.
_NATURES: list[tuple[re.Pattern[str], Nature]] = [
    (re.compile(r"\buitlen|\bontlen|\blening", re.I), Nature.LENDING),
    (re.compile(r"\bverpand|\bpand\b", re.I), Nature.PLEDGE),
    (re.compile(r"\bschenking|\bgift\b", re.I), Nature.GIFT),
    (re.compile(r"\berf(enis|rechtelijk)|\bnalatenschap", re.I), Nature.INHERITANCE),
    (re.compile(r"\buitoefening", re.I), Nature.EXERCISE),
    (re.compile(r"\bomwisseling van soort|\bconversie|\bomzetting", re.I), Nature.CONVERSION),
    (re.compile(r"\bomwisseling|\bruil", re.I), Nature.EXCHANGE),
    (re.compile(r"\bfusie", re.I), Nature.MERGER),
    (re.compile(r"\btoeken|\btoekenning|\btoegekend", re.I), Nature.GRANT),
    (re.compile(r"\binschrijving|\bclaimemissie", re.I), Nature.SUBSCRIPTION),
    (re.compile(r"\bverkoop", re.I), Nature.SELL),
    (re.compile(r"\bkoop\b", re.I), Nature.BUY),
]
_NEUTRAL = {Nature.LENDING, Nature.PLEDGE}

# Dutch instrument names, checked before the shared English vocabulary. Conditional or restricted share awards
# and rights are rights to shares, not shares.
_INSTRUMENTS: list[tuple[re.Pattern[str], InstrumentType]] = [
    (re.compile(r"\b(conditional|restricted|performance|deferred)\b.*\b(award|right|unit)s?\b", re.I),
     InstrumentType.OTHER),
    (re.compile(r"\bconverteerbare?\b", re.I), InstrumentType.CONVERTIBLE),
    (re.compile(r"\bobligatie", re.I), InstrumentType.BOND),
    (re.compile(r"\bclaimrecht|\bclaim\b", re.I), InstrumentType.SUBSCRIPTION_RIGHT),
    (re.compile(r"\bopties?\b|\bpersoneelsoptie", re.I), InstrumentType.OPTION),
    (re.compile(r"\baandeel|\baandelen|\bcertificaten?\b|\bdepositor[iy]\s+receipts?", re.I), InstrumentType.SHARE),
]

# Dutch (and Dutch-English) positions, rewritten into words the shared role vocabulary knows. A two-tier
# company's management board is its executive body, a one-tier company's non-executive directors sit on its
# board, and a "Business Group President" is an executive, not a chair.
_ROLE_WORDS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bvoorzitter\b", re.I), "chair"),
    (re.compile(r"\bnon[-\u2010 ]?executive\b", re.I), "board"),
    (re.compile(r"\bpresident\b(?!\s+(of\s+the\s+)?(supervisory\s+)?board)", re.I), "executive"),
    (re.compile(r"\b(management|executive)\s+board\b", re.I), "executive"),
    (re.compile(r"\b(raad van commissarissen|commissaris\w*|lid rvc|rvc)\b", re.I), "supervisory board"),
    (re.compile(r"\b(raad van bestuur|bestuurder\w*|statutair directeur|directeur)\b", re.I), "executive"),
]


def transaction_lines(content: str) -> list[dict[str, str]]:
    """Rows of the 'Transactions' table; the 'Aggregated information' table repeats them with shifted labels."""
    section = _TRANSACTIONS.search(content)
    if not section:
        return []
    lines = []
    for row in _ROW.findall(section.group(1)):
        cells = {_text(label): _text(value) for label, value in _CELL.findall(row)}
        if cells:
            lines.append(cells)
    return lines


def details(content: str) -> dict[str, str]:
    return {_text(label): _text(value) for label, value in _LABEL.findall(content)}


def _text(fragment: str) -> str:
    return clean(html.unescape(_TAGS.sub(" ", fragment))) or ""


def nature_of(transaction_type: str, category: str) -> tuple[Nature, int]:
    category_direction = {ACQUISITION: 1, DISPOSAL: -1}.get(category.casefold(), 0)
    for pattern, nature in _NATURES:
        if pattern.search(transaction_type):
            return nature, 0 if nature in _NEUTRAL else category_direction
    nature, direction = normalize_nature(transaction_type)
    if nature is Nature.BUY and transaction_type.casefold() == ACQUISITION:
        # "Verwerving" as a type is the generic acquisition (often a free award), not necessarily a purchase.
        nature = Nature.OTHER
    return nature, category_direction or direction


def instrument_of(raw: str | None) -> InstrumentType:
    for pattern, instrument in _INSTRUMENTS:
        if pattern.search(raw or ""):
            return instrument
    return normalize_instrument(raw)


def role_text(raw: str | None) -> str:
    text = raw or ""
    for pattern, word in _ROLE_WORDS:
        text = pattern.sub(word, text)
    return text


def mentions_name(text: str | None, *names: str | None) -> bool:
    """True when the text contains a word of a person's name (positions of relatives sometimes name them)."""
    words = {w.casefold() for name in names for w in re.findall(r"[^\W\d_]{3,}", name or "")}
    return bool(words & {w.casefold() for w in re.findall(r"[^\W\d_]{3,}", text or "")})


def index_entries(content: bytes) -> list[dict[str, str]]:
    """Notifications of the register export (XML): one dict of child-element text per entry."""
    root = ET.fromstring(content)
    return [{child.tag: (child.text or "") for child in entry} for entry in root.findall("vermelding")]


def entry_date(entry: dict[str, str]) -> date | None:
    try:
        return datetime.strptime(entry.get("transactiedatum", ""), "%m/%d/%Y %I:%M:%S %p").date()
    except ValueError:
        return None


@register
class NetherlandsAFMPDMR(Connector):
    name = "nl_afm_pdmr"
    dataset = Dataset.INSIDER_TRANSACTIONS
    jurisdiction = "NL"
    authority = "Autoriteit Financiële Markten (AFM)"
    homepage = REGISTER_URL

    def fetch(self, start: date, end: date) -> Iterator[RawDocument]:
        response = self.http.get(EXPORT_URL, params={"type": EXPORT_TYPE, "format": "xml"})
        lower = start - timedelta(days=LATE_FILING_DAYS)
        for entry in index_entries(response.content):
            transaction_date = entry_date(entry)
            melding_id = entry.get("meldingid")
            if not melding_id or transaction_date is None or not lower <= transaction_date <= end:
                continue
            yield from self._fetch_notification(melding_id, transaction_date)

    def _fetch_notification(self, melding_id: str, transaction_date: date) -> Iterator[RawDocument]:
        params = {"id": melding_id}
        try:
            response = self.http.get(DETAIL_URL, params=params)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in MISSING_DOCUMENT_STATUSES:
                log.warning("%s: notification %s not available (%s)", self.name, melding_id,
                            exc.response.status_code)
                return
            raise
        yield RawDocument(
            source=self.name,
            url=str(response.url),
            params=params,
            content=response.content,
            content_type=response.headers.get("content-type"),
            fetched_at=datetime.now(UTC),
            suffix=".html",
            meta={"kind": "notification", "meldingid": melding_id, "transaction_date": transaction_date.isoformat()},
        )

    def parse(self, doc: RawDocument, ctx: ParseContext) -> Iterator[InsiderTransaction]:
        content = doc.content.decode("utf-8", errors="replace")
        melding_id = doc.params.get("id") or doc.meta.get("meldingid")
        fields = details(content)
        issuer = fields.get("Issuing institution") or "unknown issuer"
        lei = valid_lei(fields.get("LEI"))
        scope = lei or issuer
        notifier_name = fields.get("Notifiable")
        affiliated_to = fields.get("Closely affiliated")
        pdmr_name = affiliated_to or notifier_name
        notifier_is_entity = is_legal_entity(notifier_name)
        pdmr_id = ctx.person_id(pdmr_name, scope=scope)
        notifier_id = ctx.person_id(notifier_name, scope=scope)
        position = fields.get("Position/Status") or None
        if mentions_name(position, notifier_name if not notifier_is_entity else None, pdmr_name):
            position = None  # never store a natural person's name, even inside a free-text field
        transaction_date = _date_of(doc)
        source_url = f"{DETAIL_URL}?id={melding_id}" if melding_id else self.homepage

        for index, line in enumerate(transaction_lines(content)):
            category = line.get("Transaction category", "")
            transaction_type = line.get("Transaction type", "")
            nature, direction = nature_of(transaction_type, category)
            instrument_raw = line.get("Instrument type") or None
            volume = parse_number(line.get("Quantity"))
            price = parse_number(line.get("Price"))
            venue = line.get("Trading place") or None
            programme = line.get("Stock option program")
            yield InsiderTransaction(
                record_id=record_id(self.name, melding_id, index),
                source=self.name,
                jurisdiction=self.jurisdiction,
                source_url=source_url,
                published_at=None,
                transaction_date=transaction_date,
                issuer_name=issuer,
                issuer_lei=lei,
                pdmr_id=pdmr_id,
                pdmr_role=normalize_role(role_text(position), separators=r";|\n"),
                pdmr_role_raw=position,
                notifier_id=notifier_id,
                notifier_name=notifier_name if notifier_is_entity else None,
                notifier_is_legal_entity=notifier_is_entity,
                closely_associated=bool(affiliated_to),
                linked_to_share_option_programme=None if not programme else yes(programme),
                nature=nature,
                nature_raw=" / ".join(p for p in (category, transaction_type) if p),
                direction=direction,
                instrument_type=instrument_of(instrument_raw),
                instrument_type_raw=instrument_raw,
                isin=valid_isin(line.get("ISIN")),
                volume=volume,
                price=price,
                currency=line.get("Unit") or None,
                amount=volume * price if volume is not None and price is not None else None,
                venue=venue,
                on_venue=None if venue is None else venue.casefold() != OTC,
                fetched_at=doc.fetched_at,
                parser=self.parser_id,
            )


def _date_of(doc: RawDocument) -> date | None:
    """The transaction date: from the fetch metadata, else from the notification id (it starts with YYYYMMDD)."""
    if doc.meta.get("transaction_date"):
        return date.fromisoformat(doc.meta["transaction_date"])
    melding_id = str(doc.params.get("id") or "")
    try:
        return datetime.strptime(melding_id[:8], "%Y%m%d").date()
    except ValueError:
        return None
