"""Issuer-made notifications that follow the template of Implementing Regulation (EU) 2016/523.

Several authorities (Denmark, Norway) publish the notification as the issuer drew it up: a Word table saved
as PDF, sometimes an HTML page, with English, Danish or Norwegian labels and free-form values ("DKK 372.98",
"156 stk.", "25/9 2026"). This module reads the template's table rows and is deliberately conservative: a
value it cannot read unambiguously is left empty rather than guessed, and a document without the template
raises ``TemplateError``.
"""

from __future__ import annotations

import html
import io
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime

from ..models import InsiderTransaction
from ..normalize import (
    clean,
    normalize_instrument,
    normalize_nature,
    normalize_role,
    record_id,
    valid_isin,
    valid_lei,
)
from ..privacy import is_legal_entity
from .base import ParseContext
from .fr_amf_pdmr import scrub_names


class TemplateError(ValueError):
    """The document does not contain a readable 2016/523 template."""


# --- Rows -------------------------------------------------------------------------------------------

Row = list[str]


def pdf_rows(content: bytes) -> list[Row]:
    """Table rows of every page, in reading order. Cells keep their line breaks."""
    import pdfplumber

    start = content.find(b"%PDF-")
    if start < 0:
        raise TemplateError("not a PDF")
    try:
        with pdfplumber.open(io.BytesIO(content[start:])) as pdf:
            return [[cell or "" for cell in row] for page in pdf.pages for table in page.extract_tables()
                    for row in table]
    except Exception as exc:  # pdfminer raises many types
        raise TemplateError(f"unreadable PDF: {type(exc).__name__}") from exc


