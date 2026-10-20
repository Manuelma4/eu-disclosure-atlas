"""Belgium FSMA insider connector. All pages here are synthetic: no real person appears in the test suite."""

from __future__ import annotations

from datetime import UTC, date, datetime

import httpx
import pytest
from conftest import make_isin

from disclosure_atlas.connectors.base import ParseContext, RawDocument
from disclosure_atlas.connectors.be_fsma_pdmr import (
    BASE_URL,
    CRAWL_DELAY,
    SEARCH_URL,
    BelgiumFSMAPDMR,
    be_number,
    parse_fields,
    parse_listing,
)
from disclosure_atlas.lake import Lake
from disclosure_atlas.pipeline import ingest

BE_ISIN = make_isin("BE000EXAMPL")


def field(name: str, value: str, content: str | None = None, typ: str = "string") -> str:
    attr = f' content="{content}"' if content is not None else ""
    return (f'<div class="field field--name-field-ct-{name} field--type-{typ} field--label-inline">\n'
            f'    <div class="field__label">{name}</div>\n'
            f'              <div{attr} class="field__item">{value}</div>\n  </div>\n')


def detail_html(declarant="Exemple Jan", declarer_type="Senior executive", description=None,
                transaction_type="Purchase / Acquisition", spec=None, place="Euronext Brussels",
                quantity=("10.000", "10000"), price=("2,50", "2.500"), amount=("25.000,00", "25000.000"),
                node="640001", body="Comment naming Exemple Jan"):
    fields = [
        field("date-time", "16/10/2026", typ="datetime"), field("declarer-name", declarant),
        field("declarer-type", declarer_type, typ="entity-reference"),
        *([field("description", description)] if description else []),
        field("issuer", "EXEMPLE GROUP", typ="entity-reference"), field("instrument-type", "Share"),
        field("instrument-isin-code", BE_ISIN), field("transaction-type", transaction_type),
        *([field("transaction-type-spec", spec)] if spec else []),
        field("transaction-place", place),
        field("transaction-date", '<time datetime="2026-10-09T19:45:34Z" class="datetime">09/10/2026</time>\n',
              typ="datetime"),
        field("transaction-currency", "EUR"), field("transaction-quantity", quantity[0], quantity[1], "integer"),
        field("price", price[0], price[1], "decimal"), field("amount", amount[0], amount[1], "decimal"),
        field("body", body),
    ]
    return (f'<html><head><title>EXEMPLE GROUP | FSMA</title></head><body>'
            f'<li data-drupal-link-system-path="node/{node}" class="language-switcher__item">EN</li>'
            f'<article>{"".join(fields)}</article></body></html>')


def doc_of(page: str, published="2026-10-16") -> RawDocument:
    return RawDocument(
        source="be_fsma_pdmr", url=f"{BASE_URL}/en/manager-transaction/exemple-group-1", params={},
        content=page.encode("utf-8"), content_type="text/html", fetched_at=datetime(2026, 10, 20, 6, tzinfo=UTC),
        suffix=".html", meta={"kind": "transaction", "path": "/en/manager-transaction/exemple-group-1",
                              "published": published},
    )


def parse(page: str, ctx):
    return list(BelgiumFSMAPDMR(http=object()).parse(doc_of(page), ctx))


def test_senior_executive_purchase(ctx):
    (tx,) = parse(detail_html(), ctx)
    assert tx.source == "be_fsma_pdmr" and tx.jurisdiction == "BE"
    assert tx.issuer_name == "EXEMPLE GROUP" and tx.issuer_lei is None and tx.isin == BE_ISIN
    assert tx.published_at == datetime(2026, 10, 15, 22, tzinfo=UTC)  # Brussels midnight, CEST
    assert tx.transaction_date == date(2026, 10, 9)
    assert (tx.nature, tx.direction, tx.instrument_type) == ("buy", 1, "share")
    assert tx.pdmr_role == "executive" and tx.pdmr_role_raw == "Senior executive"
    assert tx.volume == 10000.0 and tx.price == 2.5 and tx.amount == 25000.0  # not 10.0: Belgian thousands
    assert tx.on_venue is True and tx.currency == "EUR"
    assert tx.record_id == parse(detail_html(), ctx)[0].record_id
    assert tx.parser == "be_fsma_pdmr@1"


def test_names_of_natural_persons_never_reach_the_record(ctx):
    (tx,) = parse(detail_html(), ctx)
    assert "exemple jan" not in tx.model_dump_json().casefold()
    assert tx.notifier_name is None and tx.pdmr_id == tx.notifier_id


