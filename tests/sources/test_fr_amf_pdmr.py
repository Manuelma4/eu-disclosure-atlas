"""France AMF connector. All documents here are synthetic: no real person appears in the test suite."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import pytest
from conftest import EXAMPLE_LEI, make_isin

from disclosure_atlas.connectors.base import RawDocument
from disclosure_atlas.connectors.fr_amf_pdmr import (
    LIST_URL,
    FranceAMFPDMR,
    TemplateError,
    find_isin,
    parse_amount,
    parse_template,
    parse_template_date,
    pdf_text,
    read_pdf,
    redact_position,
    split_declarant,
)
from disclosure_atlas.extraction import (
    ExtractedFiling,
    ExtractionError,
    LLMExtractor,
    OllamaProvider,
    provider_from_env,
    strict_json_schema,
)
from disclosure_atlas.extraction.evaluation import load_gold, score
from disclosure_atlas.extraction.providers import AnthropicProvider, ProviderError, parse_json_object
from disclosure_atlas.lake import Lake
from disclosure_atlas.pipeline import ingest

FR_ISIN = make_isin("FR000EXAMPL")
EVAL_DIR = Path(__file__).resolve().parents[2] / "evals" / "fr_amf_pdmr"

# --- Synthetic documents ----------------------------------------------------------------------

FR_MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
             "novembre", "décembre"]
EN_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
             "November", "December"]


def fr_date(d: date) -> str:
    return f"{d.day:02d} {FR_MONTHS[d.month - 1]} {d.year}"


def en_date(d: date) -> str:
    return f"{d.day:02d} {EN_MONTHS[d.month - 1]} {d.year}"


def tx(when=date(2026, 10, 16), venue="Euronext Paris", nature=("Acquisition", "Acquisition"),
       instrument=("Action", "Share"), isin=FR_ISIN, lines=(("5.6723 Euro", "2 738.0000"),),
       aggregated=("5.6723 Euro", "2 738.0000"), linked=False):
    return dict(when=when, venue=venue, nature=nature, instrument=instrument, isin=isin, lines=lines,
                aggregated=aggregated, linked=linked)


def amf_text(declarant="Camille Exemple, Directeur Général", issuer="EXEMPLE INDUSTRIES", lei=EXAMPLE_LEI,
             transactions=None, amendment=("Notification initiale", "Initial notification"), english=True,
             numero="2026DD0000001", reference=FR_ISIN, published=date(2026, 10, 19)):
    """Text as pdfplumber returns it for a BDIF notification: French version, then English version."""
    transactions = transactions or [tx()]
    many = len(transactions) > 1
    fr = [f"{numero} {fr_date(published)}", f"{reference} - DD100001", "INFORMATION",
          "Déclaration individuelle relative aux opérations des personnes",
          "mentionnées à l’article L.621-18-2 du Code monétaire et financier sur les titres de la société",
          "LA PRESENTE NOTIFICATION N’A PAS FAIT L’OBJET D’UN CONTROLE DE L’AMF ET EST ETABLIE",
          "SOUS LA RESPONSABILITE EXCLUSIVE DU DECLARANT.",
          "NOM /FONCTION DE LA PERSONNE EXERCANT DES RESPONSABILITES DIRIGEANTES OU DE LA",
          "PERSONNE ETROITEMENT LIEE :", declarant, "NOTIFICATION INITIALE / MODIFICATION:", amendment[0],
          "COORDONNEES DE L’EMETTEUR", f"NOM : {issuer}", f"LEI : {lei}"]
    en = [f"{numero} {en_date(published)}", f"{reference} - DD100001", "INFORMATION",
          "Individual notification relating to transactions by persons referred to in Article L.621-18-2 of the "
          "Monetary and", "Financial Code on the securities of the company",
          "NAME / POSITION – STATUS OF THE PERSON DISCHARGING MANAGERIAL RESPONSABILITIES /",
          "PERSON CLOSELY ASSOCIATED:", declarant.replace("personne morale liée à", "legal person closely "
                                                          "associated with"),
          "INITIAL NOTIFICATION / AMENDMENT:", amendment[1], "DETAILS OF THE ISSUER", f"NAME : {issuer}",
          f"LEI : {lei}"]
    for i, t in enumerate(transactions, start=1):
        fr += [f"DETAIL DE LA TRANSACTION {i}" if many else "DETAIL DE LA TRANSACTION",
               f"DATE DE LA TRANSACTION : {fr_date(t['when'])}", f"LIEU DE LA TRANSACTION : {t['venue']}",
               f"NATURE DE LA TRANSACTION : {t['nature'][0]}",
               f"DESCRIPTION DE L’INSTRUMENT FINANCIER : {t['instrument'][0]}",
               f"CODE D’IDENTIFICATION DE L’INSTRUMENT FINANCIER : {t['isin']}",
               "INFORMATION DETAILLEE PAR OPERATION"]
        en += [f"DETAIL OF THE TRANSACTION {i}" if many else "DETAIL OF THE TRANSACTION",
               f"DATE OF THE TRANSACTION : {en_date(t['when'])}", f"PLACE OF THE TRANSACTION : {t['venue']}",
               f"NATURE OF THE TRANSACTION : {t['nature'][1]}",
               f"DESCRIPTION OF THE FINANCIAL INSTRUMENT, TYPE OF INSTRUMENT : {t['instrument'][1]}",
               f"IDENTIFICATION CODE : {t['isin']}", "DETAILED OPERATION INFORMATION"]
        for price, volume in t["lines"]:
            fr += [f"PRIX UNITAIRE : {price}", f"VOLUME : {volume}"]
            en += [f"PRICE : {price}", f"VOLUME : {volume}"]
        fr += ["INFORMATIONS AGREGEES", f"PRIX : {t['aggregated'][0]}", f"VOLUME : {t['aggregated'][1]}",
               "TRANSACTION LIEE A L’EXERCICE DE PROGRAMMES D’OPTIONS SUR ACTIONS OU SUR UNE ATTRIBUTION",
               f"D’ACTIONS GRATUITES OU DE PERFORMANCES : {'OUI' if t['linked'] else 'NON'}"]
        en += ["AGGREGATED INFORMATION", f"PRICE : {t['aggregated'][0]}",
               f"AGGREGATED VOLUME : {t['aggregated'][1]}",
               "TRANSACTION LINKED TO THE EXERCISE OF A SHARE OPTION PROGRAM OR AN ALLOCATION OF BONUS",
               f"OR PERFORMANCE SHARES : {'YES' if t['linked'] else 'NO'}"]
    fr += [f"DATE DE RECEPTION DE LA NOTIFICATION : {fr_date(published)}", "COMMENTAIRES :",
           "Ordres passés par un prestataire de services d'investissement.",
           "\"Les données à caractère personnel collectées par le biais de ce formulaire font l’objet d’un "
           "traitement\"."]
    en += [f"DATE OF RECEIPT OF THE NOTIFICATION : {en_date(published)}", "COMMENTS :",
           "\"Personal data collected through this form is processed automatically.\""]
    return "\n".join(fr + (en if english else []))


def make_pdf(pages: list[list[str]]) -> bytes:
    """A minimal valid PDF (Helvetica, WinAnsi) with one text line per entry: enough for pdfplumber."""

    def escape(text: str) -> bytes:
        raw = text.encode("cp1252")
        return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")

    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"]
    kids = []
    for lines in pages:
        stream = b"BT /F1 8 Tf 30 810 Td 11 TL\n" + b"".join(b"(" + escape(line) + b") Tj T*\n" for line in lines)
        stream += b"ET"
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        content_ref = len(objects)
        objects.append(b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents %d 0 R "
                       b"/Resources << /Font << /F1 3 0 R >> >> >>" % content_ref)
        kids.append(b"%d 0 R" % len(objects))
    objects[1] = b"<< /Type /Pages /Kids [" + b" ".join(kids) + b"] /Count %d >>" % len(kids)
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def pdf_of(text: str) -> bytes:
    lines = text.split("\n")
    return make_pdf([lines[i:i + 70] for i in range(0, len(lines), 70)])


def doc_of(text: str, numero="2026DD0000001", published="2026-10-19T18:10:09.68+02:00") -> RawDocument:
    return RawDocument(
        source="fr_amf_pdmr", url="https://bdif.amf-france.org/back/api/v1/documents/2026/x/y.pdf", params={},
        content=pdf_of(text), content_type="application/pdf", fetched_at=datetime(2026, 10, 20, 6, tzinfo=UTC),
        suffix=".pdf", meta={"kind": "filing", "numero": numero, "published_at": published},
    )


def parse(text: str, ctx, **kwargs):
    return list(FranceAMFPDMR(http=object(), extractor=None).parse(doc_of(text, **kwargs), ctx))


# --- Template parsing -------------------------------------------------------------------------

def test_text_helpers():
    assert parse_template_date("16 octobre 2026") == date(2026, 10, 16)
    assert parse_template_date("1er août 2026") == date(2026, 8, 1)
    assert parse_template_date("16 October 2026") == date(2026, 10, 16)
    assert parse_template_date("31 février 2026") is None
    assert parse_amount("2 738.0000") == (2738.0, None)
    assert parse_amount("5.6723 Euro") == (5.6723, "EUR")
    assert parse_amount("1 000.0000 Livre Sterling") == (1000.0, "GBP")
    assert parse_amount("1.4877 Dollar des États-Unis (Même jour)") == (1.4877, "USD")
    assert find_isin(f"{FR_ISIN} – ALEXP") == FR_ISIN
    assert find_isin(f"xpar{FR_ISIN}") == FR_ISIN
    assert find_isin("ALEXP") is None and find_isin(None) is None


def test_pdf_round_trip_reads_the_french_version_only():
    filing = read_pdf(pdf_of(amf_text()))
    assert filing.declarant_name == "Camille Exemple" and filing.declarant_position == "Directeur Général"
    assert filing.issuer_name == "EXEMPLE INDUSTRIES" and filing.issuer_lei == EXAMPLE_LEI
    assert filing.reference_isin == FR_ISIN and not filing.is_amendment
    (t,) = filing.transactions
    assert t.transaction_date == date(2026, 10, 16) and t.nature == "Acquisition" and t.instrument == "Action"
    assert [(line.price, line.volume) for line in t.lines] == [(5.6723, 2738.0)]
    assert (t.aggregated_price, t.aggregated_volume, t.currency) == (5.6723, 2738.0, "EUR")
    assert t.linked_to_share_option_programme is False
    assert pdf_text(pdf_of("Déclaration – l’AMF « test »")).strip() == "Déclaration – l’AMF « test »"


def test_english_only_text_parses_the_same_way():
    text = amf_text()
    english = text[text.index("2026DD0000001 19 October"):]
    fr, en = parse_template(text), parse_template(english)
    assert (en.declarant_name, en.issuer_name, en.issuer_lei, en.reference_isin) == \
        (fr.declarant_name, fr.issuer_name, fr.issuer_lei, fr.reference_isin)
    keep = {"transaction_date", "venue", "isin", "currency", "lines", "aggregated_price", "aggregated_volume"}
    assert [t.model_dump(include=keep) for t in en.transactions] == \
        [t.model_dump(include=keep) for t in fr.transactions]
    assert (fr.transactions[0].instrument, en.transactions[0].instrument) == ("Action", "Share")


def test_open_market_purchase_record(ctx):
    (r,) = parse(amf_text(), ctx)
    assert (r.source, r.jurisdiction, r.parser) == ("fr_amf_pdmr", "FR", "fr_amf_pdmr@1")
    assert r.published_at == datetime(2026, 10, 19, 16, 10, 9, 680000, tzinfo=UTC)
    assert r.transaction_date == date(2026, 10, 16)
    assert (r.nature, r.direction, r.instrument_type, r.pdmr_role) == ("buy", 1, "share", "ceo")
    assert r.isin == FR_ISIN and r.issuer_lei == EXAMPLE_LEI and r.currency == "EUR"
    assert r.amount == pytest.approx(5.6723 * 2738) and r.on_venue is True
    assert r.source_url == "https://bdif.amf-france.org/fr/details/2026DD0000001"
    assert r.extraction_confidence is None and r.notifier_name is None and not r.closely_associated
    assert "camille" not in r.model_dump_json().casefold()


def test_one_record_per_price_volume_line_with_stable_ids(ctx):
    text = amf_text(transactions=[
        tx(lines=(("10.0000 Euro", "100.0000"), ("11.0000 Euro", "300.0000")), aggregated=("10.7500 Euro", "400")),
        tx(when=date(2026, 10, 17), nature=("Cession", "Disposal"), lines=(("12.0000 Euro", "50.0000"),),
           aggregated=("12.0000 Euro", "50.0000")),
    ])
    records = parse(text, ctx)
    assert [(r.nature, r.volume, r.price) for r in records] == [
        ("buy", 100.0, 10.0), ("buy", 300.0, 11.0), ("sell", 50.0, 12.0)]
    assert sum(r.amount for r in records if r.nature == "buy") == pytest.approx(10.75 * 400)
    assert len({r.record_id for r in records}) == 3
    assert [r.record_id for r in parse(text, ctx)] == [r.record_id for r in records]


def test_closely_associated_legal_entity_links_to_the_manager(ctx):
    own = parse(amf_text(declarant="Camille Exemple, Président-directeur général"), ctx)[0]
    linked = parse(amf_text(
        declarant="EXEMPLE FAMILLE SAS personne morale liée à Camille Exemple, Président-directeur général"), ctx)[0]
    assert linked.notifier_is_legal_entity and linked.notifier_name == "EXEMPLE FAMILLE SAS"
    assert linked.closely_associated and linked.pdmr_id == own.pdmr_id and linked.notifier_id != own.pdmr_id
    assert linked.pdmr_role == "ceo" and "camille" not in linked.model_dump_json().casefold()


def test_closely_associated_natural_person_stays_pseudonymous(ctx):
    (r,) = parse(amf_text(declarant="Dominique Exemple personne liée à Camille Exemple, Administrateur"), ctx)
    assert r.closely_associated and r.notifier_name is None and not r.notifier_is_legal_entity
    assert r.pdmr_id != r.notifier_id and r.pdmr_role == "board_member"
    assert "dominique" not in r.model_dump_json().casefold()


def test_entity_named_after_its_manager_is_not_stored(ctx):
    (r,) = parse(amf_text(declarant="Camille Exemple personne morale liée à Camille Exemple, Gérant"), ctx)
    assert r.notifier_name is None and "camille" not in r.model_dump_json().casefold()


def test_legal_form_printed_twice_is_deduplicated(ctx):
    (r,) = parse(amf_text(declarant="EXEMPLE PARTICIPATIONS SE SE, Membre du conseil d'administration"), ctx)
    assert r.notifier_name == "EXEMPLE PARTICIPATIONS SE" and r.pdmr_role == "board_member"


def test_names_in_free_text_are_redacted(ctx):
    assert redact_position("Épouse de M. Camille Exemple, Directeur Général") == "[redacted]"
    assert redact_position("Holding contrôlée par Camille Exemple") == "Holding [redacted]"
    assert redact_position("Membre du Comité exécutif") == "Membre du Comité exécutif"
    (r,) = parse(amf_text(declarant="EXEMPLE HOLDING SAS, M. Alex Exemple et M. Sam Exemple, membres du conseil "
                                    "d'administration"), ctx)
    assert r.pdmr_role_raw == "[redacted]" and r.pdmr_role == "board_member"
    (r,) = parse(amf_text(declarant="Camille Exemplaire, Administratrice",
                          transactions=[tx(nature=("Donation de Camille Exemplaire", "Gift"))]), ctx)
    assert r.nature == "gift" and "exemplaire" not in r.model_dump_json().casefold()


def test_family_name_shared_with_the_issuer_is_not_redacted(ctx):
    (r,) = parse(amf_text(declarant="Camille Exemple, Administrateur de EXEMPLE SE", issuer="EXEMPLE SE"), ctx)
    assert r.pdmr_role_raw == "Administrateur de EXEMPLE SE"


def test_split_declarant():
    assert split_declarant("A SAS personne morale liée à B C, PDG") == ("A SAS", "B C, PDG", True)
    assert split_declarant("A legal person closely associated with B, CEO") == ("A", "B, CEO", True)
    assert split_declarant("A personne physique liée à B, PDG") == ("A", "B, PDG", False)
    assert split_declarant("A personne liée à B, PDG") == ("A", "B, PDG", None)
    assert split_declarant("A B, PDG") == ("A B, PDG", None, None)


def test_amendment_currencies_venues_and_reference_isin(ctx):
    records = parse(amf_text(amendment=("Modification", "Amendment"), transactions=[
        tx(venue="Hors plateforme de négociation", isin=""),
        tx(venue="MTF hors Dark Pools", lines=(("2.1020 Livre Sterling", "100"),), aggregated=("2.1020 Livre "
                                                                                               "Sterling", "100")),
        tx(venue="Nasdaq Global Market", instrument=("American Depository Shares", "American Depository Shares"),
           isin="", lines=(("1.4877 Dollar des États-Unis (Même jour)", "30 000.0000"),),
           aggregated=("1.4877 Euro", "30 000.0000")),
        tx(instrument=("Parts de FCPE", "Units"), isin="FCPE 123"),
    ]), ctx)
    assert all(r.is_amendment for r in records)
    assert [r.on_venue for r in records] == [False, True, True, True]
    assert [r.currency for r in records] == ["EUR", "GBP", "USD", "EUR"]
    assert records[0].isin == FR_ISIN  # reference ISIN of the filing, for a share
    assert records[2].isin is None and records[2].instrument_type == "share"  # ADSs have their own ISIN
    assert records[3].isin is None and records[3].instrument_type == "other"


def test_linked_to_share_option_programme(ctx):
    (r,) = parse(amf_text(transactions=[tx(nature=("Livraison d'actions de performance", "Other"), linked=True,
                                           lines=(("0.0000 Euro", "1 500.0000"),),
                                           aggregated=("0.0000 Euro", "1 500.0000"))]), ctx)
    assert r.linked_to_share_option_programme is True and r.nature == "grant" and r.amount == 0.0


@pytest.mark.parametrize("text, message", [
    ("Nothing to see", "no transaction block"),
    (amf_text().replace("NOM : EXEMPLE INDUSTRIES", "NOM :"), "no issuer name"),
    (amf_text().replace("16 octobre 2026", "seize octobre"), "unreadable date"),
    (amf_text().replace("VOLUME : 2 738.0000", "VOLUME : n/a", 1), "unreadable price or volume"),
    (amf_text(transactions=[tx(lines=(("1.0000 Euro", "1"), ("1.0000 Livre Sterling", "1")))]),
     "several currencies"),
])
def test_template_errors(text, message):
    with pytest.raises(TemplateError, match=message):
        parse_template(text)


# --- Fetch, LLM fallback, pipeline ------------------------------------------------------------

def listing_item(numero, path="2026/{n}/ABC.pdf", issuer="EXEMPLE INDUSTRIES"):
    return {"numero": numero, "datePublication": "2026-10-19T18:10:09.68+02:00",
            "dateInformation": "2026-10-19T00:00:00", "langue": "FR", "version": 1,
            "documents": [{"nomFichier": f"{numero}.pdf", "path": path.format(n=numero), "accessible": True}],
            "societes": [{"raisonSociale": issuer, "role": "SocieteConcernee"}]}


class FakeBDIF:
    """Serves a 150-item listing over two pages; one document is withdrawn (451), one is unreadable."""

    def __init__(self, texts):
        self.texts = texts
        self.calls = []

    def get(self, url, params=None):
        self.calls.append((url, params))
        request = httpx.Request("GET", url, params=params)
        if url == LIST_URL:
            items = [listing_item(n) for n in self.texts]
            page = items[params["From"]:params["From"] + params["Size"]]
            body = json.dumps({"result": page, "total": len(items), "aggregations": {}}).encode()
            return httpx.Response(200, content=body, headers={"content-type": "application/json"}, request=request)
        numero = url.split("/")[-2]
        if self.texts[numero] is None:
            response = httpx.Response(451, request=request)
            raise httpx.HTTPStatusError("unavailable", request=request, response=response)
        return httpx.Response(200, content=pdf_of(self.texts[numero]), headers={"content-type": "application/pdf"},
                              request=request)


def test_fetch_pages_listing_and_skips_unavailable_documents(tmp_path):
    texts = {f"2026DD{i:07d}": amf_text(numero=f"2026DD{i:07d}") for i in range(150)}
    texts["2026DD0000007"] = None
    http = FakeBDIF(texts)
    connector = FranceAMFPDMR(http=http, extractor=None)
    report = ingest(connector, Lake(tmp_path), date(2026, 10, 19), date(2026, 10, 19))
    listings = [p for u, p in http.calls if u == LIST_URL]
    assert [p["From"] for p in listings] == [0, 100]
    assert listings[0]["DateDebut"] == "2026-10-18T22:00:00.000Z"  # Paris midnight, CEST
    assert listings[0]["DateFin"] == "2026-10-19T21:59:59.999Z"
    assert report.documents == 2 + 149 and report.records == 149 and not report.warnings


class FakeProvider:
    model = "fake-model"

    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts = []

    def complete_json(self, system, document, schema):
        self.prompts.append(document)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def llm_answer(**overrides):
    answer = {
        "declarant_name": "Camille Exemple", "declarant_is_legal_entity": False, "linked_pdmr_name": None,
        "declarant_position": "Directeur Général", "is_amendment": False, "amendment_details": None,
        "issuer_name": "EXEMPLE INDUSTRIES",
        "issuer_lei": EXAMPLE_LEI, "reference_isin": None,
        "transactions": [{
            "transaction_date": "2026-10-16", "venue": "Euronext Paris", "nature": "Acquisition",
            "instrument": "Action", "isin": FR_ISIN, "currency": "EUR",
            "lines": [{"price": 5.5, "volume": 100.0}], "aggregated_price": 5.5, "aggregated_volume": 100.0,
            "linked_to_share_option_programme": False,
            "confidence": {"transaction_date": 0.99, "venue": 0.9, "nature": 0.97, "instrument": 0.95,
                           "isin": 0.99, "price_volume": 0.8},
        }],
        "confidence": {"declarant": 0.95, "linked_pdmr": 0.99, "position": 0.9, "amendment": 0.99,
                       "issuer_name": 0.99, "issuer_lei": 0.98},
    }
    answer.update(overrides)
    return answer


def test_llm_fallback_runs_only_when_the_parser_fails(tmp_path, ctx):
    broken = amf_text(numero="2026DD0000002").replace("NATURE DE LA TRANSACTION : Acquisition",
                                                      "NATURE DE LA TRANSACTION :")
    texts = {"2026DD0000001": amf_text(), "2026DD0000002": broken}
    provider = FakeProvider([{"not": "valid"}, llm_answer()])
    connector = FranceAMFPDMR(http=FakeBDIF(texts), extractor=LLMExtractor(provider))
    docs = list(connector.fetch(date(2026, 10, 19), date(2026, 10, 19)))
    assert [d.meta["kind"] for d in docs] == ["listing", "filing", "filing", "llm_extraction"]
    assert len(provider.prompts) == 2  # one invalid answer, one retry, and only for the broken document
    records = [r for d in docs for r in connector.parse(d, ctx)]
    llm = [r for r in records if r.parser.startswith("llm:")]
    assert len(records) == 2 and len(llm) == 1
    assert llm[0].parser == "llm:fake-model@fr-amf-1" and llm[0].extraction_confidence == 0.8
    assert "camille" not in llm[0].model_dump_json().casefold()
    report = ingest(FranceAMFPDMR(http=FakeBDIF(texts), extractor=LLMExtractor(FakeProvider([llm_answer()]))),
                    Lake(tmp_path), date(2026, 10, 19), date(2026, 10, 19))
    assert report.records == 2 and not report.warnings


def test_without_an_llm_failures_are_reported_not_fatal(tmp_path):
    texts = {"2026DD0000001": amf_text(), "2026DD0000002": "garbage"}
    report = ingest(FranceAMFPDMR(http=FakeBDIF(texts), extractor=None), Lake(tmp_path),
                    date(2026, 10, 19), date(2026, 10, 19))
    assert report.records == 1
    assert report.warnings == ["2026DD0000002: deterministic parser failed (no transaction block); "
                               "no LLM fallback configured"]


def test_llm_extractor_gives_up_after_its_attempts():
    provider = FakeProvider([ProviderError("refusal"), llm_answer(transactions=[])])
    with pytest.raises(ExtractionError, match="refusal; no transaction extracted"):
        LLMExtractor(provider).extract("text")


def test_strict_schema_is_closed_and_self_contained():
    schema = strict_json_schema(ExtractedFiling)
    assert "$defs" not in json.dumps(schema) and "$ref" not in json.dumps(schema)

    def objects(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                yield node
            for value in node.values():
                yield from objects(value)
        elif isinstance(node, list):
            for value in node:
                yield from objects(value)

    for node in objects(schema):
        assert node["additionalProperties"] is False and set(node["required"]) == set(node["properties"])


def test_provider_from_env():
    assert provider_from_env({}) is None
    assert provider_from_env({"ATLAS_LLM_PROVIDER": "none", "ANTHROPIC_API_KEY": "x"}) is None
    ollama = provider_from_env({"OLLAMA_HOST": "gpu-box:11434"})
    assert isinstance(ollama, OllamaProvider) and ollama.host == "http://gpu-box:11434"
    assert provider_from_env({"ATLAS_LLM_PROVIDER": "ollama", "ATLAS_LLM_MODEL": "m"}).model == "m"
    with pytest.raises(ValueError):
        provider_from_env({"ATLAS_LLM_PROVIDER": "other"})


def test_parse_json_object_tolerates_fences_and_prose():
    assert parse_json_object('{"a": 1}') == {"a": 1}
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('Here it is: {"a": {"b": 2}} Done.') == {"a": {"b": 2}}
    with pytest.raises(ProviderError):
        parse_json_object("no json")


def test_ollama_provider_sends_the_schema(monkeypatch):
    seen = {}

    def fake_post(url, json, timeout):
        seen.update(url=url, payload=json)
        return httpx.Response(200, json={"message": {"content": '{"ok": true}'}}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)
    assert OllamaProvider(model="m", host="http://localhost:11434").complete_json("s", "d", {"type": "object"}) == \
        {"ok": True}
    assert seen["url"] == "http://localhost:11434/api/chat" and seen["payload"]["format"] == {"type": "object"}


def test_anthropic_provider_requests_structured_output():
    class Block:
        type, text = "text", '{"ok": true}'

    class Message:
        stop_reason, content = "end_turn", [Block()]

    class Stream:
        def __init__(self, kwargs):
            self.kwargs = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get_final_message(self):
            return Message()

    class Messages:
        def stream(self, **kwargs):
            self.kwargs = kwargs
            return Stream(kwargs)

    class Client:
        class beta:  # noqa: N801 - mirrors the SDK attribute
            messages = Messages()

    provider = AnthropicProvider(client=Client())
    assert provider.model == "claude-opus-5-5"
    assert provider.complete_json("system", "document", {"type": "object"}) == {"ok": True}
    kwargs = Client.beta.messages.kwargs
    assert kwargs["output_config"]["format"] == {"type": "json_schema", "schema": {"type": "object"}}
    assert kwargs["model"] == "claude-opus-5-5" and kwargs["fallbacks"] == "default"


# --- Evaluation gate --------------------------------------------------------------------------

def test_gold_set_has_no_real_names_and_enough_filings():
    gold = load_gold(EVAL_DIR / "gold")
    assert len(gold) >= 50
    for case in gold:
        names = [case.labels.declarant_name, case.labels.linked_pdmr_name]
        assert all(name is None or "Exemple" in name or "Synthetic" in name for name in names), case.id


def test_deterministic_parser_meets_the_evaluation_gate():
    """CI gate (docs/decisions/0003): field-level precision and recall on the hand-verified gold set."""
    gold = load_gold(EVAL_DIR / "gold")
    report = score([(case, parse_template(case.text)) for case in gold])
    baseline = json.loads((EVAL_DIR / "baseline.json").read_text())
    assert report.micro_precision >= 0.99 and report.micro_recall >= 0.99, report.summary()
    for name, field in report.fields.items():
        floor = baseline["fields"].get(name)
        if floor:
            assert field.precision >= floor["precision"] - 1e-9, f"{name} precision regressed"
            assert field.recall >= floor["recall"] - 1e-9, f"{name} recall regressed"


@pytest.mark.network
def test_live_listing_and_documents_parse(tmp_path):
    end = date.today()
    report = ingest(FranceAMFPDMR(extractor=None), Lake(tmp_path), date.fromordinal(end.toordinal() - 3), end)
    assert report.documents >= 1
    con = Lake(tmp_path).connect()
    rows, missing_dates = con.execute(
        "SELECT count(*), count(*) FILTER (WHERE transaction_date IS NULL) FROM insider_transactions").fetchone()
    assert rows >= 1 and missing_dates == 0
