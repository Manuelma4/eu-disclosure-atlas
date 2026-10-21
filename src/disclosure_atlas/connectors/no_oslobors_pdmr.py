"""Norway - managers' transactions published on Oslo Børs NewsWeb (MAR Article 19).

Source notes and quirks: docs/sources/no_oslobors_pdmr.md. In short:
- NewsWeb (Euronext Oslo Børs, the Norwegian officially appointed mechanism) has a JSON API used by its own
  front end: ``list`` by category and date range, ``message`` for one announcement, ``attachment`` for its files.
  Category 1102 is "Mandatory notification of trade primary insiders" (managers' transactions).
- The transaction details are in the attachments. Most are Finanstilsynet's own form KRT-1500 printed to PDF
  (two layouts: the labelled form "Skjemaversjon 46345" and, since 2026, a numbered form); the others are
  issuer-made 2016/523 templates, read with ``_mar_template``.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from ..models import Dataset, InsiderTransaction
from ..normalize import (
    clean,
    normalize_instrument,
    normalize_nature,
    normalize_role,
    record_id,
    valid_isin,
    valid_lei,
)
from ._mar_template import TemplateError, read_document, template_records, to_number
from .base import Connector, ParseContext, RawDocument, date_windows, register
from .fr_amf_pdmr import pdf_text

API_URL = "https://api3.oslo.oslobors.no/v1/newsreader"
MESSAGE_URL = "https://newsweb.oslobors.no/message/{id}"
CATEGORY_MANAGERS_TRANSACTIONS = 1102


class FormError(ValueError):
    """The attachment is not a KRT-1500 form."""


# --- KRT-1500 -----------------------------------------------------------------------------------------

_SECTIONS = [(name, re.compile(pattern, re.I)) for name, pattern in [
    ("reporter", r"^(\d[\d.]*\s+)?(informasjon om (den som rapporterer|rapportør)|info about the one who reports)"),
    ("pdmr", r"^(\d[\d.]*\s+)?(om primærinnsider|about (the )?pdmr)$"),
    ("ca_entity", r"^(\d[\d.]*\s+)?(om )?(nærstående foretak|about (the )?closely associated (legal person|company"
                  r"|entity))$"),
    ("ca_person", r"^(\d[\d.]*\s+)?(om )?(nærstående person|about (the )?closely associated (natural )?person)$"),
    ("issuer", r"^(\d[\d.]*\s+)?(informasjon om utstederen|details about the issuer)"),
    ("transaction", r"^(\d[\d.]*\s+)?(beskrivelse av det finansielle instrumentet|description of the financial "
                    r"instrument|den meldepliktige transaksjonen)"),
    ("correction", r"^(\d[\d.]*\s+)?(beskrivelse av korrigering|description of (the )?amendment)"),
    ("comment", r"^(\d[\d.]*\s+)?(kommentar|comment)$"),
]]
# Field labels: (field, pattern). The value follows on the same line, after " : " in the numbered layout.
_FIELDS = [(name, re.compile(pattern, re.I)) for name, pattern in [
    ("reporting_as", r"^jeg rapporterer som( / på vegne av)?:?"),
    ("new_or_correction", r"^er dette en ny melding eller korrigering av tidligere meldt transaksjon\??"),
    ("last_name", r"^(etternavn|last name)\b"),
    ("first_name", r"^(fornavn|first name)\b"),
    ("full_name", r"^(fullt navn|full name)\b"),
    ("company", r"^(foretaksnavn|business name|company name)( \(til [^)]*\))?"),
    ("lei", r"^(lei-nummer|lei number|lei-kode)( \(til [^)]*\))?"),
    ("role", r"^(stilling ?/ ?rolle|position ?/ ?role)\b"),
    ("instrument", r"^instrument\b"),
    ("isin", r"^(isin-kode|isin code)\b"),
    ("nature", r"^(transaksjonstype|transaction type)\b"),
    ("currency", r"^(valuta|currency)\b"),
    ("average_price", r"^(gjennomsnittlig pris per enhet|average price per unit)\b"),
    ("aggregated_volume", r"^(aggregert volum|aggregated volume)\b"),
    ("price", r"^(pris per enhet|price per unit)\b"),
    ("volume", r"^(volum|volume)\b"),
    ("date", r"^(angi dato|dato|date)\b"),
    ("venue", r"^(handelsplass|trading venue)\b"),
    ("linked", r"^(er transaksjonen knyttet til utøvelse av et aksjeopsjonsprogram\??)"),
]]
_NUMBERING = re.compile(r"^\d+(?:\.\d+)+\s+")
_EMPTY = re.compile(r"^du har ikke lagt inn informasjon her$", re.I)
_SELECTED = re.compile(r"\(\s*[•●]\s*\)\s*([^()Q0O©Ø]+)")


@dataclass
class KRTTransaction:
    instrument: str | None = None
    isin: str | None = None
    nature: str | None = None
    currency: str | None = None
    lines: list[list[str | None]] = field(default_factory=list)
    average_price: str | None = None
    aggregated_volume: str | None = None
    date: str | None = None
    venue: str | None = None
    linked: str | None = None


@dataclass
class KRTForm:
    english: bool = False
    pdmr_name: str | None = None
    role: str | None = None
    ca_entity: str | None = None
    ca_person: str | None = None
    issuer: str | None = None
    lei: str | None = None
    reporting_as: str | None = None
    amendment: bool = False
    transactions: list[KRTTransaction] = field(default_factory=list)


def _name(parts: dict[str, str]) -> str | None:
    if parts.get("full_name"):
        return clean(parts["full_name"])
    return clean(f"{parts.get('first_name', '')} {parts.get('last_name', '')}")


def parse_krt(text: str) -> KRTForm:
    """Read a KRT-1500 form (either layout) from its PDF text; raises ``FormError`` when it is not one."""
    if not re.search(r"KRT\s?-?\s?1500", text):
        raise FormError("not a KRT-1500 form")
    form = KRTForm(english="Template for notification" in text)
    lines = [c for c in (clean(line) for line in text.splitlines()) if c]
    section = None
    names: dict[str, dict[str, str]] = {}
    pending: str | None = None  # numbered layout: a label whose value is on the next line
    tx: KRTTransaction | None = None
    correction_text = False
    for line in lines:
        if selected := _SELECTED.findall(line):  # scanned forms mark the chosen box with "(•)"
            chosen = " ".join(clean(s) or "" for s in selected).casefold()
            if "korrigering" in chosen or "amendment" in chosen:
                form.amendment = True
            if "nærstående" in chosen:
                form.reporting_as = chosen
            continue
        stripped = _NUMBERING.sub("", line)
        matched_section = next((name for name, p in _SECTIONS if p.match(line) or p.match(stripped)), None)
        if matched_section:
            section, pending = matched_section, None
            if section == "transaction" and (tx is None or tx.nature or tx.isin):
                tx = KRTTransaction()
                form.transactions.append(tx)
            continue
        if section == "correction" and not re.match(r"^(skjemaversjon|[\d.:/ APM]+$)", stripped, re.I):
            correction_text = True  # the free text itself is never read
            continue
        key, value = None, None
        for name, pattern in _FIELDS:
            match = pattern.match(stripped)
            if match:
                key = name
                value = clean(stripped[match.end():].lstrip(" :"))
                break
        if key is None:
            if pending and not _EMPTY.match(stripped):
                key, value = pending, stripped
            else:
                continue
        pending = None
        if not value:
            pending = key  # the numbered layout prints some values on the next line
            continue
        if _EMPTY.match(value):
            continue
        if key == "reporting_as":
            form.reporting_as = value.casefold()
        elif key == "new_or_correction":
            form.amendment = form.amendment or value.casefold().startswith(("korrig", "amend"))
        elif key in ("last_name", "first_name", "full_name"):
            names.setdefault(section or "", {})[key] = value
        elif key == "company":
            if section == "issuer":
                form.issuer = form.issuer or value
            elif section == "ca_entity":
                form.ca_entity = value
        elif key == "lei":
            if section == "issuer":
                form.lei = value
        elif key == "role":
            form.role = value
        elif section == "transaction" or tx is not None:
            if tx is None:
                tx = KRTTransaction()
                form.transactions.append(tx)
            if key == "instrument":
                tx.instrument = value
            elif key == "isin":
                tx.isin = value
            elif key == "nature":
                tx.nature = value
            elif key == "currency":
                tx.currency = value
            elif key == "price":
                tx.lines.append([value, None])
            elif key == "volume":
                if tx.lines and tx.lines[-1][1] is None:
                    tx.lines[-1][1] = value
                else:
                    tx.lines.append([None, value])
            elif key == "average_price":
                tx.average_price = value
            elif key == "aggregated_volume":
                tx.aggregated_volume = value
            elif key == "date":
                tx.date = value
            elif key == "venue":
                tx.venue = value
            elif key == "linked":
                tx.linked = value
    form.amendment = form.amendment or correction_text
    form.pdmr_name = _name(names.get("pdmr", {}))
    form.ca_person = _name(names.get("ca_person", {})) or None
    form.transactions = [t for t in form.transactions if t.nature or t.isin or t.lines]
    if not form.pdmr_name:
        raise FormError("KRT-1500 without the manager's name")
    if not form.transactions:
        raise FormError("KRT-1500 without a transaction")
    return form


def krt_date(text: str | None, english: bool, published: date | None) -> date | None:
    """'18.02.2026', '2026-02-18' or, in the English layout, US-style '2/3/2026' (checked against publication)."""
    text = clean(text) or ""
    if m := re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", text):
        candidates = [(int(m.group(1)), int(m.group(2)), int(m.group(3)))]
    elif m := re.search(r"\b(\d{1,2})[./](\d{1,2})[./](\d{4})\b", text):
        a, b, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        us = english and "/" in m.group(0)
        candidates = [(year, a, b), (year, b, a)] if us else [(year, b, a), (year, a, b)]
    else:
        return None
    dates = []
    for year, month, day in candidates:
        try:
            dates.append(date(year, month, day))
        except ValueError:
            continue
    if published:
        dates = [d for d in dates if d <= published] or dates
    return dates[0] if dates else None


_OFF_VENUE = re.compile(r"\bxoff\b|\bxxxx\b|utenfor|outside", re.I)
_LEGAL_WORDS = {"as", "asa", "ab", "aps", "a/s", "ltd", "limited", "holding", "holdings", "invest", "investment",
                "investments", "capital", "eiendom", "group", "og", "and", "&"}


def _words(name: str | None) -> set[str]:
    return {w.casefold() for w in re.findall(r"[^\W\d_][\w'’-]*", name or "")}


def krt_records(form: KRTForm, *, source: str, key: str, ctx: ParseContext, source_url: str | None,
                published_at: datetime | None, fetched_at: datetime, parser: str,
                issuer_fallback: str | None, is_superseded: bool) -> Iterator[InsiderTransaction]:
    issuer = clean(form.issuer) or issuer_fallback or "unknown issuer"
    lei = valid_lei(form.lei)
    scope = lei or issuer
    reporting = form.reporting_as or ""
    entity = clean(form.ca_entity) if form.ca_entity and "person" not in reporting else None
    person = clean(form.ca_person) if not entity else None
    closely_associated = bool(entity or person or "nærstående" in reporting)
    pdmr_id = ctx.person_id(form.pdmr_name, scope=scope)
    declarant = entity or person or form.pdmr_name
    notifier_id = ctx.person_id(declarant, scope=scope) if declarant != form.pdmr_name else pdmr_id
    # The form says the declarant is a company ("nærstående foretak"); one named after the manager
    # ("<surname> Holding AS") would identify them, so it stays pseudonymous.
    is_entity = bool(entity) and not (_words(entity) - _LEGAL_WORDS) & _words(form.pdmr_name)
    published = published_at.date() if published_at else None
    for t, tx in enumerate(form.transactions):
        nature, direction = normalize_nature(tx.nature)
        lines = tx.lines or [[tx.average_price, tx.aggregated_volume]]
        venue = clean(tx.venue)
        for n, (price_text, volume_text) in enumerate(lines):
            price = to_number(price_text, nordic=not form.english) if price_text else None
            volume = to_number(volume_text, nordic=not form.english) if volume_text else None
            yield InsiderTransaction(
                record_id=record_id(source, key, t, n),
                source=source,
                jurisdiction="NO",
                source_url=source_url,
                published_at=published_at,
                transaction_date=krt_date(tx.date, form.english, published),
                issuer_name=issuer,
                issuer_lei=lei,
                pdmr_id=pdmr_id,
                pdmr_role=normalize_role(form.role),
                pdmr_role_raw=clean(form.role),
                notifier_id=notifier_id,
                notifier_name=entity if is_entity else None,
                notifier_is_legal_entity=is_entity,
                closely_associated=closely_associated,
                is_amendment=form.amendment,
                linked_to_share_option_programme=(
                    None if not tx.linked else tx.linked.casefold().startswith(("ja", "yes"))),
                nature=nature,
                nature_raw=clean(tx.nature) or "",
                direction=direction,
                instrument_type=normalize_instrument(tx.instrument),
                instrument_type_raw=clean(tx.instrument),
                isin=valid_isin(tx.isin),
                volume=volume,
                price=price,
                currency=clean(tx.currency),
                amount=price * volume if price is not None and volume is not None else None,
                venue=venue,
                on_venue=None if venue is None else not _OFF_VENUE.search(venue),
                is_superseded=is_superseded,
                fetched_at=fetched_at,
                parser=parser,
            )


def read_attachment(content: bytes) -> tuple[str, Any]:
    """('krt', KRTForm) or ('template', [TemplateFiling]); raises ``TemplateError`` when neither reads."""
    start = content.find(b"%PDF-")
    if 0 <= start < 1024:
        try:
            text = pdf_text(content[start:])
        except Exception as exc:  # unreadable PDF (pdfminer raises many types)
            raise TemplateError(f"unreadable PDF: {type(exc).__name__}") from exc
        if re.search(r"KRT\s?-?\s?1500", text):
            try:
                return "krt", parse_krt(text)
            except FormError as exc:
                raise TemplateError(str(exc)) from exc
    return "template", read_document(content)


# --- Connector ------------------------------------------------------------------------------------------

def _published(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


@register
class NorwayOsloBorsPDMR(Connector):
    name = "no_oslobors_pdmr"
    dataset = Dataset.INSIDER_TRANSACTIONS
    jurisdiction = "NO"
    authority = "Oslo Børs NewsWeb (forms of Finanstilsynet)"
    homepage = "https://newsweb.oslobors.no/search?category=1102"
    window_days = 7
    parser_version = 2  # 2: decimal-point prices checked against the stated total

    def fetch(self, start: date, end: date) -> Iterator[RawDocument]:
        for window_start, window_end in date_windows(start, end, self.window_days):
            yield from self._fetch_window(window_start, window_end)

    def _fetch_window(self, start: date, end: date) -> Iterator[RawDocument]:
        params = {"category": CATEGORY_MANAGERS_TRANSACTIONS, "fromDate": start.isoformat(),
                  "toDate": end.isoformat()}
        response = self.http.post(f"{API_URL}/list?" + "&".join(f"{k}={v}" for k, v in params.items()))
        data = response.json().get("data") or {}
        messages = data.get("messages") or []
        if data.get("overflow") and start < end:
            middle = start + (end - start) // 2
            yield from self._fetch_window(start, middle)
            yield from self._fetch_window(middle + timedelta(days=1), end)
            return
        unread = 0
        for item in messages:
            readable = False
            for doc in self._fetch_message(item):
                readable = readable or bool(doc.meta.get("use"))
                yield doc
            unread += not readable
        meta: dict[str, Any] = {"kind": "listing", "window": [start.isoformat(), end.isoformat()],
                                "rows": len(messages), "unread": unread, "truncated": bool(data.get("overflow"))}
        yield RawDocument(
            source=self.name, url=str(response.url), params=params, content=response.content,
            content_type=response.headers.get("content-type"), fetched_at=datetime.now(UTC), suffix=".json",
            meta=meta,
        )

    def _fetch_message(self, item: dict[str, Any]) -> Iterator[RawDocument]:
        message_id = item.get("messageId") or item.get("id")
        response = self.http.post(f"{API_URL}/message?messageId={message_id}")
        message = (response.json().get("data") or {}).get("message") or {}
        base = {"message_id": message_id, "issuer": message.get("issuerName") or item.get("issuerName"),
                "published": message.get("publishedTime") or item.get("publishedTime"),
                "superseded": bool(message.get("correctedByMessageId"))}
        yield RawDocument(
            source=self.name, url=str(response.url), params={}, content=response.content,
            content_type=response.headers.get("content-type"), fetched_at=datetime.now(UTC), suffix=".json",
            meta={"kind": "message", **base},
        )
        attachments = []
        for attachment in message.get("attachments") or []:
            url = f"{API_URL}/attachment?messageId={message_id}&attachmentId={attachment['id']}"
            got = self.http.get(url)
            try:
                kind, _ = read_attachment(got.content)
            except TemplateError:
                kind = None
            attachments.append((attachment, got, kind))
        # Issuers sometimes attach the KRT-1500 form and their own template for the same trades: the
        # authority's form wins, so only one of them is read.
        use_kind = "krt" if any(k == "krt" for *_, k in attachments) else "template"
        for attachment, got, kind in attachments:
            name = attachment.get("name") or ""
            yield RawDocument(
                source=self.name, url=str(got.url), params={}, content=got.content,
                content_type=got.headers.get("content-type"), fetched_at=datetime.now(UTC),
                suffix="." + (name.rsplit(".", 1)[-1].lower() if "." in name else "bin")[:5],
                meta={"kind": "attachment", "attachment_id": attachment.get("id"), "form": kind,
                      "use": kind is not None and kind == use_kind, **base},
            )

    def parse(self, doc: RawDocument, ctx: ParseContext) -> Iterator[InsiderTransaction]:
        if doc.meta.get("kind") != "attachment" or not doc.meta.get("use"):
            return
        try:
            kind, parsed = read_attachment(doc.content)
        except TemplateError:
            return
        # Keyed by content: the same form attached to a Norwegian and an English announcement is one record.
        key = doc.sha256[:24]
        common = dict(source=self.name, key=key, ctx=ctx,
                      source_url=MESSAGE_URL.format(id=doc.meta.get("message_id")),
                      published_at=_published(doc.meta.get("published")), fetched_at=doc.fetched_at,
                      parser=self.parser_id, issuer_fallback=doc.meta.get("issuer"))
        if kind == "krt":
            yield from krt_records(parsed, is_superseded=bool(doc.meta.get("superseded")), **common)
            return
        for f, filing in enumerate(parsed):
            for record in template_records(filing, jurisdiction=self.jurisdiction, default_currency="NOK",
                                           **{**common, "key": f"{key}/{f}"}):
                yield record.model_copy(update={"is_superseded": bool(doc.meta.get("superseded"))})

