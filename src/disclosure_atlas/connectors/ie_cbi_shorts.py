"""Ireland - Central Bank of Ireland (CBI) public net short positions.

Source notes and quirks: docs/sources/ie_cbi_shorts.md. In short:
- One Excel workbook with a "Current" and a "Historical" sheet; positions that fall below the threshold
  leave the current table after one business day and stay in the historical table.
- Percentages are stored as fractions (0.0105 is 1.05%); a position that fell below 0.5% is published as 0.
- Column A holds the sheet title and the "Last Updated" date, so data starts in column B.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

from ._shorts import Row, ShortsConnector, parse_pct, read_xlsx, table_rows, to_date
from .base import RawDocument, register

FILE_URL = (
    "https://www.centralbank.ie/docs/default-source/regulation/industry-market-sectors/securities-markets/"
    "short-selling-regulation/public-net-short-positions/table-of-significant-net-short-positions-in-shares.xlsx"
)
COLUMNS = {
    "holder": "Position Holder",
    "issuer": "Name of the Issuer",
    "isin": "ISIN",
    "pct": "Net short position",
    "date": "Position Date",
}


@register
class IrelandCBIShorts(ShortsConnector):
    name = "ie_cbi_shorts"
    jurisdiction = "IE"
    authority = "Central Bank of Ireland (CBI)"
    homepage = (
        "https://www.centralbank.ie/regulation/industry-market-sectors/securities-markets/"
        "short-selling-regulation/public-net-short-positions"
    )

    def fetch(self, start: date, end: date) -> Iterator[RawDocument]:
        response = self.http.get(FILE_URL)
        yield self.document(str(response.url), response, {}, start, end, ".xlsx")

    def rows(self, doc: RawDocument) -> Iterator[Row]:
        for sheet in read_xlsx(doc.content).values():
            for row in table_rows(sheet, COLUMNS):
                pct, below = parse_pct(row["pct"], fraction=True, zero_means_below=True)
                yield Row(
                    holder=row["holder"],
                    issuer=row["issuer"],
                    isin=row["isin"],
                    pct=pct,
                    below_threshold=below,
                    position_date=to_date(row["date"], "%Y-%m-%d", "%d/%m/%Y"),
                )
