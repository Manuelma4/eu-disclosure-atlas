"""Spain - CNMV notifications of managers and closely associated persons (MAR Article 19).

Source notes and quirks: docs/sources/es_cnmv_pdmr.md. In short:
- A public search (``Directivos-Resultado``) lists notifications registered between two dates, ten per page,
  with the issuer, the registration number and a link to the notification.
- Each notification is a PDF that the CNMV generates from its own filing form (the Implementing Regulation
  (EU) 2016/523 template, bilingual Spanish/English labels), with one text line per transaction.
- The listing gives the registration date only, not a time.
"""

from __future__ import annotations

import html
import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from ..models import Dataset, InsiderTransaction, Role
from ..normalize import (
    clean,
    normalize_instrument,
    normalize_nature,
    normalize_role,
    parse_number,
    record_id,
    valid_isin,
    valid_lei,
)
from ..privacy import is_legal_entity
from .base import Connector, ParseContext, RawDocument, date_windows, register
from .fr_amf_pdmr import pdf_text, redact_position, scrub_names

log = logging.getLogger(__name__)

PORTAL_URL = "https://www.cnmv.es/portal/consultas"
LIST_URL = f"{PORTAL_URL}/directivos-resultado.aspx"
SEARCH_PAGE = "https://www.cnmv.es/portal/Consultas/Directivos-Consulta.aspx"
TIMEZONE = "Europe/Madrid"
MISSING_DOCUMENT_STATUSES = {404, 410, 451}
# Market identifier codes that mean "outside a trading venue" (MiFIR: XOFF for instruments admitted to trading,
# XXXX for the others).
OFF_VENUE_CODES = {"XOFF", "XXXX"}


class TemplateError(ValueError):
    """The PDF does not follow the CNMV notification form."""


# --- Listing ------------------------------------------------------------------------------------

_ITEM_SPLIT = re.compile(r'class="blocks-single"')
_ITEM_DATE = re.compile(r'liFechaRegistro"[^>]*>\s*(\d{2}/\d{2}/\d{4})')
_ITEM_ISSUER = re.compile(r'hlTituloCabecera" href="datosentidad\.aspx\?nif=([^"]*)"[^>]*>\s*<span[^>]*>([^<]*)<')
_ITEM_LINK = re.compile(r'subtituloRegistroEnlace" href="([^"]+)"[^>]*>([^<]*)<')
_ITEM_NUMBER = re.compile(r"N[úu]mero de registro:\s*(\d+)")
_PAGINATION = re.compile(r'lblInfoPaginacion"[^>]*>\D*(\d+)\D+(\d+)\s*<')


@dataclass(frozen=True)
class ListingItem:
    registered: date
    issuer: str | None
    nif: str | None
    url: str
    reason: str | None
    number: str | None


def parse_listing(text: str) -> tuple[list[ListingItem], int, int]:
    """(items, current page, number of pages) of one result page; pages are 1-based, (0, 0) when empty."""
    items = []
    for block in _ITEM_SPLIT.split(text)[1:]:
        when, link = _ITEM_DATE.search(block), _ITEM_LINK.search(block)
        if not when or not link:
            continue
        issuer, number = _ITEM_ISSUER.search(block), _ITEM_NUMBER.search(block)
        reason = clean(html.unescape(link.group(2)).partition(":")[2])
        items.append(ListingItem(
            registered=datetime.strptime(when.group(1), "%d/%m/%Y").date(),
            issuer=clean(html.unescape(issuer.group(2))) if issuer else None,
            nif=clean(issuer.group(1)) if issuer else None,
            url=html.unescape(link.group(1)),
            reason=reason,
            number=number.group(1) if number else None,
        ))
    pages = _PAGINATION.search(text)
    current, total = (int(pages.group(1)), int(pages.group(2))) if pages else (0, 0)
    return items, current, total


# --- Notification form --------------------------------------------------------------------------

