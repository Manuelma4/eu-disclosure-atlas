"""France - Autorité des marchés financiers (AMF), managers' transactions ("Déclarations des dirigeants").

Source notes and quirks: docs/sources/fr_amf_pdmr.md. In short:
- The BDIF website is a client of a JSON API: ``/back/api/v1/informations`` lists publications of type
  ``DD`` between two dates, 100 per page; each publication has one PDF under ``/back/api/v1/documents/``.
- Each PDF is one notification on the EU template, printed twice since August 2026: in French, then in
  English. The parser reads the first version found, so older French-only documents parse the same way.
- One notification has one or more transaction blocks, each with one or more price and volume lines. The
  connector emits one record per price and volume line, so amounts add up; the aggregated price and volume
  are used as a consistency check only.
- The declarant is printed as "NAME, position". The name becomes a pseudonym and is never stored.
- When the deterministic parser cannot read a document and an LLM is configured (see
  ``disclosure_atlas.extraction``), ``fetch`` stores the model's answer next to the PDF in bronze, so
  ``parse`` stays pure and re-parsing never calls the model again.
"""

from __future__ import annotations

import io
import json
import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

from ..extraction import ExtractedFiling, ExtractedTransaction, LLMExtractor, PriceVolume
from ..extraction import provider_from_env as llm_provider_from_env
from ..models import Dataset, InsiderTransaction, InstrumentType
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

log = logging.getLogger(__name__)

API_URL = "https://bdif.amf-france.org/back/api/v1"
LIST_URL = f"{API_URL}/informations"
DOCUMENT_URL = f"{API_URL}/documents"
DETAILS_URL = "https://bdif.amf-france.org/fr/details/{numero}"
TYPE_DIRIGEANTS = "DD"
PAGE_SIZE = 100
TIMEZONE = "Europe/Paris"
# 404: removed since listing; 451: withdrawn by the AMF (the website shows "document not available").
MISSING_DOCUMENT_STATUSES = {404, 410, 451}


class TemplateError(ValueError):
    """The document does not follow the notification template closely enough to be read by rules."""


# --- Template text --------------------------------------------------------------------------

_MONTHS = {
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6, "juillet": 7,
    "août": 8, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}
_DATE = re.compile(r"(\d{1,2})(?:er|st|nd|rd|th)?\s+([^\W\d_]+)\s+(\d{4})")
_CURRENCIES = {
    "euro": "EUR", "euros": "EUR", "livre sterling": "GBP", "livres sterling": "GBP", "pound sterling": "GBP",
    "dollar us": "USD", "dollar américain": "USD", "dollar americain": "USD", "us dollar": "USD",
    "dollar des états-unis": "USD", "dollar des etats-unis": "USD", "united states dollar": "USD",
    "dollar canadien": "CAD", "franc suisse": "CHF", "swiss franc": "CHF", "couronne suédoise": "SEK",
    "couronne danoise": "DKK", "couronne norvégienne": "NOK", "yen": "JPY", "zloty": "PLN",
}
_AMOUNT = re.compile(r"^(-?[\d\s.,']*\d)\s*(.*)$")
_HEADER = re.compile(r"^\d{4}DD\d+\s")
_OFF_VENUE = re.compile(
    r"\bhors (de )?(la )?(plate-?forme|plateforme|march[ée])|outside|off[- ]market|over[- ]the[- ]counter|"
    r"gr[ée] [àa] gr[ée]|\bOTC\b",
    re.I,
)
_REFERENCE = re.compile(r"^(?P<isin>[A-Z]{2}[A-Z0-9]{9}\d)\s*-\s*DD\d+$")
_ISIN_LIKE = re.compile(r"[A-Z]{2}[A-Z0-9]{9}\d")
# Depositary receipts have their own (usually US) ISIN, never the reference ISIN of the French share.
_DEPOSITARY = re.compile(r"deposit[ao]ry|\bad[rs]s?\b", re.I)

