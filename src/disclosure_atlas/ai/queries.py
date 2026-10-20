"""Curated, parameterized queries behind the MCP tools. Pure functions of a read-model snapshot."""

from __future__ import annotations

from typing import Any

from ..api.readmodel import Snapshot
from .guard import jsonable

TX_FIELDS = """
    record_id AS id, jurisdiction AS country, published_at, transaction_date, issuer_name, issuer_lei,
    pdmr_id AS person_id, pdmr_role AS role, closely_associated, nature, direction, instrument_type, isin,
    volume, price, currency, amount, amount_eur, is_open_market AS open_market, filing_lag_days
"""


def _rows(snap: Snapshot, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    return [{k: jsonable(v) for k, v in row.items()} for row in snap.rows(sql, params)]


def search_issuers(snap: Snapshot, query: str, country: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    where, params = ["issuer_lei IS NOT NULL", "(issuer_name ILIKE ? OR issuer_lei = ? OR isin = ?)"], []
    params += [f"%{query.strip()}%", query.strip().upper(), query.strip().upper()]
    if country:
        where.append("jurisdiction = ?")
        params.append(country.upper())
    return _rows(
        snap,
        f"""
        SELECT issuer_lei AS lei, arg_max(issuer_name, published_at) AS name,
            arg_max(jurisdiction, published_at) AS country, count(*) AS transactions,
            count(*) FILTER (WHERE is_open_market) AS open_market_transactions,
            max(published_at) AS last_published_at
        FROM insider_transactions WHERE {' AND '.join(where)}
        GROUP BY issuer_lei ORDER BY transactions DESC, name LIMIT ?
        """,
        [*params, limit],
    )


def insider_activity(snap: Snapshot, lei: str, days: int = 365, recent: int = 25) -> dict[str, Any] | None:
    lei = lei.strip().upper()
    window = "transaction_date >= current_date - CAST(? AS INTEGER)"
    summary = _rows(
        snap,
        f"""
        SELECT issuer_lei AS lei, arg_max(issuer_name, published_at) AS name,
            arg_max(jurisdiction, published_at) AS country,
            count(*) FILTER (WHERE {window}) AS transactions,
            count(*) FILTER (WHERE {window} AND is_open_market AND direction > 0) AS open_market_buys,
            count(*) FILTER (WHERE {window} AND is_open_market AND direction < 0) AS open_market_sells,
            coalesce(sum(amount_eur) FILTER (WHERE {window} AND is_open_market AND direction > 0), 0) AS buy_eur,
            coalesce(sum(amount_eur) FILTER (WHERE {window} AND is_open_market AND direction < 0), 0) AS sell_eur,
            count(DISTINCT pdmr_id) FILTER (WHERE {window} AND is_open_market AND direction > 0) AS distinct_buyers,
            count(DISTINCT pdmr_id) FILTER (WHERE {window} AND is_open_market AND direction < 0) AS distinct_sellers
        FROM insider_transactions WHERE issuer_lei = ? GROUP BY issuer_lei
        """,
        [days] * 7 + [lei],
    )
    if not summary:
        return None
    result = summary[0]
    result["days"] = days
    result["net_eur"] = result["buy_eur"] - result["sell_eur"]
    result["monthly"] = monthly_flow(snap, lei, days)
    result["recent"] = _rows(
        snap,
        f"SELECT {TX_FIELDS} FROM insider_transactions WHERE issuer_lei = ? AND {window} "
        "ORDER BY transaction_date DESC, published_at DESC, record_id LIMIT ?",
        [lei, days, recent],
    )
    return result


def monthly_flow(snap: Snapshot, lei: str, days: int = 730) -> list[dict[str, Any]]:
    """Open-market insider buying and selling (EUR) per month for one issuer, by transaction date."""
    return _rows(
        snap,
        """
        SELECT strftime(date_trunc('month', transaction_date), '%Y-%m') AS month,
            coalesce(sum(amount_eur) FILTER (WHERE direction > 0), 0) AS buy_eur,
            coalesce(sum(amount_eur) FILTER (WHERE direction < 0), 0) AS sell_eur,
            count(*) FILTER (WHERE direction > 0) AS buys,
            count(*) FILTER (WHERE direction < 0) AS sells
        FROM insider_transactions
        WHERE issuer_lei = ? AND is_open_market AND transaction_date >= current_date - CAST(? AS INTEGER)
        GROUP BY 1 ORDER BY 1
        """,
        [lei.strip().upper(), days],
    )


def largest_insider_buys(snap: Snapshot, days: int = 30, country: str | None = None, limit: int = 10) -> list[dict]:
    where = ["is_open_market", "direction > 0", "amount_eur IS NOT NULL",
             "transaction_date >= current_date - CAST(? AS INTEGER)"]
    params: list[Any] = [days]
    if country:
        where.append("jurisdiction = ?")
        params.append(country.upper())
    return _rows(
        snap,
        f"SELECT {TX_FIELDS} FROM insider_transactions WHERE {' AND '.join(where)} "
        "ORDER BY amount_eur DESC, record_id LIMIT ?",
        [*params, limit],
    )


def filing_lag_stats(snap: Snapshot, country: str | None = None) -> list[dict[str, Any]]:
    """Calendar days from transaction to publication, per country. MAR Article 19(1) gives managers three
    business days to notify; more than five calendar days is late under any weekday pattern."""
    where, params = ["filing_lag_days IS NOT NULL", "filing_lag_days >= 0"], []
    if country:
        where.append("jurisdiction = ?")
        params.append(country.upper())
    return _rows(
        snap,
        f"""
        SELECT jurisdiction AS country, count(*) AS filings,
            round(avg(filing_lag_days), 2) AS mean_days,
            quantile_cont(filing_lag_days, 0.5) AS median_days,
            quantile_cont(filing_lag_days, 0.9) AS p90_days,
            max(filing_lag_days) AS max_days,
            round(avg(CASE WHEN filing_lag_days > 5 THEN 1.0 ELSE 0.0 END), 4) AS late_share
        FROM insider_transactions WHERE {' AND '.join(where)}
        GROUP BY 1 ORDER BY filings DESC, country
        """,
        params,
    )


def short_interest(snap: Snapshot, query: str, as_of: str | None = None) -> dict[str, Any] | None:
    """Current public net short positions on one issuer (by LEI, ISIN or name): each holder's latest
    disclosure on or before `as_of`, holders whose latest disclosure fell below 0.5% excluded."""
    key = query.strip()
    issuer = _rows(
        snap,
        "SELECT issuer_lei AS lei, arg_max(issuer_name, position_date) AS name, "
        "arg_max(jurisdiction, position_date) AS country FROM net_short_positions "
        "WHERE issuer_lei = ? OR isin = ? OR issuer_name ILIKE ? GROUP BY issuer_lei ORDER BY count(*) DESC LIMIT 1",
        [key.upper(), key.upper(), f"%{key}%"],
    )
    if not issuer:
        return None
    result = issuer[0]
    as_of_clause = "position_date <= CAST(? AS DATE)" if as_of else "true"
    positions = _rows(
        snap,
        f"""
        SELECT holder_name, holder_lei, position_date, position_pct FROM net_short_positions
        WHERE issuer_lei = ? AND {as_of_clause}
        QUALIFY row_number() OVER (PARTITION BY coalesce(holder_lei, holder_name) ORDER BY position_date DESC) = 1
        ORDER BY position_pct DESC, holder_name
        """,
        [result["lei"], *([as_of] if as_of else [])],
    )
    result["as_of"] = as_of
    result["positions"] = [p for p in positions if p["position_pct"] >= 0.5]
    result["total_disclosed_pct"] = round(sum(p["position_pct"] for p in result["positions"]), 2)
    return result


def cluster_buys(
    snap: Snapshot, days: int = 90, window_days: int = 30, min_insiders: int = 3, country: str | None = None
) -> list[dict[str, Any]]:
    """Issuers where at least `min_insiders` distinct managers bought shares on the open market within
    `window_days` of each other, in the last `days`: the classic "cluster buy" signal of the insider literature."""
    where = ["is_open_market", "direction > 0", "transaction_date >= current_date - CAST(? AS INTEGER)"]
    params: list[Any] = [days]
    if country:
        where.append("jurisdiction = ?")
        params.append(country.upper())
    return _rows(
        snap,
        f"""
        WITH buys AS (
            SELECT issuer_lei, issuer_name, jurisdiction, pdmr_id, transaction_date, amount_eur
            FROM insider_transactions WHERE {' AND '.join(where)} AND issuer_lei IS NOT NULL
        ), clusters AS (
            SELECT a.issuer_lei, a.transaction_date AS window_start,
                count(DISTINCT b.pdmr_id) AS insiders, sum(b.amount_eur) AS buy_eur, count(*) AS buys,
                max(b.transaction_date) AS last_buy
            FROM buys a JOIN buys b ON a.issuer_lei = b.issuer_lei
                AND b.transaction_date BETWEEN a.transaction_date AND a.transaction_date + CAST(? AS INTEGER)
            GROUP BY a.issuer_lei, a.transaction_date
            HAVING count(DISTINCT b.pdmr_id) >= ?
        )
        SELECT c.issuer_lei AS lei, any_value(b.issuer_name) AS name, any_value(b.jurisdiction) AS country,
            max(c.insiders) AS insiders, arg_max(c.window_start, c.insiders) AS window_start,
            arg_max(c.last_buy, c.insiders) AS last_buy, arg_max(c.buy_eur, c.insiders) AS buy_eur
        FROM clusters c JOIN buys b USING (issuer_lei)
        GROUP BY c.issuer_lei ORDER BY insiders DESC, buy_eur DESC NULLS LAST, lei
        """,
        [*params, window_days, min_insiders],
    )
