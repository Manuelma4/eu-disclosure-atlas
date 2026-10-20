"""GLEIF, ESMA FIRDS and OpenFIGI reference data, and the gold enrichment views. Synthetic data only."""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from conftest import EXAMPLE_ISIN, EXAMPLE_LEI, fi_doc, fi_row, make_isin, make_lei
from fastapi.testclient import TestClient

from disclosure_atlas.api import create_app
from disclosure_atlas.cli import build_parser
from disclosure_atlas.connectors.base import ParseContext
from disclosure_atlas.connectors.se_fi_pdmr import SwedenFIPDMR
from disclosure_atlas.http import PoliteClient
from disclosure_atlas.lake import Lake
from disclosure_atlas.models import Dataset
from disclosure_atlas.reference import firds, gleif, openfigi

NOW = datetime(2026, 10, 20, 8, tzinfo=UTC)
OTHER_LEI = make_lei("549300EXAMPLE00002")
OPTION_ISIN = make_isin("SE000EXOPTN")


def gleif_record(lei: str, name: str, country: str = "SE", status: str = "ACTIVE") -> dict[str, Any]:
    return {
        "type": "lei-records",
        "id": lei,
        "attributes": {
            "lei": lei,
            "entity": {
                "legalName": {"name": name, "language": "sv"},
                "legalAddress": {"country": country},
                "headquartersAddress": {"country": country},
                "jurisdiction": country,
                "status": status,
            },
            "registration": {"status": "ISSUED"},
        },
    }


def firds_xml(*rows: dict[str, str]) -> bytes:
    body = "".join(
        f"""<RefData><FinInstrmGnlAttrbts><Id>{r['isin']}</Id><FullNm>{r['name']}</FullNm><ShrtNm>{r['short']}</ShrtNm>
        <ClssfctnTp>{r['cfi']}</ClssfctnTp><NtnlCcy>SEK</NtnlCcy><CmmdtyDerivInd>false</CmmdtyDerivInd>
        </FinInstrmGnlAttrbts><Issr>{r['lei']}</Issr><TradgVnRltdAttrbts><Id>{r['venue']}</Id></TradgVnRltdAttrbts>
        <TechAttrbts><RlvntCmptntAuthrty>SE</RlvntCmptntAuthrty><RlvntTradgVn>XSTO</RlvntTradgVn></TechAttrbts>
        </RefData>"""
        for r in rows
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?><BizData xmlns="urn:iso:std:iso:20022:tech:xsd:head.003.001.01">'
        '<Pyld><Document xmlns="urn:iso:std:iso:20022:tech:xsd:auth.017.001.02"><FinInstrmRptgRefDataRpt>'
        f"{body}</FinInstrmRptgRefDataRpt></Document></Pyld></BizData>"
    ).encode()


def zipped(name: str, content: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, content)
    return buffer.getvalue()


EXAMPLE_SHARE = {"isin": EXAMPLE_ISIN, "name": "Example Robotics AB ser. B", "short": "EXAMPLE ROBOT/SH B",
                 "cfi": "ESVUFR", "lei": EXAMPLE_LEI, "venue": "XSTO"}
EXAMPLE_OPTION = {"isin": OPTION_ISIN, "name": "Example Robotics option 2027", "short": "EXAMPLE/OPT",
                  "cfi": "EMXXXR", "lei": EXAMPLE_LEI, "venue": "XSTO"}

FIGI_RESPONSE = [
    {"data": [
        {"figi": "BBG000EXMPL1", "compositeFIGI": "BBG000EXMPL0", "ticker": "EXRO B", "exchCode": "GR",
         "name": "EXAMPLE ROBOTICS AB-B", "securityType": "Common Stock", "marketSector": "Equity"},
        {"figi": "BBG000EXMPL2", "compositeFIGI": "BBG000EXMPL3", "ticker": "EXROB", "exchCode": "SS",
         "name": "EXAMPLE ROBOTICS AB-B", "securityType": "Common Stock", "marketSector": "Equity"},
        {"figi": "BBG000EXMPL4", "compositeFIGI": "BBG000EXMPL3", "ticker": "EXROB", "exchCode": "XS",
         "name": "EXAMPLE ROBOTICS AB-B", "securityType": "Common Stock", "marketSector": "Equity"},
    ]},
    {"warning": "No identifier found."},
]


class Response:
    def __init__(self, payload: Any = None, content: bytes = b"") -> None:
        self._payload, self.content = payload, content

    def json(self) -> Any:
        return self._payload


