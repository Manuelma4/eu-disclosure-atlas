# Ask the Atlas evaluation

Gate for prompt and model changes to Ask the Atlas (`POST /v1/ask`, see [docs/ai.md](../../docs/ai.md)).

## What is measured

**Execution accuracy:** for each golden question, the SQL that Claude writes (after the guard and at most one
repair round) is executed on a synthetic lake, and its rows are compared with the rows of a reference query.
A prediction is correct when the rows match:

- numbers are compared to the cent, dates as ISO text, and a month start in any form (`2025-03`,
  `2025-03-01`, `2025-03-01T00:00:00`) as `YYYY-MM`;
- column order, column names and extra columns do not matter; every expected column must be present;
- row order matters only for ranking questions (`"ordered": true`); the row count must match.

The scorer is `disclosure_atlas.ai.evaluation.results_match`; its behaviour is pinned by tests.

## Files

| File | Contents |
|---|---|
| `golden.json` | 35 questions: id, category, question, reference SQL, `ordered`, and the frozen expected result |
| `run.py` | Runner: `--freeze` recomputes expected results; otherwise evaluates the model and writes a report |
| `test_ask_guard.py` | Offline tests of the SQL guard: injection, writes, stacked statements, unknown tables, comments, file and network functions, limits, timeout, sandbox |
| `test_ask_golden.py` | Offline tests: golden-set integrity (each reference query passes the guard and reproduces its frozen answer), determinism of the synthetic lake, the scorer, and the runner's skip without a key |

Categories: `lookup`, `aggregation`, `ranking`, `time`, `filing_lag`, `shorts`, `join`. Every question uses
absolute dates, so expected answers do not move with the calendar.

## The synthetic lake

`disclosure_atlas.ai.evaluation.build_synthetic_lake` writes a seeded, fully fictitious dataset: 10 issuers in
Sweden, France, Germany, the Netherlands and Spain, 400 managers' transactions from January 2025 to September
2026 (open-market trades, grants, exercises, gifts, closely associated holding companies, SEK and EUR), 252 net
short positions by 6 invented funds, and monthly SEK reference rates. No real company or person appears;
managers are opaque ids such as `p0304`.

## Running

```bash
pip install -e '.[dev,api,ai]'
pytest -q evals/ask                          # offline: guard, golden integrity, scorer (no key needed)
export ANTHROPIC_API_KEY=...
python evals/ask/run.py                      # all 35 questions; report in evals/ask/results/latest.json
python evals/ask/run.py --limit 5            # quick smoke run
python evals/ask/run.py --model claude-sonnet-5-5 --min-accuracy 0.8
python evals/ask/run.py --freeze             # after editing golden.json: recompute expected results
```

Without `ANTHROPIC_API_KEY` the runner prints a notice and exits 0, so it is safe in CI. With a key it exits 1
when accuracy falls below `--min-accuracy` (default 0.85). Each question costs one model call for the SQL, plus
one repair call when the first query is rejected; the answer-writing call is not part of the evaluation.

## Policy

- A change to a prompt in `src/disclosure_atlas/ai/prompts/` gets a new version file and a `PROMPT_VERSION`
  bump, and ships only with a run at or above the gate; note the accuracy in the pull request.
- New failure modes found in production become golden questions (with fictitious data) before they are fixed.