# Labels of the template, French and English, mapped to the field they introduce.
_LABELS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^(NOM ?/ ?FONCTION DE LA PERSONNE|NAME ?/ ?POSITION)", re.I), "declarant_label"),
    (re.compile(r"^(PERSONNE ETROITEMENT LIEE|PERSON CLOSELY ASSOCIATED) ?:", re.I), "declarant"),
    (re.compile(r"^(NOTIFICATION INITIALE ?/ ?MODIFICATION|INITIAL NOTIFICATION ?/ ?AMENDMENT) ?:", re.I),
     "amendment"),
    (re.compile(r"^(COORDONNEES DE L.EMETTEUR|DETAILS OF THE ISSUER)\b", re.I), "issuer_section"),
    (re.compile(r"^(NOM|NAME) ?:", re.I), "issuer_name"),
    (re.compile(r"^LEI ?:", re.I), "issuer_lei"),
    (re.compile(r"^(DETAIL DE LA TRANSACTION|DETAIL OF THE TRANSACTION)\b", re.I), "transaction"),
    (re.compile(r"^(DATE DE LA TRANSACTION|DATE OF THE TRANSACTION) ?:", re.I), "transaction_date"),
    (re.compile(r"^(LIEU DE LA TRANSACTION|PLACE OF THE TRANSACTION) ?:", re.I), "venue"),
    (re.compile(r"^(NATURE DE LA TRANSACTION|NATURE OF THE TRANSACTION) ?:", re.I), "nature"),
    (re.compile(r"^(DESCRIPTION DE L.INSTRUMENT FINANCIER|DESCRIPTION OF THE FINANCIAL INSTRUMENT)[^:]*:", re.I),
     "instrument"),
    (re.compile(r"^(CODE D.IDENTIFICATION DE L.INSTRUMENT FINANCIER|IDENTIFICATION CODE) ?:", re.I), "isin"),
    (re.compile(r"^(INFORMATION DETAILLEE PAR OPERATION|DETAILED OPERATION INFORMATION)\b", re.I), "detail"),
    (re.compile(r"^(INFORMATIONS AGREGEES|AGGREGATED INFORMATION)\b", re.I), "aggregated"),
    (re.compile(r"^PRIX UNITAIRE ?:", re.I), "unit_price"),
    (re.compile(r"^(PRIX|PRICE) ?:", re.I), "price"),
    (re.compile(r"^(AGGREGATED VOLUME|VOLUME) ?:", re.I), "volume"),
    (re.compile(r"(PERFORMANCES?|PERFORMANCE SHARES) ?: ?(?P<value>OUI|NON|YES|NO)\s*$", re.I), "linked"),
    (re.compile(r"^(TRANSACTION LIEE A L.EXERCICE|TRANSACTION LINKED TO THE EXERCISE)", re.I), "linked_label"),
    (re.compile(r"^(DATE DE RECEPTION DE LA NOTIFICATION|DATE OF RECEIPT OF THE NOTIFICATION) ?:", re.I),
     "received"),
    (re.compile(r"^(COMMENTAIRES|COMMENTS) ?:", re.I), "comments"),
]
_CONTINUED = {"declarant", "amendment", "issuer_name", "venue", "nature", "instrument"}
_INITIAL = re.compile(r"^(notification initiale|initial notification)\b", re.I)
_AMENDMENT_WORD = re.compile(r"^(modification|amendment)\W*$", re.I)
_BOILERPLATE = re.compile(r"^[\"“«]?\s*(Les données à caractère personnel|Personal data collected)", re.I)


def parse_template_date(text: str | None) -> date | None:
    match = _DATE.search(clean(text) or "")
    if not match:
        return None
    day, month, year = match.groups()
    number = _MONTHS.get(month.casefold())
    if number is None:
        return None
    try:
        return date(int(year), number, int(day))
    except ValueError:
        return None


def parse_amount(text: str | None) -> tuple[float | None, str | None]:
    """'2 738.0000' -> (2738.0, None); '5.6723 Euro' -> (5.6723, 'EUR')."""
    match = _AMOUNT.match(clean(text) or "")
    if not match:
        return None, None
    number, unit = match.groups()
    unit = (clean(re.sub(r"\(.*?\)", "", unit)) or "").casefold()  # "Dollar des États-Unis (Même jour)"
    currency = _CURRENCIES.get(unit) or (unit.upper() if re.fullmatch(r"[a-z]{3}", unit) else None)
    return parse_number(number), currency


def _split_versions(lines: list[str]) -> list[str]:
    """Lines of the first language version (the French one in bilingual documents)."""
    headers = [i for i, line in enumerate(lines) if _HEADER.match(line)]
    end = headers[1] if len(headers) > 1 else len(lines)
    return lines[:end]


