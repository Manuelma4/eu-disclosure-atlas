"""FastAPI application: /v1 read API over the gold views, SSE live stream, RFC 9457 errors.

Every response is pinned to a data version: the ETag is derived from it, so a new ingest invalidates
caches everywhere at once (see docs/decisions/0008).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import UTC, date, datetime
from http import HTTPStatus
from typing import Annotated, Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import __version__, connectors
from ..lake import Lake
from . import telemetry
from .readmodel import ReadModel, Snapshot
from .schemas import (
    AskRequest,
    AskResponse,
    CountryStat,
    FlowPoint,
    Health,
    IssuerDetail,
    IssuerSummary,
    MonthPoint,
    Overview,
    Page,
    RoleStat,
    SourceStatus,
    Transaction,
)
from .security import RateLimitMiddleware, SecurityHeadersMiddleware, rate_limit_settings

TX_COLUMNS = """
    record_id AS id, source, jurisdiction AS country, published_at, first_seen_at, transaction_date,
    issuer_name, issuer_lei, pdmr_id AS person_id, pdmr_role AS role, pdmr_role_raw AS role_raw,
    notifier_name, closely_associated, nature, direction, instrument_type, instrument_name, isin,
    volume, price, currency, amount, amount_eur, venue, is_open_market AS open_market, is_amendment,
    filing_lag_days
"""
ISSUER_COLUMNS = """
    issuer_lei AS lei, arg_max(issuer_name, published_at) AS name, arg_max(jurisdiction, published_at) AS country,
    count(*) AS transactions, max(published_at) AS last_published_at,
    sum(direction * amount_eur) FILTER (WHERE is_open_market AND published_at >= now() - INTERVAL 365 DAY)
        AS net_open_market_eur_365d
