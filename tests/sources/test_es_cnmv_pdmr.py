"""Spain CNMV insider connector. All documents here are synthetic: no real person appears in the test suite."""

from __future__ import annotations

from datetime import UTC, date, datetime

import httpx
import pytest
from conftest import EXAMPLE_LEI, make_isin
from test_fr_amf_pdmr import pdf_of

from disclosure_atlas.connectors.base import RawDocument
from disclosure_atlas.connectors.es_cnmv_pdmr import (
    LIST_URL,
    SpainCNMVPDMR,
    TemplateError,
    names_in_position,
    parse_form,
    parse_listing,
    split_description,
)
from disclosure_atlas.lake import Lake
from disclosure_atlas.pipeline import ingest

ES_ISIN = make_isin("ES000EXAMPL")

# --- Synthetic documents ----------------------------------------------------------------------

TX_HEADER = [
    "Código de", "Identificación Naturaleza del", "del instrumento instrumento Naturaleza de",
    "Lugar de la operación", "financiero financiero la operación Fecha Volumen Precio Unitario Divisa", "...",
    "... ... ... ... ... ... ...", "Place of the", "Identification Nature of Nature of the Date Volume Unit Price "
    "Currency", "transaction", "code of the the financial transaction 4.d) 4.f) 4.g) 4.h)", "4.e)",
    "financial instrument 4.c)", "instrument 4.b)", "4.a)",
]


def form_text(declarant="Lucía Ejemplo Prueba", position="Consejera Delegada", closely_associated=False,
              amendment="Inicial", issuer="EJEMPLO INDUSTRIAS, S.A.", lei=EXAMPLE_LEI,
              lines=(f"{ES_ISIN} Acción Compra 08/10/2026 XMAD 1.371,00 5,50 EUR",),
              additional="Operación ejecutada el 08/10/2026 por cuenta de Lucía Ejemplo Prueba."):
    """Text as pdfplumber returns it for a CNMV notification form."""
    pdmr, associated = ("[ ]", "[ x ]") if closely_associated else ("[ x ]", "[ ]")
    text = [
        "Formulario Standard Form",
        "MODELO DE NOTIFICACIÓN DE LAS STANDARD FORM FOR NOTIFICATION",
        "1. DATOS DE LA PERSONA CON RESPONSABILIDAD DE DIRECCIÓN - PERSONA ESTRECHAMENTE VINCULADA | DETAILS",
        "OF THE PERSON DISCHARGING MANAGERIAL RESPONSABILITIES (PDMR) - PERSON CLOSELY ASSOCIATED",
        "a) Nombre y apellidos - Razón social | Name and surname - Company name", declarant,
        "2. MOTIVO DE LA NOTIFICACIÓN | REASON FOR THE NOTIFICACTION",
        f"{pdmr} Persona con responsabilidad de dirección | Person discharging managerial responsibilities (PDMR)",
        f"{associated} Persona estrechamente vinculada | Person closely associated",
        "a) Cargo - posición | Job title", position,
        "b) Notificación inicial - Modificación | Initial Notification - Amendment", amendment,
        "3. DATOS DEL EMISOR, EL PARTICIPANTE DEL MERCADO DE DERECHOS DE EMISIÓN, LA PLATAFORMA DE SUBASTA, EL",
        "SUBASTADOR O LA ENTIDAD SUPERVISORA DE LAS SUBASTAS | DETAILS OF THE ISSUER, EMISSION ALLOWANCE MARKET",
        "PARTICIPANT, AUNCTION PLATFORM, AUCTIONEER OR AUCTION MONITOR",
        "a) Identificación | Name:", issuer, "b) LEI:", lei, "1 / 2",
        "Formulario", "Standard form",
        "4. DATOS DE LA OPERACIÓN O LAS OPERACIONES (El siguiente cuadro se repetirá para: i) cada tipo de "
        "instrumento", "financiero; ii) cada tipo de operación; iii) cada fecha, y iv) cada lugar en que se hayan",
    ]
    for line in lines:
        text += [*TX_HEADER, line, "Total Agregado", "...", "1371,00 5,50", "Aggregated information", "5)"]
    text += ["Otra información | Additional information", additional, "2 / 2"]
    return "\n".join(text)


def doc_of(text: str, number="2026130001", registered="2026-10-15") -> RawDocument:
    return RawDocument(
        source="es_cnmv_pdmr", url="https://www.cnmv.es/webservices/verdocumento/ver?e=abc", params={},
        content=pdf_of(text), content_type="application/pdf", fetched_at=datetime(2026, 10, 20, 6, tzinfo=UTC),
        suffix=".pdf", meta={"kind": "filing", "number": number, "registered": registered,
                             "issuer": "EJEMPLO INDUSTRIAS, S.A."},
    )


