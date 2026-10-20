"""Offline checks of the golden set and of the execution-accuracy scorer (no model calls)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("sqlglot")

from disclosure_atlas.ai.evaluation import build_synthetic_lake, results_match  # noqa: E402
from disclosure_atlas.ai.guard import SandboxedLake, run_guarded  # noqa: E402

GOLDEN = json.loads((Path(__file__).parent / "golden.json").read_text(encoding="utf-8"))


def test_golden_set_shape():
    assert len(GOLDEN) >= 30
    assert len({item["id"] for item in GOLDEN}) == len(GOLDEN)
    assert {"lookup", "aggregation", "ranking", "time", "filing_lag", "shorts", "join"} <= {
        item["category"] for item in GOLDEN
    }
    for item in GOLDEN:
        assert item["question"].endswith(("?", ".")) and item["expected"]["rows"], item["id"]
        assert all(v is not None for row in item["expected"]["rows"] for v in row), item["id"]


def test_reference_queries_pass_the_guard_and_reproduce_the_frozen_answers(tmp_path):
    cursor = SandboxedLake(build_synthetic_lake(tmp_path / "lake")).connect()
    for item in GOLDEN:
        result = run_guarded(cursor, item["sql"], max_rows=1000)
        assert results_match(item["expected"]["rows"], result.rows, ordered=item["ordered"]), item["id"]


def test_synthetic_lake_is_deterministic_and_fictitious(tmp_path):
    first = build_synthetic_lake(tmp_path / "a").connect()
    second = build_synthetic_lake(tmp_path / "b").connect()
    query = "SELECT * FROM insider_transactions ORDER BY record_id"
    assert first.execute(query).fetchall() == second.execute(query).fetchall()
    sources = {s for (s,) in first.execute("SELECT DISTINCT source FROM insider_transactions").fetchall()}
    assert all("synthetic" in s for s in sources)


@pytest.mark.parametrize(
    ("expected", "got", "ordered", "match"),
    [
        ([[400]], [[400]], False, True),
        ([[1.234]], [[1.2344]], False, True),  # rounding to cents
        ([[1.23]], [[1.3]], False, False),
        ([["SE", 3], ["FR", 2]], [["FR", 2], ["SE", 3]], False, True),  # row order ignored
        ([["SE", 3], ["FR", 2]], [["FR", 2], ["SE", 3]], True, False),  # unless the question ranks
        ([["SE", 3]], [[3, "SE"]], False, True),  # column order ignored
        ([["SE", 3]], [["SE", "Sweden", 3]], False, True),  # extra columns ignored
        ([["SE", 3]], [["SE"]], False, False),  # missing columns are wrong
        ([["2025-03-01T00:00:00", 4]], [["2025-03", 4]], False, True),  # month labels
        ([["SE", 3]], [["SE", 3], ["FR", 1]], False, False),  # extra rows are wrong
        ([], [], False, True),
    ],
)
def test_results_match(expected, got, ordered, match):
    assert results_match(expected, got, ordered=ordered) is match


def test_runner_is_skipped_without_a_key(monkeypatch, capsys, tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("ask_eval_run", Path(__file__).parent / "run.py")
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert runner.main(["--out", str(tmp_path / "report.json")]) == 0
    assert "skipping" in capsys.readouterr().out and not (tmp_path / "report.json").exists()
