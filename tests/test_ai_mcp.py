"""The MCP server answers its tools against a local (synthetic) lake, through the official SDK client:
in process, and over stateless Streamable HTTP at /mcp of the API."""

from __future__ import annotations

import asyncio
import socket
import sys
import threading
import time

import pytest

pytest.importorskip("mcp")

import uvicorn  # noqa: E402
from mcp import Client  # noqa: E402

from disclosure_atlas.ai.evaluation import build_synthetic_lake, issuer_lei  # noqa: E402
from disclosure_atlas.ai.guard import SandboxedLake  # noqa: E402
from disclosure_atlas.ai.mcp_server import FLOW_APP_URI, build_server  # noqa: E402
from disclosure_atlas.api import create_app  # noqa: E402
from disclosure_atlas.api.readmodel import ReadModel  # noqa: E402

NORDVIK = issuer_lei(0)


@pytest.fixture(scope="module")
def lake(tmp_path_factory):
    return build_synthetic_lake(tmp_path_factory.mktemp("mcp") / "lake")


@pytest.fixture(scope="module")
def server(lake):
    return build_server(ReadModel(SandboxedLake(lake)))


def call(server_or_url, name, arguments=None):
    async def go():
        async with Client(server_or_url) as client:
            return await client.call_tool(name, arguments or {})

    return asyncio.run(go())


def test_lists_read_only_tools_the_schema_and_the_app(server):
    async def go():
        async with Client(server) as client:
            return await client.list_tools(), await client.list_resources(), await client.read_resource(FLOW_APP_URI)

    tools, resources, app = asyncio.run(go())
    by_name = {t.name: t for t in tools.tools}
    assert set(by_name) == {"search_issuers", "insider_activity", "largest_insider_buys", "filing_lag_stats", "sql",
                            "short_interest", "cluster_buys", "show_insider_flow"}
    assert all(t.annotations.read_only_hint for t in tools.tools)
    assert by_name["show_insider_flow"].meta["ui"]["resourceUri"] == FLOW_APP_URI
    uris = {str(r.uri) for r in resources.resources}
    assert {"atlas://schema", FLOW_APP_URI} <= uris
    html = app.contents[0]
    assert html.mime_type == "text/html;profile=mcp-app" and "ui/notifications/tool-result" in html.text


def test_search_issuers(server):
    result = call(server, "search_issuers", {"query": "nordvik"})
    assert not result.is_error
    rows = result.structured_content["result"]
    assert rows[0]["lei"] == NORDVIK and rows[0]["country"] == "SE"


def test_insider_activity(server):
    result = call(server, "insider_activity", {"lei": NORDVIK, "days": 3660})
    body = result.structured_content
    assert body["name"] == "Nordvik Robotics AB" and body["open_market_buys"] > 0
    assert body["net_eur"] == pytest.approx(body["buy_eur"] - body["sell_eur"])
    assert body["monthly"] and len(body["recent"]) == 25


def test_unknown_issuer_is_a_tool_error(server):
    result = call(server, "insider_activity", {"lei": "0" * 20})
    assert result.is_error and "No insider transactions" in result.content[0].text


def test_largest_insider_buys_and_filing_lag(server):
    buys = call(server, "largest_insider_buys", {"days": 3660, "country": "de", "limit": 3})
    buys = buys.structured_content["result"]
    assert len(buys) == 3 and {b["country"] for b in buys} == {"DE"}
    assert buys[0]["amount_eur"] >= buys[1]["amount_eur"] >= buys[2]["amount_eur"]
    lags = {row["country"]: row for row in call(server, "filing_lag_stats").structured_content["result"]}
    assert set(lags) == {"SE", "FR", "DE", "NL", "ES"} and lags["ES"]["late_share"] > lags["NL"]["late_share"]


def test_short_interest_and_cluster_buys(server):
    shorts = call(server, "short_interest", {"issuer": "Rheinwerk"}).structured_content
    assert shorts["name"] == "Rheinwerk Systems AG" and shorts["positions"]
    assert all(p["position_pct"] >= 0.5 for p in shorts["positions"])
    assert shorts["total_disclosed_pct"] == pytest.approx(sum(p["position_pct"] for p in shorts["positions"]), abs=0.01)
    earlier = call(server, "short_interest", {"issuer": "Rheinwerk", "as_of": "2025-06-30"}).structured_content
    assert all(p["position_date"] <= "2025-06-30" for p in earlier["positions"])
    clusters = call(server, "cluster_buys", {"days": 3660, "window_days": 60, "min_insiders": 2})
    rows = clusters.structured_content["result"]
    assert rows and all(r["insiders"] >= 2 for r in rows)
    assert call(server, "short_interest", {"issuer": "No Such Company"}).is_error


def test_sql_tool_is_guarded(server):
    ok = call(server, "sql", {"query": "SELECT jurisdiction, count(*) AS n FROM insider_transactions GROUP BY 1"})
    assert not ok.is_error and ok.structured_content["sql"].endswith("LIMIT 200")
    assert len(ok.structured_content["rows"]) == 5
    for query in ("DROP VIEW insider_transactions", "SELECT * FROM read_csv('/etc/passwd')",
                  "SELECT * FROM silver_insider_transactions"):
        denied = call(server, "sql", {"query": query})
        assert denied.is_error, query


def test_show_insider_flow_returns_chart_data(server):
    body = call(server, "show_insider_flow", {"lei": NORDVIK, "days": 3660}).structured_content
    assert body["lei"] == NORDVIK and body["monthly"][0].keys() >= {"month", "buy_eur", "sell_eur"}


@pytest.fixture(scope="module")
def http_url(lake):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(str(lake.root)), host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True
    thread.join(timeout=5)


def test_streamable_http_endpoint_on_the_api(http_url):
    result = call(http_url, "search_issuers", {"query": "Grachtenbank"})
    assert result.structured_content["result"][0]["name"] == "Grachtenbank NV"

    async def negotiated():
        async with Client(http_url) as client:
            return client.protocol_version

    assert asyncio.run(negotiated()) == "2026-07-28"


def test_stdio_command(tmp_path):
    from mcp import StdioServerParameters

    own = build_synthetic_lake(tmp_path / "lake")
    own.close()  # a DuckDB-file catalog is locked by one process: release it so the server process can attach it
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "disclosure_atlas.cli", "--lake", str(own.root), "mcp"]
    )
    result = call(params, "filing_lag_stats", {"country": "SE"})
    assert [row["country"] for row in result.structured_content["result"]] == ["SE"]
