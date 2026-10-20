"""Ask the Atlas evaluation: execution accuracy of generated SQL on the synthetic lake.

    python evals/ask/run.py                      # needs ANTHROPIC_API_KEY; skipped (exit 0) without it
    python evals/ask/run.py --limit 5 --model claude-opus-5-5
    python evals/ask/run.py --freeze             # recompute expected results from the reference SQL

Each golden question has a reference query; its result on the synthetic lake is frozen in golden.json. A
prediction is correct when its rows match the expected rows (see disclosure_atlas.ai.evaluation). The
report goes to evals/ask/results/ and the exit code is 1 below --min-accuracy, so the run gates prompt
and model changes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path

from disclosure_atlas.ai.ask import DEFAULT_MODEL, PROMPT_VERSION, Asker, AskError
from disclosure_atlas.ai.evaluation import build_synthetic_lake, results_match
from disclosure_atlas.ai.guard import GuardError, SandboxedLake, run_guarded

HERE = Path(__file__).resolve().parent
GOLDEN = HERE / "golden.json"
TODAY = date(2026, 10, 1)  # "today" for the model: the day after the synthetic data ends


def load_golden() -> list[dict]:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def freeze() -> int:
    golden = load_golden()
    with tempfile.TemporaryDirectory() as tmp:
        cursor = SandboxedLake(build_synthetic_lake(Path(tmp) / "lake")).connect()
        for item in golden:
            result = run_guarded(cursor, item["sql"], max_rows=1000)
            item["expected"] = {"columns": result.columns, "rows": result.rows}
    GOLDEN.write_text(json.dumps(golden, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Froze {len(golden)} expected results into {GOLDEN.relative_to(HERE.parent.parent)}")
    return 0


def evaluate(model: str, limit: int | None, min_accuracy: float, out: Path) -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set: skipping the Ask the Atlas evaluation.")
        return 0
    import anthropic

    asker = Asker(anthropic.Anthropic(), model=model)
    golden = load_golden()[:limit]
    cases = []
    with tempfile.TemporaryDirectory() as tmp:
        cursor = SandboxedLake(build_synthetic_lake(Path(tmp) / "lake")).connect()
        for item in golden:
            started = time.monotonic()
            case = {"id": item["id"], "category": item["category"], "question": item["question"]}
            try:
                result, explanation, attempts = asker.generate(cursor, item["question"], today=TODAY)
                expected = item["expected"]["rows"]
                case |= {"sql": result.sql, "attempts": attempts,
                         "correct": results_match(expected, result.rows, ordered=item["ordered"])}
                if not case["correct"]:
                    case |= {"got": result.rows[:10], "expected": expected[:10]}
            except (AskError, GuardError) as exc:
                case |= {"correct": False, "error": str(exc)}
            case["seconds"] = round(time.monotonic() - started, 2)
            cases.append(case)
            print(f"{'PASS' if case['correct'] else 'FAIL'}  {item['id']:<26} {case['seconds']:>6.1f}s"
                  + (f"  {case.get('error', '')}" if not case["correct"] else ""), flush=True)

    by_category: dict[str, list[bool]] = defaultdict(list)
    for case in cases:
        by_category[case["category"]].append(case["correct"])
    accuracy = sum(c["correct"] for c in cases) / len(cases)
    report = {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "questions": len(cases),
        "execution_accuracy": round(accuracy, 4),
        "by_category": {k: round(sum(v) / len(v), 4) for k, v in sorted(by_category.items())},
        "repaired": sum(c.get("attempts", 1) > 1 for c in cases),
        "cases": cases,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nExecution accuracy: {accuracy:.1%} on {len(cases)} questions "
          f"({model}, prompt {PROMPT_VERSION}); report: {out}")
    for category, score in report["by_category"].items():
        print(f"  {category:<12} {score:.0%}")
    return 0 if accuracy >= min_accuracy else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--freeze", action="store_true", help="recompute expected results from the reference SQL")
    parser.add_argument("--model", default=os.environ.get("ATLAS_ASK_MODEL", DEFAULT_MODEL))
    parser.add_argument("--limit", type=int, help="evaluate only the first N questions")
    parser.add_argument("--min-accuracy", type=float, default=0.85, help="exit 1 below this (default 0.85)")
    parser.add_argument("--out", type=Path, default=HERE / "results" / "latest.json")
    args = parser.parse_args(argv)
    if args.freeze:
        return freeze()
    return evaluate(args.model, args.limit, args.min_accuracy, args.out)


if __name__ == "__main__":
    sys.exit(main())