@dataclass
class _Tx:
    fields: dict[str, str] = field(default_factory=dict)
    lines: list[list[str | None]] = field(default_factory=list)
    aggregated: dict[str, str] = field(default_factory=dict)
    linked: bool | None = None


def parse_template(text: str) -> ExtractedFiling:
    """Read the notification template from the document text; raises ``TemplateError`` when it cannot."""
    lines = [c for c in (clean(line) for line in text.splitlines()) if c]
    lines = _split_versions(lines)
    filing: dict[str, str] = {}
    transactions: list[_Tx] = []
    current: str | None = None  # field that a following unlabeled line continues
    mode = "header"  # header, declarant, issuer, transaction, detail, aggregated, end
    for line in lines:
        if mode == "end":
            break
        if mode == "header" and (reference := _REFERENCE.match(line)):
            filing["reference_isin"] = reference.group("isin")
            continue
        if _BOILERPLATE.match(line):
            mode, current = "end", None
            continue
        key, value = None, ""
        for pattern, name in _LABELS:
            match = pattern.search(line)
            if match:
                key = name
                value = match.group("value") if "value" in pattern.groupindex else clean(line[match.end():]) or ""
                break
        if key is None:
            if current in _CONTINUED:
                target = filing if current in {"declarant", "amendment", "issuer_name"} else transactions[-1].fields
                target[current] = f"{target.get(current, '')} {line}".strip()
            continue
        current = None
        if key == "declarant_label":
            mode = "declarant"
        elif key == "declarant":
            mode, current = "declarant", "declarant"
            if value:
                filing["declarant"] = value
        elif key == "amendment":
            mode, current = "amendment", "amendment"  # the value is usually printed on the next line
            if value:
                filing["amendment"] = value
        elif key == "issuer_section":
            mode = "issuer"
        elif key == "issuer_name" and mode == "issuer":
            filing["issuer_name"], current = value, "issuer_name"
        elif key == "issuer_lei":
            filing["issuer_lei"] = value
        elif key == "transaction":
            transactions.append(_Tx())
            mode = "transaction"
        elif key in {"transaction_date", "venue", "nature", "instrument", "isin"} and transactions:
            transactions[-1].fields[key] = value
            current = key
        elif key == "detail" and transactions:
            mode = "detail"
        elif key == "aggregated" and transactions:
            mode = "aggregated"
        elif key in {"unit_price", "price"} and transactions:
            if mode == "aggregated":
                transactions[-1].aggregated["price"] = value
            else:
                transactions[-1].lines.append([value, None])
        elif key == "volume" and transactions:
            if mode == "aggregated":
                transactions[-1].aggregated["volume"] = value
            elif transactions[-1].lines and transactions[-1].lines[-1][1] is None:
                transactions[-1].lines[-1][1] = value
            else:
                transactions[-1].lines.append([None, value])
        elif key == "linked" and transactions:
            transactions[-1].linked = value.casefold() in {"oui", "yes"}
        elif key in {"received", "comments"}:
            mode = "end"
    return _to_filing(filing, transactions)


