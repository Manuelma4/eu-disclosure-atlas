"""Sweden - Finansinspektionen (FI) register of significant net short positions ("Blankningsregistret").

Source notes and quirks: docs/sources/se_fi_shorts.md. In short:
- Two OpenDocument spreadsheets: current positions and historic positions (since November 2012).
- Five title rows precede the header; a position that fell below the threshold reads "<0,5".
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

from ._shorts import Row, ShortsConnector, parse_pct, read_ods, table_rows, to_date
from .base import RawDocument, register

BASE_URL = "https://www.fi.se/BlankningsRegister/"
FILES = {"current": "GetAktuellFile", "historic": "GetHistFile"}
COLUMNS = {
    "holder": "Innehavare av positionen",
    "issuer": "Namn på emittent",
    "isin": "ISIN",
    "pct": "Position i procent",
    "date": "Datum för positionen",
}


@register
class SwedenFIShorts(ShortsConnector):
    name = "se_fi_shorts"
    jurisdiction = "SE"
    authority = "Finansinspektionen (FI)"
    homepage = "https://www.fi.se/en/our-registers/net-short-positions/"

    def fetch(self, start: date, end: date) -> Iterator[RawDocument]:
        for kind, path in FILES.items():
            response = self.http.get(BASE_URL + path)
            yield self.document(str(response.url), response, {}, start, end, ".ods", file=kind)

    def rows(self, doc: RawDocument) -> Iterator[Row]:
        for sheet in read_ods(doc.content).values():
            for row in table_rows(sheet, COLUMNS):
                pct, below = parse_pct(row["pct"])
                yield Row(
                    holder=row["holder"],
                    issuer=row["issuer"],
                    isin=row["isin"],
                    pct=pct,
                    below_threshold=below,
                    position_date=to_date(row["date"]),
                )
