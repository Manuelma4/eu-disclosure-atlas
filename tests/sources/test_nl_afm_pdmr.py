from datetime import UTC, date, datetime

import httpx
import pytest
from conftest import make_isin, make_lei

from disclosure_atlas.connectors.base import RawDocument
from disclosure_atlas.connectors.nl_afm_pdmr import (
    DETAIL_URL,
    EXPORT_URL,
    LATE_FILING_DAYS,
    NetherlandsAFMPDMR,
)
from disclosure_atlas.lake import Lake
from disclosure_atlas.pipeline import ingest

LEI = make_lei("724500EXAMPLE00001")
ISIN = make_isin("NL000EXAMPL")
MELDING = "20261008ABCDEF01-2345-6789-ABCD-EF0123456789"
COLUMNS = ["Instrument type", "ISIN", "Transaction category", "Transaction type", "Stock option program",
           "Trading place", "Price", "Quantity", "Unit"]


def line(**overrides: str) -> dict[str, str]:
    values = {
        "Instrument type": "Gewoon aandeel", "ISIN": ISIN, "Transaction category": "Verwerving",
        "Transaction type": "Koop", "Stock option program": "Nee", "Trading place": "EURONEXT - EURONEXT AMSTERDAM",
        "Price": "12,50", "Quantity": "2.000,00", "Unit": "EUR",
    }
    return values | overrides


def _row(values: dict[str, str], labels: list[str]) -> str:
    cells = "".join(f'<td><span class="cc-mobile-title">{label}</span>{values[key]}</td>'
                    for label, key in zip(labels, COLUMNS, strict=False))
    return f"<tr>\n{cells}</tr>"


def detail_page(lines: list[dict[str, str]], **fields: str) -> bytes:
    """A detail page shaped like the AFM register: a field list, the lines, and the aggregated table whose
    labels are shifted by one column (the real page has that bug)."""
    values = {"Notifiable": "Example J.", "Issuing institution": "Example Holding N.V.", "LEI": LEI,
              "Position/Status": "CFO", "Transaction": "08 oct 2026"} | fields
    items = "".join(
        f'<div class="cc-em--detail-list__item"><span class="cc-em--detail-list__label">{label}</span>\n'
        f'<span class="cc-em--detail-list__value">\n  <span>{value}</span>\n</span></div>'
        for label, value in values.items()
    )
    head = "".join(f"<th>{c}</th>" for c in COLUMNS)
    shifted = [c for c in COLUMNS if c != "Stock option program"] + ["Stock option program"]
    body = "".join(_row(v, COLUMNS) for v in lines)
    aggregated = "".join(_row(v, shifted) for v in lines)
    return (
        f'<html><body><div class="cc-em--detail-list__items">{items}</div>'
        f'<h2 class="cc-em--table__title">Transactions</h2><table><thead><tr>{head}</tr></thead>'
        f"<tbody>{body}</tbody></table>"
        f'<h2 class="cc-em--table__title">Aggregated information</h2><table><tbody>{aggregated}</tbody></table>'
        f"<p>Date last update: 20 October 2026</p></body></html>"
    ).encode()


def doc(content: bytes, melding: str = MELDING, transaction_date: str | None = "2026-10-08") -> RawDocument:
    meta = {"meldingid": melding} | ({"transaction_date": transaction_date} if transaction_date else {})
    return RawDocument(source="nl_afm_pdmr", url=f"{DETAIL_URL}?id={melding}", params={"id": melding},
                       content=content, content_type="text/html", fetched_at=datetime(2026, 10, 20, tzinfo=UTC),
                       suffix=".html", meta=meta)


def parse(content: bytes, ctx, **kwargs):
    return list(NetherlandsAFMPDMR(http=object()).parse(doc(content, **kwargs), ctx))


