"""Offline tests for the read-only SQL guard: what an agent or a model may run, and what it may not."""

from __future__ import annotations

import pytest

pytest.importorskip("sqlglot")

from disclosure_atlas.ai.evaluation import build_synthetic_lake  # noqa: E402
from disclosure_atlas.ai.guard import GuardError, SandboxedLake, guard, run_guarded  # noqa: E402


@pytest.fixture(scope="module")
def cursor(tmp_path_factory):
    return SandboxedLake(build_synthetic_lake(tmp_path_factory.mktemp("lake") / "lake")).connect()


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT count(*) FROM insider_transactions",
        "select issuer_name, sum(amount_eur) from insider_transactions where is_open_market group by 1 order by 2 desc",
        "WITH b AS (SELECT issuer_lei FROM insider_transactions) SELECT count(*) FROM b",
        "SELECT issuer_name FROM insider_transactions UNION SELECT issuer_name FROM net_short_positions",
        "SELECT t.issuer_name FROM insider_transactions t JOIN net_short_positions s USING (issuer_lei)",
        "SELECT * FROM insider_transactions QUALIFY row_number() OVER (PARTITION BY issuer_lei) = 1",
        "SELECT 'DROP TABLE x; --' AS harmless_string",
    ],
)
def test_allows_read_only_selects(sql):
    assert guard(sql).sql


@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        # Writes, DDL and state changes
        ("DELETE FROM insider_transactions", "SELECT"),
        ("UPDATE insider_transactions SET amount = 0", "SELECT"),
        ("INSERT INTO insider_transactions SELECT * FROM insider_transactions", "SELECT"),
        ("DROP VIEW insider_transactions", "SELECT"),
        ("CREATE TABLE stolen AS SELECT * FROM insider_transactions", "SELECT"),
        ("ALTER VIEW insider_transactions RENAME TO x", "SELECT"),
        ("SELECT * INTO stolen FROM insider_transactions", "Into"),
        ("COPY insider_transactions TO '/tmp/out.csv'", "SELECT"),
        ("ATTACH '/tmp/other.duckdb' AS other", "SELECT"),
        ("INSTALL httpfs", "SELECT"),
        ("LOAD httpfs", "SELECT"),
        ("SET enable_external_access = true", "SELECT"),
        ("PRAGMA database_list", "SELECT"),
        ("DESCRIBE insider_transactions", "SELECT"),
        ("EXPORT DATABASE '/tmp/dump'", "SELECT|parse"),
        # Several statements, however they are hidden
        ("SELECT 1 FROM insider_transactions; DROP VIEW insider_transactions", "one statement"),
        ("SELECT 1 FROM insider_transactions;SELECT 2 FROM insider_transactions", "one statement"),
        ("SELECT 1 FROM insider_transactions /* ; */ ; DELETE FROM insider_transactions", "one statement"),
        # Injection through a value that closes the string
        ("SELECT * FROM insider_transactions WHERE issuer_name = 'x'; DROP VIEW insider_transactions; --'",
         "one statement"),
        # Tables outside the allowlist
        ("SELECT * FROM silver_insider_transactions", "Unknown table"),
        ("SELECT * FROM fx_rates", "Unknown table"),
        ("SELECT * FROM duckdb_settings()", "not allowed"),
        ("SELECT * FROM information_schema.tables", "Qualified"),
        ("SELECT * FROM main.insider_transactions", "Qualified"),
        ("SELECT * FROM insider_transactions WHERE issuer_lei IN (SELECT lei FROM secrets)", "Unknown table"),
        ("SELECT * FROM '/etc/passwd'", "Unknown table"),
        # Functions that read files, the network, the environment or settings
        ("SELECT * FROM read_csv('/etc/passwd')", "not allowed"),
        ("SELECT * FROM read_parquet('lake/silver/*/*/*.parquet')", "not allowed"),
        ("SELECT read_text('/etc/passwd')", "not allowed"),
        ("SELECT * FROM read_json_auto('https://example.com/x.json')", "not allowed"),
        ("SELECT * FROM glob('/home/*')", "not allowed"),
        ("SELECT getenv('ANTHROPIC_API_KEY')", "not allowed"),
        ("SELECT current_setting('allowed_directories')", "not allowed"),
        ("SELECT * FROM query('DELETE FROM x')", "not allowed"),
        ("SELECT count(*) FROM insider_transactions WHERE length(read_blob('/etc/hosts')) > 0", "not allowed"),
        # Hidden behind a CTE or a subquery
        ("WITH x AS (SELECT * FROM read_csv('/etc/passwd')) SELECT * FROM x", "not allowed"),
        ("SELECT (SELECT getenv('HOME')) AS h FROM insider_transactions", "not allowed"),
        # Malformed or empty
        ("", "Empty"),
        ("   ", "Empty"),
        ("SELEC * FRM insider_transactions", "parse|SELECT"),
        ("SELECT * FROM insider_transactions LIMIT (SELECT 10)", "integer literal"),
    ],
)
def test_rejects(sql, reason):
    with pytest.raises(GuardError, match=f"(?i){reason}"):
        guard(sql)