class FakeHttp:
    """Answers GLEIF, FIRDS and OpenFIGI calls from synthetic data and records every request."""

    def __init__(self) -> None:
        self.min_interval = 0.0
        self.calls: list[tuple[str, Any, Any]] = []

    def get(self, url: str, params: dict[str, Any] | None = None) -> Response:
        self.calls.append((url, params, None))
        if url == gleif.GLEIF_URL:
            names = {EXAMPLE_LEI: "Example Robotics Aktiebolag", OTHER_LEI: "Other Example Aktiebolag"}
            leis = params["filter[lei]"].split(",")
            return Response({"data": [gleif_record(lei, names[lei]) for lei in leis if lei in names]})
        if url == firds.FILES_URL:
            return Response({"response": {"docs": [
                {"file_type": "FULINS", "file_name": "FULINS_E_20261017_01of01.zip",
                 "publication_date": "2026-10-17T00:00:00Z", "download_link": "https://firds.test/E_17.zip"},
                {"file_type": "FULINS", "file_name": "FULINS_E_20261010_01of01.zip",
                 "publication_date": "2026-10-10T00:00:00Z", "download_link": "https://firds.test/E_10.zip"},
                {"file_type": "FULINS", "file_name": "FULINS_D_20261017_01of03.zip",
                 "publication_date": "2026-10-17T00:00:00Z", "download_link": "https://firds.test/D_17.zip"},
                {"file_type": "DLTINS", "file_name": "DLTINS_20261019_01of01.zip",
                 "publication_date": "2026-10-19T00:00:00Z", "download_link": "https://firds.test/DLT.zip"},
            ]}})
        if url == "https://firds.test/E_17.zip":
            return Response(content=zipped("FULINS_E_20261017_01of01.xml", firds_xml(EXAMPLE_SHARE, EXAMPLE_OPTION)))
        raise AssertionError(f"unexpected GET {url}")

    def post_json(self, url: str, payload: Any, headers: dict[str, str] | None = None) -> Response:
        self.calls.append((url, payload, headers))
        assert url == openfigi.MAPPING_URL
        answers = {EXAMPLE_ISIN: FIGI_RESPONSE[0]}
        return Response([answers.get(job["idValue"], FIGI_RESPONSE[1]) for job in payload])


STOCKHOLM = ZoneInfo("Europe/Stockholm")


@pytest.fixture
def lake(tmp_path):
    lake = Lake(tmp_path / "lake")
    published = (datetime.now(STOCKHOLM) - timedelta(hours=2)).strftime("%d/%m/%Y %H:%M:%S")
    trade_day = (date.today() - timedelta(days=3)).strftime("%d/%m/%Y 00:00:00")
    doc = fi_doc([
        fi_row(**{"Publication date": published, "Transaction date": trade_day}),
        fi_row(**{"Publication date": published, "Transaction date": trade_day, "ISIN": OPTION_ISIN,
                  "Intrument type": "Option"}),
        fi_row(**{"Publication date": published, "Transaction date": trade_day, "Issuer": "Other Example AB",
                  "LEI-code": OTHER_LEI, "ISIN": make_isin("SE000OTHERX")}),
    ], fetched_at=datetime.now(UTC))
    records = list(SwedenFIPDMR(http=object()).parse(doc, ParseContext(pseudonym_key=lake.pseudonym_key())))
    lake.write_silver(Dataset.INSIDER_TRANSACTIONS, "se_fi_pdmr", records)
    return lake


def load_references(lake: Lake, http: FakeHttp) -> None:
    gleif.refresh_lei(lake, http=http)
    firds.refresh_firds(lake, http=http)
    openfigi.refresh_figi(lake, http=http)


# Parsers ----------------------------------------------------------------------------------------------------------

def test_parse_gleif_reads_name_country_and_status():
    [record] = gleif.parse_gleif({"data": [gleif_record(EXAMPLE_LEI, "Example Robotics Aktiebolag")]}, NOW)
    assert record == {
        "lei": EXAMPLE_LEI, "legal_name": "Example Robotics Aktiebolag", "legal_country": "SE",
        "headquarters_country": "SE", "legal_jurisdiction": "SE", "entity_status": "ACTIVE",
        "registration_status": "ISSUED", "fetched_at": NOW,
    }


def test_parse_firds_keeps_wanted_isins_once():
    duplicate = {**EXAMPLE_SHARE, "venue": "SSME"}
    content = zipped("x.xml", firds_xml(EXAMPLE_SHARE, duplicate, EXAMPLE_OPTION))
    records = list(firds.parse_firds_zip(content, date(2026, 10, 17), NOW, wanted={EXAMPLE_ISIN}))
    assert len(records) == 1
    assert records[0] | {"fetched_at": None} == {
        "isin": EXAMPLE_ISIN, "full_name": "Example Robotics AB ser. B", "short_name": "EXAMPLE ROBOT/SH B",
        "cfi": "ESVUFR", "currency": "SEK", "issuer_lei": EXAMPLE_LEI, "trading_venue": "XSTO",
        "competent_authority": "SE", "file_date": date(2026, 10, 17), "fetched_at": None,
    }
    assert len(list(firds.parse_firds_zip(content, None, NOW))) == 2


