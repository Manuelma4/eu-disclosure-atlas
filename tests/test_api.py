import hashlib
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from conftest import EXAMPLE_LEI, fi_doc, fi_row, make_lei
from fastapi.testclient import TestClient

from disclosure_atlas.api import create_app
from disclosure_atlas.api.readmodel import Snapshot
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


def test_overview_uses_one_cutoff_when_the_clock_crosses_during_a_request(client, monkeypatch):
    path = "/v1/stats/overview"
    initial = client.get(path).json()
    buy = initial["largest_buys_30d"][0]
    expires = datetime.fromisoformat(buy["published_at"]) + timedelta(days=30)
    before = expires - timedelta(seconds=1)
    after = expires + timedelta(seconds=1)
    clock = before
    crosses_during_request = True
    read_rows = Snapshot.rows

    def rows_at_clock(self, sql, params=None):
        reference = after if crosses_during_request and "ORDER BY amount_eur" in sql else clock
        sql = sql.replace("now()", f"TIMESTAMPTZ '{reference.isoformat()}'")
        return read_rows(self, sql, params)

    monkeypatch.setattr(Snapshot, "rows", rows_at_clock)
    before_cutoff = client.get(path)
    assert before_cutoff.status_code == 200
    assert before_cutoff.json()["open_market_buys_30d"] == 1
    assert before_cutoff.json()["buy_eur_30d"] == buy["amount_eur"]
    assert before_cutoff.json()["largest_buys_30d"] == [buy]

    clock = after
    crosses_during_request = False
    refreshed = client.get(path, headers={"If-None-Match": before_cutoff.headers["etag"]})
    assert refreshed.status_code == 200
    assert refreshed.headers["x-atlas-snapshot"] == before_cutoff.headers["x-atlas-snapshot"]
    assert refreshed.headers["etag"] != before_cutoff.headers["etag"]
    assert refreshed.json()["open_market_buys_30d"] == 0
    assert refreshed.json()["buy_eur_30d"] is None
    assert refreshed.json()["largest_buys_30d"] == []
    cached = client.get(path, headers={"If-None-Match": refreshed.headers["etag"]})
    assert cached.status_code == 304 and not cached.content


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


def test_tied_rows_have_stable_order_and_etags(tmp_path):
    lake = Lake(tmp_path / "lake")
    today = date.today()
    trade_day = today - timedelta(days=1)
    lake.fx_path.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([
        {"date": trade_day, "currency": "SEK", "rate": 10.0},
    ], schema=SCHEMA), lake.fx_path)
    other_lei = make_lei("649300EXAMPLE00001")
    publication = _published(1)
    rows = [
        fi_row(**{
            "LEI-code": EXAMPLE_LEI if index < 6 else other_lei,
            "Publication date": publication,
            "Transaction date": trade_day.strftime("%d/%m/%Y 00:00:00"),
            "Position": "Chief Financial Officer (CFO)" if index % 2 else "Chief Executive Officer (CEO)",
            "Volume": "3000" if index == 0 else "2750",
        })
        for index in range(12)
    ]
    connector = SwedenFIPDMR(http=object())
    records = list(connector.parse(fi_doc(rows), ParseContext(pseudonym_key=lake.pseudonym_key())))
    records = [
        record.model_copy(update={"record_id": f"{index:032x}", "jurisdiction": "FR" if index < 6 else "SE"})
        for index, record in enumerate(records)
    ]
    lake.write_silver(Dataset.INSIDER_TRANSACTIONS, connector.name, records)

    overview = "/v1/stats/overview"
    detail = f"/v1/issuers/{EXAMPLE_LEI}"
    listing = "/v1/issuers?limit=1"
    with TestClient(create_app(str(lake.root))) as client:
        initial = {path: client.get(path) for path in (overview, detail, listing)}
    assert all(response.status_code == 200 for response in initial.values())
    expected_buys = [records[0].record_id, *(record.record_id for record in reversed(records[5:]))]
    assert [row["id"] for row in initial[overview].json()["largest_buys_30d"]] == expected_buys
    assert [row["country"] for row in initial[overview].json()["by_country"]] == ["FR", "SE"]
    assert [row["role"] for row in initial[detail].json()["roles"]] == ["ceo", "cfo"]
    assert [row["lei"] for row in initial[listing].json()] == [EXAMPLE_LEI]

    with TestClient(create_app(str(lake.root))) as client:
        for path, response in initial.items():
            again = client.get(path)
            assert again.json() == response.json()
            assert again.headers["etag"] == response.headers["etag"]
            cached = client.get(path, headers={"If-None-Match": response.headers["etag"]})
            assert cached.status_code == 304 and not cached.content


