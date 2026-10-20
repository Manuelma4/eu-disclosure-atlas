"""Ask the Atlas: a question in plain language -> guarded read-only SQL -> rows -> a short answer.

Claude writes the SQL from a schema description of the gold views (prompt `ask_sql`); the SQL guard
validates it and it runs on a sandboxed connection. If the guard or DuckDB rejects the query, the error is
sent back once for a corrected query. A second, cheaper call (prompt `ask_answer`) phrases the answer from
the rows. Prompts are versioned files under prompts/; the version is returned with every answer and gated
by the golden set in evals/ask/.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import date
from importlib.resources import files
from typing import Any

import duckdb

from .guard import DEFAULT_MAX_ROWS, DEFAULT_TIMEOUT_SECONDS, GuardError, QueryResult, run_guarded
from .schema import schema_text

PROMPT_VERSION = "v1"
DEFAULT_MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
ANSWER_ROWS = 50

SQL_FORMAT = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {"sql": {"type": "string"}, "explanation": {"type": "string"}},
        "required": ["sql", "explanation"],
        "additionalProperties": False,
    },
}


class AskUnavailable(RuntimeError):
    """No model is configured (ANTHROPIC_API_KEY is not set)."""


class AskError(RuntimeError):
    """The model could not produce a valid query, or refused."""


def load_prompt(name: str, version: str = PROMPT_VERSION) -> str:
    return files("disclosure_atlas.ai").joinpath(f"prompts/{name}.{version}.md").read_text(encoding="utf-8")


def sql_system_prompt(schema: str, today: date) -> str:
    return load_prompt("ask_sql").replace("{schema}", schema.rstrip()).replace("{today}", today.isoformat())


@dataclass
class AskResult:
    question: str
    sql: str
    explanation: str
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool
    answer: str
    model: str
    prompt_version: str
    attempts: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Asker:
    def __init__(
        self,
        client: Any,
        *,
        model: str = DEFAULT_MODEL,
        max_rows: int = DEFAULT_MAX_ROWS,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_attempts: int = 2,
        use_fallbacks: bool = True,
    ) -> None:
        self.client = client
        self.model = model
        self.max_rows = max_rows
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.use_fallbacks = use_fallbacks

    @classmethod
    def from_env(cls) -> Asker:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise AskUnavailable("Ask the Atlas is not configured on this server (no ANTHROPIC_API_KEY)")
        import anthropic

        return cls(anthropic.Anthropic(), model=os.environ.get("ATLAS_ASK_MODEL", DEFAULT_MODEL))

    def _create(self, **kwargs: Any) -> Any:
        if self.use_fallbacks:
            # On a safety-classifier refusal, the API re-runs the request on its recommended fallback model.
            kwargs |= {"betas": [FALLBACK_BETA], "fallbacks": "default"}
        response = self.client.beta.messages.create(model=self.model, **kwargs)
        if response.stop_reason == "refusal":
            raise AskError("The model declined to answer this question")
        return response

    @staticmethod
    def _text(response: Any) -> str:
        return "".join(block.text for block in response.content if block.type == "text").strip()

    def write_sql(self, system: str, messages: list[dict[str, Any]]) -> tuple[str, str, Any]:
        """Returns (sql, explanation, response content); the content is replayed unchanged on a retry."""
        response = self._create(
            max_tokens=8000,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=messages,
            output_config={"effort": "medium", "format": SQL_FORMAT},
        )
        try:
            payload = json.loads(self._text(response))
            return str(payload["sql"]), str(payload.get("explanation", "")), response.content
        except (ValueError, KeyError, TypeError) as exc:
            raise AskError("The model did not return a query") from exc

    def write_answer(self, question: str, result: QueryResult) -> str:
        data = {
            "columns": result.columns,
            "rows": result.rows[:ANSWER_ROWS],
            "row_count": len(result.rows),
            "truncated": result.truncated,
        }
        response = self._create(
            max_tokens=2000,
            system=load_prompt("ask_answer"),
            messages=[{
                "role": "user",
                "content": f"Question: {question}\n\nSQL:\n{result.sql}\n\nResult (JSON):\n"
                           f"{json.dumps(data, default=str)}",
            }],
            output_config={"effort": "low"},
        )
        return self._text(response)

    def generate(self, cursor: duckdb.DuckDBPyConnection, question: str, today: date | None = None
                 ) -> tuple[QueryResult, str, int]:
        """Question -> guarded, executed query (with one repair round). Returns (result, explanation, attempts)."""
        system = sql_system_prompt(schema_text(cursor), today or date.today())
        messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
        error = "no attempt"
        for attempt in range(1, self.max_attempts + 1):
            sql, explanation, content = self.write_sql(system, messages)
            try:
                return run_guarded(cursor, sql, max_rows=self.max_rows, timeout=self.timeout), explanation, attempt
            except GuardError as exc:
                error = str(exc)
                messages += [
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": f"That query was rejected: {error}. Reply with a corrected query."},
                ]
        raise AskError(f"Could not produce a valid query: {error}")

    def ask(self, cursor: duckdb.DuckDBPyConnection, question: str, today: date | None = None) -> AskResult:
        result, explanation, attempts = self.generate(cursor, question, today)
        return AskResult(
            question=question,
            sql=result.sql,
            explanation=explanation,
            columns=result.columns,
            rows=result.rows,
            truncated=result.truncated,
            answer=self.write_answer(question, result),
            model=self.model,
            prompt_version=PROMPT_VERSION,
            attempts=attempts,
        )