def test_open_market_purchase(ctx):
    (tx,) = parse(detail_page([line()]), ctx)
    assert tx.source == "nl_afm_pdmr" and tx.jurisdiction == "NL"
    assert tx.issuer_name == "Example Holding N.V." and tx.issuer_lei == LEI
    assert tx.published_at is None and tx.transaction_date == date(2026, 10, 8)
    assert (tx.nature, tx.direction, tx.instrument_type) == ("buy", 1, "share")
    assert tx.nature_raw == "Verwerving / Koop" and tx.pdmr_role == "cfo"
    assert tx.isin == ISIN and tx.currency == "EUR"
    assert tx.volume == 2000.0 and tx.price == 12.5 and tx.amount == pytest.approx(25000.0)
    assert tx.on_venue is True and tx.linked_to_share_option_programme is False
    assert tx.source_url.endswith(f"?id={MELDING}") and tx.parser == "nl_afm_pdmr@1"


def test_only_the_transactions_table_is_read(ctx):
    txs = parse(detail_page([line(), line(**{"Transaction category": "Vervreemding", "Transaction type": "Verkoop",
                                             "Trading place": "OTC"})]), ctx)
    assert [(t.nature, t.direction, t.on_venue) for t in txs] == [("buy", 1, True), ("sell", -1, False)]
    assert len({t.record_id for t in txs}) == 2


def test_names_of_natural_persons_never_reach_the_record(ctx):
    (tx,) = parse(detail_page([line()], **{"Notifiable": "Exemplar Q."}), ctx)
    assert "exemplar" not in tx.model_dump_json().casefold()
    assert tx.pdmr_id == tx.notifier_id and tx.notifier_name is None and not tx.closely_associated


def test_closely_associated_person_and_a_name_in_the_position(ctx):
    content = detail_page([line()], **{"Notifiable": "Exemplar M.", "Closely affiliated": "Exemplar Q.",
                                       "Position/Status": "Mirabel Exemplar"})
    (tx,) = parse(content, ctx)
    assert tx.closely_associated and tx.notifier_id != tx.pdmr_id
    assert tx.pdmr_role_raw is None and "mirabel" not in tx.model_dump_json().casefold()


def test_closely_associated_legal_entity_keeps_its_name(ctx):
    content = detail_page([line()], **{"Notifiable": "Example Beheer B.V.", "Closely affiliated": "Exemplar Q."})
    (tx,) = parse(content, ctx)
    assert tx.notifier_is_legal_entity and tx.notifier_name == "Example Beheer B.V."


@pytest.mark.parametrize(("category", "kind", "nature", "direction"), [
    ("Verwerving", "Omwisseling i.v.m. uitoefening", "exercise", 1),
    ("Vervreemding", "Uitoefening ((personeels-)opties)", "exercise", -1),
    ("Verwerving", "Omwisseling van soort effect", "conversion", 1),
    ("Vervreemding", "Schenking/gift", "gift", -1),
    ("Vervreemding", "Uitlenen", "lending", 0),
    ("Verwerving", "Koop uitgevoerd door de beheerder van een abi", "buy", 1),
    ("Verwerving", "Verwerving", "other", 1),
    ("Verwerving", "Dividend", "other", 1),
    ("Vervreemding", "Overdracht", "other", -1),
])
def test_dutch_transaction_types(ctx, category, kind, nature, direction):
    (tx,) = parse(detail_page([line(**{"Transaction category": category, "Transaction type": kind})]), ctx)
    assert (tx.nature, tx.direction) == (nature, direction)


@pytest.mark.parametrize(("raw", "instrument"), [
    ("Gewoon aandeel", "share"), ("Aandelen A", "share"), ("Depository Receipt", "share"),
    ("Personeelsoptie", "option"), ("Achtergestelde obligatie", "bond"),
    ("Conditional share award", "other"), ("Restricted share right", "other"),
])
def test_dutch_instrument_types(ctx, raw, instrument):
    (tx,) = parse(detail_page([line(**{"Instrument type": raw})]), ctx)
    assert tx.instrument_type == instrument and tx.instrument_type_raw == raw


