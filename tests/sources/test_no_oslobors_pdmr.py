"""Norway NewsWeb insider connector. All documents here are synthetic: no real person appears in the test suite."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import httpx
import pytest
from conftest import EXAMPLE_LEI, make_isin
from test_fr_amf_pdmr import pdf_of
from test_mar_template import html_document, template_rows

from disclosure_atlas.connectors.base import RawDocument
from disclosure_atlas.connectors.no_oslobors_pdmr import (
    API_URL,
    FormError,
    NorwayOsloBorsPDMR,
    krt_date,
    parse_krt,
)
from disclosure_atlas.lake import Lake
from disclosure_atlas.pipeline import ingest

NO_ISIN = make_isin("NO000EXAMPL")


def krt_numbered(reporting_as="Primærinnsider", correction="Ny melding", price="21,423", volume="30 000",
                 nature="Kjøp", venue="XOSL - Oslo Børs", when="25.09.2026"):
    """Text of the numbered KRT-1500 layout (2026) as pdfplumber returns it."""
    lines = [
        "Finanstilsynet",
        "KRT-1500 Skjema for melding om transaksjoner utført av personer med ledelsesansvar",
        "(«primærinnsidere») og deres nærstående", "Dato sendt: 25.09.2026 / 13:16", "Mottaker: Finanstilsynet",
        "1 Om rapporteringen", "1.1 Rapporterer du på vegne av deg selv eller andre?", "På vegne av andre",
        "1.2 Informasjon om rapportør", "1.2.1 Rapportøres Navn", "ADVOKAT EKSEMPEL",
        "1.2.2 Rapportøres Foretaksnavn", "Du har ikke lagt inn informasjon her",
        "1.3 Informasjon om primærinnsider / nærstående", "1.3.1 Jeg rapporterer som / på vegne av:", reporting_as,
    ]
    if reporting_as == "Nærstående foretak":
        lines += ["1.6 Nærstående foretak", "1.6.1 Norsk eller utenlandsk foretak", "Norsk",
                  "1.6.2 Foretaksnavn (til det nærstående foretaket)", "FJORD INVEST AS",
                  "1.6.4 LEI-nummer (til det nærstående foretaket)", "Du har ikke lagt inn informasjon her",
                  "1.7 Om primærinnsider", "1.7.1 Fullt navn", "Kari Eksempel", "1.7.2 Stilling/Rolle",
                  "Styreleder / Chairperson"]
    else:
        lines += ["1.4 Om primærinnsider", "1.4.2 Fullt navn", "Kari Eksempel", "1.4.4 Stilling/Rolle", "CEO"]
    lines += [
        "2 Om meldingen", "2.1 Er dette en ny melding eller korrigering av tidligere meldt transaksjon?", correction,
        "2.2 Informasjon om utstederen eller deltakeren på utslippskvotemarkedet",
        "2.2.1 Lei-kode (til utsteder eller deltaker på utslippskvotemarked)", EXAMPLE_LEI,
        "2.2.2 Foretaksnavn (til utsteder eller deltaker på utslippskvotemarked)", "Eksempel ASA",
        "2.3 Den meldepliktige transaksjonen",
        "Beskrivelse av det finansielle instrumentet/type finansielt instrument",
        "2.3.1 Instrument : Aksje", f"2.3.2 ISIN-kode : {NO_ISIN}", "2.3.2.1 Utstedernavn : Eksempel ASA",
        f"2.4.1 Transaksjonstype : {nature}",
        "2.5.1 Er transaksjonen knyttet til utøvelse av et aksjeopsjonsprogram? : Nei", "2.6.1 Valuta : NOK",
        f"2.8.1 Gjennomsnittlig pris per enhet : {price}", f"2.8.2 Aggregert volum : {volume}",
        "2.8.3 Total sum : 642 690", f"2.9.1 Angi dato : {when}", f"2.10.1 Handelsplass : {venue}",
        "Dersom det er foretatt mer enn én transaksjon av samme type, finansielle instrument, dag og handelsplass, "
        "så kan pris og volum angis for hver transaksjon.", "2.11 Kommentar", "Du har ikke lagt inn informasjon her",
    ]
    return "\n".join(lines)


def krt_labelled(english=False, correction_text=None, lines=(("176,555", "3 978"), ("176,6", "22"))):
    """Text of the labelled KRT-1500 layout (Skjemaversjon 46345), Norwegian or English."""
    if english:
        head = ["KRT-1500 Template for notification of transactions by",
                "persons discharging managerial responsibilities (PDMR)", "and persons closely associated with them",
                "The person liable:", "PDMR Closely associated person (natural Closely associated person (legal",
                "person) person)", "Info about the one who reports", "Last name Eksempel", "First name Kari",
                "Business name Eksempel ASA", "About PDMR", "Last name Eksempel", "First name Kari",
                "Position / role CFO", "Initial notification or amendment to prior notification", "Initial Amendment",
                "Details about the issuer or emission allowance market participant", "Business name Eksempel ASA",
                f"LEI number {EXAMPLE_LEI}", "Skjemaversjon: 46345", "2/3/2026", "10:39:20", "AM",
                "Description of the financial instrument/type of instrument", "Instrument Share",
                f"ISIN code {NO_ISIN}", "Name of the financial instrument", "Nature of the transaction",
                "Transaction type Subscription", "Currency of the transaction", "Currency USD",
                "Price(s) and volume(s)"]
        body = [x for price, volume in lines for x in (f"Price per unit {price}", f"Volume {volume}")]
        tail = ["Aggregated information", "Average price per unit 5.25000", "Aggregated volume 50 000",
                "Date of the transaction", "Date 2/2/2026", "Place of the transaction",
                "Trading venue MERK - Euronext Growth Oslo", "Comment"]
        return "\n".join(head + body + tail)
    head = ["KRT-1500 Skjema for melding om transaksjoner utført av",
            "personer med ledelsesansvar («primærinnsidere») og", "deres nærstående", "Rapporteringspliktig:",
            "Primærinnsider Nærstående person Nærstående foretak", "Informasjon om den som rapporterer",
            "Etternavn EKSEMPEL", "Fornavn OLA", "Foretaksnavn Eksempel ASA", "Om primærinnsider",
            "Etternavn Eksempel", "Fornavn Kari", "Stilling / Rolle Konserndirektør",
            "Ny melding eller korrigering av tidligere meldt transaksjon", "Ny melding Korrigering"]
    if correction_text:
        head += ["Beskrivelse av korrigering", correction_text]
    head += ["Informasjon om utstederen eller deltakeren på utslippskvotemarkedet", "Foretaksnavn Eksempel ASA",
             f"LEI-nummer {EXAMPLE_LEI}", "Skjemaversjon: 46345", "18.02.2026", "16:43:02", "1",
             "Beskrivelse av det finansielle instrumentet/type finansielt instrument", "Instrument Aksje",
             f"ISIN-kode {NO_ISIN}", "Navn på og/eller beskrivelse av det finansielle instrumentet",
             "Transaksjonstype", "Transaksjonstype Kjøp", "Aksjeopsjonsprogram",
             "Transaksjonen er knyttet til utøvelse av et aksjeopsjonsprogram Ja Nei", "Valuta for transaksjonen",
             "Valuta NOK", "Priser og volum"]
    body = [x for price, volume in lines for x in (f"Pris per enhet {price}", f"Volum {volume}")]
    tail = ["Aggregert informasjon", "Gjennomsnittlig pris per enhet 176,55500", "Aggregert volum 4 000",
            "Total sum 702 335,790000", "Dato for transaksjonen", "Dato 18.02.2026", "Handelsplass for transaksjonen",
            "Handelsplass XOFF - Utenfor en handelsplass", "Kommentar"]
    return "\n".join(head + body + tail)


def attachment_doc(content: bytes, use=True, superseded=False, message_id=700001) -> RawDocument:
    return RawDocument(
        source="no_oslobors_pdmr", url=f"{API_URL}/attachment?messageId={message_id}&attachmentId=1", params={},
        content=content, content_type="application/pdf", fetched_at=datetime(2026, 10, 20, 6, tzinfo=UTC),
        suffix=".pdf", meta={"kind": "attachment", "use": use, "message_id": message_id, "attachment_id": 1,
                             "issuer": "Eksempel ASA", "published": "2026-09-25T15:02:32.420Z",
                             "superseded": superseded},
    )


def parse(content: bytes, ctx, **kwargs):
    return list(NorwayOsloBorsPDMR(http=object()).parse(attachment_doc(content, **kwargs), ctx))


def test_numbered_krt_form(ctx):
    (tx,) = parse(pdf_of(krt_numbered()), ctx)
    assert tx.source == "no_oslobors_pdmr" and tx.jurisdiction == "NO"
    assert tx.issuer_name == "Eksempel ASA" and tx.issuer_lei == EXAMPLE_LEI and tx.isin == NO_ISIN
    assert tx.published_at == datetime(2026, 9, 25, 15, 2, 32, 420000, tzinfo=UTC)
    assert tx.transaction_date == date(2026, 9, 25)
    assert (tx.nature, tx.direction, tx.instrument_type) == ("buy", 1, "share")
    assert tx.price == 21.423 and tx.volume == 30000 and tx.currency == "NOK"
    assert tx.on_venue is True and tx.linked_to_share_option_programme is False
    assert tx.pdmr_role == "ceo" and tx.closely_associated is False and tx.is_amendment is False
    assert tx.source_url == "https://newsweb.oslobors.no/message/700001"
    dump = tx.model_dump_json().casefold()
    assert "kari" not in dump and "advokat" not in dump


def test_numbered_krt_closely_associated_company_and_correction(ctx):
    text = krt_numbered(reporting_as="Nærstående foretak", correction="Korrigering", venue="XOFF - Utenfor")
    (tx,) = parse(pdf_of(text), ctx)
    assert tx.closely_associated is True and tx.notifier_name == "FJORD INVEST AS"
    assert tx.notifier_is_legal_entity is True and tx.notifier_id != tx.pdmr_id
    assert tx.pdmr_id == ctx.person_id("Kari Eksempel", scope=EXAMPLE_LEI)
    assert tx.pdmr_role == "chair" and tx.is_amendment is True and tx.on_venue is False


def test_company_named_after_its_manager_stays_pseudonymous(ctx):
    text = krt_numbered(reporting_as="Nærstående foretak").replace("FJORD INVEST AS", "KARI EKSEMPEL HOLDING AS")
    (tx,) = parse(pdf_of(text), ctx)
    assert tx.notifier_name is None and tx.notifier_is_legal_entity is False
    assert "kari" not in tx.model_dump_json().casefold()


def test_labelled_krt_form_with_price_lines(ctx):
    txs = parse(pdf_of(krt_labelled()), ctx)
    assert [(t.price, t.volume) for t in txs] == [(176.555, 3978.0), (176.6, 22.0)]
    assert txs[0].pdmr_role == "executive" and txs[0].on_venue is False
    assert txs[0].transaction_date == date(2026, 2, 18) and txs[0].is_amendment is False
    assert "ola" not in txs[0].model_dump_json().casefold().split('"')


def test_labelled_krt_correction_text_marks_an_amendment(ctx):
    txs = parse(pdf_of(krt_labelled(correction_text="Korreksjonen gjelder antall aksjer.")), ctx)
    assert all(t.is_amendment for t in txs)
    assert "korreksjonen" not in txs[0].model_dump_json().casefold()


def test_english_labelled_krt_uses_us_dates_checked_against_publication(ctx):
    (tx,) = parse(pdf_of(krt_labelled(english=True, lines=(("5.25", "50 000"),))), ctx)
    assert tx.price == 5.25 and tx.volume == 50000 and tx.currency == "USD"
    assert tx.transaction_date == date(2026, 2, 2) and tx.nature == "subscription"
    assert krt_date("2/3/2026", english=True, published=date(2026, 2, 3)) == date(2026, 2, 3)
    assert krt_date("2/3/2026", english=True, published=date(2026, 2, 20)) == date(2026, 2, 3)
    assert krt_date("12/1/2026", english=True, published=date(2026, 1, 13)) == date(2026, 1, 12)


def test_issuer_template_and_unused_attachments(ctx):
    content = html_document(template_rows(issuer="Eksempel ASA", prices=(("NOK 54.624", "786"),)))
    (tx,) = parse(content, ctx)
    assert tx.issuer_name == "Eksempel ASA" and tx.price == 54.624 and tx.currency == "NOK"
    assert parse(content, ctx, use=False) == []
    (superseded,) = parse(content, ctx, superseded=True)
    assert superseded.is_superseded is True and superseded.record_id == tx.record_id


def test_krt_errors():
    with pytest.raises(FormError, match="not a KRT-1500"):
        parse_krt("hello")
    with pytest.raises(FormError, match="without the manager"):
        parse_krt("KRT-1500 Skjema\nInstrument Aksje")


# --- Fetch -----------------------------------------------------------------------------------------

class FakeNewsWeb:
    """Three announcements: a KRT form plus the issuer's own template for the same trade, a corrected one, and
    one with only a press release."""

    def __init__(self):
        self.calls = []

    def _json(self, url, body):
        return httpx.Response(200, content=json.dumps(body).encode(), request=httpx.Request("POST", url))

    def post(self, url, data=None):
        self.calls.append(url)
        if "/list?" in url:
            messages = [{"messageId": i, "issuerName": "Eksempel ASA", "publishedTime": "2026-09-25T15:02:32Z"}
                        for i in (1, 2, 3)]
            return self._json(url, {"data": {"messages": messages, "overflow": False}})
        message_id = int(url.rsplit("=", 1)[1])
        attachments = {1: [{"id": 11, "name": "KRT-1500.pdf"}, {"id": 12, "name": "Template.html"}],
                       2: [{"id": 21, "name": "KRT-1500 korrigert.pdf"}], 3: [{"id": 31, "name": "Press.pdf"}]}
        message = {"messageId": message_id, "issuerName": "Eksempel ASA", "publishedTime": "2026-09-25T15:02:32Z",
                   "correctedByMessageId": 9 if message_id == 2 else 0, "attachments": attachments[message_id]}
        return self._json(url, {"data": {"message": message}})

    def get(self, url, params=None):
        self.calls.append(url)
        attachment = int(url.rsplit("=", 1)[1])
        content = {11: pdf_of(krt_numbered()), 12: html_document(template_rows(issuer="Eksempel ASA")),
                   21: pdf_of(krt_numbered(price="22,0")), 31: pdf_of("Press release without a form")}[attachment]
        return httpx.Response(200, content=content, request=httpx.Request("GET", url))


def test_fetch_reads_one_form_per_announcement(tmp_path):
    http = FakeNewsWeb()
    connector = NorwayOsloBorsPDMR(http=http)
    docs = list(connector.fetch(date(2026, 9, 25), date(2026, 9, 25)))
    kinds = [(d.meta["kind"], d.meta.get("form"), d.meta.get("use")) for d in docs]
    assert kinds == [("message", None, None), ("attachment", "krt", True), ("attachment", "template", False),
                     ("message", None, None), ("attachment", "krt", True),
                     ("message", None, None), ("attachment", None, False), ("listing", None, None)]
    assert docs[-1].meta["unread"] == 1 and "category=1102" in http.calls[0]
    report = ingest(NorwayOsloBorsPDMR(http=FakeNewsWeb()), Lake(tmp_path), date(2026, 9, 25), date(2026, 9, 25))
    assert report.records == 2 and report.superseded == 1


@pytest.mark.network
def test_live_newsweb_parses(tmp_path):
    end = date.today()
    report = ingest(NorwayOsloBorsPDMR(), Lake(tmp_path), date.fromordinal(end.toordinal() - 4), end)
    assert report.documents >= 1
