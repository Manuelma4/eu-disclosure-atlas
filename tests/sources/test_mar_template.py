"""Issuer-made 2016/523 templates (Denmark, Norway). All documents here are synthetic."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from conftest import EXAMPLE_LEI, make_isin

from disclosure_atlas.connectors._mar_template import (
    TemplateError,
    amount,
    classify,
    document_rows,
    find_date,
    find_isin,
    parse_rows,
    read_document,
    template_records,
    to_number,
)
from disclosure_atlas.connectors.base import ParseContext

DK_ISIN = make_isin("DK000EXAMPL")


def template_rows(name="Anna Eksempel", position="CEO", issuer="Eksempel Holding A/S", lei=EXAMPLE_LEI,
                  description=f"Shares {DK_ISIN}", nature="Purchase", prices=(("DKK 372.98", "3,575"),),
                  aggregated=("3,575", "DKK 372.98"), when="2026-10-19", venue="Nasdaq Copenhagen (XCSE)",
                  amendment="Initial notification"):
    rows = [
        ["1.", "Details of the person discharging managerial responsibilities/person closely associated", ""],
        ["a)", "Name", name],
        ["2.", "Reason for the notification", ""],
        ["a)", "Position/status", position],
        ["b)", "Initial notification / amendment", amendment],
        ["3.", "Details of the issuer, emission allowance market participant, auction platform, auctioneer or "
               "auction monitor", ""],
        ["a)", "Name", issuer],
        ["b)", "LEI", lei],
        ["4.", "Details of the transaction(s): section to be repeated for (i) each type of instrument; (ii) each "
               "type of transaction; (iii) each date; and (iv) each place where transactions have been conducted",
         ""],
        ["a)", "Description of the financial instrument, type of instrument Identification code", description],
        ["b)", "Nature of the transaction", nature],
        ["c)", "Price(s) and volume(s)", "Price(s)", "Volume(s)"],
        *[["", "", price, volume] for price, volume in prices],
        ["d)", "Aggregated information — Aggregated volume — Price", " ".join(aggregated)],
        ["e)", "Date of the transaction", when],
        ["f)", "Place of the transaction", venue],
    ]
    return rows


def records(rows, ctx, published=datetime(2026, 10, 20, 7, tzinfo=UTC)):
    out = []
    for f, filing in enumerate(parse_rows(rows)):
        out += list(template_records(filing, source="dk_test", jurisdiction="DK", key=f"k/{f}", ctx=ctx,
                                     source_url=None, published_at=published,
                                     fetched_at=datetime(2026, 10, 20, 8, tzinfo=UTC), parser="dk_test@1",
                                     default_currency="DKK"))
    return out


def test_english_template(ctx):
    (tx,) = records(template_rows(), ctx)
    assert tx.issuer_name == "Eksempel Holding A/S" and tx.issuer_lei == EXAMPLE_LEI
    assert (tx.nature, tx.direction, tx.instrument_type, tx.isin) == ("buy", 1, "share", DK_ISIN)
    assert tx.volume == 3575.0 and tx.price == 372.98 and tx.currency == "DKK"
    assert tx.transaction_date == date(2026, 10, 19) and tx.on_venue is True
    assert tx.pdmr_role == "ceo" and tx.pdmr_role_raw == "CEO" and tx.closely_associated is False
    assert "anna" not in tx.model_dump_json().casefold()


def test_danish_template_with_several_lines_in_one_cell(ctx):
    rows = [
        ["1.", "Nærmere oplysninger om personen med ledelsesansvar/personen med nær tilknytning", ""],
        ["a)", "Navn", "Eksempel Familieholding ApS"],
        ["2.", "Å rsag til indberetningen", ""],
        ["a)", "Stilling/titel", "Selskab ejet af Anna Eksempel, bestyrelsesformand"],
        ["b)", "Første i ndberetning/ændring", "Første"],
        ["3.", "Nærmere oplysninger om udstederen", ""],
        ["a)", "Navn", "Eksempel A/S"], ["b)", "LEI-kode", EXAMPLE_LEI],
        ["4.", "Nærmere oplysninger om transaktionen/transaktionerne", ""],
        ["a)", "Beskrivelse af det finansielle instrument, instrumenttypen Identifikationskode", f"Aktie {DK_ISIN}"],
        ["b)", "Transaktionens art", "Salg"],
        ["c)", "Pris(er) og mængde(r)", "Pris(er)", "Mængde(r)"],
        ["", "", "321,9359 kr. pr. stk.\n320,5673 kr. pr. stk.", "1.156 stk.\n104 stk."],
        ["d)", "Aggregerede oplysninger - Aggregeret mængde - P ris", "1.260 stk 116.121 kr."],
        ["e)", "Dato for transaktionen", "29.09.2026"],
        ["f)", "S ted for transaktionen", "Uden for en handelsplads"],
    ]
    txs = records(rows, ctx)
    assert [(t.price, t.volume) for t in txs] == [(321.9359, 1156.0), (320.5673, 104.0)]
    assert {t.currency for t in txs} == {"DKK"}
    assert all((t.nature, t.direction, t.on_venue) == ("sell", -1, False) for t in txs)
    tx = txs[0]
    assert tx.closely_associated is True and tx.notifier_is_legal_entity is True
    assert tx.notifier_name == "Eksempel Familieholding ApS"
    assert tx.pdmr_role == "chair" and tx.pdmr_role_raw is None  # the position names the manager
    assert len({t.record_id for t in txs}) == 2


def test_closely_associated_links_to_the_named_manager(ctx):
    rows = template_rows(name="Example Capital Pte Ltd",
                         position="Closely associated person to Anna Eksempel, Chair of the Board")
    (tx,) = records(rows, ctx)
    assert tx.pdmr_id == ctx.person_id("Anna Eksempel", scope=EXAMPLE_LEI) != tx.notifier_id
    assert tx.pdmr_role == "chair" and "eksempel" not in (tx.pdmr_role_raw or "").casefold()


def test_ambiguous_prices_are_left_empty(ctx):
    rows = template_rows(prices=(("Refer to the appendix", "56,351"),), aggregated=("Aggregated volume: 56,351",))
    (tx,) = records(rows, ctx)
    assert tx.price is None and tx.volume == 56351.0 and tx.amount is None


def test_labelled_aggregated_values_and_inline_labels(ctx):
    rows = template_rows(prices=())
    rows[1] = ["a)", "Name Example Holding AS"]
    rows[-3] = ["d)", "Aggregated information", "Average price per share: NOK 51.70 Total number of shares: 84"]
    (tx,) = records(rows, ctx)
    assert tx.notifier_name == "Example Holding AS"
    assert (tx.price, tx.volume, tx.currency) == (51.7, 84.0, "NOK")


def test_issuer_as_declarant_and_impossible_dates(ctx):
    assert records(template_rows(name="Eksempel Holding A/S"), ctx) == []
    (tx,) = records(template_rows(when="08.10.2028"), ctx)
    assert tx.transaction_date is None


def test_several_notifications_in_one_document(ctx):
    rows = template_rows() + template_rows(name="Bo Eksempel", position="CFO")
    txs = records(rows, ctx)
    assert [t.pdmr_role for t in txs] == ["ceo", "cfo"] and txs[0].pdmr_id != txs[1].pdmr_id


def test_helpers():
    assert to_number("3,575", nordic=False) == 3575 and to_number("21,423", nordic=True) == 21.423
    assert to_number("116.121", nordic=True) == 116121 and to_number("1,333,403.50", nordic=False) == 1333403.5
    assert to_number("30 000", nordic=True) == 30000
    assert amount("DKK 372.98", nordic=False) == (372.98, "DKK")
    assert amount("12 and 13", nordic=False) == (None, None)
    assert find_date("1. oktober 2026") == date(2026, 10, 1) and find_date("October 1, 2026") == date(2026, 10, 1)
    assert find_date("25/9 og 29/9 2026") is None
    assert find_isin(f"Shares ISIN {DK_ISIN[:2]} {DK_ISIN[2:5]} {DK_ISIN[5:]}") == DK_ISIN
    assert find_isin(f"D K{DK_ISIN[2:]}") == DK_ISIN
    assert classify(["e)", "Date of the transaction", "2026-10-19"]) == ("date", ["2026-10-19"])
    assert classify(["", "- Price", "DKK 10"])[0] is None


def html_document(rows) -> bytes:
    body = "".join("<tr>" + "".join(f"<td>{c.replace(chr(10), '<br>')}</td>" for c in row) + "</tr>" for row in rows)
    return f"<!DOCTYPE html><html><body><table>{body}</table></body></html>".encode()


def table_pdf(rows) -> bytes:
    """A one-page PDF that draws every row as bordered cells, so pdfplumber detects a table."""
    widths = [30, 200, 200, 100]
    ops, y = [], 800
    for row in rows:
        x = 20
        for i, width in enumerate(widths):
            text = (row[i] if i < len(row) else "").split("\n")[0][:40]
            ops.append(f"{x} {y - 14} {width} 14 re S")
            safe = text.encode("cp1252", "replace").decode("cp1252").replace("\\", "\\\\").replace("(", "\\(") \
                .replace(")", "\\)")
            ops.append(f"BT /F1 7 Tf {x + 2} {y - 10} Td ({safe}) Tj ET")
            x += width
        y -= 14
    stream = "\n".join(ops).encode("cp1252", "replace")
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 5 0 R "
               b"/Resources << /Font << /F1 3 0 R >> >> >>",
               b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def short_rows():
    return template_rows(position="CEO", description=DK_ISIN, venue="XCSE",
                         nature="Purchase")


def test_html_and_pdf_documents_read_the_same(ctx):
    html_filings = read_document(html_document(short_rows()))
    # Some blobs carry a multipart header before the PDF itself.
    pdf = b'Content-Disposition: form-data; name="x.pdf"\r\nContent-Type: application/pdf\r\n\r\n' + \
        table_pdf(short_rows())
    pdf_filings = read_document(pdf)
    for filings in (html_filings, pdf_filings):
        (filing,) = filings
        assert filing.issuer == "Eksempel Holding A/S" and filing.transactions[0].nature == "Purchase"
    assert document_rows(html_document([["a", "b"]])) == [["a", "b"]]


@pytest.mark.parametrize("content", [b"no template here", html_document([["Hello", "world"]])])
def test_documents_without_a_template(content):
    with pytest.raises(TemplateError):
        read_document(content)


def test_context_fixture_is_deterministic():
    assert ParseContext(b"k").person_id("x", scope="y") == ParseContext(b"k").person_id("x", scope="y")


def test_repeated_label_cells_and_prices_in_one_cell(ctx):
    rows = template_rows(prices=())
    rows[1] = ["a)", "Name", "Name", "", "Anna Eksempel"]
    rows[11] = ["c)", "Price(s) and volume(s)", "", "", "Price(s) Volume(s)\nNOK 54.624 786"]
    (tx,) = records(rows, ctx)
    assert tx.notifier_name is None and "name" not in (tx.pdmr_role_raw or "").casefold()
    assert (tx.price, tx.volume, tx.currency) == (54.624, 786.0, "NOK")
    # The same values also split into cells: the split cells win, nothing is counted twice.
    (tx,) = records(rows[:12] + [["", "", "NOK 54.624", "786"]] + rows[12:], ctx)
    assert (tx.price, tx.volume) == (54.624, 786.0)
    rows[11] = ["c)", "Price(s) and volume(s)", "Price(s) Volume(s)\nNOK 21,423 30 000"]
    (tx,) = records(rows, ctx)
    assert tx.price is None and tx.volume is None  # three numbers on one line: not guessed