_SECTION_1 = re.compile(r"^a\) Nombre y apellidos", re.I)
_SECTION_2 = re.compile(r"^2\. MOTIVO DE LA NOTIFICACI", re.I)
_CHECKBOX = re.compile(r"^\[\s*(?P<mark>[^\]\s]?)\s*\]\s*(?P<label>.*)$")
_POSITION = re.compile(r"^a\) Cargo", re.I)
_AMENDMENT = re.compile(r"^b\) Notificaci[óo]n inicial", re.I)
_SECTION_3 = re.compile(r"^3\. DATOS DEL EMISOR", re.I)
_ISSUER = re.compile(r"^a\) Identificaci[óo]n", re.I)
_LEI = re.compile(r"^b\) LEI:?\s*(?P<value>.*)$", re.I)
_SECTION_4 = re.compile(r"^4\. DATOS DE LA OPERACI", re.I)
_ADDITIONAL = re.compile(r"^Otra informaci[óo]n", re.I)
_NOISE = re.compile(r"^(\d+ / \d+|Formulario|Standard form|Formulario Standard Form)$", re.I)
_TX_LINE = re.compile(
    r"^(?:(?P<code>\S*\d\S*) )?(?P<description>\D+?) (?P<date>\d{2}/\d{2}/\d{4}) (?P<venue>.+?) "
    r"(?P<volume>-?\d[\d.,]*)(?: (?P<price>-?\d[\d.,]*))?(?: (?P<currency>[A-Z]{3}))?$"
)
# Instruments offered by the CNMV form; the nature of the transaction follows the instrument on the same line.
_INSTRUMENTS = [
    "derechos de suscripción", "derecho de suscripción", "derechos", "derecho", "acciones", "acción", "opciones",
    "opción", "obligaciones convertibles", "obligación convertible", "obligaciones", "obligación", "bonos", "bono",
    "warrants", "warrant", "futuros", "futuro", "permutas", "permuta", "swap", "cfd", "participaciones",
    "participación", "otros instrumentos", "otros", "otro",
]


@dataclass
class Filing:
    declarant: str
    closely_associated: bool
    position: str | None
    is_amendment: bool
    issuer_name: str | None
    issuer_lei: str | None
    transactions: list[dict[str, Any]] = field(default_factory=list)


def split_description(description: str) -> tuple[str, str]:
    """'Acción Compra' -> ('Acción', 'Compra'): the longest known instrument prefix, else the first word."""
    text = clean(description) or ""
    folded = text.casefold()
    for instrument in sorted(_INSTRUMENTS, key=len, reverse=True):
        if folded.startswith(instrument + " "):
            return text[: len(instrument)], text[len(instrument) + 1:]
    first, _, rest = text.partition(" ")
    return first, rest


def parse_form(text: str) -> Filing:
    """Read the CNMV notification form from the PDF text; raises ``TemplateError`` when it cannot."""
    lines = [c for c in (clean(line) for line in text.splitlines()) if c and not _NOISE.match(c)]
    collect: str | None = None
    values: dict[str, list[str]] = {"declarant": [], "position": [], "amendment": [], "issuer": [], "lei": []}
    closely_associated: bool | None = None
    transactions: list[dict[str, Any]] = []
    in_transactions = False
    for line in lines:
        if _ADDITIONAL.match(line):
            break  # free text typed by the declarant, which can name anyone: never read
        if in_transactions:
            match = _TX_LINE.match(line)
            if match:
                transactions.append(match.groupdict())
            continue
        if _SECTION_1.match(line):
            collect = "declarant"
        elif _SECTION_2.match(line):
            collect = None
        elif checkbox := _CHECKBOX.match(line):
            if checkbox.group("mark") and re.search(r"vinculada|closely", checkbox.group("label"), re.I):
                closely_associated = True
            elif checkbox.group("mark") and closely_associated is None:
                closely_associated = False
            collect = None
        elif _POSITION.match(line):
            collect = "position"
        elif _AMENDMENT.match(line):
            collect = "amendment"
        elif _SECTION_3.match(line):
            collect = None
        elif _ISSUER.match(line):
            collect = "issuer"
        elif lei := _LEI.match(line):
            collect = "lei"
            if lei.group("value"):
                values["lei"].append(lei.group("value"))
        elif _SECTION_4.match(line):
            collect, in_transactions = None, True
        elif collect and not (collect == "issuer" and line.startswith("AUCTION")):
            values[collect].append(line)
    joined = {key: clean(" ".join(parts)) for key, parts in values.items()}
    if not joined["declarant"]:
        raise TemplateError("no declarant")
    if not transactions:
        raise TemplateError("no transaction line")
    amendment = (joined["amendment"] or "").casefold()
    return Filing(
        declarant=joined["declarant"],
        closely_associated=bool(closely_associated),
        position=joined["position"],
        is_amendment=amendment.startswith(("modific", "amend")),
        # Some declarants append the tax id: "Example Group, SA; NIF: A00000000".
        issuer_name=clean(re.sub(r"[;,]?\s*NIF:.*$", "", joined["issuer"] or "", flags=re.I)),
        issuer_lei=(joined["lei"] or "").split(" ")[0] or None,
        transactions=transactions,
    )