def _to_filing(filing: dict[str, str], transactions: list[_Tx]) -> ExtractedFiling:
    if not transactions:
        raise TemplateError("no transaction block")
    if not filing.get("issuer_name"):
        raise TemplateError("no issuer name")
    if not filing.get("declarant"):
        raise TemplateError("no declarant")
    declarant, linked, entity = split_declarant(filing["declarant"])
    name, _, position = declarant.partition(",")
    if linked:
        linked, _, position = linked.partition(",")
    extracted: list[ExtractedTransaction] = []
    for i, tx in enumerate(transactions, start=1):
        f = tx.fields
        when = parse_template_date(f.get("transaction_date"))
        if when is None:
            raise TemplateError(f"transaction {i}: unreadable date {f.get('transaction_date')!r}")
        if not f.get("nature"):
            raise TemplateError(f"transaction {i}: no nature")
        currencies: set[str] = set()
        lines: list[PriceVolume] = []
        for raw_price, raw_volume in tx.lines:
            price, currency = parse_amount(raw_price)
            volume, _ = parse_amount(raw_volume)
            if (raw_price and price is None) or (raw_volume and volume is None):
                raise TemplateError(f"transaction {i}: unreadable price or volume {raw_price!r} {raw_volume!r}")
            currencies.update([currency] if currency else [])
            lines.append(PriceVolume(price=price, volume=volume))
        aggregated_price, aggregated_currency = parse_amount(tx.aggregated.get("price"))
        aggregated_volume, _ = parse_amount(tx.aggregated.get("volume"))
        # The declarant types the aggregated currency separately and sometimes gets it wrong: the price
        # lines win when they state one.
        if not currencies and aggregated_currency:
            currencies.add(aggregated_currency)
        if len(currencies) > 1:
            raise TemplateError(f"transaction {i}: several currencies {sorted(currencies)}")
        if not lines and aggregated_volume is None:
            raise TemplateError(f"transaction {i}: no price or volume")
        extracted.append(ExtractedTransaction(
            transaction_date=when,
            venue=clean(f.get("venue")),
            nature=clean(f.get("nature")),
            instrument=clean(f.get("instrument")),
            isin=clean(f.get("isin")),
            currency=next(iter(currencies), None),
            lines=lines,
            aggregated_price=aggregated_price,
            aggregated_volume=aggregated_volume,
            linked_to_share_option_programme=tx.linked,
        ))
    amendment = clean(filing.get("amendment"))
    return ExtractedFiling(
        declarant_name=clean(name),
        declarant_is_legal_entity=entity,
        linked_pdmr_name=clean(linked),
        declarant_position=clean(position),
        # An initial notification says so; an amendment says "Modification" or explains itself in free text.
        is_amendment=bool(amendment) and not _INITIAL.match(amendment),
        amendment_details=None if not amendment or _INITIAL.match(amendment) or _AMENDMENT_WORD.match(amendment)
        else amendment,
        issuer_name=clean(filing.get("issuer_name")),
        issuer_lei=clean(filing.get("issuer_lei")),
        reference_isin=filing.get("reference_isin"),
        transactions=extracted,
    )


# "ENTITY personne morale liée à PERSON, position" / "ENTITY legal person closely associated with PERSON, ...".
_LINKED = re.compile(
    r"\s+(?P<kind>personne\s+(?P<fr>morale|physique)\s+|(?P<en>legal|natural)\s+person\s+|personne\s+|person\s+)?"
    r"(?:(?:étroitement|etroitement)\s+)?(?:li[ée]e?s?\s+(?:à|a|au)|closely\s+associated\s+with)\s+",
    re.I,
)


def find_isin(text: str | None) -> str | None:
    """The first valid ISIN in a code field: 'FR0012613610 – ALPWG', 'xparFR0010557264', 'FR 0005302 7'."""
    text = (clean(text) or "").upper()
    for candidate in [text.replace(" ", ""), *_ISIN_LIKE.findall(text), *_ISIN_LIKE.findall(text.replace(" ", ""))]:
        if isin := valid_isin(candidate):
            return isin
    return None


def split_declarant(text: str) -> tuple[str, str | None, bool | None]:
    """(notifying person, linked manager or None, legal entity flag or None when not stated)."""
    match = _LINKED.search(text)
    if not match:
        return text, None, None
    kind = (match.group("fr") or match.group("en") or "").casefold()
    entity = True if kind in {"morale", "legal"} else False if kind in {"physique", "natural"} else None
    return text[: match.start()], text[match.end():], entity


def pdf_text(content: bytes) -> str:
    """Text of every page, in reading order."""
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - pdfplumber is a core dependency
        raise RuntimeError("fr_amf_pdmr needs pdfplumber: pip install pdfplumber") from exc
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def read_pdf(content: bytes) -> ExtractedFiling:
    return parse_template(pdf_text(content))


# --- Position text --------------------------------------------------------------------------

# A position may name the manager a closely associated person is linked to ("épouse de M. X, ...").
# Everything after such a relationship word is dropped before the text is stored.
_RELATION = re.compile(
    r"\b(li[ée]e?s? [àa]|conjointe?|[ée]poux|[ée]pouse|fils|fille|p[èe]re|m[èe]re|enfant|partenaire|"
    r"spouse|husband|wife|son of|daughter|related to|linked to|associated with|M\.|Mme|Monsieur|Madame|Mr\.?|Mrs\.?|"
    r"contr[ôo]l[ée]e?s? par|repr[ée]sent[ée]e?s? par|d[ée]tenue?s? par|controlled by|represented by|held by)"
    r"(?=\W)",
    re.I,
)
_CLOSELY_ASSOCIATED = re.compile(
    r"personne (morale |physique )?(étroitement )?li[ée]e|closely associated|li[ée]e? [àa]", re.I
)


