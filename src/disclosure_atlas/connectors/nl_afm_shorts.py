"""Netherlands - Autoriteit Financiële Markten (AFM) net short positions register.

Source notes and quirks: docs/sources/nl_afm_shorts.md. In short:
- Two CSV exports: current positions and the history (every earlier publication, since November 2012).
- ';'-separated, quoted, Windows-1252, CRLF; two footer lines (disclaimer, last update) follow the data.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from datetime import date

from ._shorts import Row, ShortsConnector, parse_pct, to_date
from .base import RawDocument, register

EXPORT_URL = "https://www.afm.nl/export.aspx"
FILES = {
    "current": "8a46a4ef-f196-4467-a7ab-1ae1cb58f0e7",
    "historic": "3ca31b3d-23d9-4fa2-b846-29c7e3f0e5ff",
}


def decode(content: bytes) -> str:
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return content.decode("cp1252")


@register
class NetherlandsAFMShorts(ShortsConnector):
    name = "nl_afm_shorts"
    jurisdiction = "NL"
    authority = "Autoriteit Financiële Markten (AFM)"
    homepage = "https://www.afm.nl/en/sector/registers/meldingenregisters/netto-shortposities-actueel"

    def fetch(self, start: date, end: date) -> Iterator[RawDocument]:
        for kind, file_type in FILES.items():
            params = {"type": file_type, "format": "csv"}
            response = self.http.get(EXPORT_URL, params=params)
            yield self.document(str(response.url), response, params, start, end, ".csv", file=kind)

    def rows(self, doc: RawDocument) -> Iterator[Row]:
        for row in csv.DictReader(io.StringIO(decode(doc.content)), delimiter=";"):
            pct, below = parse_pct(row.get("Netto Shortpositie"))
            yield Row(
                holder=row.get("Positie houder"),
                issuer=row.get("Naam van de emittent"),
                isin=row.get("ISIN"),
                pct=pct,
                below_threshold=below,
                position_date=to_date(row.get("Positiedatum"), "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"),
            )
