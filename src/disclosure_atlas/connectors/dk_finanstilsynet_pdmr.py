"""Denmark - Finanstilsynet's officially appointed mechanism (OAM), managers' transactions (MAR Article 19).

Source notes and quirks: docs/sources/dk_finanstilsynet_pdmr.md. In short:
- The public company announcement search on finanstilsynet.dk is an embedded app whose JSON API takes a
  category filter ("MANTRA", managers' transactions) and a publication date range, 100 rows per page.
- Each announcement has a details record (issuer, CVR number, LEI, publication time) and one or more
  documents uploaded by the issuer: press releases and the 2016/523 template, often in Danish and English.
- Documents are read with ``_mar_template``; when both languages carry the template, only English is read.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from ..models import Dataset, InsiderTransaction
from ._mar_template import TemplateError, filing_fingerprint, read_document, template_records
from .base import Connector, ParseContext, RawDocument, date_windows, register

# The module id is the search app's configuration on https://www.finanstilsynet.dk/finansielle-temaer/
# kapitalmarked/selskabsmeddelelser (base64 "config" parameter of its iframe).
MODULE_ID = "9217fa13-5d9a-46c6-9921-69ee7e6cfaf6"
API_URL = f"https://appft.gold.extension.gopublic.dk/api/{MODULE_ID}"
SEARCH_PAGE = "https://www.finanstilsynet.dk/finansielle-temaer/kapitalmarked/selskabsmeddelelser"
CATEGORIES = ["MANTRA", "FT_MANTRA"]
PAGE_SIZE = 100
TIMEZONE = "Europe/Copenhagen"
_LANGUAGE = re.compile(r"\((English|Danish|[A-Z][a-z]+)\)\s*$")


def search_body(start: date, end: date, page: int) -> dict[str, Any]:
    return {
        "query": "",
        "filters": [
            {"type": "dropdown", "key": "CategoryFilter", "options": CATEGORIES},
            {"type": "daterange", "key": "PublicationDateFilter", "min": start.isoformat(), "max": end.isoformat()},
        ],
        "page": page,
        "pageSize": PAGE_SIZE,
        "sorting": {"key": "PublicationDateColumn", "direction": "ascending"},
    }


def details_values(details: dict[str, Any]) -> tuple[dict[str, str], list[dict[str, str]]]:
    """({"Section/Key": text value}, [{"title", "url"} of each document link])."""
    values: dict[str, str] = {}
    links: list[dict[str, str]] = []
    for section in details.get("sections") or []:
        for element in section.get("elements") or []:
            value = element.get("value") or {}
            if value.get("type") == "link" and value.get("url"):
                links.append({"title": value.get("title") or "", "url": value["url"]})
            elif value.get("type") == "text":
                key = f"{section.get('heading')}/{(element.get('key') or {}).get('name')}"
                values[key] = (value.get("value") or "").strip()
    return values, links


def language_of(title: str) -> str:
    match = _LANGUAGE.search(title or "")
    return match.group(1).casefold() if match else "unknown"


def document_url(url: str) -> str:
    """Document file names contain spaces and non-ASCII letters; the blob store wants them percent-encoded."""
    return quote(url, safe=":/?=&%")


def published_at(text: str | None) -> datetime | None:
    try:
        local = datetime.strptime(text or "", "%d-%m-%Y %H:%M:%S")
    except ValueError:
        return None
    return local.replace(tzinfo=ZoneInfo(TIMEZONE)).astimezone(UTC)


@register
class DenmarkFinanstilsynetPDMR(Connector):
    name = "dk_finanstilsynet_pdmr"
    dataset = Dataset.INSIDER_TRANSACTIONS
    jurisdiction = "DK"
    authority = "Finanstilsynet (Danish Financial Supervisory Authority), OAM"
    homepage = SEARCH_PAGE
    window_days = 31
    parser_version = 2  # 2: decimal-point prices checked against the stated total

    def fetch(self, start: date, end: date) -> Iterator[RawDocument]:
        for window_start, window_end in date_windows(start, end, self.window_days):
            yield from self._fetch_window(window_start, window_end)

    def _fetch_window(self, start: date, end: date) -> Iterator[RawDocument]:
        page = 1
        while True:
            body = search_body(start, end, page)
            response = self.http.request("POST", f"{API_URL}/search", json=body)
            payload = response.json()
            rows = (payload.get("data") or {}).get("rows") or []
            paging = payload.get("paging") or {}
            unread = 0
            for row in rows:
                readable = False
                for doc in self._fetch_announcement(row):
                    readable = readable or bool(doc.meta.get("use"))
                    yield doc
                unread += not readable
            yield RawDocument(
                source=self.name, url=str(response.url), params=body, content=response.content,
                content_type=response.headers.get("content-type"), fetched_at=datetime.now(UTC), suffix=".json",
                meta={"kind": "listing", "window": [start.isoformat(), end.isoformat()], "page": page,
                      "rows": len(rows), "total": paging.get("totalCount"), "unread": unread},
            )
            if not rows or page >= int(paging.get("totalPages") or 1):
                break
            page += 1

    def _fetch_announcement(self, row: dict[str, Any]) -> Iterator[RawDocument]:
        announcement = str(row.get("id"))
        response = self.http.get(f"{API_URL}/details/{announcement}")
        values, links = details_values(response.json())
        base = {
            "announcement": announcement,
            "published": values.get("Time/Published") or row.get("PublicationDateColumn"),
            "issuer": values.get("Issuer/Company") or row.get("IssuerColumn"),
            "issuer_lei": values.get("Issuer/LEI code"),
            "issuer_id": values.get("Issuer/National business ID"),
        }
        yield RawDocument(
            source=self.name, url=str(response.url), params={}, content=response.content,
            content_type=response.headers.get("content-type"), fetched_at=datetime.now(UTC), suffix=".json",
            meta={"kind": "announcement", **base},
        )
        documents = []
        for n, link in enumerate(links):
            got = self.http.get(document_url(link["url"]))
            try:
                filings = read_document(got.content)
            except TemplateError:
                filings = []
            documents.append((n, link, got, filings))
        # Danish and English versions of the same notification: read one language only, English first.
        languages = {language_of(link["title"]) for _, link, _, filings in documents if filings}
        chosen = "english" if "english" in languages else next(iter(sorted(languages)), None)
        seen: set[tuple] = set()
        for n, link, got, filings in documents:
            title = link["title"]
            use = bool(filings) and language_of(title) == chosen
            if use:
                # A press release often repeats the template attached next to it: read each notification once.
                prints = {filing_fingerprint(filing) for filing in filings}
                use = not prints <= seen
                seen |= prints
            yield RawDocument(
                source=self.name, url=str(got.url), params={}, content=got.content,
                content_type=got.headers.get("content-type"), fetched_at=datetime.now(UTC),
                suffix=".html" if title.rsplit(" (", 1)[0].lower().endswith((".html", ".htm")) else ".pdf",
                meta={"kind": "document", "index": n, "title": title, "language": language_of(title),
                      "use": use, **base},
            )

    def parse(self, doc: RawDocument, ctx: ParseContext) -> Iterator[InsiderTransaction]:
        if doc.meta.get("kind") != "document" or not doc.meta.get("use"):
            return
        try:
            # A Danish document writes Danish numbers ("3.575 stk."), whatever language its labels are in.
            filings = read_document(doc.content, nordic=True if doc.meta.get("language") == "danish" else None)
        except TemplateError:
            return
        for f, filing in enumerate(filings):
            if not filing.lei and doc.meta.get("issuer_lei"):
                filing.lei = doc.meta["issuer_lei"]
            yield from template_records(
                filing, source=self.name, jurisdiction=self.jurisdiction,
                # Not the document URL: issuers often put the manager's name in the file name.
                key=f"{doc.sha256[:24]}/{f}", ctx=ctx, source_url=f"{API_URL}/details/{doc.meta.get('announcement')}",
                published_at=published_at(doc.meta.get("published")), fetched_at=doc.fetched_at,
                parser=self.parser_id, default_currency="DKK", issuer_fallback=doc.meta.get("issuer"),
            )

