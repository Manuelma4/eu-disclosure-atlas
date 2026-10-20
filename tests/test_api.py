from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from conftest import fi_doc, fi_row
from fastapi.testclient import TestClient

from disclosure_atlas.api import create_app
from disclosure_atlas.connectors.base import ParseContext
from disclosure_atlas.connectors.se_fi_pdmr import SwedenFIPDMR
from disclosure_atlas.lake import Lake
from disclosure_atlas.models import Dataset
from disclosure_atlas.reference.fx import SCHEMA

STOCKHOLM = ZoneInfo("Europe/Stockholm")


def _published(hours_ago: float) -> str:
    return (datetime.now(STOCKHOLM) - timedelta(hours=hours_ago)).strftime("%d/%m/%Y %H:%M:%S")


def _load(lake: Lake, rows, fetched_at: datetime | None = None) -> None:
    connector = SwedenFIPDMR(http=object())
    doc = fi_doc(rows, fetched_at=fetched_at or datetime.now(UTC))
    lake.write_bronze(doc)
    records = list(connector.parse(doc, ParseContext(pseudonym_key=lake.pseudonym_key())))
    lake.write_silver(Dataset.INSIDER_TRANSACTIONS, connector.name, records)


@pytest.fixture
def lake(tmp_path):
    lake = Lake(tmp_path / "lake")
    today = date.today()
    pq.write_table(
        pa.Table.from_pylist([{"date": today - timedelta(days=400), "currency": "SEK", "rate": 10.0}], schema=SCHEMA),
        lake.fx_path if lake.fx_path.parent.mkdir(parents=True, exist_ok=True) is None else lake.fx_path,
    )
    trade_day = (today - timedelta(days=1)).strftime("%d/%m/%Y 00:00:00")
    _load(lake, [
        fi_row(**{"Publication date": _published(1), "Transaction date": trade_day}),
        fi_row(**{"Publication date": _published(2), "Transaction date": trade_day, "Nature of transaction": "Disposal",
                  "Volume": "100.0", "Price": "50.0", "Person discharging managerial responsibilities": "Erik Example",
                  "Notifier": "Erik Example",
                  "Position": "Chief\xa0Financial\xa0Officer\xa0(CFO)"}),
        fi_row(**{"Publication date": _published(3), "Transaction date": "01/01/2026 00:00:00", "Volume": "10.0",
                  "Issuer": "Other Example AB", "LEI-code": "", "Trading venue": "Outside a trading venue"}),
    ])
    return lake


@pytest.fixture
def client(lake):
    return TestClient(create_app(str(lake.root), stream_poll_seconds=0.01))


def test_health_and_sources(client):
    health = client.get("/v1/health").json()
    assert health["status"] == "ok" and health["records"] == 3
    sources = {s["name"]: s for s in client.get("/v1/sources").json()}
    assert sources["se_fi_pdmr"]["records"] == 3 and sources["se_fi_pdmr"]["country"] == "SE"


def test_overview_uses_eur_amounts_and_orders_latest(client):
    body = client.get("/v1/stats/overview").json()
    assert body["transactions"] == 3 and body["countries"] == 1
    assert body["open_market_buys_30d"] == 1 and body["open_market_sells_30d"] == 1
    assert body["buy_eur_30d"] == pytest.approx(2750 * 61.5 / 10)
    assert body["largest_buys_30d"][0]["amount_eur"] == pytest.approx(16912.5)
    published = [t["published_at"] for t in body["latest"]]
    assert published == sorted(published, reverse=True)
    assert "anna" not in str(body).casefold() and "erik" not in str(body).casefold()


def test_transactions_filters_and_cursor_pagination(client):
    sells = client.get("/v1/insider-transactions", params={"nature": "sell"}).json()
    assert [t["role"] for t in sells["items"]] == ["cfo"]
    first = client.get("/v1/insider-transactions", params={"limit": 2}).json()
    assert len(first["items"]) == 2 and first["next_cursor"]
    second = client.get("/v1/insider-transactions", params={"limit": 2, "cursor": first["next_cursor"]}).json()
    assert len(second["items"]) == 1 and second["next_cursor"] is None
    ids = {t["id"] for t in first["items"] + second["items"]}
    assert len(ids) == 3
    detail = client.get(f"/v1/insider-transactions/{second['items'][0]['id']}").json()
    assert detail["id"] == second["items"][0]["id"]