def redact_position(position: str | None) -> str | None:
    position = clean(position)
    if not position:
        return None
    match = _RELATION.search(position)
    if match:
        position = clean(position[: match.start()].rstrip(" ,;:-(")) or ""
        position = f"{position} [redacted]".strip()
    return position


_WORD = re.compile(r"[^\W\d_][^\W\d_'’-]{2,}", re.UNICODE)


def _words(name: str | None) -> set[str]:
    return {w.casefold() for w in _WORD.findall(name or "")}


def scrub_names(text: str | None, private: list[str], public: list[str | None]) -> str | None:
    """Replace any word of a natural person's name with '[redacted]', unless the word is also part of a
    public name (the issuer, a legal entity): families often give their name to their company."""
    if not text or not private:
        return text
    allowed = {w.casefold() for name in public if name for w in _WORD.findall(name)}
    words = {w.casefold() for name in private for w in _WORD.findall(name)} - allowed
    if not words:
        return text
    return _WORD.sub(lambda m: "[redacted]" if m.group(0).casefold() in words else m.group(0), text)


def dedupe_legal_form(name: str | None) -> str | None:
    """'EXAMPLE INDUSTRIES SE SE' -> 'EXAMPLE INDUSTRIES SE' (the AMF appends the legal form twice)."""
    name = clean(name)
    if not name:
        return name
    words = name.split(" ")
    if len(words) > 2 and words[-1].casefold() == words[-2].casefold():
        words.pop()
    return " ".join(words)


# --- Connector ------------------------------------------------------------------------------

def _published_at(meta: dict[str, Any]) -> datetime | None:
    value = meta.get("published_at")
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        from zoneinfo import ZoneInfo

        parsed = parsed.replace(tzinfo=ZoneInfo(TIMEZONE))
    return parsed.astimezone(UTC)