def test_closely_associated_entity_links_to_the_described_manager(ctx):
    page = detail_html(declarant="Société Exemple de Participations",
                       declarer_type="Person closely associated to a member of administrative management or "
                                     "supervisory body",
                       description="Exemple Marie", transaction_type="Sale / Disposal", place="Off exchange")
    (tx,) = parse(page, ctx)
    assert tx.closely_associated is True and tx.pdmr_role == "board_member"
    assert tx.notifier_is_legal_entity and tx.notifier_name == "Société Exemple de Participations"
    assert tx.pdmr_id == ctx.person_id("Exemple Marie", scope="EXEMPLE GROUP") != tx.notifier_id
    assert (tx.nature, tx.direction, tx.on_venue) == ("sell", -1, False)
    assert "marie" not in tx.model_dump_json().casefold()


def test_remuneration_spec_and_exact_price(ctx):
    page = detail_html(transaction_type="Sale / Disposal",
                       spec="Transaction linked to remuneration package: acceptance and exercise of options/"
                            "warrants, as well as the sale of shares stemming from the exercise",
                       price=("3,25", "3.249"), amount=("2.436,94", "2436.940"), quantity=("750", None))
    (tx,) = parse(page, ctx)
    assert (tx.nature, tx.direction) == ("sell", -1)
    assert tx.linked_to_share_option_programme is True
    assert tx.price == 3.249 and tx.volume == 750.0 and tx.amount == 2436.94


def test_other_type_falls_back_to_the_specification(ctx):
    (tx,) = parse(detail_html(transaction_type="Other", spec="Donation"), ctx)
    assert (tx.nature, tx.nature_raw) == ("gift", "Other - Donation")


def test_helpers():
    fields = parse_fields(detail_html())
    assert fields["node"] == ["640001"] and fields["issuer"] == ["EXEMPLE GROUP"]
    assert be_number({"x": ["1.234.567,5"]}, "x") == 1234567.5
    assert be_number({"x": ["n/a"]}, "x") is None and be_number({}, "x") is None


def listing_html(rows: list[tuple[str, str]]) -> str:
    body = "".join(
        f'<tr><td headers="a" class="views-field views-field-field-ct-date-time">{when}          </td>'
        f'<td headers="b" class="views-field views-field-title"><a href="{path}">EXEMPLE GROUP</a></td>'
        f'<td headers="c" class="views-field views-field-field-ct-declarer-name">Exemple Jan</td></tr>'
        for when, path in rows
    )
    return f'<table class="views-table"><thead><tr><th>Publication date</th></tr></thead><tbody>{body}</tbody></table>'


def test_parse_listing():
    rows = parse_listing(listing_html([("16/10/2026", "/en/manager-transaction/exemple-group-1")]))
    assert rows == [(date(2026, 10, 16), "/en/manager-transaction/exemple-group-1")]


class FakeFSMA:
    """60 notifications: a full first page of 50 rows, then 10."""

    def __init__(self):
        self.calls = []

    def get(self, url, params=None):
        self.calls.append((url, params))
        request = httpx.Request("GET", url, params=params)
        if url == SEARCH_URL:
            start = params["page"] * 50
            rows = [("16/10/2026", f"/en/manager-transaction/exemple-group-{i}")
                    for i in range(start, min(60, start + 50))]
            return httpx.Response(200, text=listing_html(rows), request=request)
        return httpx.Response(200, text=detail_html(node=url.rsplit("-", 1)[1]), request=request)


def test_fetch_pages_listing_and_details(tmp_path):
    http = FakeFSMA()
    report = ingest(BelgiumFSMAPDMR(http=http), Lake(tmp_path), date(2026, 10, 16), date(2026, 10, 16))
    listings = [p for u, p in http.calls if u == SEARCH_URL]
    assert [p["page"] for p in listings] == [0, 1]
    assert listings[0]["date[min]"] == "2026-10-16" and listings[0]["sort"] == "asc"
    assert report.documents == 2 + 60 and report.records == 60 and not report.warnings


def test_default_client_respects_the_crawl_delay():
    assert BelgiumFSMAPDMR().http.min_interval == CRAWL_DELAY == 30.0


@pytest.mark.network
def test_live_listing_and_details_parse(tmp_path):
    # Two requests 30 seconds apart at most: one listing page and the first detail page.
    connector = BelgiumFSMAPDMR()
    end = date.today()
    docs = connector.fetch(date.fromordinal(end.toordinal() - 14), end)
    listing = next(docs)
    assert listing.meta["rows"] >= 1
    detail = next(docs)
    (record,) = connector.parse(detail, ParseContext(pseudonym_key=b"k"))
    assert record.isin and record.transaction_date