def test_etag_round_trip_and_new_data_changes_it(client, lake):
    first = client.get("/v1/insider-transactions")
    etag = first.headers["etag"]
    assert first.headers["x-atlas-snapshot"] in etag and "s-maxage" in first.headers["cache-control"]
    assert client.get("/v1/insider-transactions", headers={"If-None-Match": etag}).status_code == 304
    _load(lake, [fi_row(**{"Publication date": _published(0.5), "Volume": "1.0"})])
    import time

    time.sleep(2.1)  # the read model re-checks the lake version every 2 seconds
    refreshed = client.get("/v1/insider-transactions", headers={"If-None-Match": etag})
    assert refreshed.status_code == 200 and refreshed.headers["etag"] != etag
    assert len(refreshed.json()["items"]) == 4


def test_issuers_search_and_detail(client):
    issuers = client.get("/v1/issuers", params={"q": "robotics"}).json()
    assert [i["name"] for i in issuers] == ["Example Robotics AB (publ)"]
    lei = issuers[0]["lei"]
    detail = client.get(f"/v1/issuers/{lei.lower()}").json()
    assert detail["transactions"] == 2 and detail["distinct_buyers_90d"] == 1 and detail["distinct_sellers_90d"] == 1
    assert {r["role"] for r in detail["roles"]} == {"ceo", "cfo"}
    assert detail["monthly"][0]["buys"] == 1 and detail["monthly"][0]["sells"] == 1
    assert detail["net_open_market_eur_365d"] == pytest.approx(16912.5 - 500.0)


def test_errors_are_problem_details(client):
    missing = client.get("/v1/issuers/549300XXXXXXXXXXXX00")
    assert missing.status_code == 404
    assert missing.headers["content-type"].startswith("application/problem+json")
    assert missing.json()["status"] == 404
    invalid = client.get("/v1/insider-transactions", params={"limit": 0})
    assert invalid.status_code == 422 and invalid.json()["status"] == 422
    assert client.get("/v1/insider-transactions", params={"cursor": "%%%"}).status_code == 400


def test_stream_replays_after_position(client):
    old = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    with client.stream("GET", "/v1/stream", params={"since": f"{old}|"}) as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        events = []
        for line in response.iter_lines():
            if line.startswith("event: "):
                events.append(line.removeprefix("event: "))
            if len(events) == 3:
                break
    assert events == ["filing", "filing", "filing"]


def test_openapi_document_describes_the_api(client):
    spec = client.get("/openapi.json").json()
    assert spec["info"]["title"] == "EU Disclosure Atlas API"
    assert {"/v1/insider-transactions", "/v1/issuers/{lei}", "/v1/stats/overview", "/v1/stream"} <= set(spec["paths"])


def test_flow_buckets_buys_and_sells(client):
    weeks = client.get("/v1/stats/flow", params={"bucket": "week", "days": 30}).json()
    assert sum(w["buys"] for w in weeks) == 1 and sum(w["sells"] for w in weeks) == 1
    assert client.get("/v1/stats/flow", params={"bucket": "year"}).status_code == 422


def test_as_of_time_travel_on_transactions_and_issuers(client, lake):
    pinned = client.get("/v1/insider-transactions").headers["x-atlas-snapshot"]
    _load(lake, [fi_row(**{"Publication date": _published(0.5), "Volume": "1.0"})])
    old = client.get("/v1/insider-transactions", params={"as_of": pinned})
    assert old.status_code == 200 and len(old.json()["items"]) == 3 and old.json()["snapshot"] == pinned
    assert "immutable" in old.headers["cache-control"]
    lei = client.get("/v1/issuers", params={"q": "robotics", "as_of": pinned}).json()[0]["lei"]
    assert client.get(f"/v1/issuers/{lei}", params={"as_of": pinned}).json()["transactions"] == 2
    item = old.json()["items"][0]["id"]
    assert client.get(f"/v1/insider-transactions/{item}", params={"as_of": pinned}).status_code == 200
    missing = client.get("/v1/insider-transactions", params={"as_of": 10_000})
    assert missing.status_code == 404 and missing.json()["status"] == 404
