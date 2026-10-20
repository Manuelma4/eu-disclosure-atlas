"""Entity reference tables (GLEIF, ESMA FIRDS, OpenFIGI) and the gold enrichment views built on them.

lake/reference/gleif_lei.parquet        LEI -> official legal name, country, entity and registration status
lake/reference/firds_instruments.parquet ISIN -> instrument full name, CFI, currency, issuer LEI
lake/reference/openfigi_listings.parquet ISIN -> exchange listings (ticker, Bloomberg exchange code), one primary

Gold views (next to ``insider_transactions`` and ``net_short_positions``, whose columns stay unchanged):

instruments                    one row per ISIN known to FIRDS or OpenFIGI, with the primary ticker
issuers                        one row per issuer LEI seen in the filings, with the GLEIF legal name and tickers
insider_transactions_enriched  insider_transactions plus legal name, instrument name and CFI, primary ticker

A missing reference file reads as an empty table, so the views always exist. This module holds no I/O, so
``lake`` can import it without a cycle.
"""

from __future__ import annotations

import pyarrow as pa

_UTC = pa.timestamp("us", tz="UTC")

GLEIF_SCHEMA = pa.schema([
    ("lei", pa.string()),
    ("legal_name", pa.string()),
    ("legal_country", pa.string()),
    ("headquarters_country", pa.string()),
    ("legal_jurisdiction", pa.string()),
    ("entity_status", pa.string()),
    ("registration_status", pa.string()),
    ("fetched_at", _UTC),
])

FIRDS_SCHEMA = pa.schema([
    ("isin", pa.string()),
    ("full_name", pa.string()),
    ("short_name", pa.string()),
    ("cfi", pa.string()),
    ("currency", pa.string()),
    ("issuer_lei", pa.string()),
    ("trading_venue", pa.string()),
    ("competent_authority", pa.string()),
    ("file_date", pa.date32()),
    ("fetched_at", _UTC),
])

FIGI_SCHEMA = pa.schema([
    ("isin", pa.string()),
    ("figi", pa.string()),
    ("composite_figi", pa.string()),
    ("share_class_figi", pa.string()),
    ("ticker", pa.string()),
    ("exch_code", pa.string()),
    ("name", pa.string()),
    ("security_type", pa.string()),
    ("market_sector", pa.string()),
    ("is_primary", pa.bool_()),
    ("fetched_at", _UTC),
])
"""An ISIN that OpenFIGI does not know is cached as one row with a null ``figi`` (so it is not asked again)."""

REFERENCE_TABLES: dict[str, tuple[str, pa.Schema]] = {
    "gleif_lei": ("gleif_lei.parquet", GLEIF_SCHEMA),
    "firds_instruments": ("firds_instruments.parquet", FIRDS_SCHEMA),
    "openfigi_listings": ("openfigi_listings.parquet", FIGI_SCHEMA),
}
"""View name -> (file name under lake/reference/, schema)."""

ENRICHED_VIEWS = ("instruments", "issuers", "insider_transactions_enriched")

_DUCKDB_TYPES = {pa.string(): "VARCHAR", pa.bool_(): "BOOLEAN", pa.date32(): "DATE", _UTC: "TIMESTAMPTZ"}


def empty_select(schema: pa.Schema) -> str:
    """A typed, empty SELECT standing in for a reference file that has not been downloaded yet."""
    columns = ", ".join(f"CAST(NULL AS {_DUCKDB_TYPES[field.type]}) AS {field.name}" for field in schema)
    return f"SELECT {columns} WHERE false"


