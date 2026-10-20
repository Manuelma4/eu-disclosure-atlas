"""Read-only SQL guard for untrusted queries (from agents over MCP, or written by a model).

Two independent layers, so one bug does not open the lake:

1. Static: the query is parsed with sqlglot (DuckDB dialect) and must be exactly one SELECT (or a set
   operation of SELECTs, optionally with CTEs) that reads only allowlisted gold views, calls no function
   that touches files, the network, settings or the catalog, and carries a LIMIT no larger than the cap.
   The SQL that runs is regenerated from the syntax tree, so comments and formatting tricks never reach
   DuckDB.
2. Runtime: queries run on a DuckDB connection with external access disabled (except the lake folder the
   gold views read), the configuration locked, and a timeout that interrupts the query.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from .schema import ALLOWED_TABLES

DEFAULT_MAX_ROWS = 200
DEFAULT_TIMEOUT_SECONDS = 5.0
MAX_SQL_CHARS = 10_000

# Functions that read files or URLs, inspect the catalog or settings, or run nested SQL.
_FORBIDDEN_FUNCTION = re.compile(
    r"^(read_\w*|\w*_scan|scan_\w*|parquet_\w*|sniff_csv|glob|getenv|current_setting|getvariable|"
    r"query|query_table|duckdb_\w*|pragma_\w*|which_secret|load_\w*|install_\w*|enable_\w*|"
    r"sqlite_\w*|postgres_\w*|mysql_\w*|iceberg_\w*|delta_\w*|ducklake_\w*|http\w*|"
    r"system|shell|copy\w*|export\w*|import\w*|attach|detach|checkpoint|force_checkpoint)$",
    re.IGNORECASE,
)

# Statement and clause types that write, change state or leave the read-only SELECT world.
_FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = tuple(
    getattr(exp, name)
    for name in (
        "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "TruncateTable", "Copy", "Command",
        "Pragma", "Set", "Use", "Attach", "Detach", "Install", "Load", "Transaction", "Commit", "Rollback",
        "Describe", "Summarize", "Into", "Export", "Analyze",
    )
    if hasattr(exp, name)
)


class GuardError(ValueError):
    """The query is not allowed; the message is safe to show to the caller."""


@dataclass(frozen=True)
class GuardedQuery:
    sql: str
    """The SQL that will run: regenerated from the parsed tree, with the enforced LIMIT."""
    tables: frozenset[str]
    limit: int


def _function_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.name)
    return node.sql_name()


def guard(
    sql: str, *, max_rows: int = DEFAULT_MAX_ROWS, allowed_tables: Iterable[str] = ALLOWED_TABLES
) -> GuardedQuery:
    """Validate an untrusted query and return the SQL to run, or raise GuardError."""
    if not sql or not sql.strip():
        raise GuardError("Empty query")
    if len(sql) > MAX_SQL_CHARS:
        raise GuardError(f"Query longer than {MAX_SQL_CHARS} characters")
    try:
        statements = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None]
    except ParseError as exc:
        raise GuardError(f"Could not parse the query: {str(exc).splitlines()[0]}") from exc
    if len(statements) != 1:
        raise GuardError("Exactly one statement is allowed")
    tree = statements[0]
    if not isinstance(tree, exp.Query):
        raise GuardError("Only SELECT queries are allowed")

    for node in tree.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            raise GuardError(f"{type(node).__name__} is not allowed: the dataset is read-only")
        if isinstance(node, exp.Func) and _FORBIDDEN_FUNCTION.match(_function_name(node)):
            raise GuardError(f"Function {_function_name(node).lower()}() is not allowed")

    allowed = {t.lower() for t in allowed_tables}
    ctes = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    tables: set[str] = set()
    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            raise GuardError("Table functions are not allowed; query the gold views by name")
        if table.args.get("db") or table.args.get("catalog"):
            raise GuardError(f"Qualified table names are not allowed: {table.sql(dialect='duckdb')}")
        name = table.name.lower()
        if name in ctes:
            continue
        if name not in allowed:
            raise GuardError(f"Unknown table {table.name!r}; allowed: {', '.join(sorted(allowed))}")
        tables.add(name)

    limit_node = tree.args.get("limit")
    current = None
    if isinstance(limit_node, exp.Limit) and isinstance(limit_node.expression, exp.Literal):
        literal = limit_node.expression
        current = int(literal.this) if literal.is_int else None
    if limit_node is not None and current is None:
        raise GuardError("LIMIT must be an integer literal")
    if tree.args.get("offset") is not None and current is None:
        raise GuardError("OFFSET requires a LIMIT")
    limit = max_rows if current is None else min(current, max_rows)
    if current != limit:
        tree = tree.limit(limit, copy=True)
    return GuardedQuery(sql=tree.sql(dialect="duckdb", comments=False), tables=frozenset(tables), limit=limit)


def lock_down(con: duckdb.DuckDBPyConnection, allowed_directories: Iterable[Path | str]) -> duckdb.DuckDBPyConnection:
    """Turn a connection with gold views into a sandbox: no file or network access outside the lake, no
    extension loading, and a locked configuration (a guarded query cannot undo any of this)."""
    dirs = [str(Path(d).resolve()).replace("'", "''") for d in allowed_directories]
    con.execute(f"SET allowed_directories = [{', '.join(repr(d) for d in dirs)}]")
    con.execute("SET enable_external_access = false")
    con.execute("SET autoinstall_known_extensions = false")
    con.execute("SET autoload_known_extensions = false")
    con.execute("SET lock_configuration = true")
    return con


class SandboxedLake:
    """Adapter for api.readmodel.ReadModel: same version() as the lake, isolated read-only connections.

    The lake's DuckDB engine is shared by the whole process (a DuckDB-file catalog can be attached only once), and
    the lock-down settings are database-wide, so applying them to the engine would lock every other reader. Instead,
    each data snapshot is copied into a private in-memory database that holds only the allowlisted tables, with no
    file or network access at all and a locked configuration. The copy is rebuilt when the snapshot changes.
    """

    def __init__(self, lake: Any) -> None:
        self.lake = lake
        self._lock = threading.Lock()
        self._key: str | None = None
        self._sandbox: duckdb.DuckDBPyConnection | None = None

    def version(self) -> str:
        return self.lake.version()

    def connect(self, *, as_of: int | str | None = None) -> duckdb.DuckDBPyConnection:
        key = str(as_of if as_of is not None else self.lake.version())
        with self._lock:
            if self._sandbox is None or key != self._key:
                source = self.lake.connect(as_of=as_of)
                sandbox = duckdb.connect()
                sandbox.execute("SET TimeZone = 'UTC'")
                try:
                    for table in sorted(ALLOWED_TABLES):
                        data = source.execute(f"SELECT * FROM {table}").fetch_arrow_table()  # noqa: S608
                        sandbox.register("_copy", data)
                        sandbox.execute(f"CREATE TABLE {table} AS SELECT * FROM _copy")
                        sandbox.unregister("_copy")
                finally:
                    source.close()
                if self._sandbox is not None:
                    self._sandbox.close()
                self._sandbox, self._key = lock_down(sandbox, []), key
            return self._sandbox.cursor()


@dataclass
class QueryResult:
    sql: str
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool
    """True when the enforced LIMIT may have cut the result."""


def jsonable(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, list | tuple):
        return [jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value).hex()
    return value


def run_guarded(
    cursor: duckdb.DuckDBPyConnection,
    sql: str,
    *,
    max_rows: int = DEFAULT_MAX_ROWS,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> QueryResult:
    """Guard, then execute on a (sandboxed) cursor; interrupts the query after `timeout` seconds."""
    query = guard(sql, max_rows=max_rows)
    timer = threading.Timer(timeout, cursor.interrupt)
    timer.start()
    try:
        result = cursor.execute(query.sql)
        columns = [d[0] for d in result.description]
        rows = result.fetchall()
    except duckdb.InterruptException as exc:
        raise GuardError(f"Query exceeded the {timeout:g} s time limit") from exc
    except duckdb.Error as exc:
        raise GuardError(f"Query failed: {str(exc).splitlines()[0]}") from exc
    finally:
        timer.cancel()
    return QueryResult(
        sql=query.sql,
        columns=columns,
        rows=[[jsonable(v) for v in row] for row in rows],
        truncated=len(rows) >= query.limit,
    )
