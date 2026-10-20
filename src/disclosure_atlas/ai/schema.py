"""The queryable surface for AI clients: the gold views, their columns and what each one means.

Column names and types are read from the live views, so the description cannot drift from the lake;
the prose comes from docs/data-model.md.
"""

from __future__ import annotations

import duckdb

from ..models import InstrumentType, Nature, Role

ALLOWED_TABLES: frozenset[str] = frozenset({"insider_transactions", "net_short_positions"})

TABLE_DOCS = {
    "insider_transactions": (
        "One managers' transaction (MAR Article 19), one row per transaction line. Natural persons appear "
        "only as pseudonymous ids. Amendments and cancellations are already resolved: every row is current."
    ),
    "net_short_positions": (
        "One public net short position (SSR Article 6: at least 0.5% of issued share capital, then every 0.1%). "
        "Holders are legal entities (funds)."
    ),
}

COLUMN_DOCS = {
    "record_id": "Stable id from the source's natural key",
    "source": "Connector name, e.g. se_fi_pdmr",
    "jurisdiction": "ISO 3166-1 alpha-2 country of the publishing authority, e.g. SE, FR, DE",
    "source_url": "Where the record was published",
    "published_at": "Publication by the authority (UTC timestamp)",
    "transaction_date": "Date of the transaction",
    "issuer_name": "Issuer name as published",
    "issuer_lei": "Issuer Legal Entity Identifier (20 characters); the best key for an issuer",
    "pdmr_id": "Pseudonym of the manager (person discharging managerial responsibilities), stable within an issuer",
    "pdmr_role": f"Most senior role: {', '.join(r.value for r in Role)}",
    "pdmr_role_raw": "Position as published",
    "notifier_id": "Pseudonym of the notifying person; equals pdmr_id unless a closely associated person notified",
    "notifier_name": "Notifier name, only when the notifier is a legal entity (e.g. a holding company)",
    "notifier_is_legal_entity": "True when the notifier is a legal entity",
    "closely_associated": "True when a person closely associated with the manager made the transaction",
    "is_amendment": "True when the notification amends an earlier one",
    "amendment_details": "Free-text amendment details",
    "linked_to_share_option_programme": "True when linked to a share option programme",
    "nature": f"Normalized nature: {', '.join(n.value for n in Nature)}",
    "nature_raw": "Nature as published",
    "direction": "+1 the holding increases, -1 it decreases, 0 neutral or unknown",
    "instrument_type": f"Instrument: {', '.join(i.value for i in InstrumentType)}",
    "instrument_type_raw": "Instrument type as published",
    "instrument_name": "Instrument name",
    "isin": "ISIN of the instrument",
    "volume": "Number of units",
    "price": "Unit price in `currency`",
    "currency": "ISO 4217 currency of price and amount",
    "amount": "volume x price in `currency`",
    "venue": "Trading venue as published",
    "on_venue": "False for transactions outside a trading venue",
    "is_superseded": "Always false in gold (superseded rows are removed)",
    "status_raw": "Status as published",
    "fetched_at": "When the Atlas fetched the record (UTC)",
    "parser": "Parser that produced the record",
    "first_seen_at": "When the Atlas first ingested the record (UTC)",
    "is_open_market": "True for a buy or sell of shares on a trading venue: the trades that carry information",
    "filing_lag_days": "Calendar days between transaction_date and publication",
    "amount_eur": "amount converted to EUR with the ECB reference rate of the transaction date",
    "position_date": "Date of the net short position",
    "holder_name": "Position holder (a legal entity)",
    "holder_lei": "LEI of the position holder",
    "position_pct": "Net short position in % of issued share capital (0.5 means 0.5%)",
}


def describe(con: duckdb.DuckDBPyConnection) -> dict[str, list[tuple[str, str, str]]]:
    """{table: [(column, type, description), ...]} for every allowlisted gold view."""
    tables: dict[str, list[tuple[str, str, str]]] = {}
    for table in sorted(ALLOWED_TABLES):
        rows = con.execute(f"DESCRIBE {table}").fetchall()
        tables[table] = [(name, str(kind), COLUMN_DOCS.get(name, "")) for name, kind, *_ in rows]
    return tables


def schema_text(con: duckdb.DuckDBPyConnection) -> str:
    """Compact, model-friendly description of the gold views (used in prompts and the MCP `sql` tool)."""
    lines: list[str] = []
    for table, columns in describe(con).items():
        lines.append(f"TABLE {table} -- {TABLE_DOCS[table]}")
        lines += [f"  {name} {kind}" + (f" -- {doc}" if doc else "") for name, kind, doc in columns]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