def enriched_sql(schema: str) -> dict[str, str]:
    """SQL of each enrichment view, reading the reference views and gold views of ``schema``."""
    s = schema
    return {
        "instruments": f"""
            WITH firds AS (SELECT * FROM {s}.firds_instruments),
                 primary_listing AS (
                     SELECT isin, arg_max(ticker, fetched_at) AS ticker, arg_max(exch_code, fetched_at) AS exch_code,
                            arg_max(figi, fetched_at) AS figi, arg_max(name, fetched_at) AS name
                     FROM {s}.openfigi_listings WHERE is_primary GROUP BY isin
                 ),
                 listings AS (
                     SELECT isin, list(DISTINCT ticker || ' ' || exch_code ORDER BY ticker || ' ' || exch_code)
                                AS tickers
                     FROM {s}.openfigi_listings WHERE ticker IS NOT NULL AND exch_code IS NOT NULL GROUP BY isin
                 ),
                 isins AS (SELECT isin FROM firds UNION SELECT isin FROM primary_listing)
            SELECT i.isin,
                   coalesce(f.full_name, p.name) AS full_name,
                   f.short_name, f.cfi, f.currency, f.issuer_lei, f.trading_venue,
                   p.figi, p.ticker AS primary_ticker, p.exch_code AS primary_exchange,
                   coalesce(l.tickers, []) AS tickers
            FROM isins i
            LEFT JOIN firds f USING (isin)
            LEFT JOIN primary_listing p USING (isin)
            LEFT JOIN listings l USING (isin)
        """,
        "issuers": f"""
            WITH seen AS (
                     SELECT issuer_lei AS lei, issuer_name AS name, jurisdiction AS country, published_at AS ts
                     FROM {s}.insider_transactions WHERE issuer_lei IS NOT NULL
                     UNION ALL
                     SELECT issuer_lei, issuer_name, jurisdiction, CAST(position_date AS TIMESTAMPTZ)
                     FROM {s}.net_short_positions WHERE issuer_lei IS NOT NULL
                 ),
                 reported AS (
                     SELECT lei, arg_max(name, ts) AS name, arg_max(country, ts) AS country FROM seen GROUP BY lei
                 ),
                 isin_use AS (
                     SELECT lei, isin, sum(n) AS n FROM (
                         SELECT issuer_lei AS lei, isin, count(*) AS n FROM {s}.insider_transactions
                         WHERE issuer_lei IS NOT NULL AND isin IS NOT NULL GROUP BY ALL
                         UNION ALL
                         SELECT issuer_lei, isin, count(*) FROM {s}.net_short_positions
                         WHERE issuer_lei IS NOT NULL AND isin IS NOT NULL GROUP BY ALL
                         UNION ALL
                         SELECT issuer_lei, isin, 0 FROM {s}.firds_instruments WHERE issuer_lei IS NOT NULL
                     ) GROUP BY ALL
                 ),
                 listed AS (
                     -- Shares first (fully classified ones before CFI ESXXXX, such as interim units), then the
                     -- ISIN used in the most filings.
                     SELECT u.lei,
                            arg_max(u.isin, [r.rank, u.n]) AS primary_isin,
                            arg_max(i.primary_ticker, [r.rank, u.n]) AS primary_ticker,
                            arg_max(i.primary_exchange, [r.rank, u.n]) AS primary_exchange,
                            list(DISTINCT i.primary_ticker || ' ' || i.primary_exchange
                                 ORDER BY i.primary_ticker || ' ' || i.primary_exchange) AS tickers
                     FROM isin_use u JOIN {s}.instruments i USING (isin),
                          LATERAL (SELECT CASE WHEN i.cfi LIKE 'ESX%' THEN 1 WHEN i.cfi LIKE 'ES%' THEN 2 ELSE 0 END
                                       AS rank) r
                     WHERE i.primary_ticker IS NOT NULL
                     GROUP BY u.lei
                 )
            SELECT r.lei, r.name, r.country,
                   g.legal_name, g.legal_country, g.headquarters_country, g.entity_status, g.registration_status,
                   l.primary_isin, l.primary_ticker, l.primary_exchange, coalesce(l.tickers, []) AS tickers
            FROM reported r
            LEFT JOIN (SELECT * FROM {s}.gleif_lei QUALIFY row_number() OVER (PARTITION BY lei
                                                                             ORDER BY fetched_at DESC) = 1) g
                USING (lei)
            LEFT JOIN listed l USING (lei)
        """,
        "insider_transactions_enriched": f"""
            SELECT t.*,
                   iss.legal_name AS issuer_legal_name,
                   iss.legal_country AS issuer_legal_country,
                   ins.full_name AS instrument_full_name,
                   ins.cfi AS instrument_cfi,
                   coalesce(ins.primary_ticker, iss.primary_ticker) AS primary_ticker,
                   CASE WHEN ins.primary_ticker IS NOT NULL THEN ins.primary_exchange ELSE iss.primary_exchange END
                       AS primary_exchange
            FROM {s}.insider_transactions t
            LEFT JOIN {s}.issuers iss ON iss.lei = t.issuer_lei
            LEFT JOIN {s}.instruments ins ON ins.isin = t.isin
        """,
    }
