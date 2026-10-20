from datetime import date, datetime

import pytest
from shorts_support import doc, isin, xlsx_bytes

from disclosure_atlas.connectors.ie_cbi_shorts import IrelandCBIShorts
from disclosure_atlas.lake import Lake
from disclosure_atlas.pipeline import ingest

HEADER = ["Table of Current Significant Net Short Positions in Shares", "Position Holder:", "Name of the Issuer:",
          "ISIN:", "Net short position %:", "Position Date:", "Comments:"]


def test_parse_fractions_zero_marker_and_column_a_noise(ctx):
    pytest.importorskip("openpyxl")
    content = xlsx_bytes({
        "Current": [HEADER, [None] * 7, ["Last Updated:"], [datetime(2026, 6, 2)]],
        "Historical": [
            HEADER,
            [None, "Example Capital LLP", "EXAMPLE AIRLINES PLC", isin("IE"), 0, datetime(2026, 5, 29), None],
            [None, "Example Capital LLP", "Example Airlines PLC", isin("IE"), 0.0105, datetime(2026, 5, 28), None],
            ["Last Updated:", "Example Capital LLP", "Example Airlines PLC", isin("IE"), 0.0051, "14/05/2026", None],
        ],
    })
    closed, high, low = IrelandCBIShorts(http=object()).parse(doc("ie_cbi_shorts", content, ".xlsx"), ctx)
    assert closed.position_pct is None and closed.below_threshold and closed.position_date == date(2026, 5, 29)
    assert high.position_pct == 1.05 and not high.below_threshold and high.issuer_name == "Example Airlines PLC"
    assert low.position_pct == 0.51 and low.position_date == date(2026, 5, 14)


@pytest.mark.network
def test_live_workbook(tmp_path):
    report = ingest(IrelandCBIShorts(), Lake(tmp_path), date(2020, 1, 1), date.today())
    assert report.records > 0