def test_latest_full_files_picks_latest_equity_set():
    http = FakeHttp()
    assert firds.latest_full_files(http, date(2026, 10, 20)) == (date(2026, 10, 17), ["https://firds.test/E_17.zip"])


def test_primary_listing_prefers_home_market_then_composite():
    rows = FIGI_RESPONSE[0]["data"]
    assert openfigi.primary_index(EXAMPLE_ISIN, rows) == 1  # SS for a Swedish ISIN
    assert openfigi.primary_index("XS0000000000", rows) == 0  # only foreign-market listings: the first one
    assert openfigi.primary_index(EXAMPLE_ISIN, [{"figi": "X", "ticker": None}]) is None
    growth_market = [  # no SS listing: the local venue wins over XS and German composites
        {"figi": "F1", "compositeFIGI": "F1", "ticker": "EXSEK", "exchCode": "XS", "marketSector": "Equity"},
        {"figi": "F2", "compositeFIGI": "F2", "ticker": "E5X", "exchCode": "GR", "marketSector": "Equity"},
        {"figi": "F3", "compositeFIGI": "F9", "ticker": "E5X", "exchCode": "GF", "marketSector": "Equity"},
        {"figi": "F4", "compositeFIGI": "F8", "ticker": "EX", "exchCode": "SF", "marketSector": "Equity"},
    ]
    assert openfigi.primary_index(EXAMPLE_ISIN, growth_market) == 3


def test_parse_mapping_caches_misses_and_skips_errors():
    other = make_isin("SE000OTHERX")
    records = openfigi.parse_mapping(
        [EXAMPLE_ISIN, other, "SE0000000003"], [*FIGI_RESPONSE, {"error": "Invalid idValue"}], NOW
    )
    assert [r["isin"] for r in records] == [EXAMPLE_ISIN] * 3 + [other]
    assert [r["is_primary"] for r in records[:3]] == [False, True, False]
    assert records[3] == {"isin": other, "is_primary": False, "fetched_at": NOW}


def test_openfigi_batches_within_free_limits():
    http = FakeHttp()
    isins = [make_isin(f"SE{n:09d}") for n in range(23)]
    openfigi.fetch_listings(isins, http)
    assert [len(payload) for _, payload, _ in http.calls] == [10, 10, 3]
    assert http.min_interval >= 60 / 25 and http.calls[0][2] is None
    keyed = FakeHttp()
    openfigi.fetch_listings(isins, keyed, api_key="k")
    assert [len(payload) for _, payload, _ in keyed.calls] == [23]
    assert keyed.calls[0][2] == {"X-OPENFIGI-APIKEY": "k"} and keyed.min_interval >= 60 / 250


def test_polite_client_posts_json(monkeypatch):
    import httpx

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(body=request.content, key=request.headers.get("X-OPENFIGI-APIKEY"))
        return httpx.Response(200, json=[])

    client = PoliteClient(min_interval=0)
    client._client = httpx.Client(transport=httpx.MockTransport(handler))
    assert client.post_json("https://api.openfigi.test/v3/mapping", [{"a": 1}], {"X-OPENFIGI-APIKEY": "k"}).json() == []
    assert seen == {"body": b'[{"a":1}]', "key": "k"}


# Caches -----------------------------------------------------------------------------------------------------------

def test_refresh_fetches_only_uncached_identifiers(lake):
    http = FakeHttp()
    assert gleif.refresh_lei(lake, http=http) == (2, 2)
    assert openfigi.refresh_figi(lake, http=http) == (3, 5)  # 3 example listings + 2 cached misses
    calls = len(http.calls)
    assert gleif.refresh_lei(lake, http=http) == (0, 2)
    assert openfigi.refresh_figi(lake, http=http) == (0, 5)
    assert len(http.calls) == calls
    assert gleif.refresh_lei(lake, refresh=True, http=http) == (2, 2)
    assert firds.refresh_firds(lake, http=http) == (2, 2)


# Gold views -------------------------------------------------------------------------------------------------------

