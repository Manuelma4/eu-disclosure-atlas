"""Spain - Comisión Nacional del Mercado de Valores (CNMV) public net short positions.

Source notes and quirks: docs/sources/es_cnmv_shorts.md. In short:
- One Excel 97-2003 workbook with sheets "Última - Current" (latest position per holder and issuer),
  "Serie - Series" (every publication of current positions) and "Anteriores - Previous" (closed positions,
  back to Spain's pre-SSR regime of 2010).
- The LEI column is the issuer's; a position that fell below 0.5% is published as 0.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

from ._shorts import Row, ShortsConnector, parse_pct, read_xls, table_rows, to_date
from .base import RawDocument, register

FILE_URL = "https://www.cnmv.es/DocPortal/Posiciones-Cortas/NetShortPositions.xls"
COLUMNS = {
    "issuer_lei": "LEI",
    "isin": "ISIN",
    "issuer": "Emisor",
    "holder": "Tenedor de la Posición",
    "date": "Fecha posición",
    "pct": "Posición corta",
}


@register
class SpainCNMVShorts(ShortsConnector):
    name = "es_cnmv_shorts"
    jurisdiction = "ES"
    authority = "Comisión Nacional del Mercado de Valores (CNMV)"
    homepage = "https://www.cnmv.es/portal/consultas/busqueda.aspx?id=29"

    def fetch(self, start: date, end: date) -> Iterator[RawDocument]:
        response = self.http.get(FILE_URL)
        yield self.document(str(response.url), response, {}, start, end, ".xls")

    def rows(self, doc: RawDocument) -> Iterator[Row]:
        for sheet in read_xls(doc.content).values():
            for row in table_rows(sheet, COLUMNS):
                pct, below = parse_pct(row["pct"], zero_means_below=True)
                yield Row(
                    holder=row["holder"],
                    issuer=row["issuer"],
                    issuer_lei=row["issuer_lei"],
                    isin=row["isin"],
                    pct=pct,
                    below_threshold=below,
                    position_date=to_date(row["date"]),
                )
