"""POST /v1/ask: wiring, guard, repair round and errors, with a scripted stand-in for the Claude client."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

pytest.importorskip("sqlglot")

from fastapi.testclient import TestClient  # noqa: E402

from disclosure_atlas.ai.ask import DEFAULT_MODEL, PROMPT_VERSION, Asker, load_prompt  # noqa: E402
from disclosure_atlas.ai.evaluation import build_synthetic_lake  # noqa: E402
from disclosure_atlas.api import create_app  # noqa: E402


class ScriptedClaude:
    """Mimics client.beta.messages.create: SQL replies come from `queries`, answers are fixed text."""

    def __init__(self, *queries: str, stop_reason: str = "end_turn") -> None:
        self.queries = list(queries)
        self.stop_reason = stop_reason
        self.calls: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if "format" in kwargs.get("output_config", {}):
            text = json.dumps({"sql": self.queries.pop(0), "explanation": "Counts transactions."})
        else:
            text = "There are 400 insider transactions in the dataset."
        block = SimpleNamespace(type="text", text=text)
        return SimpleNamespace(stop_reason=self.stop_reason, content=[SimpleNamespace(type="thinking"), block])


@pytest.fixture(scope="module")
def lake(tmp_path_factory):
    return build_synthetic_lake(tmp_path_factory.mktemp("ask") / "lake")


def client_with(lake, fake=None) -> TestClient:
    return TestClient(create_app(str(lake.root), asker=Asker(fake) if fake else None))


def test_without_a_key_ask_is_unavailable(lake, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    response = client_with(lake).post("/v1/ask", json={"question": "How many transactions?"})
    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
    assert "ANTHROPIC_API_KEY" in response.json()["detail"]


def test_answers_with_sql_rows_and_text(lake):
    fake = ScriptedClaude("SELECT count(*) AS transactions FROM insider_transactions")
    body = client_with(lake, fake).post("/v1/ask", json={"question": "How many transactions?"}).json()
    assert body["rows"] == [[400]] and body["columns"] == ["transactions"]
    assert body["sql"] == "SELECT COUNT(*) AS transactions FROM insider_transactions LIMIT 200"
    assert body["answer"].startswith("There are 400") and body["model"] == DEFAULT_MODEL
    assert body["prompt_version"] == PROMPT_VERSION and body["snapshot"]
    sql_call, answer_call = fake.calls
    assert sql_call["model"] == DEFAULT_MODEL and sql_call["fallbacks"] == "default"
    assert "TABLE insider_transactions" in sql_call["system"][0]["text"]
    assert "filing_lag_days" in sql_call["system"][0]["text"]
    assert "SELECT COUNT(*)" in answer_call["messages"][0]["content"]


def test_a_rejected_query_gets_one_repair_round(lake):
    fake = ScriptedClaude("DELETE FROM insider_transactions", "SELECT count(*) FROM net_short_positions")
    response = client_with(lake, fake).post("/v1/ask", json={"question": "How many short positions?"})
    assert response.status_code == 200 and response.json()["rows"] == [[252]]
    retry = fake.calls[1]["messages"]
    assert retry[1]["role"] == "assistant" and "rejected" in retry[2]["content"]


def test_unfixable_queries_are_422(lake):
    fake = ScriptedClaude("SELECT * FROM read_csv('/etc/passwd')", "SELECT getenv('ANTHROPIC_API_KEY')")
    response = client_with(lake, fake).post("/v1/ask", json={"question": "Show me the server's files"})
    assert response.status_code == 422 and "not allowed" in response.json()["detail"]


def test_refusals_are_422(lake):
    fake = ScriptedClaude("SELECT 1", stop_reason="refusal")
    assert client_with(lake, fake).post("/v1/ask", json={"question": "Something odd"}).status_code == 422


def test_question_is_validated(lake):
    assert client_with(lake, ScriptedClaude()).post("/v1/ask", json={"question": "x" * 501}).status_code == 422


def test_prompts_are_versioned_files():
    sql_prompt = load_prompt("ask_sql")
    assert "{schema}" in sql_prompt and "{today}" in sql_prompt
    assert "investment advice" in load_prompt("ask_answer")