@pytest.mark.parametrize(("position", "role"), [
    ("Business Group President, Home Care", "executive"), ("lid Management Board", "executive"),
    ("Voorzitter Raad van Commissarissen", "chair"), ("commissaris", "board_member"),
    ("chairman Board of Directors", "chair"), ("President and CEO", "ceo"),
    ("Non-Executive Director", "board_member"), ("Non\u2010Executive Director", "board_member"),
    ("Executive Director", "executive"),
])
def test_positions(ctx, position, role):
    (tx,) = parse(detail_page([line()], **{"Position/Status": position}), ctx)
    assert tx.pdmr_role == role


def test_free_award_and_option_programme(ctx):
    (tx,) = parse(detail_page([line(**{"Transaction type": "Verwerving", "Stock option program": "Ja",
                                       "Trading place": "OTC", "Price": "0,00", "ISIN": ""})]), ctx)
    assert tx.linked_to_share_option_programme is True and tx.amount == 0.0 and tx.isin is None


def test_transaction_date_falls_back_to_the_notification_id(ctx):
    (tx,) = parse(detail_page([line()]), ctx, transaction_date=None)
    assert tx.transaction_date == date(2026, 10, 8)


def test_invalid_lei_is_dropped(ctx):
    (tx,) = parse(detail_page([line()], LEI="NOT-A-LEI"), ctx)
    assert tx.issuer_lei is None


def export_xml(entries: list[tuple[str, str]]) -> bytes:
    items = "".join(
        f"<vermelding><meldingid>{m}</meldingid><transactiedatum>{d}</transactiedatum>"
        f"<uitgevendeinstelling>Example Holding N.V.</uitgevendeinstelling></vermelding>"
        for m, d in entries
    )
    return f'<register name="transacties-leidinggevenden-mar19-">{items}</register>'.encode()


class FakeHTTP:
    def __init__(self, entries, missing=()):
        self.entries, self.missing, self.calls = entries, set(missing), []

    def get(self, url, params=None):
        self.calls.append((url, dict(params or {})))
        request = httpx.Request("GET", url, params=params)
        if url == EXPORT_URL:
            return httpx.Response(200, content=export_xml(self.entries), request=request)
        if params["id"] in self.missing:
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError("not found", request=request, response=response)
        return httpx.Response(200, content=detail_page([line()]), headers={"content-type": "text/html"},
                              request=request)


def test_fetch_selects_by_transaction_date_with_a_late_filing_grace():
    entries = [
        ("20261012AAAA", "10/12/2026 12:00:00 AM"),  # inside the window
        ("20260925BBBB", "9/25/2026 12:00:00 AM"),   # before the window, within the grace period
        ("20260901CCCC", "9/1/2026 12:00:00 AM"),    # too old
        ("20261020DDDD", "10/20/2026 12:00:00 AM"),  # after the window
        ("20261011EEEE", "10/11/2026 12:00:00 AM"),  # inside, but the page is gone
    ]
    http = FakeHTTP(entries, missing={"20261011EEEE"})
    docs = list(NetherlandsAFMPDMR(http=http).fetch(date(2026, 10, 1), date(2026, 10, 15)))
    assert LATE_FILING_DAYS >= 7
    assert [d.params["id"] for d in docs] == ["20261012AAAA", "20260925BBBB"]
    assert docs[0].meta["transaction_date"] == "2026-10-12" and docs[0].suffix == ".html"
    assert http.calls[0] == (EXPORT_URL, {"type": http.calls[0][1]["type"], "format": "xml"})


def test_ingest_end_to_end(tmp_path):
    http = FakeHTTP([("20261012AAAA", "10/12/2026 12:00:00 AM")])
    report = ingest(NetherlandsAFMPDMR(http=http), Lake(tmp_path), date(2026, 10, 1), date(2026, 10, 15))
    assert report.documents == 1 and report.records == 1


@pytest.mark.network
def test_live_register_parses(tmp_path):
    end = date.today()
    report = ingest(NetherlandsAFMPDMR(), Lake(tmp_path), date.fromordinal(end.toordinal() - 3), end)
    assert report.documents >= 1 and report.records >= 1
    con = Lake(tmp_path).connect()
    assert con.execute("SELECT count(*) FROM insider_transactions WHERE issuer_lei IS NULL").fetchone()[0] == 0