def read_pdf(content: bytes) -> Filing:
    try:
        text = pdf_text(content)
    except Exception as exc:  # unreadable PDF (pdfminer raises many types)
        raise TemplateError(f"unreadable PDF: {type(exc).__name__}") from exc
    return parse_form(text)


# --- Names in the position text -----------------------------------------------------------------

_POSITION_PARTS = re.compile(r"\s*[/,;–—(]\s*|\s+-\s*|\s*-\s+|\)")
_NAME_WORD = re.compile(r"^[^\W\d_](?:[^\W\d_'’.]|-(?=[^\W\d_]))*\.?$", re.UNICODE)


def names_in_position(position: str | None, issuer: str | None) -> list[str]:
    """Parts of a position that look like a person's name ('CONSEJERO - <name>', '<name>, PRESIDENTE').

    For closely associated persons the CNMV form's job title field usually names the manager they are
    linked to; those words must not be stored."""
    names = []
    for part in _POSITION_PARTS.split(position or ""):
        part = clean(part) or ""
        words = part.split(" ")
        if not 2 <= len(words) <= 6 or not all(_NAME_WORD.match(w) for w in words):
            continue
        if normalize_role(part) not in (Role.OTHER, Role.UNKNOWN) or is_legal_entity(part):
            continue
        if issuer and part.casefold() in issuer.casefold():
            continue
        names.append(part)
    return names


# --- Connector ----------------------------------------------------------------------------------