"""
CACHE_CONTROL = "public, max-age=30, s-maxage=60, stale-while-revalidate=600"
DESCRIPTION = """
Open, unified data on insider trading (MAR Article 19) and short selling (SSR Article 6) across European
markets. Natural persons appear only as pseudonymous ids. Every response is pinned to a data snapshot,
exposed in the `ETag` and `X-Atlas-Snapshot` headers. Not investment advice.
"""


def _encode_cursor(published_at: datetime | None, record_id: str) -> str:
    raw = json.dumps([published_at.isoformat() if published_at else None, record_id]).encode()
    return urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime | None, str]:
    try:
        published, record = json.loads(urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        return (datetime.fromisoformat(published) if published else None), str(record)
    except (ValueError, TypeError) as exc:
        raise HTTPException(HTTPStatus.BAD_REQUEST, "Invalid cursor") from exc


def _parse_event_id(event_id: str) -> tuple[datetime, str]:
    try:
        seen, record = event_id.split("|", 1)
        return datetime.fromisoformat(seen), record
    except ValueError as exc:
        raise HTTPException(HTTPStatus.BAD_REQUEST, "Invalid stream position") from exc


def _conditional(request: Request, response: Response, snapshot: Snapshot) -> Response | None:
    """Set ETag and caching headers; return a 304 response when the client already has this version."""
    etag = f'W/"{snapshot.version}-{hashlib.sha1(str(request.url).encode()).hexdigest()[:10]}"'
    headers = {"ETag": etag, "Cache-Control": CACHE_CONTROL, "X-Atlas-Snapshot": snapshot.version}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=HTTPStatus.NOT_MODIFIED, headers=headers)
    response.headers.update(headers)
    return None


def _problem(request: Request, status: int, detail: Any) -> JSONResponse:
    body = {"type": "about:blank", "title": HTTPStatus(status).phrase, "status": status, "detail": detail,
            "instance": request.url.path}
    return JSONResponse(body, status_code=status, media_type="application/problem+json")


def _mount_mcp(app: FastAPI, lake: Lake) -> Any:
    """Mount the stateless MCP server at /mcp when the `ai` extra is installed; returns its session manager."""
    try:
        from mcp.server.transport_security import TransportSecuritySettings

        from ..ai.guard import SandboxedLake
        from ..ai.mcp_server import build_server
    except ImportError:
        return None
    # DNS-rebinding protection: only these Host values are served ("*" turns the check off behind a gateway).
    setting = os.environ.get("ATLAS_MCP_ALLOWED_HOSTS", "127.0.0.1:*,localhost:*,[::1]:*")
    hosts = [h.strip() for h in setting.split(",") if h.strip()]
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=setting.strip() != "*",
        allowed_hosts=hosts,
        allowed_origins=[f"{scheme}://{h}" for h in hosts for scheme in ("http", "https")],
    )
    server = build_server(ReadModel(SandboxedLake(lake)))
    mcp_app = server.streamable_http_app(
        streamable_http_path="/mcp", stateless_http=True, json_response=True, transport_security=security
    )
    app.router.routes.extend(mcp_app.routes)
    return server.session_manager


def create_app(
    lake_root: str | None = None,
    stream_poll_seconds: float = 5.0,
    asker: Any = None,
    rate_limit: tuple[int, int] | None = None,
) -> FastAPI:
    """The API. `asker` overrides the Ask the Atlas model client (tests and evaluations); `rate_limit` is
    (requests per minute, burst) per client IP, defaulting to the environment settings."""
    lake = Lake(lake_root or os.environ.get("ATLAS_LAKE", "lake"))
    read_model = ReadModel(lake)
    mcp_sessions: Any = None

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> Any:
        async with mcp_sessions.run() if mcp_sessions else contextlib.nullcontext():
            yield

    app = FastAPI(
        lifespan=lifespan,
        title="EU Disclosure Atlas API",
        version=__version__,
        description=DESCRIPTION,
        contact={"name": "Manuel David Maya Rosero", "url": "https://manuelma4.github.io/"},
        license_info={"name": "Apache-2.0", "identifier": "Apache-2.0"},
        openapi_tags=[
            {"name": "insider-transactions", "description": "Managers' transactions (MAR Article 19)"},
            {"name": "issuers", "description": "Issuer profiles and insider activity"},
            {"name": "stats", "description": "Market-wide aggregates"},
            {"name": "stream", "description": "Server-sent events for new filings"},
            {"name": "ai", "description": "Ask the Atlas: questions in plain English, answered with SQL"},
            {"name": "meta", "description": "Health and source coverage"},
        ],
    )
    mcp_sessions = _mount_mcp(app, lake)
    ai_read_model: ReadModel | None = None
    # Middleware runs outermost last-added: security headers, then CORS, then the per-IP rate limit.
    per_minute, burst = rate_limit or rate_limit_settings()
    if per_minute > 0:
        app.add_middleware(RateLimitMiddleware, per_minute=per_minute, burst=burst)
    origins = os.environ.get("ATLAS_CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in origins.split(",") if o.strip()],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
        expose_headers=["ETag", "X-Atlas-Snapshot"],
    )
    app.add_middleware(SecurityHeadersMiddleware)

    @app.exception_handler(StarletteHTTPException)
    async def http_problem(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _problem(request, exc.status_code, exc.detail)

    @app.exception_handler(RequestValidationError)
    async def validation_problem(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _problem(request, HTTPStatus.UNPROCESSABLE_ENTITY, jsonable_errors(exc))

    @app.get("/v1/health", response_model=Health, tags=["meta"])
    def health() -> Health:
        with read_model.snapshot() as snap:
            records = snap.one("SELECT count(*) AS n FROM insider_transactions")["n"]
            return Health(status="ok", snapshot=snap.version, records=records)

    @app.get("/v1/sources", response_model=list[SourceStatus], tags=["meta"])
    def sources(request: Request, response: Response) -> Any:
        with read_model.snapshot() as snap:
            if not_modified := _conditional(request, response, snap):
                return not_modified
            stats = {
                row["source"]: row
                for row in snap.rows(
                    "SELECT source, count(*) AS records, max(published_at) AS last_published_at, "
                    "max(first_seen_at) AS last_ingested_at FROM insider_transactions GROUP BY 1 "
                    "UNION ALL SELECT source, count(*), max(published_at), max(first_seen_at) "
                    "FROM net_short_positions GROUP BY 1"
                )
            }
        return [
            SourceStatus(
                name=name, country=cls.jurisdiction, dataset=cls.dataset.value, authority=cls.authority,
                homepage=cls.homepage, records=stats.get(name, {}).get("records", 0),
                last_published_at=stats.get(name, {}).get("last_published_at"),
                last_ingested_at=stats.get(name, {}).get("last_ingested_at"),
            )
            for name, cls in connectors.all_connectors().items()
        ]

    @app.get("/v1/insider-transactions", response_model=Page[Transaction], tags=["insider-transactions"])
    def list_transactions(
        request: Request,
        response: Response,
        country: Annotated[list[str] | None, Query(description="ISO country codes, repeatable")] = None,
        issuer_lei: str | None = None,
        isin: str | None = None,
        nature: Annotated[list[str] | None, Query(description="buy, sell, grant, ... repeatable")] = None,
        role: Annotated[list[str] | None, Query(description="ceo, cfo, chair, ... repeatable")] = None,
        open_market: bool | None = None,
        since: Annotated[date | None, Query(description="Published on or after (YYYY-MM-DD)")] = None,
        until: Annotated[date | None, Query(description="Published on or before (YYYY-MM-DD)")] = None,
        min_amount_eur: float | None = None,
        q: Annotated[str | None, Query(description="Issuer name contains")] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        cursor: str | None = None,
    ) -> Any:
        where: list[str] = []
        params: list[Any] = []
        if country:
            where.append(f"jurisdiction IN ({', '.join('?' * len(country))})")
            params += [c.upper() for c in country]
        if issuer_lei:
            where.append("issuer_lei = ?")
            params.append(issuer_lei.upper())
        if isin:
            where.append("isin = ?")
            params.append(isin.upper())
        if nature:
            where.append(f"nature IN ({', '.join('?' * len(nature))})")
            params += nature
        if role:
            where.append(f"pdmr_role IN ({', '.join('?' * len(role))})")
            params += role
        if open_market is not None:
            where.append("is_open_market = ?")
            params.append(open_market)
        if since:
            where.append("CAST(timezone('UTC', published_at) AS DATE) >= ?")
            params.append(since)
        if until:
            where.append("CAST(timezone('UTC', published_at) AS DATE) <= ?")
            params.append(until)
        if min_amount_eur is not None:
            where.append("amount_eur >= ?")
            params.append(min_amount_eur)
        if q:
            where.append("issuer_name ILIKE ?")
            params.append(f"%{q}%")
        if cursor:
            published, record = _decode_cursor(cursor)
            if published is None:
                where.append("(published_at IS NULL AND record_id < ?)")
                params.append(record)
            else:
                where.append(
                    "(published_at < ? OR (published_at = ? AND record_id < ?) OR published_at IS NULL)"
                )
                params += [published, published, record]
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        with read_model.snapshot() as snap:
            if not_modified := _conditional(request, response, snap):
                return not_modified
            rows = snap.rows(
                f"SELECT {TX_COLUMNS} FROM insider_transactions {clause} "
                "ORDER BY published_at DESC NULLS LAST, record_id DESC LIMIT ?",
                [*params, limit + 1],
            )
            has_more = len(rows) > limit
            items = rows[:limit]
            next_cursor = _encode_cursor(items[-1]["published_at"], items[-1]["id"]) if has_more else None
            return Page[Transaction](items=items, next_cursor=next_cursor, snapshot=snap.version)

    @app.get("/v1/insider-transactions/{record_id}", response_model=Transaction, tags=["insider-transactions"])
    def get_transaction(record_id: str, request: Request, response: Response) -> Any:
        with read_model.snapshot() as snap:
            if not_modified := _conditional(request, response, snap):
                return not_modified
            row = snap.one(f"SELECT {TX_COLUMNS} FROM insider_transactions WHERE record_id = ?", [record_id])
        if row is None:
            raise HTTPException(HTTPStatus.NOT_FOUND, f"No transaction {record_id}")
        return row

    @app.get("/v1/issuers", response_model=list[IssuerSummary], tags=["issuers"])
    def list_issuers(
        request: Request,
        response: Response,
        q: Annotated[str | None, Query(description="Name contains, or exact LEI")] = None,
        country: str | None = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 50,
    ) -> Any:
        where, params = ["issuer_lei IS NOT NULL"], []
        if q:
            where.append("(issuer_name ILIKE ? OR issuer_lei = ?)")
            params += [f"%{q}%", q.upper()]
        if country:
            where.append("jurisdiction = ?")
            params.append(country.upper())
        with read_model.snapshot() as snap:
            if not_modified := _conditional(request, response, snap):
                return not_modified
            return snap.rows(
                f"SELECT {ISSUER_COLUMNS} FROM insider_transactions WHERE {' AND '.join(where)} "
                "GROUP BY issuer_lei ORDER BY last_published_at DESC NULLS LAST LIMIT ?",
                [*params, limit],
            )

    @app.get("/v1/issuers/{lei}", response_model=IssuerDetail, tags=["issuers"])
    def get_issuer(lei: str, request: Request, response: Response) -> Any:
        lei = lei.upper()
        with read_model.snapshot() as snap:
            if not_modified := _conditional(request, response, snap):
                return not_modified
            summary = snap.one(
                f"""
                SELECT {ISSUER_COLUMNS},
                    count(DISTINCT pdmr_id) FILTER (WHERE is_open_market AND direction > 0
                        AND published_at >= now() - INTERVAL 90 DAY) AS distinct_buyers_90d,
                    count(DISTINCT pdmr_id) FILTER (WHERE is_open_market AND direction < 0
                        AND published_at >= now() - INTERVAL 90 DAY) AS distinct_sellers_90d
                FROM insider_transactions WHERE issuer_lei = ? GROUP BY issuer_lei
                """,
                [lei],
            )
            if summary is None:
                raise HTTPException(HTTPStatus.NOT_FOUND, f"No issuer with LEI {lei}")
            monthly = snap.rows(
                """
                SELECT CAST(date_trunc('month', transaction_date) AS DATE) AS month,
                    sum(amount_eur) FILTER (WHERE is_open_market AND direction > 0) AS buy_eur,
                    sum(amount_eur) FILTER (WHERE is_open_market AND direction < 0) AS sell_eur,
                    count(*) FILTER (WHERE is_open_market AND direction > 0) AS buys,
                    count(*) FILTER (WHERE is_open_market AND direction < 0) AS sells
                FROM insider_transactions WHERE issuer_lei = ? AND transaction_date IS NOT NULL
                GROUP BY 1 ORDER BY 1
                """,
                [lei],
            )
            roles = snap.rows(
                "SELECT pdmr_role AS role, count(*) AS transactions, count(DISTINCT pdmr_id) AS people "
                "FROM insider_transactions WHERE issuer_lei = ? GROUP BY 1 ORDER BY 2 DESC",
                [lei],
            )
            recent = snap.rows(
                f"SELECT {TX_COLUMNS} FROM insider_transactions WHERE issuer_lei = ? "
                "ORDER BY published_at DESC NULLS LAST, record_id DESC LIMIT 100",
                [lei],
            )
        return IssuerDetail(
            **summary,
            monthly=[MonthPoint(**m) for m in monthly],
            roles=[RoleStat(**r) for r in roles],
            recent=[Transaction(**t) for t in recent],
        )

    @app.get("/v1/stats/overview", response_model=Overview, tags=["stats"])
    def overview(request: Request, response: Response) -> Any:
        recent_30 = "published_at >= now() - INTERVAL 30 DAY"
        with read_model.snapshot() as snap:
            if not_modified := _conditional(request, response, snap):
                return not_modified
            totals = snap.one(
                f"""
                SELECT count(*) AS transactions, count(DISTINCT issuer_lei) AS issuers,
                    count(DISTINCT jurisdiction) AS countries, count(DISTINCT source) AS sources,
                    max(published_at) AS last_published_at, max(first_seen_at) AS last_ingested_at,
                    count(*) FILTER (WHERE is_open_market AND direction > 0 AND {recent_30}) AS open_market_buys_30d,
                    count(*) FILTER (WHERE is_open_market AND direction < 0 AND {recent_30}) AS open_market_sells_30d,
                    sum(amount_eur) FILTER (WHERE is_open_market AND direction > 0 AND {recent_30}) AS buy_eur_30d,
                    sum(amount_eur) FILTER (WHERE is_open_market AND direction < 0 AND {recent_30}) AS sell_eur_30d,
                    avg(CASE WHEN filing_lag_days > 5 THEN 1.0 ELSE 0.0 END) FILTER (
                        WHERE filing_lag_days IS NOT NULL AND published_at >= now() - INTERVAL 365 DAY
                    ) AS late_share_365d
                FROM insider_transactions
                """
            )
            by_country = snap.rows(
                """
                SELECT jurisdiction AS country, count(*) AS transactions, count(DISTINCT issuer_lei) AS issuers,
                    avg(CASE WHEN filing_lag_days > 5 THEN 1.0 ELSE 0.0 END)
                        FILTER (WHERE filing_lag_days IS NOT NULL) AS late_share
                FROM insider_transactions GROUP BY 1 ORDER BY 2 DESC
                """
            )
            largest = snap.rows(
                f"SELECT {TX_COLUMNS} FROM insider_transactions WHERE is_open_market AND direction > 0 "
                f"AND {recent_30} AND amount_eur IS NOT NULL ORDER BY amount_eur DESC LIMIT 8"
            )
            latest = snap.rows(
                f"SELECT {TX_COLUMNS} FROM insider_transactions "
                "ORDER BY published_at DESC NULLS LAST, record_id DESC LIMIT 15"
            )
            return Overview(
                snapshot=snap.version,
                **totals,
                by_country=[CountryStat(**c) for c in by_country],
                largest_buys_30d=[Transaction(**t) for t in largest],
                latest=[Transaction(**t) for t in latest],
            )

    @app.get("/v1/stats/flow", response_model=list[FlowPoint], tags=["stats"])
    def flow(
        request: Request,
        response: Response,
        bucket: Annotated[Literal["day", "week", "month"], Query()] = "week",
        days: Annotated[int, Query(ge=7, le=3660)] = 182,
        country: str | None = None,
    ) -> Any:
        """Open-market insider buying and selling (EUR) per period, by transaction date."""
        where, params = ["is_open_market", "transaction_date >= current_date - CAST(? AS INTEGER)"], [days]
        if country:
            where.append("jurisdiction = ?")
            params.append(country.upper())
        with read_model.snapshot() as snap:
            if not_modified := _conditional(request, response, snap):
                return not_modified
            return snap.rows(
                f"""
                SELECT CAST(date_trunc('{bucket}', transaction_date) AS DATE) AS period,
                    sum(amount_eur) FILTER (WHERE direction > 0) AS buy_eur,
                    sum(amount_eur) FILTER (WHERE direction < 0) AS sell_eur,
                    count(*) FILTER (WHERE direction > 0) AS buys,
                    count(*) FILTER (WHERE direction < 0) AS sells
                FROM insider_transactions WHERE {' AND '.join(where)}
                GROUP BY 1 ORDER BY 1
                """,
                params,
            )

    def new_filings(position: tuple[datetime, str]) -> list[dict[str, Any]]:
        seen, record = position
        with read_model.snapshot() as snap:
            return snap.rows(
                f"SELECT {TX_COLUMNS} FROM insider_transactions "
                "WHERE first_seen_at > ? OR (first_seen_at = ? AND record_id > ?) "
                "ORDER BY first_seen_at, record_id LIMIT 200",
                [seen, seen, record],
            )

    @app.get(
        "/v1/stream",
        tags=["stream"],
        response_class=StreamingResponse,
        responses={200: {"content": {"text/event-stream": {}}, "description": "`filing` events, newest last"}},
    )
    async def stream(
        request: Request,
        since: Annotated[str | None, Query(description="Resume after this event id")] = None,
    ) -> StreamingResponse:
        start = request.headers.get("last-event-id") or since
        position = _parse_event_id(start) if start else (datetime.now(UTC), "")

        async def events() -> Any:
            nonlocal position
            telemetry.stream_opened()
            try:
                yield "retry: 5000\n\n"
                last_ping = time.monotonic()
                while not await request.is_disconnected():
                    for row in await run_in_threadpool(new_filings, position):
                        position = (row["first_seen_at"], row["id"])
                        event_id = f"{row['first_seen_at'].isoformat()}|{row['id']}"
                        yield f"id: {event_id}\nevent: filing\ndata: {Transaction(**row).model_dump_json()}\n\n"
                        telemetry.stream_event_sent(row["first_seen_at"])
                    if time.monotonic() - last_ping >= 15:
                        yield ": ping\n\n"
                        last_ping = time.monotonic()
                    await asyncio.sleep(stream_poll_seconds)
            finally:
                telemetry.stream_closed()

        return StreamingResponse(
            events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        )

    @app.post(
        "/v1/ask",
        response_model=AskResponse,
        tags=["ai"],
        responses={
            422: {"description": "The question could not be turned into a valid read-only query"},
            502: {"description": "The model provider failed"},
            503: {"description": "Ask the Atlas is not configured on this server"},
        },
    )
    def ask(body: AskRequest) -> Any:
        """Answer a question with a guarded, read-only SQL query over the gold views, the rows it returned
        and a short answer written from those rows. Research data, not investment advice."""
        nonlocal ai_read_model
        try:
            from ..ai.ask import Asker, AskError, AskUnavailable
            from ..ai.guard import SandboxedLake
        except ImportError as exc:
            raise HTTPException(HTTPStatus.SERVICE_UNAVAILABLE, "The ai extra is not installed") from exc
        try:
            client = asker or Asker.from_env()
        except AskUnavailable as exc:
            raise HTTPException(HTTPStatus.SERVICE_UNAVAILABLE, str(exc)) from exc
        ai_read_model = ai_read_model or ReadModel(SandboxedLake(lake))
        with ai_read_model.snapshot() as snap:
            try:
                result = client.ask(snap.cursor, body.question)
            except AskError as exc:
                raise HTTPException(HTTPStatus.UNPROCESSABLE_ENTITY, str(exc)) from exc
            except Exception as exc:
                if type(exc).__module__.startswith("anthropic"):
                    raise HTTPException(HTTPStatus.BAD_GATEWAY, "The model provider failed; try again") from exc
                raise
            return AskResponse(**result.to_dict(), snapshot=snap.version)

    def source_stats() -> list[dict[str, Any]]:
        with read_model.snapshot() as snap:
            return snap.rows(telemetry.SOURCE_STATS_SQL)

    telemetry.configure(app, source_stats)
    return app


def jsonable_errors(exc: RequestValidationError) -> list[dict[str, Any]]:
    return [{"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]


def app_from_env() -> FastAPI:
    """Factory for uvicorn: `uvicorn disclosure_atlas.api.app:app_from_env --factory`."""
    return create_app()