_TR = re.compile(r"<tr\b.*?</tr>", re.S | re.I)
_CELL = re.compile(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", re.S | re.I)
_BREAK = re.compile(r"<br\s*/?>|</p>|</div>", re.I)
_TAG = re.compile(r"<[^>]+>")


def html_rows(text: str) -> list[Row]:
    rows = []
    for tr in _TR.finditer(text):
        cells = [html.unescape(_TAG.sub(" ", _BREAK.sub("\n", cell))) for cell in _CELL.findall(tr.group(0))]
        rows.append(["\n".join(c for c in (clean(line) for line in cell.split("\n")) if c) for cell in cells])
    return rows


def document_rows(content: bytes) -> list[Row]:
    head = content[:2048].lstrip().lower()
    is_html = head.startswith((b"<!doctype html", b"<html")) or b"<table" in content[:200000].lower()
    if is_html and b"%PDF-" not in content[:1024]:
        return html_rows(content.decode("utf-8", errors="replace"))
    return pdf_rows(content)


# --- Labels -----------------------------------------------------------------------------------------

_LABELS: list[tuple[str, re.Pattern[str]]] = [(name, re.compile(pattern)) for name, pattern in [
    ("section_person", r"^(detailsoftheperson|nærmereoplysningeromperson|opplysningerompersonen)"),
    ("section_reason", r"^(reasonfor(the)?notification|årsagtilindberetning|grunnenformeldingen|grunnformeldingen)"),
    ("section_issuer", r"^(detailsoftheissuer|nærmereoplysningeromudstederen|opplysningeromutstederen)"),
    ("section_tx", r"^(detailsofthetransaction|nærmereoplysningeromtransaktion|opplysningeromtransaksjon)"),
    ("issuer_name", r"^(nameoftheissuer|navnpåutsteder|udstederensnavn|utstedersnavn)"),
    ("declarant", r"^(nameoftheperson|navnpåperson)"),
    ("issuer_lei", r"^(lei|leioftheissuer)"),
    ("name", r"^(name|navn|fullname|fulltnavn)\W*$"),
    ("position", r"^(position|stilling)"),
    ("amendment", r"^(initialnotification|førsteindberetning|førstemelding|førsteinnrapportering|første)"),
    ("description", r"^(descriptionofthe|beskrivelseafdet|beskrivelseavdet)"),
    ("identification", r"^(identificationcode|identifikationskode|identifikasjonskode)"),
    ("nature", r"^(natureof(the)?transaction|nameof(the)?transaction|transaktionensart|transaksjonensart|"
               r"typetransaksjon)"),
    ("aggregated", r"^(aggregated|aggregerede|aggregert)"),
    ("prices", r"^(price\(s\)andvolume|pris\(er\)ogmængde|pris\(er\)ogvolum|priceandvolume|price(\(s\))?$|"
               r"pris(\(er\))?$)"),
    ("date", r"^(dateof(the)?transaction|datofortransaktionen|datofortransaksjonen|transaktionsdato|"
             r"transaksjonsdato)"),
    ("venue", r"^(placeof(the)?transaction|stedfortransaktionen|stedfortransaksjonen|handelsplass|"
              r"placeoftrading)"),
    ("additional", r"^(additionalinformation|yderligereoplysninger|tilleggsinformasjon|øvrigeoplysninger)"),
]]
_NORDIC_LABELS = re.compile(r"navn|stilling|transaktion|transaksjon|pris\(er\)|mængde|udsteder|utsteder")
_MARKER = re.compile(r"^(\d{1,2}(\.\d)?\.?|[a-h]\)|[a-h]|\d{2})$", re.I)
_HEADER_WORDS = re.compile(r"^(price|prices|price\(s\)|pris|pris\(er\)|volume|volumes|volume\(s\)|mængde|"
                           r"mængde\(r\)|volum|volum\(er\)|antal|antall)$", re.I)


def _compact(text: str) -> str:
    return re.sub(r"[\s\-–—:]+", "", text).casefold()


# A label printed in the same cell as its value: "Name Example Holding AS", "Position/status CEO".
_INLINE = re.compile(r"^(?P<label>name|navn|position/status|position|stilling/titel|stilling/status|"
                     r"lei-kode|lei code|lei)\s*:?\s+(?P<value>\S.*)$", re.I | re.S)


def classify(row: Row) -> tuple[str | None, list[str]]:
    """(label name or None, values) of one table row; leading row markers ("1.", "a)") are dropped."""
    cells = [c.strip() for c in row if c and c.strip()]
    while cells and _MARKER.match(cells[0]):
        cells.pop(0)
    if cells and (inline := _INLINE.match(cells[0])) and not any(p.search(_compact(cells[0])) for _, p in _LABELS):
        cells = [inline.group("label"), inline.group("value"), *cells[1:]]
    for i, cell in enumerate(cells[:2]):
        if re.match(r"^[-–—]", cell):
            break  # "- Aggregated volume", "— Price": sub-items of the row above, not labels
        compact = _compact(cell)
        for name, pattern in _LABELS:
            if pattern.search(compact):
                # Some layouts repeat the label in a second merged cell.
                return name, [v for v in cells[i + 1:] if _compact(v) != compact]
    return None, cells


# --- Values -----------------------------------------------------------------------------------------

_CURRENCY = re.compile(r"\b(DKK|NOK|SEK|EUR|USD|GBP|ISK|CHF|CAD|AUD)\b", re.I)
_KRONER = re.compile(r"\b(kr|kroner)\b\.?", re.I)
# Grouped thousands ("1,333,403.50", "30 000") or a plain number ("372.98").
_NUMBER = re.compile(r"(?<![\w.,])-?\d{1,3}(?:[ .,'\u00a0]\d{3})+(?:[.,]\d+)?(?![\w])|"
                     r"(?<![\w.,])-?\d+(?:[.,]\d+)?(?![\w])")
_ISIN_SPACED = re.compile(r"\b[A-Z]\s?[A-Z](?:\s?[A-Z0-9]){9}\s?\d\b")
_MONTHS = {name: i for i, names in enumerate([
    ("january", "januar"), ("february", "februar"), ("march", "marts", "mars"), ("april",),
    ("may", "maj", "mai"), ("june", "juni"), ("july", "juli"), ("august",), ("september",),
    ("october", "oktober"), ("november",), ("december", "desember"),
], start=1) for name in names}


def to_number(token: str, nordic: bool) -> float | None:
    """'3,575' (English) -> 3575; '116.121' (Danish) -> 116121; '1,333,403.50'; '30 000'; '21,423' (Nordic)."""
    token = token.replace("\u00a0", " ").replace(" ", "").replace("'", "")
    if "," in token and "." in token:
        decimal = "," if token.rfind(",") > token.rfind(".") else "."
        token = token.replace("." if decimal == "," else ",", "").replace(",", ".")
    elif "," in token:
        thousands = not nordic and re.fullmatch(r"-?\d{1,3}(,\d{3})+", token)
        token = token.replace(",", "") if thousands else token.replace(",", ".")
    elif "." in token and nordic and re.fullmatch(r"-?\d{1,3}(\.\d{3})+", token):
        token = token.replace(".", "")
    try:
        return float(token)
    except ValueError:
        return None


def amount(text: str, nordic: bool) -> tuple[float | None, str | None]:
    """The single number in a cell and its currency; (None, ...) when the cell holds no or several numbers."""
    text = _ISIN_SPACED.sub(" ", text)
    numbers = _NUMBER.findall(text)
    currency = _CURRENCY.search(text)
    unit = currency.group(1).upper() if currency else ("kr" if _KRONER.search(text) else None)
    if len(numbers) != 1:
        return None, unit
    return to_number(numbers[0], nordic), unit


def find_date(text: str | None) -> date | None:
    text = clean(text) or ""
    if m := re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", text):
        year, month, day = map(int, m.groups())
    elif m := re.search(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{4})\b", text):
        day, month, year = map(int, m.groups())
    elif m := re.search(r"\b(\d{1,2})\.?\s+([^\W\d_]+)\.?,?\s+(\d{4})\b", text):
        month = _MONTHS.get(m.group(2).casefold(), 0)
        day, year = int(m.group(1)), int(m.group(3))
    elif m := re.search(r"\b([^\W\d_]+)\s+(\d{1,2}),?\s+(\d{4})\b", text):
        month = _MONTHS.get(m.group(1).casefold(), 0)
        day, year = int(m.group(2)), int(m.group(3))
    else:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def find_isin(text: str | None) -> str | None:
    for candidate in _ISIN_SPACED.findall((text or "").upper()):
        if isin := valid_isin(candidate.replace(" ", "")):
            return isin
    return None


# --- Filing -----------------------------------------------------------------------------------------

@dataclass
class TemplateTransaction:
    description: list[str] = field(default_factory=list)
    nature: str | None = None
    price_rows: list[list[str]] = field(default_factory=list)
    aggregated: list[str] = field(default_factory=list)
    date_text: str | None = None
    venue: str | None = None


@dataclass
class TemplateFiling:
    declarant: str | None = None
    position: str | None = None
    amendment: str | None = None
    issuer: str | None = None
    lei: str | None = None
    nordic: bool = False
    transactions: list[TemplateTransaction] = field(default_factory=list)


def _join(values: list[str]) -> str | None:
    return clean(" ".join(values))


def parse_rows(rows: list[Row], nordic: bool | None = None) -> list[TemplateFiling]:
    """Every notification in a document (some documents hold several, one per person).

    ``nordic`` says whether numbers use the Danish/Norwegian convention ("1.000,5"); by default it is guessed
    from the labels."""
    filings: list[TemplateFiling] = []
    filing: TemplateFiling | None = None
    tx: TemplateTransaction | None = None
    section, mode = 0, None
    if nordic is None:
        nordic = any(_NORDIC_LABELS.search(_compact(" ".join(row[:3]))) for row in rows)

    def new_filing() -> TemplateFiling:
        nonlocal filing, tx
        filing, tx = TemplateFiling(nordic=nordic), None
        filings.append(filing)
        return filing

    for row in rows:
        label, values = classify(row)
        if label is None:
            if not values or filing is None:
                continue
            if mode == "prices" and tx is not None:
                if not all(_HEADER_WORDS.match(v) for v in values):
                    tx.price_rows.append(values)
            elif mode == "aggregated" and tx is not None:
                tx.aggregated += values
            elif mode == "description" and tx is not None:
                tx.description += values
            elif mode == "position":
                filing.position = _join([filing.position or "", *values])
            continue
        mode = None
        if label == "section_person":
            if filing is None or filing.transactions or filing.declarant:
                new_filing()
            section = 1
        elif label == "section_reason":
            section = 2
        elif label == "section_issuer":
            section = 3
        elif label == "section_tx":
            section = 4
        elif label in ("declarant", "name") and not (label == "name" and section == 3):
            if filing is None or filing.transactions or (filing.declarant and section != 1):
                new_filing()
            assert filing is not None
            filing.declarant = _join(values)
            section = 1
        elif filing is None:
            continue
        elif label in ("issuer_name", "name"):
            filing.issuer = _join(values)
        elif label == "position":
            filing.position, mode = _join(values), "position"
        elif label == "amendment":
            filing.amendment = _join(values)
        elif label == "issuer_lei":
            filing.lei = _join(values)
        elif label in ("description", "identification", "nature", "prices", "aggregated", "date", "venue"):
            if label == "description" and (tx is None or tx.nature or tx.description) or tx is None:
                tx = TemplateTransaction()
                filing.transactions.append(tx)
            if label in ("description", "identification"):
                tx.description += values
                mode = "description"
            elif label == "nature":
                tx.nature = _join(values)
            elif label == "prices":
                mode = "prices"
                numbers = [v for v in values if not _HEADER_WORDS.match(v)]
                if numbers:
                    tx.price_rows.append(numbers)
            elif label == "aggregated":
                tx.aggregated += values
                mode = "aggregated"
            elif label == "date":
                tx.date_text = _join(values)
            elif label == "venue":
                tx.venue = _join(values)
    filings = [f for f in filings if f.declarant and f.transactions]
    if not filings:
        raise TemplateError("no 2016/523 template table")
    return filings


def read_document(content: bytes, nordic: bool | None = None) -> list[TemplateFiling]:
    return parse_rows(document_rows(content), nordic)


# --- Records ----------------------------------------------------------------------------------------

_REGISTRATION_NO = re.compile(r"\((?:CVR|CVR-no|CVR-nr|org\.?\s?nr|organisasjonsnummer|reg\.? no)[^)]*\)", re.I)
_CLOSELY = re.compile(
    r"closely|related|associated|nærtstående|nærstående|nær tilknytning|tilknyttet|owned by|ejes af|eies av|"
    r"eiet av|ejet af|controlled|kontrolleret|kontrollert|spouse|ægtefælle|ektefelle|wife|husband|hustru|"
    r"datter|daughter|\bsøn\b|\bson\b|child|barn",
    re.I,
)
_LINKED = re.compile(
    r"(?:closely associated|associated|related(?: party)?|connected|nærtstående|nærstående|tilknyttet|knyttet)"
    r"(?:\s+person|\s+party|\s+company)?\s+(?:to|with|til)\s+(?:the\s+)?"
    r"(?P<name>[A-ZÆØÅÄÖÜ][\w'’.-]+(?:\s+(?:[A-ZÆØÅÄÖÜ][\w'’.-]+|af|von|van|de|der)){1,4})",
)
_OFF_VENUE = re.compile(r"outside|off[- ]exchange|off[- ]market|\bxoff\b|\bxxxx\b|uden for|utenfor|udenfor|\botc\b|"
                        r"not on a trading venue|ikke på en handelsplads", re.I)
_PRICE_KEY = re.compile(r"(average|weighted|volume weighted|gennemsnitlig|gns\.?|snitt|gjennomsnittlig)\w*\s+"
                        r"(price|pris|kurs)\w*[^:\d]*:?\s*(?P<value>[A-Z]{0,3}\s?-?\d[\d.,' ]*)", re.I)
_VOLUME_KEY = re.compile(r"(volume|number of shares|antal|antall|mængde|volum|quantity)\w*[^:\d]*:?\s*"
                         r"(?P<value>-?\d[\d.,' ]*\d|\d)", re.I)


_LEGAL_FORMS = {"as", "asa", "a/s", "aps", "ab", "publ", "plc", "ltd", "limited", "inc", "sa", "se", "nv", "bv",
                "ag", "oyj", "oy", "hf", "the"}


def _core_words(name: str | None) -> set[str]:
    return {w.casefold() for w in re.findall(r"[^\W_][\w&]*", entity_name(name) or "")} - _LEGAL_FORMS


def entity_name(name: str | None) -> str | None:
    return clean(_REGISTRATION_NO.sub("", name or ""))


_LABELLED_VALUE = re.compile(r"^(?P<kind>price|pris|volume|mængde|volum|antal)\w*(\(\w+\))?\s*:(?P<value>.*)$", re.I)
_NUMERIC_TOKEN = re.compile(r"-?\d+(?:[.,]\d+)*")
Line = tuple[float | None, float | None, str | None]


def _paired_cells(rows: list[list[str]], nordic: bool) -> list[Line]:
    """Rows of two cells, price then volume, possibly several values per cell on separate lines."""
    lines = []
    for row in rows:
        prices, volumes = row[0].split("\n"), row[1].split("\n")
        if len(prices) != len(volumes):
            return []
        for price_text, volume_text in zip(prices, volumes, strict=True):
            price, currency = amount(price_text, nordic)
            volume, _ = amount(volume_text, nordic)
            if price is None or volume is None:
                return []
            lines.append((price, volume, currency))
    return lines


def _single_cells(rows: list[list[str]], nordic: bool) -> list[Line]:
    """Rows of one cell: "Price(s): 147.50 DKK" / "Volume(s): 10,070" rows, or "NOK 54.624 786" lines. A line is
    split only when it holds exactly two numbers ("21,423 30 000" is ambiguous and left alone)."""
    texts = [row[0] for row in rows]
    labelled = [_LABELLED_VALUE.match(t) for t in texts]
    if any(labelled):
        if not all(labelled):
            return []
        prices = [m.group("value") for m in labelled if m and m.group("kind").casefold() in ("price", "pris")]
        volumes = [m.group("value") for m in labelled if m and m.group("kind").casefold() not in ("price", "pris")]
        if len(prices) != len(volumes):
            return []
        return _paired_cells([[p, v] for p, v in zip(prices, volumes, strict=True)], nordic)
    lines = []
    for text in texts:
        for line in text.split("\n"):
            tokens = [t for t in line.split() if not _HEADER_WORDS.match(t)]
            if not tokens:
                continue
            numbers = [t for t in tokens if _NUMERIC_TOKEN.fullmatch(t)]
            if len(numbers) != 2:
                return []
            currency = next((t.upper() for t in tokens if _CURRENCY.fullmatch(t)), None)
            lines.append((to_number(numbers[0], nordic), to_number(numbers[1], nordic), currency))
    return lines


def price_lines(tx: TemplateTransaction, nordic: bool) -> list[Line]:
    """(price, volume, currency) per price line, or [] when the rows cannot be read unambiguously."""
    pairs = [row for row in tx.price_rows if len(row) == 2]
    singles = [row for row in tx.price_rows if len(row) == 1]
    if len(pairs) + len(singles) != len(tx.price_rows):
        return []
    # Some PDFs yield both a merged cell and the same values split into cells: the split cells win.
    return _paired_cells(pairs, nordic) if pairs else _single_cells(singles, nordic)


def aggregated_line(tx: TemplateTransaction, nordic: bool) -> tuple[float | None, float | None, str | None]:
    """(price, volume, currency) from labelled aggregated text only ("Average price ...: NOK 51.70")."""
    text = " ".join(tx.aggregated)
    price = currency = volume = None
    if m := _PRICE_KEY.search(text):
        price, currency = amount(m.group("value"), nordic)
    if m := _VOLUME_KEY.search(text):
        volume, _ = amount(m.group("value"), nordic)
    if price is None and volume is not None and not volume.is_integer():
        volume = None  # most likely an amount in the volume line; not guessed
    return price, volume, currency


def template_records(filing: TemplateFiling, *, source: str, jurisdiction: str, key: str, ctx: ParseContext,
                     source_url: str | None, published_at: datetime | None, fetched_at: datetime,
                     parser: str, default_currency: str, issuer_fallback: str | None = None,
                     ) -> Iterator[InsiderTransaction]:
    issuer = entity_name(filing.issuer) or issuer_fallback or "unknown issuer"
    if _core_words(filing.declarant) and _core_words(filing.declarant) == _core_words(issuer):
        return  # the issuer named as the notifying person: an issuer's own announcement, not a manager's
    lei = valid_lei((filing.lei or "").split(" ")[0])
    scope = lei or issuer
    declarant = entity_name(filing.declarant) or ""
    position = clean(filing.position)
    is_entity = is_legal_entity(declarant)
    # Managers are natural persons, so a legal entity always notifies as a closely associated person.
    closely_associated = is_entity or bool(_CLOSELY.search(position or ""))
    linked = None
    if closely_associated and (m := _LINKED.search(position or "")):
        linked = clean(m.group("name"))
    notifier_id = ctx.person_id(declarant, scope=scope)
    pdmr_id = ctx.person_id(linked, scope=scope) if linked else notifier_id
    # A closely associated person's position text describes the manager, usually by name: only the
    # normalized role is kept. A manager's own position is kept with their name redacted.
    position_raw = None if closely_associated else scrub_names(position, [declarant] if not is_entity else [],
                                                              [issuer])
    amendment = (filing.amendment or "").casefold()
    is_amendment = bool(re.search(r"amend|ændring|endring|correct|korriger", amendment)) and not re.search(
        r"^(initial|første|first)", amendment)
    for t, tx in enumerate(filing.transactions):
        description = clean(" ".join(tx.description))
        nature_raw = clean(tx.nature) or ""
        nature, direction = normalize_nature(nature_raw)
        lines = price_lines(tx, filing.nordic)
        if not lines:
            lines = [aggregated_line(tx, filing.nordic)]
        venue = clean(tx.venue)
        when = find_date(tx.date_text)
        if when and published_at and when > published_at.date():
            when = None  # a typo in the notification ("08.10.2028" published in 2026)
        instrument = clean(_ISIN_SPACED.sub("", description or "")) or description
        for n, (price, volume, currency) in enumerate(lines):
            currency = default_currency if currency in (None, "kr") else currency
            yield InsiderTransaction(
                record_id=record_id(source, key, t, n),
                source=source,
                jurisdiction=jurisdiction,
                source_url=source_url,
                published_at=published_at,
                transaction_date=when,
                issuer_name=issuer,
                issuer_lei=lei,
                pdmr_id=pdmr_id,
                pdmr_role=normalize_role(position),
                pdmr_role_raw=position_raw,
                notifier_id=notifier_id,
                notifier_name=declarant if is_entity else None,
                notifier_is_legal_entity=is_entity,
                closely_associated=closely_associated,
                is_amendment=is_amendment,
                nature=nature,
                nature_raw=scrub_names(nature_raw, [declarant] if not is_entity else [], [issuer]) or "",
                direction=direction,
                instrument_type=normalize_instrument(description),
                instrument_type_raw=instrument,
                instrument_name=instrument,
                isin=find_isin(description),
                volume=volume,
                price=price,
                currency=currency,
                amount=price * volume if price is not None and volume is not None else None,
                venue=venue,
                on_venue=None if venue is None else not _OFF_VENUE.search(venue),
                fetched_at=fetched_at,
                parser=parser,
            )