def test_views_exist_and_are_empty_without_reference_files(lake):
    con = lake.connect()
    assert con.execute("SELECT count(*) FROM instruments").fetchone() == (0,)
    rows = con.execute("SELECT lei, legal_name, primary_ticker, tickers FROM issuers ORDER BY lei").fetchall()
    assert rows == sorted([(EXAMPLE_LEI, None, None, []), (OTHER_LEI, None, None, [])])
    assert con.execute("SELECT count(*), count(primary_ticker) FROM insider_transactions_enriched").fetchone() == (3, 0)


def test_gold_enrichment(lake):
    before = [d[0] for d in lake.connect().execute("SELECT * FROM insider_transactions LIMIT 0").description]
    load_references(lake, FakeHttp())
    con = lake.connect()
    assert [d[0] for d in con.execute("SELECT * FROM insider_transactions LIMIT 0").description] == before

    instruments = {r[0]: r[1:] for r in con.execute(
        "SELECT isin, full_name, cfi, issuer_lei, primary_ticker, primary_exchange, tickers FROM instruments"
    ).fetchall()}
    assert instruments[EXAMPLE_ISIN] == (
        "Example Robotics AB ser. B", "ESVUFR", EXAMPLE_LEI, "EXROB", "SS", ["EXRO B GR", "EXROB SS", "EXROB XS"]
    )
    assert instruments[OPTION_ISIN][:5] == ("Example Robotics option 2027", "EMXXXR", EXAMPLE_LEI, None, None)

    issuer = con.execute(
        "SELECT name, legal_name, legal_country, entity_status, primary_isin, primary_ticker, primary_exchange, "
        "tickers FROM issuers WHERE lei = ?", [EXAMPLE_LEI]
    ).fetchone()
    assert issuer == ("Example Robotics AB (publ)", "Example Robotics Aktiebolag", "SE", "ACTIVE", EXAMPLE_ISIN,
                      "EXROB", "SS", ["EXROB SS"])

    enriched = con.execute(
        "SELECT isin, issuer_legal_name, instrument_cfi, primary_ticker, primary_exchange, amount_eur IS NULL "
        "FROM insider_transactions_enriched WHERE issuer_lei = ? ORDER BY isin", [EXAMPLE_LEI]
    ).fetchall()
    # The option has no listing of its own: it inherits the issuer's primary ticker.
    assert sorted(enriched) == sorted([
        (EXAMPLE_ISIN, "Example Robotics Aktiebolag", "ESVUFR", "EXROB", "SS", True),
        (OPTION_ISIN, "Example Robotics Aktiebolag", "EMXXXR", "EXROB", "SS", True),
    ])
    assert con.execute("SELECT count(*) FROM insider_transactions_enriched").fetchone() == (3,)


# API and CLI ------------------------------------------------------------------------------------------------------

def test_issuer_api_exposes_legal_name_and_tickers(lake):
    load_references(lake, FakeHttp())
    client = TestClient(create_app(str(lake.root)))
    detail = client.get(f"/v1/issuers/{EXAMPLE_LEI}").json()
    assert detail["name"] == "Example Robotics AB (publ)"
    assert detail["legal_name"] == "Example Robotics Aktiebolag"
    assert (detail["primary_ticker"], detail["primary_exchange"], detail["tickers"]) == ("EXROB", "SS", ["EXROB SS"])
    listed = {i["lei"]: i for i in client.get("/v1/issuers").json()}
    assert listed[EXAMPLE_LEI]["primary_ticker"] == "EXROB"
    assert listed[OTHER_LEI]["legal_name"] == "Other Example Aktiebolag" and listed[OTHER_LEI]["tickers"] == []


def test_issuer_api_without_reference_data(lake):
    client = TestClient(create_app(str(lake.root)))
    detail = client.get(f"/v1/issuers/{EXAMPLE_LEI}").json()
    assert (detail["legal_name"], detail["primary_ticker"], detail["tickers"]) == (None, None, [])


def test_cli_reference_choices():
    parser = build_parser()
    for name in ("fx", "lei", "firds", "figi"):
        assert parser.parse_args(["reference", name]).name == name
    args = parser.parse_args(["reference", "figi", "--ids", "a,b", "--refresh"])
    assert args.ids == "a,b" and args.refresh


# Live smoke tests (opt-in: pytest -m network) ---------------------------------------------------------------------

@pytest.mark.network
def test_live_reference_endpoints():
    http = PoliteClient()
    [record] = gleif.fetch_lei_records(["969500UP76J52A9OXU27"], http)  # a retired French legal entity
    assert record["legal_country"] == "FR" and record["legal_name"]
    file_date, links = firds.latest_full_files(http)
    assert links and all("FULINS_E_" in link for link in links) and date.today() - file_date < timedelta(days=15)
    listings = openfigi.fetch_listings(["FR0000120271"])
    assert any(r["is_primary"] and r["exch_code"] == "FP" for r in listings)