def test_issuer_name_and_country_use_the_same_latest_filing(tmp_path, monkeypatch):
    publication = _published(1)
    doc = fi_doc([
        fi_row(Issuer="Alpha AB", **{"Publication date": publication}),
        fi_row(Issuer="Beta AB", **{"Publication date": publication}),
        fi_row(Issuer="Earlier AB", **{"Publication date": _published(2)}),
        fi_row(Issuer="Undated AB", **{"Publication date": ""}),
    ])
    lake = Lake(tmp_path / "lake")
    connector = SwedenFIPDMR(http=object())
    parsed = list(connector.parse(doc, ParseContext(pseudonym_key=lake.pseudonym_key())))
    records = [
        row.model_copy(update={"record_id": f"{index:032x}", "jurisdiction": country})
        for index, (row, country) in enumerate(zip(parsed, ("SE", "FI", "FR", "DE"), strict=True))
    ]
    lake.write_silver(Dataset.INSIDER_TRANSACTIONS, connector.name, records)
    read_rows = Snapshot.rows
    previous = None
    for direction in ("ASC", "DESC"):
        def rows_in_input_order(self, sql, params=None, *, direction=direction):
            if "arg_max(issuer_name" in sql:
                sql = sql.replace(
                    "FROM insider_transactions",
                    f"FROM (SELECT * FROM insider_transactions ORDER BY record_id {direction}) filings",
                )
            return read_rows(self, sql, params)

        monkeypatch.setattr(Snapshot, "rows", rows_in_input_order)
        with TestClient(create_app(str(lake.root))) as client:
            listing = client.get("/v1/issuers", params={"limit": 1})
            detail = client.get(f"/v1/issuers/{EXAMPLE_LEI}")
            assert listing.status_code == detail.status_code == 200
            for response, issuer in ((listing, listing.json()[0]), (detail, detail.json())):
                assert (issuer["name"], issuer["country"]) == ("Beta AB", "FI")
                assert issuer["transactions"] == 4
                cached = client.get(str(response.url), headers={"If-None-Match": response.headers["etag"]})
                assert cached.status_code == 304 and not cached.content
            current = [(response.json(), response.headers["etag"]) for response in (listing, detail)]
            if previous is not None:
                assert current == previous
            previous = current


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


@pytest.mark.parametrize(("validators", "status"), [
    (['"unrelated,opaque"', "{strong}"], 304),
    (['"unrelated,opaque", {weak}'], 304),
    (["*"], 304),
    (["{weak}, malformed"], 200),
    (['"unrelated"'], 200),
])
def test_if_none_match_validators(client, validators, status):
    path = "/v1/insider-transactions"
    first = client.get(path)
    weak = first.headers["etag"]
    headers = [("If-None-Match", value.format(weak=weak, strong=weak.removeprefix("W/")))
               for value in validators]
    conditional = client.get(path, headers=headers)
    assert conditional.status_code == status
    if status == 304:
        assert not conditional.content
        for header in ("etag", "cache-control", "x-atlas-snapshot"):
            assert conditional.headers[header] == first.headers[header]


@pytest.mark.parametrize(("collection", "items", "key"), [
    ("insider-transactions", "items", "id"),
    ("issuers", None, "lei"),
])
def test_if_none_match_wildcard_requires_an_existing_resource(client, collection, items, key):
    path = f"/v1/{collection}"
    records = client.get(path).json()
    if items is not None:
        records = records[items]
    headers = {"If-None-Match": "*"}
    assert client.get(f"{path}/{records[0][key]}", headers=headers).status_code == 304
    assert client.get(f"{path}/does-not-exist", headers=headers).status_code == 404


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