@register
class FranceAMFPDMR(Connector):
    name = "fr_amf_pdmr"
    dataset = Dataset.INSIDER_TRANSACTIONS
    jurisdiction = "FR"
    authority = "Autorité des marchés financiers (AMF)"
    homepage = "https://bdif.amf-france.org/fr"
    window_days = 7

    def __init__(self, http=None, extractor: LLMExtractor | None | str = "env") -> None:
        super().__init__(http)
        if extractor == "env":
            provider = llm_provider_from_env()
            extractor = LLMExtractor(provider) if provider is not None else None
        self.extractor: LLMExtractor | None = extractor  # type: ignore[assignment]

    # Fetch ----------------------------------------------------------------------------------

    def fetch(self, start: date, end: date) -> Iterator[RawDocument]:
        for window_start, window_end in date_windows(start, end, self.window_days):
            yield from self._fetch_window(window_start, window_end)

    def _listing_params(self, start: date, end: date, offset: int) -> dict[str, Any]:
        # The API filters on the publication date; bounds are Paris calendar days, sent as UTC instants.
        from zoneinfo import ZoneInfo

        paris = ZoneInfo(TIMEZONE)
        lower = datetime(start.year, start.month, start.day, tzinfo=paris).astimezone(UTC)
        upper = (datetime(end.year, end.month, end.day, tzinfo=paris) + timedelta(days=1)).astimezone(UTC)
        upper -= timedelta(milliseconds=1)
        return {
            "TypesInformation": TYPE_DIRIGEANTS,
            "DateDebut": lower.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "DateFin": upper.strftime("%Y-%m-%dT%H:%M:%S.999Z"),
            "From": offset,
            "Size": PAGE_SIZE,
        }

    def _fetch_window(self, start: date, end: date) -> Iterator[RawDocument]:
        offset = 0
        while True:
            params = self._listing_params(start, end, offset)
            response = self.http.get(LIST_URL, params=params)
            payload = response.json()
            results = payload.get("result") or []
            total = int(payload.get("total") or 0)
            yield RawDocument(
                source=self.name,
                url=str(response.url),
                params=params,
                content=response.content,
                content_type=response.headers.get("content-type"),
                fetched_at=datetime.now(UTC),
                suffix=".json",
                meta={"kind": "listing", "window": [start.isoformat(), end.isoformat()], "total": total,
                      "offset": offset, "rows": len(results)},
            )
            for item in results:
                yield from self._fetch_publication(item)
            offset += len(results)
            if not results or offset >= total:
                break

    def _fetch_publication(self, item: dict[str, Any]) -> Iterator[RawDocument]:
        issuers = [s.get("raisonSociale") for s in item.get("societes") or [] if s.get("raisonSociale")]
        for document in item.get("documents") or []:
            if not document.get("path") or document.get("accessible") is False:
                continue
            url = f"{DOCUMENT_URL}/{document['path']}"
            try:
                response = self.http.get(url)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in MISSING_DOCUMENT_STATUSES:
                    log.warning("%s: document %s not available (%s)", self.name, item.get("numero"),
                                exc.response.status_code)
                    continue
                raise
            meta = {
                "kind": "filing",
                "numero": item.get("numero"),
                "file": document.get("nomFichier"),
                "published_at": item.get("datePublication"),
                "information_date": item.get("dateInformation"),
                "language": item.get("langue"),
                "version": item.get("version"),
                "issuers": issuers,
            }
            pdf = RawDocument(
                source=self.name,
                url=url,
                params={},
                content=response.content,
                content_type=response.headers.get("content-type"),
                fetched_at=datetime.now(UTC),
                suffix=".pdf",
                meta=meta,
            )
            failure = self._deterministic_failure(pdf)
            if failure is None:
                yield pdf
                continue
            llm_doc = self._llm_fallback(pdf, failure) if self.extractor else None
            if llm_doc is None:
                outcome = "succeeded" if self.extractor else "configured"
                pdf = replace(pdf, meta={**meta, "warning": (
                    f"{item.get('numero')}: deterministic parser failed ({failure}); no LLM fallback {outcome}")})
            yield pdf
            if llm_doc is not None:
                yield llm_doc

    @staticmethod
    def _deterministic_failure(doc: RawDocument) -> str | None:
        try:
            read_pdf(doc.content)
        except TemplateError as exc:
            return str(exc)
        except Exception as exc:  # unreadable PDF (pdfminer raises many types)
            return f"unreadable PDF: {type(exc).__name__}"
        return None

    def _llm_fallback(self, pdf: RawDocument, failure: str) -> RawDocument | None:
        assert self.extractor is not None
        try:
            extraction = self.extractor.extract(pdf_text(pdf.content))
        except Exception as exc:  # the fallback must never break ingestion (ExtractionError, network...)
            log.warning("%s: LLM fallback failed for %s: %s", self.name, pdf.meta.get("numero"), exc)
            return None
        body = {"parser": extraction.parser, "pdf_sha256": pdf.sha256, "deterministic_failure": failure,
                "filing": extraction.filing.model_dump(mode="json")}
        return RawDocument(
            source=self.name,
            url=pdf.url,
            params={},
            content=json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8"),
            content_type="application/json",
            fetched_at=datetime.now(UTC),
            suffix=".json",
            meta={**pdf.meta, "kind": "llm_extraction", "parser": extraction.parser},
        )

    # Parse ----------------------------------------------------------------------------------

    def parse(self, doc: RawDocument, ctx: ParseContext) -> Iterator[InsiderTransaction]:
        kind = doc.meta.get("kind") or ("filing" if doc.suffix == ".pdf" else "listing")
        if kind == "filing":
            try:
                filing = read_pdf(doc.content)
            except TemplateError:
                return  # read by the LLM fallback, if any, from its own bronze document
            yield from self.records(filing, doc, ctx, parser=self.parser_id)
        elif kind == "llm_extraction":
            body = json.loads(doc.content)
            filing = ExtractedFiling.model_validate(body["filing"])
            yield from self.records(filing, doc, ctx, parser=body["parser"])

    def records(self, filing: ExtractedFiling, doc: RawDocument, ctx: ParseContext,
                parser: str) -> Iterator[InsiderTransaction]:
        numero = doc.meta.get("numero") or doc.sha256[:16]
        issuer = filing.issuer_name or next(iter(doc.meta.get("issuers") or []), None) or "unknown issuer"
        lei = valid_lei(filing.issuer_lei)
        scope = lei or issuer
        declarant = dedupe_legal_form(filing.declarant_name)
        linked = clean(filing.linked_pdmr_name)
        is_entity = filing.declarant_is_legal_entity
        if is_entity is None:
            is_entity = is_legal_entity(declarant)
        if is_entity and linked and _words(linked) <= _words(declarant):
            is_entity = False  # an entity named after the manager: keep it pseudonymous
        notifier_id = ctx.person_id(declarant, scope=scope)
        pdmr_id = ctx.person_id(linked, scope=scope) if linked else notifier_id
        closely_associated = bool(linked) or bool(_CLOSELY_ASSOCIATED.search(filing.declarant_position or ""))
        # Free text that is stored must not carry the name of a natural person (docs/decisions/0002).
        private = [name for name in (linked, None if is_entity else declarant) if name]
        public = [issuer, declarant if is_entity else None]
        position = scrub_names(redact_position(filing.declarant_position), private, public)
        published_at = _published_at(doc.meta)
        filing_confidence = filing.confidence.model_dump().values() if filing.confidence else []
        for t, tx in enumerate(filing.transactions):
            nature, direction = normalize_nature(tx.nature)
            venue = scrub_names(clean(tx.venue), private, public)
            instrument = scrub_names(clean(tx.instrument), private, public)
            instrument_type = normalize_instrument(tx.instrument)
            isin = find_isin(tx.isin)
            if isin is None and instrument_type == InstrumentType.SHARE and not _DEPOSITARY.search(instrument or ""):
                isin = valid_isin(filing.reference_isin)  # the AMF's reference security for the filing
            lines = tx.lines or [PriceVolume(price=tx.aggregated_price, volume=tx.aggregated_volume)]
            confidences = [*filing_confidence, *(tx.confidence.model_dump().values() if tx.confidence else [])]
            for n, line in enumerate(lines):
                amount = line.price * line.volume if line.price is not None and line.volume is not None else None
                yield InsiderTransaction(
                    record_id=record_id(self.name, numero, t, n),
                    source=self.name,
                    jurisdiction=self.jurisdiction,
                    source_url=DETAILS_URL.format(numero=numero),
                    published_at=published_at,
                    transaction_date=tx.transaction_date,
                    issuer_name=issuer,
                    issuer_lei=lei,
                    pdmr_id=pdmr_id,
                    pdmr_role=normalize_role(filing.declarant_position),
                    pdmr_role_raw=position,
                    notifier_id=notifier_id,
                    notifier_name=clean(declarant) if is_entity else None,
                    notifier_is_legal_entity=is_entity,
                    closely_associated=closely_associated,
                    is_amendment=filing.is_amendment,
                    # Not stored: free text typed by the declarant that can name anyone.
                    amendment_details=None,
                    linked_to_share_option_programme=tx.linked_to_share_option_programme,
                    nature=nature,
                    nature_raw=scrub_names(clean(tx.nature), private, public) or "",
                    direction=direction,
                    instrument_type=instrument_type,
                    instrument_type_raw=instrument,
                    instrument_name=instrument,
                    isin=isin,
                    volume=line.volume,
                    price=line.price,
                    currency=tx.currency,
                    amount=amount,
                    venue=venue,
                    on_venue=None if venue is None else not _OFF_VENUE.search(venue),
                    fetched_at=doc.fetched_at,
                    parser=parser,
                    extraction_confidence=min(confidences) if confidences else None,
                )


def normalized_view(filing: ExtractedFiling) -> dict[str, Any]:
    """The normalized values the connector derives from a filing, for evaluation against the gold set."""
    doc = RawDocument(source=FranceAMFPDMR.name, url="", params={}, content=b"", content_type=None,
                      fetched_at=datetime(2000, 1, 1, tzinfo=UTC), meta={"numero": "eval"})
    connector = FranceAMFPDMR(http=object(), extractor=None)
    records = list(connector.records(filing, doc, ParseContext(pseudonym_key=b"eval"), parser="eval"))
    if not records:
        return {}
    first = records[0]
    view: dict[str, Any] = {"pdmr_role": first.pdmr_role, "closely_associated": first.closely_associated,
                            "notifier_is_legal_entity": first.notifier_is_legal_entity}
    for t in range(len(filing.transactions)):
        r = next(r for r in records if r.record_id == record_id(FranceAMFPDMR.name, "eval", t, 0))
        view.update({f"transactions[{t}].nature": r.nature, f"transactions[{t}].direction": r.direction,
                     f"transactions[{t}].instrument_type": r.instrument_type,
                     f"transactions[{t}].on_venue": r.on_venue, f"transactions[{t}].isin": r.isin})
    return view