def test_comments_never_reach_duckdb():
    query = guard("SELECT count(*) /* hidden */ FROM insider_transactions -- trailing ; DROP VIEW x")
    assert "hidden" not in query.sql and "DROP" not in query.sql and "--" not in query.sql


def test_limit_is_added_or_clamped():
    assert guard("SELECT * FROM insider_transactions", max_rows=50).sql.endswith("LIMIT 50")
    assert guard("SELECT * FROM insider_transactions LIMIT 5000", max_rows=50).limit == 50
    assert guard("SELECT * FROM insider_transactions LIMIT 7", max_rows=50).sql.endswith("LIMIT 7")
    union = guard("SELECT issuer_name FROM insider_transactions UNION ALL SELECT holder_name FROM net_short_positions",
                  max_rows=10)
    assert union.sql.endswith("LIMIT 10") and union.tables == {"insider_transactions", "net_short_positions"}


def test_cte_names_resolve_to_their_source_tables():
    query = guard("WITH insider AS (SELECT * FROM insider_transactions) SELECT count(*) FROM insider")
    assert query.tables == {"insider_transactions"}


def test_overlong_queries_are_rejected():
    with pytest.raises(GuardError, match="longer"):
        guard("SELECT 1 FROM insider_transactions WHERE " + " OR ".join(["true"] * 5000))


def test_runs_on_the_sandbox_and_enforces_the_row_cap(cursor):
    result = run_guarded(cursor, "SELECT record_id FROM insider_transactions ORDER BY record_id", max_rows=25)
    assert len(result.rows) == 25 and result.truncated and result.columns == ["record_id"]
    count = run_guarded(cursor, "SELECT count(*) AS n FROM insider_transactions")
    assert count.rows == [[400]] and not count.truncated


def test_slow_queries_are_interrupted(cursor):
    sql = ("SELECT count(*) FROM insider_transactions a, insider_transactions b, insider_transactions c, "
           "insider_transactions d WHERE a.amount + b.amount + c.amount + d.amount > 0")
    with pytest.raises(GuardError, match="time limit"):
        run_guarded(cursor, sql, timeout=0.2)


def test_sandbox_blocks_files_even_if_the_static_guard_were_bypassed(cursor):
    """Defense in depth: the connection itself refuses file access, extension loading and config changes."""
    for sql in ("SELECT * FROM read_csv('/etc/passwd')", "SET enable_external_access = true", "INSTALL httpfs"):
        with pytest.raises(Exception):  # noqa: B017 - any DuckDB error is fine, success is not
            cursor.execute(sql)
    assert cursor.execute("SELECT count(*) FROM insider_transactions").fetchone() == (400,)


def test_execution_errors_are_reported_as_guard_errors(cursor):
    with pytest.raises(GuardError, match="Query failed"):
        run_guarded(cursor, "SELECT no_such_column FROM insider_transactions")