def test_pinned_issuer_windows_use_snapshot_time(tmp_path, monkeypatch):
    lake = Lake(tmp_path / "lake")
    now = datetime.now(STOCKHOLM)
    lake.fx_path.parent.mkdir(parents=True)
    rates = [{"date": now.date() - timedelta(days=401), "currency": "SEK", "rate": 10.0}]
    pq.write_table(pa.Table.from_pylist(rates, schema=SCHEMA), lake.fx_path)
    rows = []
    for days, volume in [(120, "75"), (400, "50")]:
        published = now - timedelta(days=days)
        rows.append(fi_row(**{
            "Publication date": published.strftime("%d/%m/%Y %H:%M:%S"),
            "Transaction date": (published - timedelta(days=1)).strftime("%d/%m/%Y %H:%M:%S"),
            "Volume": volume, "Price": "2.0", "Currency": "EUR",
        }))
    _load(lake, rows)
    pinned = lake.current_snapshot()

    def historical_time(self, snapshot_id):
        assert snapshot_id == pinned
        return now - timedelta(days=60)

    monkeypatch.setattr(Lake, "snapshot_time", historical_time)

    with TestClient(create_app(str(lake.root))) as client:
        latest = client.get("/v1/issuers").json()[0]
        lei = latest["lei"]
        assert latest["net_open_market_eur_365d"] == 150
        assert client.get(f"/v1/issuers/{lei}").json()["distinct_buyers_90d"] == 0
        params = {"as_of": pinned}
        listing = client.get("/v1/issuers", params={**params, "limit": 1})
        assert listing.json()[0]["net_open_market_eur_365d"] == 250
        detail = client.get(f"/v1/issuers/{lei}", params=params)
        assert detail.json()["net_open_market_eur_365d"] == 250
        assert detail.json()["distinct_buyers_90d"] == 1
        for response in (listing, detail):
            assert response.status_code == 200
            assert "immutable" in response.headers["cache-control"]
            url = str(response.url)
            old_hash = hashlib.sha1(url.encode()).hexdigest()[:10]
            for variant in ("", "issuer-clock-", "issuer-clock-order-"):
                old_headers = {"If-None-Match": f'W/"{pinned}-{variant}{old_hash}"'}
                refreshed = client.get(url, headers=old_headers)
                assert refreshed.status_code == 200
                assert refreshed.json() == response.json()
                assert refreshed.headers["etag"] == response.headers["etag"]
            cached = client.get(url, headers={"If-None-Match": response.headers["etag"]})
            assert cached.status_code == 304 and not cached.content


@pytest.mark.parametrize(("path", "window"), [
    ("/v1/stats/overview", 30),
    ("/v1/stats/flow?days=7", 7),
    ("/v1/issuers", 365),
    (f"/v1/issuers/{EXAMPLE_LEI}", 90),
])
def test_live_etag_changes_when_a_record_leaves_its_window(tmp_path, monkeypatch, path, window):
    lake = Lake(tmp_path / "lake")
    before = datetime(2026, 1, 1, 23, 59, 59, tzinfo=UTC)
    after = before + timedelta(seconds=2)
    transaction_day = before.date() - timedelta(days=window)
    publication = before - timedelta(days=window) + timedelta(seconds=1)
    lake.fx_path.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([
        {"date": transaction_day, "currency": "SEK", "rate": 10.0},
    ], schema=SCHEMA), lake.fx_path)
    _load(lake, [fi_row(**{
        "Publication date": publication.astimezone(STOCKHOLM).strftime("%d/%m/%Y %H:%M:%S"),
        "Transaction date": transaction_day.strftime("%d/%m/%Y 00:00:00"),
    })], fetched_at=before)
    clock = before
    query_rows = Snapshot.rows

    def rows_at_clock(self, sql, params=None):
        sql = sql.replace("now()", f"TIMESTAMPTZ '{clock.isoformat()}'")
        sql = sql.replace("current_date", f"DATE '{clock.date().isoformat()}'")
        return query_rows(self, sql, params)

    monkeypatch.setattr(Snapshot, "rows", rows_at_clock)

    with TestClient(create_app(str(lake.root))) as client:
        first = client.get(path)
        assert first.status_code == 200
        old_headers = {"If-None-Match": first.headers["etag"]}
        assert client.get(path, headers=old_headers).status_code == 304
        historical_params = {"as_of": first.headers["x-atlas-snapshot"]}
        historical = client.get(path, params=historical_params) if path.startswith("/v1/issuers") else None

        clock = after
        fresh = client.get(path)
        assert fresh.status_code == 200
        assert fresh.headers["x-atlas-snapshot"] == first.headers["x-atlas-snapshot"]
        assert fresh.json() != first.json()
        changed = client.get(path, headers=old_headers)
        assert changed.status_code == 200
        assert changed.json() == fresh.json()
        assert changed.headers["etag"] != first.headers["etag"]

        cached = client.get(path, headers={"If-None-Match": changed.headers["etag"]})
        assert cached.status_code == 304 and not cached.content
        for header in ("etag", "cache-control", "x-atlas-snapshot"):
            assert cached.headers[header] == changed.headers[header]
        if historical is not None:
            again = client.get(path, params=historical_params)
            assert historical.status_code == again.status_code == 200
            assert historical.json() == again.json()
            assert historical.headers["etag"] == again.headers["etag"]
            assert "immutable" in again.headers["cache-control"]
            assert client.get(path, params=historical_params, headers={
                "If-None-Match": historical.headers["etag"],
            }).status_code == 304
