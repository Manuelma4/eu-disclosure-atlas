from datetime import date, timedelta

import pytest
from shorts_support import doc, isin, ods_bytes

from disclosure_atlas.connectors.se_fi_shorts import SwedenFIShorts
from disclosure_atlas.lake import Lake
from disclosure_atlas.pipeline import ingest

TITLE = [
    ["Historiska positioner"],
    ["Betydande korta nettopositioner i aktier (blankning)", None, None, "fi.se/blankning"],
    [None, None, None, "rapportering@fi.se"],
    ["Innehavare av positionen\n(Position holder)", "Namn på emittent\n(Name of the issuer)", "ISIN",
     "Position i procent(Position in per cent)", "Datum för positionen\n(Position date)", "Kommentar(Comment)"],
]


def test_parse_numbers_strings_dates_and_below_threshold(ctx):
    content = ods_bytes(TITLE + [
        ["Example Capital LLP", "Example Robotics AB", isin("SE"), 0.89, "2026-10-19", None],
        ["Example Capital LLP", "Example Robotics AB", isin("SE"), "<0,5", "2026-10-20", None],
        ["EXAMPLE PARTNERS LP", "EXAMPLE ROBOTICS AB\xa0", isin("SE"), "0,53", date(2016, 6, 16), "Technical"],
    ])
    a, b, c = SwedenFIShorts(http=object()).parse(doc("se_fi_shorts", content, ".ods"), ctx)
    assert (a.holder_name, a.issuer_name, a.isin, a.position_pct) == (
        "Example Capital LLP", "Example Robotics AB", isin("SE"), 0.89)
    assert a.position_date == date(2026, 10, 19) and not a.below_threshold
    assert b.position_pct is None and b.below_threshold
    assert c.position_pct == 0.53 and c.position_date == date(2016, 6, 16) and c.issuer_name == "EXAMPLE ROBOTICS AB"


@pytest.mark.network
def test_live_current_and_historic(tmp_path):
    end = date.today()
    report = ingest(SwedenFIShorts(), Lake(tmp_path), end - timedelta(days=30), end)
    assert report.documents == 2 and report.records > 0