def parse(text: str, ctx, **kwargs):
    return list(SpainCNMVPDMR(http=object()).parse(doc_of(text, **kwargs), ctx))


# --- Form parsing ---------------------------------------------------------------------------

def test_open_market_purchase(ctx):
    (tx,) = parse(form_text(), ctx)
    assert tx.source == "es_cnmv_pdmr" and tx.jurisdiction == "ES"
    assert tx.issuer_name == "EJEMPLO INDUSTRIAS, S.A." and tx.issuer_lei == EXAMPLE_LEI
    assert tx.published_at == datetime(2026, 10, 14, 22, tzinfo=UTC)  # Madrid midnight, CEST
    assert tx.transaction_date == date(2026, 10, 8)
    assert (tx.nature, tx.direction, tx.instrument_type) == ("buy", 1, "share")
    assert (tx.nature_raw, tx.instrument_type_raw) == ("Compra", "Acción")
    assert tx.pdmr_role == "ceo" and tx.pdmr_role_raw == "Consejera Delegada"
    assert tx.isin == ES_ISIN and tx.currency == "EUR"
    assert tx.volume == 1371.0 and tx.price == 5.5 and tx.amount == pytest.approx(7540.5)
    assert tx.venue == "XMAD" and tx.on_venue is True
    assert tx.closely_associated is False and tx.is_amendment is False
    assert tx.parser == "es_cnmv_pdmr@1"


def test_names_of_natural_persons_never_reach_the_record(ctx):
    text = form_text(declarant="Lucía Fulana Prueba", position="Lucía Fulana Prueba, Consejera Delegada")
    (tx,) = parse(text, ctx)
    dump = tx.model_dump_json().casefold()
    assert "fulana" not in dump and "lucía" not in dump
    assert tx.pdmr_id == tx.notifier_id and tx.notifier_name is None
    assert tx.pdmr_role_raw == "[redacted] [redacted] [redacted], Consejera Delegada"


def test_closely_associated_entity_links_to_the_named_manager(ctx):
    text = form_text(declarant="EJEMPLO INVERSIONES 2002, S.L.", closely_associated=True,
                     position="CONSEJERO - Martín Ejemplo Sánchez-Prueba")
    (tx,) = parse(text, ctx)
    assert tx.closely_associated is True
    assert tx.notifier_is_legal_entity is True and tx.notifier_name == "EJEMPLO INVERSIONES 2002, S.L."
    assert tx.pdmr_id == ctx.person_id("Martín Ejemplo Sánchez-Prueba", scope=EXAMPLE_LEI)
    assert tx.pdmr_id != tx.notifier_id
    assert tx.pdmr_role == "board_member" and tx.pdmr_role_raw is None
    assert "martín" not in tx.model_dump_json().casefold()


def test_closely_associated_natural_person_stays_pseudonymous(ctx):
    text = form_text(declarant="Marta Ejemplo Prueba", closely_associated=True,
                     position="Persona vinculada a D. Martín Ejemplo, Presidente")
    (tx,) = parse(text, ctx)
    assert tx.notifier_name is None and tx.notifier_is_legal_entity is False
    assert tx.pdmr_role == "chair" and tx.pdmr_role_raw is None
    assert "marta" not in tx.model_dump_json().casefold()


def test_several_lines_amendment_and_off_venue(ctx):
    lines = [f"{ES_ISIN} Acción Venta 01/10/2026 XOFF 500,00 12,00 EUR",
             f"{ES_ISIN} Acción Otros 02/10/2026 XXXX 20,00 0,00 EUR",
             f"{ES_ISIN} Derechos de suscripción Suscripción 03/10/2026 XMAD 10,00 1,25 EUR"]
    txs = parse(form_text(lines=lines, amendment="Modificación"), ctx)
    assert [(t.nature, t.direction) for t in txs] == [("sell", -1), ("other", 0), ("subscription", 1)]
    assert [t.on_venue for t in txs] == [False, False, True]
    assert txs[2].instrument_type_raw == "Derechos de suscripción" and txs[2].instrument_type == "subscription_right"
    assert all(t.is_amendment for t in txs)
    assert len({t.record_id for t in txs}) == 3
    assert [t.record_id for t in parse(form_text(lines=lines), ctx)] == [t.record_id for t in txs]


def test_dates_in_additional_information_are_not_transactions():
    filing = parse_form(form_text())
    assert len(filing.transactions) == 1


def test_issuer_with_tax_id_and_invalid_isin(ctx):
    (tx,) = parse(form_text(issuer="Ejemplo Group, SA; NIF: A00000000",
                            lines=("ES12345 Acción Compra 08/10/2026 XMAD 10,00 2,00 EUR",)), ctx)
    assert tx.issuer_name == "Ejemplo Group, SA" and tx.isin is None


