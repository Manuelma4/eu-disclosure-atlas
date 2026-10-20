from datetime import date, timedelta

import httpx
import pytest
from shorts_support import doc, isin, mock_client

from disclosure_atlas.connectors.de_bafin_shorts import SEARCH_URL, GermanyBaFinShorts
from disclosure_atlas.lake import Lake
from disclosure_atlas.pipeline import ingest

CSV = (
    '﻿"Positionsinhaber","Emittent","ISIN","Position","Datum"\n'
    f'"Example Capital, LLC","Example Laser SE","{isin("DE")}","1,21","2026-10-19"\n'
    f'"Example Capital, LLC","Example Laser SE","{isin("DE")}","0,49","2026-10-20"\n'
).encode()


def test_parse(ctx):
    a, b = GermanyBaFinShorts(http=object()).parse(doc("de_bafin_shorts", CSV, ".csv"), ctx)
    assert (a.holder_name, a.issuer_name, a.isin, a.position_pct) == (
        "Example Capital, LLC", "Example Laser SE", isin("DE"), 1.21)
    assert a.position_date == date(2026, 10, 19) and not a.below_threshold
    assert b.position_pct == 0.49 and b.below_threshold


def test_fetch_submits_the_historical_search_then_downloads_the_csv():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url), request.content.decode()))
        if request.method == "GET" and str(request.url) == SEARCH_URL:
            return httpx.Response(200, text=f'<form action="{SEARCH_URL}?0-1.-nlp~filter~form~panel-form">',
                                  headers={"set-cookie": "JSESSIONID=abc; Path=/"})
        if request.method == "POST":
            assert request.headers["cookie"] == "JSESSIONID=abc"
            return httpx.Response(200, text=f'<a href="{SEARCH_URL}?2--top~csv~form~panel-form-csv~resource~link">')
        return httpx.Response(200, content=CSV, headers={"content-type": "text/csv"})

    (raw,) = GermanyBaFinShorts(http=mock_client(handler)).fetch(date(2012, 11, 1), date(2026, 10, 20))
    assert [c[0] for c in calls] == ["GET", "POST", "GET"]
    assert "isHistorical=true" in calls[1][2]
    assert raw.content == CSV and raw.params == {"isHistorical": "true"}


def test_fetch_fails_loudly_when_the_page_changes():
    client = mock_client(lambda request: httpx.Response(200, text="<html></html>"))
    with pytest.raises(RuntimeError, match="search form"):
        list(GermanyBaFinShorts(http=client).fetch(date(2026, 1, 1), date(2026, 1, 2)))


@pytest.mark.network
def test_live_historical_search(tmp_path):
    end = date.today()
    report = ingest(GermanyBaFinShorts(), Lake(tmp_path), end - timedelta(days=30), end)
    assert report.records > 0
