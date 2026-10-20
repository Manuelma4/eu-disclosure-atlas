"""Regenerate es_cnmv_shorts.xls, a synthetic copy of the CNMV workbook layout (no real holder or issuer).

xlwt is only needed here, not by the package or the tests: `pip install xlwt && python make_es_cnmv_shorts_xls.py`.
"""

from pathlib import Path

import xlwt

ISSUER_LEI = "959800EXAMPLE0000180"  # make_lei("959800EXAMPLE00001")
ISIN = "ES0EXAMPLE17"  # make_isin("ES0EXAMPLE1")
HEADER = ["LEI", "ISIN", "Emisor / Issuer", "Tenedor de la Posición / Position holder",
          "Fecha posición / Position date", "Posición corta (%) / Net short position (%)"]
CURRENT = [[ISSUER_LEI, ISIN, "EXAMPLE ENERGIA, S.A.", "Example Capital LLP", " 2026-10-14 ", 0.62]]
SERIES = CURRENT + [[ISSUER_LEI, ISIN, "EXAMPLE ENERGIA, S.A.", "Example Capital LLP", " 2026-09-30 ", 0.55]]
PREVIOUS = [
    [ISSUER_LEI, ISIN, "EXAMPLE ENERGIA, S.A.", "Sample Partners LP", " 2021-02-19 ", 0.0],
    [ISSUER_LEI, ISIN, "EXAMPLE ENERGIA, S.A.", "Sample Partners LP", " 2021-02-18 ", 0.55],
    [ISSUER_LEI, ISIN, "EXAMPLE ENERGIA, S.A.", "Sample Partners LP", " 2010-06-10 ", 0.52],
]


def main() -> None:
    book = xlwt.Workbook()
    dates = book.add_sheet("Fecha_-_Date")
    for i, text in enumerate(["Fecha de Publicación", "Date of publication", " 2026-10-19 "], start=1):
        dates.write(i, 1, text)
    for name, rows in (("Última_-_Current", CURRENT), ("Serie_-_Series", SERIES), ("Anteriores_-_Previous", PREVIOUS)):
        sheet = book.add_sheet(name)
        for col, text in enumerate(HEADER):
            sheet.write(3, col, text)
        for r, row in enumerate(rows, start=4):
            for col, value in enumerate(row):
                sheet.write(r, col, value)
    book.save(str(Path(__file__).with_name("es_cnmv_shorts.xls")))


if __name__ == "__main__":
    main()