def test_helpers():
    assert split_description("Acción Compra") == ("Acción", "Compra")
    assert split_description("Opción Ejercicio de opciones") == ("Opción", "Ejercicio de opciones")
    assert split_description("Cosa Rara Compra") == ("Cosa", "Rara Compra")
    assert names_in_position("Ana Ejemplo- Administrador único", "X") == ["Ana Ejemplo"]
    assert names_in_position("DIRECTOR GENERAL FINANCIERO", "X") == []
    assert names_in_position("CONSEJERO DOMINICAL (J.M.EJEMPLO)", "X") == []


@pytest.mark.parametrize("text, message", [
    ("garbage", "no declarant"),
    (form_text(lines=()), "no transaction line"),
])
def test_form_errors(text, message):
    with pytest.raises(TemplateError, match=message):
        parse_form(text)


# --- Listing and fetch ----------------------------------------------------------------------------

def listing_html(items: list[tuple[str, str]], page: int, pages: int) -> str:
    blocks = "".join(
        f'<li id="x_elementoPrimerNivel" class="blocks-single"><ul>'
        f'<li id="x_liFechaRegistro" class="fecha-no-icon">\n 15/10/2026\n</li>'
        f'<li><a id="x_hlTituloCabecera" href="datosentidad.aspx?nif=A00000000" target="_self">'
        f'<span id="x_spanTituloCabecera" class="tit-small">EJEMPLO INDUSTRIAS, S.A.</span></a></li>'
        f'<li><span class="negrita">Declarante: Lucía Ejemplo Prueba</span></li>'
        f'<li><a id="x_subtituloRegistroEnlace" href="https://www.cnmv.es/webservices/verdocumento/ver?e={key}'
        f'&amp;x=1" target="_blank">Motivo de la notificación: Persona con Responsabilidad de Dirección</a></li>'
        f'<li class="padding-r resumen">\n Número de registro: {number}\n</li></ul></li>'
        for key, number in items
    )
    pager = (f'<span id="x_lblInfoPaginacion" class="PagActivaTXT">Página {page} de {pages}</span>'
             if pages else "")
    return f"<html><body>{pager}<ul id=\"listaElementosPrimernivel\">{blocks}</ul></body></html>"


def test_parse_listing():
    items, page, pages = parse_listing(listing_html([("abc", "2026130001")], 1, 2))
    assert (page, pages) == (1, 2)
    (item,) = items
    assert item.registered == date(2026, 10, 15) and item.number == "2026130001"
    assert item.url == "https://www.cnmv.es/webservices/verdocumento/ver?e=abc&x=1"
    assert item.issuer == "EJEMPLO INDUSTRIAS, S.A." and item.nif == "A00000000"
    assert item.reason == "Persona con Responsabilidad de Dirección"
    assert parse_listing("<html>No hay resultados</html>") == ([], 0, 0)


class FakeCNMV:
    """Serves 12 notifications over two listing pages; one is withdrawn (404), one is not a CNMV form."""

    def __init__(self):
        self.calls = []
        self.texts = {f"k{i}": form_text() for i in range(12)}
        self.texts["k3"] = None
        self.texts["k4"] = "garbage"

    def get(self, url, params=None):
        self.calls.append((url, params))
        request = httpx.Request("GET", url, params=params)
        if url == LIST_URL:
            keys = list(self.texts)
            page = params["page"]
            chunk = keys[page * 10:(page + 1) * 10]
            body = listing_html([(k, f"2026{n:06d}") for n, k in enumerate(chunk, start=page * 10)], page + 1, 2)
            return httpx.Response(200, text=body, request=request)
        key = url.split("e=")[1].split("&")[0]
        if self.texts[key] is None:
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError("missing", request=request, response=response)
        return httpx.Response(200, content=pdf_of(self.texts[key]), headers={"content-type": "application/pdf"},
                              request=request)


def test_fetch_pages_listing_and_reports_unreadable_forms(tmp_path):
    http = FakeCNMV()
    report = ingest(SpainCNMVPDMR(http=http), Lake(tmp_path), date(2026, 10, 15), date(2026, 10, 15))
    listings = [p for u, p in http.calls if u == LIST_URL]
    assert listings == [{"fechad": "15/10/2026", "fechah": "15/10/2026", "page": 0},
                        {"fechad": "15/10/2026", "fechah": "15/10/2026", "page": 1}]
    assert report.documents == 2 + 11 and report.records == 10
    assert report.warnings == ["2026000004: CNMV form not recognized (no declarant)"]


@pytest.mark.network
def test_live_listing_and_forms_parse(tmp_path):
    end = date.today()
    report = ingest(SpainCNMVPDMR(), Lake(tmp_path), date.fromordinal(end.toordinal() - 4), end)
    assert report.documents >= 1
    assert not report.warnings
