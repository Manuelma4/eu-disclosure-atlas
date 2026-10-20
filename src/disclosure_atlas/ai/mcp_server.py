"""MCP server (protocol 2026-07-28, stateless): curated tools, a guarded `sql` tool and an MCP App.

Served over Streamable HTTP at `/mcp` by the API (`disclosure-atlas serve`) and over stdio by
`disclosure-atlas mcp` (for Claude Desktop and other local hosts). Every tool is read-only.
"""

from __future__ import annotations

from importlib.resources import files
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.server.apps import Apps, ResourceCsp
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from .. import __version__
from ..api.readmodel import ReadModel
from . import queries
from .guard import DEFAULT_MAX_ROWS, GuardError, run_guarded
from .schema import schema_text

FLOW_APP_URI = "ui://disclosure-atlas/issuer-flow.html"
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

INSTRUCTIONS = """\
EU Disclosure Atlas: insider transactions (MAR Article 19) and net short positions (SSR Article 6) across
European markets, unified in two tables. Start with `search_issuers` to resolve a company to its LEI, then
use `insider_activity`, `largest_insider_buys`, `cluster_buys`, `short_interest` or `filing_lag_stats`.
For anything else, read the `atlas://schema` resource and use the read-only `sql` tool. Managers are
pseudonymous ids; never try to re-identify them. Amounts in EUR use ECB reference rates. This is research
data, not investment advice.
"""

Country = Annotated[str | None, Field(description="ISO 3166-1 alpha-2 country code, e.g. SE, FR, DE")]
Days = Annotated[int, Field(ge=1, le=3660, description="Look-back window in days, by transaction date")]


def _flow_html() -> str:
    return files("disclosure_atlas.ai").joinpath("ui/issuer_flow.html").read_text(encoding="utf-8")


def build_server(read_model: ReadModel, *, max_rows: int = DEFAULT_MAX_ROWS, timeout: float = 5.0) -> MCPServer:
    """An MCP server over a (sandboxed) read model; see guard.SandboxedLake."""
    apps = Apps()

    # MCP App: registered before the server is built, which consumes the extension.
    @apps.tool(resource_uri=FLOW_APP_URI, annotations=READ_ONLY, name="show_insider_flow")
    def show_insider_flow(
        lei: Annotated[str, Field(min_length=20, max_length=20, description="Issuer LEI (from search_issuers)")],
        days: Annotated[int, Field(ge=30, le=3660)] = 730,
    ) -> dict[str, Any]:
        """Chart an issuer's monthly open-market insider buying and selling in EUR (renders inline in hosts
        that support MCP Apps; others get the same data as JSON)."""
        with read_model.snapshot() as snap:
            activity = queries.insider_activity(snap, lei, days, recent=0)
        if activity is None:
            raise ToolError(f"No insider transactions for LEI {lei.upper()}")
        return {key: activity[key] for key in ("lei", "name", "country", "days", "buy_eur", "sell_eur", "monthly")}

    apps.add_html_resource(
        FLOW_APP_URI,
        _flow_html(),
        name="issuer-flow",
        title="Insider flow",
        description="Monthly open-market insider buying and selling for one issuer",
        csp=ResourceCsp(connect_domains=[], resource_domains=[]),
        prefers_border=True,
    )
    server = MCPServer(
        "disclosure-atlas",
        title="EU Disclosure Atlas",
        description="European insider trades and short positions as tools for agents",
        instructions=INSTRUCTIONS,
        website_url="https://github.com/Manuelma4/eu-disclosure-atlas",
        version=__version__,
        extensions=[apps],
    )

    @server.tool(annotations=READ_ONLY)
    def search_issuers(
        query: Annotated[str, Field(min_length=2, max_length=100, description="Part of a name, an LEI or an ISIN")],
        country: Country = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 20,
    ) -> list[dict[str, Any]]:
        """Find issuers by name, LEI or ISIN; returns LEI, name, country and transaction counts."""
        with read_model.snapshot() as snap:
            return queries.search_issuers(snap, query, country, limit)

    @server.tool(annotations=READ_ONLY)
    def insider_activity(
        lei: Annotated[str, Field(min_length=20, max_length=20, description="Issuer LEI (from search_issuers)")],
        days: Days = 365,
    ) -> dict[str, Any]:
        """Insider activity at one issuer: open-market buys and sells (counts, EUR, distinct managers),
        monthly flow and the most recent transactions."""
        with read_model.snapshot() as snap:
            activity = queries.insider_activity(snap, lei, days)
        if activity is None:
            raise ToolError(f"No insider transactions for LEI {lei.upper()}")
        return activity

    @server.tool(annotations=READ_ONLY)
    def largest_insider_buys(
        days: Days = 30,
        country: Country = None,
        limit: Annotated[int, Field(ge=1, le=100)] = 10,
    ) -> list[dict[str, Any]]:
        """Largest open-market share purchases by managers (EUR), most recent `days`, optionally per country."""
        with read_model.snapshot() as snap:
            return queries.largest_insider_buys(snap, days, country, limit)

    @server.tool(annotations=READ_ONLY)
    def filing_lag_stats(country: Country = None) -> list[dict[str, Any]]:
        """How fast managers' transactions are published, per country: mean, median, 90th percentile and
        share later than five calendar days (MAR Article 19(1) allows three business days)."""
        with read_model.snapshot() as snap:
            return queries.filing_lag_stats(snap, country)

    @server.tool(annotations=READ_ONLY)
    def short_interest(
        issuer: Annotated[str, Field(min_length=2, max_length=100, description="Issuer LEI, ISIN or name")],
        as_of: Annotated[str | None, Field(description="YYYY-MM-DD; default: latest disclosures")] = None,
    ) -> dict[str, Any]:
        """Public net short positions on one issuer (SSR Article 6, holders at or above 0.5%): each holder's
        latest disclosed position and the disclosed total."""
        with read_model.snapshot() as snap:
            result = queries.short_interest(snap, issuer, as_of)
        if result is None:
            raise ToolError(f"No net short positions found for {issuer!r}")
        return result

    @server.tool(annotations=READ_ONLY)
    def cluster_buys(
        days: Days = 90,
        window_days: Annotated[int, Field(ge=1, le=180, description="Maximum spread of the buys")] = 30,
        min_insiders: Annotated[int, Field(ge=2, le=20)] = 3,
        country: Country = None,
    ) -> list[dict[str, Any]]:
        """Issuers where several distinct managers bought shares on the open market within a short window
        (a "cluster buy"), most recent `days`."""
        with read_model.snapshot() as snap:
            return queries.cluster_buys(snap, days, window_days, min_insiders, country)

    @server.tool(annotations=READ_ONLY)
    def sql(
        query: Annotated[str, Field(max_length=10_000, description="One DuckDB SELECT over the gold views")],
    ) -> dict[str, Any]:
        """Run one read-only SELECT over the gold views `insider_transactions` and `net_short_positions`
        (schema: resource atlas://schema). No other tables, no file or network functions; a LIMIT is enforced
        and slow queries are interrupted."""
        with read_model.snapshot() as snap:
            try:
                result = run_guarded(snap.cursor, query, max_rows=max_rows, timeout=timeout)
            except GuardError as exc:
                raise ToolError(str(exc)) from exc
        return {"sql": result.sql, "columns": result.columns, "rows": result.rows, "truncated": result.truncated}

    @server.resource("atlas://schema", name="schema", title="Gold schema", mime_type="text/plain")
    def schema() -> str:
        """Tables and columns that the `sql` tool can query, with descriptions."""
        with read_model.snapshot() as snap:
            return schema_text(snap.cursor)

    return server
