"""Field-level evaluation of the fr_amf_pdmr extractors on the hand-verified gold set.

Usage (from the repository root):
    python evals/fr_amf_pdmr/run_eval.py                    # deterministic parser
    python evals/fr_amf_pdmr/run_eval.py --llm              # also the LLM fallback, if one is configured
    python evals/fr_amf_pdmr/run_eval.py --update-baseline  # accept the deterministic scores as the new floor

It prints a Markdown report (the one in README.md), writes results.json next to this file and exits with
status 1 if any field regressed below baseline.json. See docs/decisions/0003.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from disclosure_atlas.connectors.fr_amf_pdmr import TemplateError, normalized_view, parse_template
from disclosure_atlas.extraction import ExtractionError, LLMExtractor, provider_from_env
from disclosure_atlas.extraction.evaluation import Report, load_gold, score

HERE = Path(__file__).resolve().parent


def deterministic(text: str):
    try:
        return parse_template(text)
    except TemplateError as exc:
        return exc


def table(report: Report, baseline: dict | None) -> list[str]:
    lines = ["| Field | Precision | Recall | TP | FP | FN | vs baseline |", "|---|---:|---:|---:|---:|---:|---|"]
    for name, s in sorted(report.fields.items()):
        floor = (baseline or {}).get("fields", {}).get(name)
        delta = ""
        if floor:
            if s.precision < floor["precision"] - 1e-9 or s.recall < floor["recall"] - 1e-9:
                delta = "**regressed**"
            elif s.precision > floor["precision"] + 1e-9 or s.recall > floor["recall"] + 1e-9:
                delta = "improved"
            else:
                delta = "="
        lines.append(f"| `{name}` | {s.precision:.3f} | {s.recall:.3f} | {s.tp} | {s.fp} | {s.fn} | {delta} |")
    return lines


def regressions(report: Report, baseline: dict) -> list[str]:
    out = []
    for name, floor in baseline.get("fields", {}).items():
        s = report.fields.get(name)
        if s is None or s.precision < floor["precision"] - 1e-9 or s.recall < floor["recall"] - 1e-9:
            out.append(name)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--gold", type=Path, default=HERE / "gold")
    parser.add_argument("--llm", action="store_true", help="also evaluate the configured LLM provider")
    parser.add_argument("--update-baseline", action="store_true")
    args = parser.parse_args(argv)

    gold = load_gold(args.gold)
    baseline_path = HERE / "baseline.json"
    baseline = json.loads(baseline_path.read_text()) if baseline_path.exists() else None

    started = time.perf_counter()
    det = score([(case, deterministic(case.text)) for case in gold], normalize=normalized_view)
    elapsed = time.perf_counter() - started
    results = {"deterministic": det.to_json()}
    out = ["## Deterministic parser (`fr_amf_pdmr@1`)", "",
           f"{det.documents} filings, {len(det.unread)} unread, micro precision **{det.micro_precision:.4f}**, "
           f"micro recall **{det.micro_recall:.4f}**, {1000 * elapsed / max(1, det.documents):.1f} ms per filing.",
           "", *table(det, baseline), ""]
    if det.errors:
        out += ["Errors (gold vs predicted):", ""]
        out += [f"- `{doc}` `{field}`: gold `{g!r}`, predicted `{p!r}`" for doc, field, g, p in det.errors]
        out.append("")

    if args.llm:
        provider = provider_from_env()
        if provider is None:
            out += ["## LLM fallback", "",
                    "Not run: no provider configured (set ANTHROPIC_API_KEY or OLLAMA_HOST).", ""]
        else:
            extractor = LLMExtractor(provider)
            predictions = []
            for case in gold:
                try:
                    predictions.append((case, extractor.extract(case.text).filing))
                except ExtractionError as exc:
                    predictions.append((case, exc))
            llm = score(predictions, normalize=normalized_view)
            results["llm"] = {"parser": extractor.parser_id, **llm.to_json()}
            out += [f"## LLM fallback (`{extractor.parser_id}`)", "",
                    f"{llm.documents} filings, {len(llm.unread)} unread, micro precision "
                    f"**{llm.micro_precision:.4f}**, micro recall **{llm.micro_recall:.4f}**.", "",
                    *table(llm, None), ""]

    (HERE / "results.json").write_text(json.dumps(results, indent=1, default=str) + "\n")
    print("\n".join(out))
    if args.update_baseline:
        baseline_path.write_text(json.dumps({"fields": {k: {"precision": v["precision"], "recall": v["recall"]}
                                                       for k, v in results["deterministic"]["fields"].items()}},
                                            indent=1) + "\n")
        return 0
    failed = regressions(det, baseline) if baseline else []
    if failed:
        print(f"Regressed fields: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