@register
class SpainCNMVPDMR(Connector):
    name = "es_cnmv_pdmr"
    dataset = Dataset.INSIDER_TRANSACTIONS
    jurisdiction = "ES"
    authority = "Comisión Nacional del Mercado de Valores (CNMV)"
    homepage = SEARCH_PAGE
    window_days = 7

    def fetch(self, start: date, end: date) -> Iterator[RawDocument]:
        for window_start, window_end in date_windows(start, end, self.window_days):
            yield from self._fetch_window(window_start, window_end)

    def _fetch_window(self, start: date, end: date) -> Iterator[RawDocument]:
        page = 0
        while True:
            params = {"fechad": start.strftime("%d/%m/%Y"), "fechah": end.strftime("%d/%m/%Y"), "page": page}
            response = self.http.get(LIST_URL, params=params)
            items, current, total = parse_listing(response.text)
            yield RawDocument(
                source=self.name,
                url=str(response.url),
                params=params,
                content=response.content,
                content_type=response.headers.get("content-type"),
                fetched_at=datetime.now(UTC),
                suffix=".html",
                meta={"kind": "listing", "window": [start.isoformat(), end.isoformat()], "page": current,
                      "pages": total, "rows": len(items)},
            )
            for item in items:
                yield from self._fetch_notification(item)
            if not items or current >= total:
                break
            page += 1

    def _fetch_notification(self, item: ListingItem) -> Iterator[RawDocument]:
        try:
            response = self.http.get(item.url)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in MISSING_DOCUMENT_STATUSES:
                log.warning("%s: notification %s not available (%s)", self.name, item.number,
                            exc.response.status_code)
                return
            raise
        meta = {"kind": "filing", "number": item.number, "registered": item.registered.isoformat(),
                "issuer": item.issuer, "nif": item.nif, "reason": item.reason}
        try:
            read_pdf(response.content)
        except TemplateError as exc:
            meta["warning"] = f"{item.number}: CNMV form not recognized ({exc})"
        yield RawDocument(
            source=self.name,
            url=item.url,
            params={},
            content=response.content,
            content_type=response.headers.get("content-type"),
            fetched_at=datetime.now(UTC),
            suffix=".pdf",
            meta=meta,
        )

    def parse(self, doc: RawDocument, ctx: ParseContext) -> Iterator[InsiderTransaction]:
        if doc.meta.get("kind") != "filing":
            return
        try:
            filing = read_pdf(doc.content)
        except TemplateError:
            return  # reported as a warning at fetch time
        number = doc.meta.get("number") or doc.sha256[:16]
        issuer = filing.issuer_name or doc.meta.get("issuer") or "unknown issuer"
        lei = valid_lei(filing.issuer_lei)
        scope = lei or issuer
        declarant = filing.declarant
        linked_names = names_in_position(filing.position, issuer) if filing.closely_associated else []
        linked = linked_names[0] if linked_names else None
        is_entity = is_legal_entity(declarant)
        if is_entity and linked and {w.casefold() for w in linked.split()} <= {w.casefold() for w in declarant.split()}:
            is_entity = False  # an entity named after the manager: keep it pseudonymous
        notifier_id = ctx.person_id(declarant, scope=scope)
        pdmr_id = ctx.person_id(linked, scope=scope) if linked else notifier_id
        private = [*linked_names, *([] if is_entity else [declarant])]
        public = [issuer, declarant if is_entity else None]
        position = scrub_names(redact_position(filing.position), private, public)
        # A closely associated person's job title field describes the manager, often by name, in free form:
        # only the normalized role is kept.
        position_raw = None if filing.closely_associated else position
        registered = doc.meta.get("registered")
        published_at = (
            datetime.fromisoformat(registered).replace(tzinfo=ZoneInfo(TIMEZONE)).astimezone(UTC)
            if registered else None
        )
        for n, tx in enumerate(filing.transactions):
            instrument, nature_raw = split_description(tx["description"])
            nature, direction = normalize_nature(nature_raw)
            volume, price = parse_number(tx["volume"]), parse_number(tx["price"])
            venue = clean(tx["venue"])
            yield InsiderTransaction(
                record_id=record_id(self.name, number, n),
                source=self.name,
                jurisdiction=self.jurisdiction,
                source_url=doc.url,
                published_at=published_at,
                transaction_date=datetime.strptime(tx["date"], "%d/%m/%Y").date(),
                issuer_name=issuer,
                issuer_lei=lei,
                pdmr_id=pdmr_id,
                pdmr_role=normalize_role(filing.position),
                pdmr_role_raw=position_raw,
                notifier_id=notifier_id,
                notifier_name=declarant if is_entity else None,
                notifier_is_legal_entity=is_entity,
                closely_associated=filing.closely_associated,
                is_amendment=filing.is_amendment,
                nature=nature,
                nature_raw=nature_raw,
                direction=direction,
                instrument_type=normalize_instrument(instrument),
                instrument_type_raw=instrument,
                isin=valid_isin(tx["code"]),
                volume=volume,
                price=price,
                currency=tx["currency"],
                amount=volume * price if volume is not None and price is not None else None,
                venue=venue,
                on_venue=None if venue is None else venue.upper() not in OFF_VENUE_CODES,
                fetched_at=doc.fetched_at,
                parser=self.parser_id,
            )
