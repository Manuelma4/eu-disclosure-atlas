"""Denmark Finanstilsynet OAM insider connector. All documents here are synthetic."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import httpx
import pytest
from conftest import EXAMPLE_LEI
from test_fr_amf_pdmr import pdf_of
from test_mar_template import DK_ISIN, html_document, template_rows

from disclosure_atlas.connectors.base import ParseContext
from disclosure_atlas.connectors.dk_finanstilsynet_pdmr import (
    API_URL,
    DenmarkFinanstilsynetPDMR,
    details_values,
    document_url,
    language_of,
    published_at,
    search_body,
)
from disclosure_atlas.lake import Lake
from disclosure_atlas.pipeline import ingest

BLOB = "https://saesappublicprd.blob.core.windows.net/stc-public-submissions/2026/10/SUBMISSION-x/documents"


def details(announcement: str, documents: list[str]) -> dict:
    links = [{"type": "keyvalue", "key": {"name": "Document(s)" if i == 0 else ""},
              "value": {"type": "link", "title": title, "url": f"{BLOB}/{title.rsplit(' (', 1)[0]}"}}
             for i, title in enumerate(documents)]

    def text(name, value):
        return {"type": "keyvalue", "key": {"name": name}, "value": {"type": "text", "value": value}}

    return {"heading": "Eksempel A/S – Managers' transaction", "sections": [
        {"heading": "Notification", "elements": [text("Announcement ID", announcement),
                                                 text("Type", "Managers' transactions"), *links]},
        {"heading": "Time", "elements": [text("Published", "19-10-2026 15:01:07"),
                                         text("Registration time", "19-10-2026 15:01:05")]},
        {"heading": "Issuer", "elements": [text("Company", "EKSEMPEL A/S"), text("National business ID", "DK-1"),
                                           text("LEI code", EXAMPLE_LEI)]},
    ]}


ANNOUNCEMENTS = {
    # English and Danish templates of the same trade, plus a press release.
    "400000001": ["en.pdf (English)", "Notification Anna Eksempel-en.html (English)",
                  "Indberetning Anna Eksempel-da.html (Danish)"],
    # Only a Danish template.
    "400000002": ["Skema-da.html (Danish)"],
    # Only a press release.
    "400000003": ["da.pdf (Danish)"],
}


class FakeOAM:
    def __init__(self):
        self.calls = []

    def request(self, method, url, params=None, data=None, json=None):
        self.calls.append((method, url, json))
        rows = [{"id": a, "HeadlineColumn": "Ledende medarbejderes transaktioner", "IssuerColumn": "EKSEMPEL A/S",
                 "CategoryColumn": "Managers' transactions", "PublicationDateColumn": "19-10-2026 15:01:07"}
                for a in ANNOUNCEMENTS]
        body = {"paging": {"page": 1, "pageSize": 100, "totalCount": 3, "totalPages": 1},
                "data": {"type": "table", "rows": rows}}
        return httpx.Response(200, content=_dumps(body), request=httpx.Request(method, url))

    def get(self, url, params=None):
        self.calls.append(("GET", url, None))
        request = httpx.Request("GET", url)
        if "/details/" in url:
            announcement = url.rsplit("/", 1)[1]
            return httpx.Response(200, content=_dumps(details(announcement, ANNOUNCEMENTS[announcement])),
                                  request=request)
        if url.endswith("-en.html"):
            return httpx.Response(200, content=html_document(template_rows(issuer="Eksempel A/S")), request=request)
        if url.endswith("-da.html"):
            rows = template_rows(issuer="Eksempel A/S", nature="Køb", prices=(("372,98 kr.", "3.575 stk."),))
            return httpx.Response(200, content=html_document(rows), request=request)
        return httpx.Response(200, content=pdf_of("Pressemeddelelse"), request=request)


def _dumps(body) -> bytes:
    return json.dumps(body).encode()


def test_fetch_reads_one_language_per_announcement(tmp_path, ctx):
    http = FakeOAM()
    connector = DenmarkFinanstilsynetPDMR(http=http)
    docs = list(connector.fetch(date(2026, 10, 19), date(2026, 10, 19)))
    used = [(d.meta["announcement"], d.meta["language"]) for d in docs if d.meta.get("use")]
    assert used == [("400000001", "english"), ("400000002", "danish")]
    assert docs[-1].meta["kind"] == "listing" and docs[-1].meta["unread"] == 1
    method, url, body = http.calls[0]
    assert (method, url) == ("POST", f"{API_URL}/search")
    assert body["filters"][1] == {"type": "daterange", "key": "PublicationDateFilter", "min": "2026-10-19",
                                  "max": "2026-10-19"}
    assert any("Notification%20Anna%20Eksempel-en.html" in u for _, u, _ in http.calls)
    records = [r for d in docs for r in connector.parse(d, ctx)]
    assert len(records) == 2
    english, danish = records
    assert english.issuer_lei == EXAMPLE_LEI and english.isin == DK_ISIN and english.jurisdiction == "DK"
    assert english.published_at == datetime(2026, 10, 19, 13, 1, 7, tzinfo=UTC)
    assert (danish.nature, danish.price, danish.volume, danish.currency) == ("buy", 372.98, 3575.0, "DKK")
    assert "anna" not in english.model_dump_json().casefold()
    report = ingest(DenmarkFinanstilsynetPDMR(http=FakeOAM()), Lake(tmp_path), date(2026, 10, 19),
                    date(2026, 10, 19))
    assert report.records == 2 and not report.warnings


def test_helpers():
    values, links = details_values(details("1", ["a b.pdf (English)"]))
    assert values["Issuer/LEI code"] == EXAMPLE_LEI and links[0]["title"] == "a b.pdf (English)"
    assert language_of("x-da.pdf (Danish)") == "danish" and language_of("x.pdf") == "unknown"
    assert document_url(f"{BLOB}/Kari Ø.pdf") == f"{BLOB}/Kari%20%C3%98.pdf"
    assert published_at("28-12-2016 11:00:00") == datetime(2016, 12, 28, 10, tzinfo=UTC)
    assert search_body(date(2026, 1, 1), date(2026, 1, 31), 2)["page"] == 2


def test_lei_from_the_announcement_when_the_template_has_none(ctx):
    connector = DenmarkFinanstilsynetPDMR(http=object())
    from disclosure_atlas.connectors.base import RawDocument

    doc = RawDocument(source="dk_finanstilsynet_pdmr", url=f"{BLOB}/x.html", params={},
                      content=html_document(template_rows(lei="")), content_type="text/html",
                      fetched_at=datetime(2026, 10, 20, tzinfo=UTC), suffix=".html",
                      meta={"kind": "document", "use": True, "announcement": "1", "index": 0,
                            "published": "19-10-2026 15:01:07", "issuer": "EKSEMPEL A/S",
                            "issuer_lei": EXAMPLE_LEI})
    (tx,) = connector.parse(doc, ParseContext(b"k"))
    assert tx.issuer_lei == EXAMPLE_LEI


@pytest.mark.network
def test_live_oam_parses(tmp_path):
    end = date.today()
    report = ingest(DenmarkFinanstilsynetPDMR(), Lake(tmp_path), date.fromordinal(end.toordinal() - 6), end)
    assert report.documents >= 1
