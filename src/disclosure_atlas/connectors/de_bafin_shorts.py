"""Germany - BaFin net short positions, published in the Federal Gazette (Bundesanzeiger).

Source notes and quirks: docs/sources/de_bafin_shorts.md. In short:
- The Bundesanzeiger search ("Netto-Leerverkaufspositionen") offers a CSV download of the result list.
  It is a stateful (Apache Wicket) page: load it, submit the search form with "historical" ticked, then
  follow the CSV link of the result page. The session lives in a cookie.
- The historical result includes the current positions. ','-separated, quoted, UTF-8 with BOM, '0,50'.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterator
from datetime import date

from ._shorts import Row, ShortsConnector, parse_pct, to_date
from .base import RawDocument, register

SEARCH_URL = "https://www.bundesanzeiger.de/pub/en/nlp"
_FORM = re.compile(r'action="([^"]*nlp~filter~form~panel-form)"')
_CSV = re.compile(r'href="([^"]*top~csv~form~panel-form-csv~resource~link)"')
FORM = {
    "fulltext": "", "positionsinhaber": "", "ermittent": "", "isin": "", "positionVon": "", "positionBis": "",
    "datumVon": "", "datumBis": "", "isHistorical": "true", "nlp-search-button": "Search net short positions",
}


@register
class GermanyBaFinShorts(ShortsConnector):
    name = "de_bafin_shorts"
    jurisdiction = "DE"
    authority = "Bundesanstalt für Finanzdienstleistungsaufsicht (BaFin), via the Bundesanzeiger"
    homepage = SEARCH_URL

    def fetch(self, start: date, end: date) -> Iterator[RawDocument]:
        page = self.http.get(SEARCH_URL).text
        form = _FORM.search(page)
        if not form:
            raise RuntimeError("Bundesanzeiger: search form not found; the page layout changed")
        result = self.http.post(form.group(1).replace("&amp;", "&"), data=FORM).text
        link = _CSV.search(result)
        if not link:
            raise RuntimeError("Bundesanzeiger: CSV link not found on the result page")
        response = self.http.get(link.group(1).replace("&amp;", "&"))
        yield self.document(SEARCH_URL, response, {"isHistorical": "true"}, start, end, ".csv")

    def rows(self, doc: RawDocument) -> Iterator[Row]:
        for row in csv.DictReader(io.StringIO(doc.content.decode("utf-8-sig"))):
            pct, below = parse_pct(row.get("Position"))
            yield Row(
                holder=row.get("Positionsinhaber"),
                issuer=row.get("Emittent"),
                isin=row.get("ISIN"),
                pct=pct,
                below_threshold=below,
                position_date=to_date(row.get("Datum")),
            )
