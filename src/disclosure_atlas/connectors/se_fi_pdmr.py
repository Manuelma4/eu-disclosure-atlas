"""Sweden - Finansinspektionen (FI) PDMR transactions register ("Insynsregistret").

Source notes and quirks: docs/sources/se_fi_pdmr.md. In short:
- CSV export of the public register search, UTF-16LE, ';'-separated, one row per transaction.
- The export silently caps at 1,000 rows, so windows that hit the cap are split in halves.
- Status 'Current' is the latest version; 'Revised' and 'Cancelled' rows are superseded.
- Roles use non-breaking spaces inside a role and ', ' between roles.
"""

from __future__ import annotations

import csv
import io
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

from ..models import Dataset, InsiderTransaction
from ..normalize import (
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
    yes,
)
from ..privacy import is_legal_entity
from .base import Connector, ParseContext, RawDocument, date_windows, register

EXPORT_URL = "https://marknadssok.fi.se/publiceringsklient/en-GB/Search/Search"
ROW_CAP = 1000
TIMEZONE = "Europe/Stockholm"
OUTSIDE_VENUE = "outside a trading venue"
SUPERSEDED_STATUSES = {"revised", "cancelled"}


def decode(content: bytes) -> str:
    if content[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return content.decode("utf-16")
    if len(content) > 1 and content[1:2] == b"\x00":
        return content.decode("utf-16-le")
    return content.decode("utf-8-sig")


def read_rows(content: bytes) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(decode(content)), delimiter=";")
    return [row for row in reader if any((v or "").strip() for v in row.values())]


@register
class SwedenFIPDMR(Connector):
    name = "se_fi_pdmr"
    dataset = Dataset.INSIDER_TRANSACTIONS
    jurisdiction = "SE"
    authority = "Finansinspektionen (FI)"
    homepage = "https://www.fi.se/en/our-registers/pdmr-transactions/"
    window_days = 7

    def fetch(self, start: date, end: date) -> Iterator[RawDocument]:
        for window_start, window_end in date_windows(start, end, self.window_days):
            yield from self._fetch_window(window_start, window_end)

    def _fetch_window(self, start: date, end: date) -> Iterator[RawDocument]:
        params = {
            "SearchFunctionType": "Insyn",
            "Publiceringsdatum.From": start.isoformat(),
            "Publiceringsdatum.To": end.isoformat(),
            "button": "export",
        }
        response = self.http.get(EXPORT_URL, params=params)
        content = response.content
        rows = len(read_rows(content)) if content else 0
        if rows >= ROW_CAP and start < end:
            middle = start + (end - start) // 2
            yield from self._fetch_window(start, middle)
            yield from self._fetch_window(middle + timedelta(days=1), end)
            return
        yield RawDocument(
            source=self.name,
            url=str(response.url),
            params=params,
            content=content,
            content_type=response.headers.get("content-type"),
            fetched_at=datetime.now(UTC),
            suffix=".csv",
            meta={"window": [start.isoformat(), end.isoformat()], "rows": rows, "truncated": rows >= ROW_CAP},
        )

    def parse(self, doc: RawDocument, ctx: ParseContext) -> Iterator[InsiderTransaction]:
        seen: Counter[tuple] = Counter()
        for row in read_rows(doc.content):
            issuer = clean(row.get("Issuer")) or "unknown issuer"
            lei = valid_lei(row.get("LEI-code"))
            scope = lei or issuer
            pdmr_name = row.get("Person discharging managerial responsibilities")
            notifier_name = row.get("Notifier") or pdmr_name
            notifier_is_entity = is_legal_entity(notifier_name)
            pdmr_id = ctx.person_id(pdmr_name, scope=scope)
            notifier_id = ctx.person_id(notifier_name, scope=scope)

            nature_raw = clean(row.get("Nature of transaction")) or ""
            nature, direction = normalize_nature(nature_raw)
            instrument_raw = clean(row.get("Intrument type") or row.get("Instrument type"))
            volume = parse_number(row.get("Volume"))
            price = parse_number(row.get("Price"))
            unit = (clean(row.get("Unit")) or "").casefold()
            venue = clean(row.get("Trading venue"))
            status = clean(row.get("Status"))
            published_at = parse_datetime(row.get("Publication date"), "%d/%m/%Y %H:%M:%S", TIMEZONE)
            transaction_date = parse_date((row.get("Transaction date") or "")[:10], "%d/%m/%Y")
            isin = valid_isin(row.get("ISIN"))
            instrument_name = clean(row.get("Instrument name"))

            key = (
                published_at, scope, pdmr_id, notifier_id, nature_raw, instrument_name, isin,
                transaction_date, row.get("Volume"), row.get("Price"), row.get("Currency"), venue,
            )
            occurrence = seen[key]
            seen[key] += 1

            yield InsiderTransaction(
                record_id=record_id(self.name, *key, occurrence),
                source=self.name,
                jurisdiction=self.jurisdiction,
                source_url=self.homepage,
                published_at=published_at,
                transaction_date=transaction_date,
                issuer_name=issuer,
                issuer_lei=lei,
                pdmr_id=pdmr_id,
                pdmr_role=normalize_role(row.get("Position"), separators=r", "),
                pdmr_role_raw=clean(row.get("Position")),
                notifier_id=notifier_id,
                notifier_name=clean(notifier_name) if notifier_is_entity else None,
                notifier_is_legal_entity=notifier_is_entity,
                closely_associated=yes(row.get("Closely associated")),
                is_amendment=yes(row.get("Amendment")),
                amendment_details=clean(row.get("Details of amendment")),
                linked_to_share_option_programme=yes(row.get("Linked to share option programme")),
                nature=nature,
                nature_raw=nature_raw,
                direction=direction,
                instrument_type=normalize_instrument(instrument_raw),
                instrument_type_raw=instrument_raw,
                instrument_name=instrument_name,
                isin=isin,
                volume=volume,
                price=price,
                currency=clean(row.get("Currency")),
                amount=volume * price if volume is not None and price is not None and unit == "quantity" else None,
                venue=venue,
                on_venue=None if venue is None else venue.casefold() != OUTSIDE_VENUE,
                is_superseded=(status or "").casefold() in SUPERSEDED_STATUSES,
                status_raw=status,
                fetched_at=doc.fetched_at,
                parser=self.parser_id,
            )
