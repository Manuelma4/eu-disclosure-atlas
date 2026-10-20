"""Belgium - FSMA managers' transactions (MAR Article 19).

Source notes and quirks: docs/sources/be_fsma_pdmr.md. In short:
- A public search (``/en/transaction-search``) lists notifications by publication date, 50 rows per page.
- Each row links to a detail page with one transaction in labelled Drupal fields; numbers carry their exact
  value in a ``content`` attribute next to the Belgian-formatted display value.
- robots.txt asks for a 30 second crawl delay, so the connector waits 30 seconds between requests.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterator
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from ..http import PoliteClient
from ..models import Dataset, InsiderTransaction, Role
from ..normalize import (
    clean,
    normalize_instrument,
    normalize_nature,
    normalize_role,
    record_id,
    valid_isin,
)
from ..privacy import is_legal_entity
from .base import Connector, ParseContext, RawDocument, date_windows, register

BASE_URL = "https://www.fsma.be"
SEARCH_URL = f"{BASE_URL}/en/transaction-search"
CRAWL_DELAY = 30.0  # seconds, from https://www.fsma.be/robots.txt
PAGE_SIZE = 50
TIMEZONE = "Europe/Brussels"

_ROW = re.compile(r"<tr>(.*?)</tr>", re.S)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_DETAIL_LINK = re.compile(r'href="(/en/manager-transaction/[^"]+)"')
_FIELD = re.compile(r'class="field field--name-field-ct-([\w-]+)')
_ITEM = re.compile(r'<(?:div|time)[^>]*class="field__item"[^>]*>(.*?)</div>', re.S)
_CONTENT = re.compile(r'class="field__item"[^>]*content="([^"]*)"|content="([^"]*)"[^>]*class="field__item"')
_NODE = re.compile(r'data-drupal-link-system-path="node/(\d+)"')
_TAGS = re.compile(r"<[^>]+>")
_OFF_VENUE = re.compile(r"\b(off[- ]exchange|outside|otc|hors (bourse|march[ée])|buiten)\b", re.I)
_LINKED_PROGRAMME = re.compile(r"remuneration|option|warrant", re.I)
_DECLARER_ROLES = [
    (re.compile(r"senior executive", re.I), Role.EXECUTIVE),
    (re.compile(r"administrative,? management or supervisory", re.I), Role.BOARD_MEMBER),
]


def _text(fragment: str) -> str | None:
    return clean(html.unescape(_TAGS.sub(" ", fragment)))


def parse_listing(text: str) -> list[tuple[date, str]]:
    """(publication date, detail path) of each row of one result page."""
    rows = []
    for row in _ROW.finditer(text):
        cells = _CELL.findall(row.group(1))
        link = _DETAIL_LINK.search(row.group(1))
        if len(cells) < 2 or not link:
            continue
        try:
            published = datetime.strptime(_text(cells[0]) or "", "%d/%m/%Y").date()
        except ValueError:
            continue
        rows.append((published, html.unescape(link.group(1))))
    return rows


def parse_fields(text: str) -> dict[str, list[str]]:
    """Field name (without the ``field-ct-`` prefix) -> display values; exact numbers as ``<name>@content``."""
    fields: dict[str, list[str]] = {}
    matches = list(_FIELD.finditer(text))
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else min(len(text), match.end() + 4000)
        segment = text[match.end():end]
        name = match.group(1)
        fields[name] = [v for v in (_text(item) for item in _ITEM.findall(segment)) if v]
        content = _CONTENT.search(segment)
        if content:
            fields[f"{name}@content"] = [content.group(1) or content.group(2)]
    if node := _NODE.search(text):
        fields["node"] = [node.group(1)]
    return fields


def be_number(fields: dict[str, list[str]], name: str) -> float | None:
    """The exact value from the ``content`` attribute, else the display value in Belgian format (1.234,5)."""
    for key, belgian in ((f"{name}@content", False), (name, True)):
        value = (fields.get(key) or [None])[0]
        if not value:
            continue
        if belgian:
            value = value.replace(" ", "").replace(".", "").replace(",", ".")
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _first(fields: dict[str, list[str]], name: str) -> str | None:
    return clean(" / ".join(fields.get(name) or [])) or None


def declarer_role(declarer_type: str | None) -> Role:
    for pattern, role in _DECLARER_ROLES:
        if pattern.search(declarer_type or ""):
            return role
    return normalize_role(declarer_type)


@register
class BelgiumFSMAPDMR(Connector):
    name = "be_fsma_pdmr"
    dataset = Dataset.INSIDER_TRANSACTIONS
    jurisdiction = "BE"
    authority = "Financial Services and Markets Authority (FSMA)"
    homepage = SEARCH_URL
    window_days = 31

    def __init__(self, http: PoliteClient | None = None) -> None:
        super().__init__(http or PoliteClient(min_interval=CRAWL_DELAY))

    def fetch(self, start: date, end: date) -> Iterator[RawDocument]:
        for window_start, window_end in date_windows(start, end, self.window_days):
            yield from self._fetch_window(window_start, window_end)

    def _fetch_window(self, start: date, end: date) -> Iterator[RawDocument]:
        page = 0
        while True:
            # Ascending order keeps rows from shifting between pages while new notifications are published.
            params = {"issuer": "", "date[min]": start.isoformat(), "date[max]": end.isoformat(),
                      "order": "field_ct_date_time", "sort": "asc", "page": page}
            response = self.http.get(SEARCH_URL, params=params)
            rows = parse_listing(response.text)
            yield RawDocument(
                source=self.name,
                url=str(response.url),
                params=params,
                content=response.content,
                content_type=response.headers.get("content-type"),
                fetched_at=datetime.now(UTC),
                suffix=".html",
                meta={"kind": "listing", "window": [start.isoformat(), end.isoformat()], "page": page,
                      "rows": len(rows)},
            )
            for published, path in rows:
                detail = self.http.get(BASE_URL + path)
                yield RawDocument(
                    source=self.name,
                    url=str(detail.url),
                    params={},
                    content=detail.content,
                    content_type=detail.headers.get("content-type"),
                    fetched_at=datetime.now(UTC),
                    suffix=".html",
                    meta={"kind": "transaction", "path": path, "published": published.isoformat()},
                )
            if len(rows) < PAGE_SIZE:
                break
            page += 1

    def parse(self, doc: RawDocument, ctx: ParseContext) -> Iterator[InsiderTransaction]:
        if doc.meta.get("kind") != "transaction":
            return
        fields = parse_fields(doc.content.decode("utf-8", errors="replace"))
        declarant = _first(fields, "declarer-name")
        transaction_date = _first(fields, "transaction-date")
        if not declarant or not transaction_date:
            return
        issuer = _first(fields, "issuer") or "unknown issuer"
        scope = issuer
        declarer_type = _first(fields, "declarer-type")
        closely_associated = bool(re.search(r"closely associated", declarer_type or "", re.I))
        # For a closely associated person, the description field names the manager they are linked to.
        linked = _first(fields, "description") if closely_associated else None
        is_entity = is_legal_entity(declarant)
        notifier_id = ctx.person_id(declarant, scope=scope)
        pdmr_id = ctx.person_id(linked, scope=scope) if linked else notifier_id
        transaction_type = _first(fields, "transaction-type") or ""
        spec = _first(fields, "transaction-type-spec")
        nature, direction = normalize_nature(transaction_type)
        if nature == "other" and spec:
            nature, direction = normalize_nature(spec)
        instrument = _first(fields, "instrument-type")
        venue = _first(fields, "transaction-place")
        volume, price = be_number(fields, "transaction-quantity"), be_number(fields, "price")
        amount = be_number(fields, "amount")
        if amount is None and volume is not None and price is not None:
            amount = volume * price
        published = doc.meta.get("published") or None
        if published is None and (shown := _first(fields, "date-time")):
            published = datetime.strptime(shown, "%d/%m/%Y").date().isoformat()
        node = _first(fields, "node") or doc.meta.get("path") or doc.sha256[:16]
        yield InsiderTransaction(
            record_id=record_id(self.name, node),
            source=self.name,
            jurisdiction=self.jurisdiction,
            source_url=doc.url,
            published_at=(
                datetime.fromisoformat(published).replace(tzinfo=ZoneInfo(TIMEZONE)).astimezone(UTC)
                if published else None
            ),
            transaction_date=datetime.strptime(transaction_date, "%d/%m/%Y").date(),
            issuer_name=issuer,
            pdmr_id=pdmr_id,
            pdmr_role=declarer_role(declarer_type),
            pdmr_role_raw=declarer_type,
            notifier_id=notifier_id,
            notifier_name=declarant if is_entity else None,
            notifier_is_legal_entity=is_entity,
            closely_associated=closely_associated,
            linked_to_share_option_programme=bool(spec and _LINKED_PROGRAMME.search(spec)) or None,
            nature=nature,
            nature_raw=" - ".join(p for p in (transaction_type, spec) if p),
            direction=direction,
            instrument_type=normalize_instrument(instrument),
            instrument_type_raw=instrument,
            isin=valid_isin(_first(fields, "instrument-isin-code")),
            volume=volume,
            price=price,
            currency=_first(fields, "transaction-currency"),
            amount=amount,
            venue=venue,
            on_venue=None if venue is None else not _OFF_VENUE.search(venue),
            fetched_at=doc.fetched_at,
            parser=self.parser_id,
        )

