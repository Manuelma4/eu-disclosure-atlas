from datetime import date, timedelta

import pytest
from shorts_support import doc, isin

from disclosure_atlas.connectors.nl_afm_shorts import NetherlandsAFMShorts
from disclosure_atlas.lake import Lake
from disclosure_atlas.pipeline import ingest

HEADER = '"Positie houder";"Naam van de emittent";"ISIN";"Netto Shortpositie";"Positiedatum"'
FOOTER = '"Disclaimer CSV"\r\n"Datum laatste update 20 Oct 2026"\r\n'


def csv_bytes(*rows: str) -> bytes:
    return ("\r\n".join([HEADER, *rows]) + "\r\n" + FOOTER).encode("cp1252")


def test_parse_windows_1252_and_footer(ctx):
    content = csv_bytes(
        f'"Caisse de dépôt Example";"Example Chips N.V.";"{isin("NL")}";"0.78";"2026-10-16 00:00:00"',
        f'"Caisse de dépôt Example";"Example Chips N.V.";"{isin("NL")}";"0.48";"2026-10-17 00:00:00"',
    )
    first, second = NetherlandsAFMShorts(http=object()).parse(doc("nl_afm_shorts", content, ".csv"), ctx)
    assert first.holder_name == "Caisse de dépôt Example" and first.issuer_name == "Example Chips N.V."
    assert (first.position_pct, first.position_date, first.isin) == (0.78, date(2026, 10, 16), isin("NL"))
    assert second.below_threshold and second.position_pct == 0.48


@pytest.mark.network
def test_live_current_and_historic(tmp_path):
    end = date.today()
    report = ingest(NetherlandsAFMShorts(), Lake(tmp_path), end - timedelta(days=30), end)
    assert report.documents == 2 and report.records > 0
