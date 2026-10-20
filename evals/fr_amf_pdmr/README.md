# Evaluation: `fr_amf_pdmr`

Field-level precision and recall of the France AMF extractors on a hand-verified gold set, as required by
[ADR 0003](../../docs/decisions/0003-deterministic-first-extraction-with-evaluated-llm-fallback.md). The
deterministic parser is gated in CI (`tests/sources/test_fr_amf_pdmr.py`); the LLM fallback is measured here
whenever a provider is configured.

```bash
python evals/fr_amf_pdmr/run_eval.py                       # deterministic parser, report + results.json
ATLAS_LLM_PROVIDER=ollama ATLAS_LLM_MODEL=qwen3.5:9b \
  python evals/fr_amf_pdmr/run_eval.py --llm               # plus the LLM fallback
python evals/fr_amf_pdmr/run_eval.py --update-baseline     # accept new deterministic scores as the floor
```

## Gold set
- **60 filings** published by the AMF between 22 July and 20 October 2026, drawn by stratified random sampling
  (seed 20261020) from the 1,003 filings of that period, so the hard cases are over-represented: closely
  associated legal persons (22) and natural persons (6), several transactions (13) or several price lines (13)
  in one filing, non-EUR prices (5), amendments (2), non open-market natures (contributions in kind, gifts,
  pledges, free shares, subscriptions, exercises, conversions) and instruments (employee-fund units, options,
  mandatory convertibles), missing or malformed ISINs and LEIs, off-venue trades.
- **Inputs** are the text that `pdfplumber` extracts from each PDF (`text` in `gold/*.json`), redacted:
  every natural person and every closely associated entity has a **synthetic name** (`Camille Exemple-07`,
  `Synthetic Alpha 12 SAS`), third persons named in a position are replaced, comments and the AMF filing
  numbers are removed, and the boilerplate is shortened. Legal-form keywords of entity names are kept, so the
  legal-entity heuristic faces the same difficulty as on the real name. Each case keeps the SHA-256 of its
  source PDF, which lives only in the private bronze layer.
- **Labels** (`labels`) are the template fields exactly as printed (an `ExtractedFiling`); `normalized` holds
  the values the connector derives (role, nature, direction, instrument type, venue flag, ISIN, closely
  associated, legal entity).
- **Verification.** Every case was read by hand against its labels, field by field, and cross-checked against
  the English version of the same notification, which the AMF prints in the same PDF (an independent second
  rendering of every number, date and identifier). The review found and fixed five parser or vocabulary
  defects before the baseline was set (a free-text amendment read as an initial notification, the reference
  ISIN applied to depositary receipts, `CONSEILD'ADMINISTRATION` without a space, contributions in kind and
  "donation à" without a direction, French mandatory convertibles). Four labels remain where the right answer
  needs judgment the rules do not have; they are recorded as known errors (see below).

Because the labels were drafted from the parser's output and then corrected by hand, a perfect extraction
score means "no disagreement found by a careful reader", not an independent double annotation. The value of
the set is as a regression gate and as a benchmark for the LLM path.

## Scoring
Every leaf field is scored; transactions and price lines are aligned by position. A correct value is a true
positive; a wrong value is both a false positive and a false negative; a value the extractor misses is a false
negative. A filing the extractor cannot read counts all its gold fields as false negatives. Confidence values
are not scored. The CI gate requires micro precision and recall of at least 0.99 and no field below
`baseline.json`.

## Results

### Deterministic parser (`fr_amf_pdmr@1`)

60 filings, 0 unread, micro precision **0.9980**, micro recall **0.9980**, 0.3 ms per filing.

| Field | Precision | Recall | TP | FP | FN | vs baseline |
|---|---:|---:|---:|---:|---:|---|
| `amendment_details` | 1.000 | 1.000 | 2 | 0 | 0 | = |
| `declarant_is_legal_entity` | 1.000 | 1.000 | 22 | 0 | 0 | = |
| `declarant_name` | 1.000 | 1.000 | 60 | 0 | 0 | = |
| `declarant_position` | 1.000 | 1.000 | 60 | 0 | 0 | = |
| `is_amendment` | 1.000 | 1.000 | 60 | 0 | 0 | = |
| `issuer_lei` | 1.000 | 1.000 | 51 | 0 | 0 | = |
| `issuer_name` | 1.000 | 1.000 | 60 | 0 | 0 | = |
| `linked_pdmr_name` | 1.000 | 1.000 | 28 | 0 | 0 | = |
| `normalized.closely_associated` | 1.000 | 1.000 | 60 | 0 | 0 | = |
| `normalized.notifier_is_legal_entity` | 0.983 | 0.983 | 59 | 1 | 1 | = |
| `normalized.pdmr_role` | 0.967 | 0.967 | 58 | 2 | 2 | = |
| `normalized.transactions.direction` | 1.000 | 1.000 | 80 | 0 | 0 | = |
| `normalized.transactions.instrument_type` | 1.000 | 1.000 | 80 | 0 | 0 | = |
| `normalized.transactions.isin` | 1.000 | 1.000 | 77 | 0 | 0 | = |
| `normalized.transactions.nature` | 1.000 | 1.000 | 80 | 0 | 0 | = |
| `normalized.transactions.on_venue` | 0.988 | 0.988 | 79 | 1 | 1 | = |
| `reference_isin` | 1.000 | 1.000 | 56 | 0 | 0 | = |
| `transactions.aggregated_price` | 1.000 | 1.000 | 80 | 0 | 0 | = |
| `transactions.aggregated_volume` | 1.000 | 1.000 | 80 | 0 | 0 | = |
| `transactions.currency` | 1.000 | 1.000 | 80 | 0 | 0 | = |
| `transactions.instrument` | 1.000 | 1.000 | 80 | 0 | 0 | = |
| `transactions.isin` | 1.000 | 1.000 | 68 | 0 | 0 | = |
| `transactions.lines.price` | 1.000 | 1.000 | 145 | 0 | 0 | = |
| `transactions.lines.volume` | 1.000 | 1.000 | 145 | 0 | 0 | = |
| `transactions.linked_to_share_option_programme` | 1.000 | 1.000 | 80 | 0 | 0 | = |
| `transactions.nature` | 1.000 | 1.000 | 80 | 0 | 0 | = |
| `transactions.transaction_date` | 1.000 | 1.000 | 80 | 0 | 0 | = |
| `transactions.venue` | 1.000 | 1.000 | 80 | 0 | 0 | = |

Errors (gold vs predicted):

- `fr-amf-009` `normalized.transactions[0].on_venue`: gold `False`, predicted `True`
- `fr-amf-020` `normalized.notifier_is_legal_entity`: gold `True`, predicted `False`
- `fr-amf-039` `normalized.pdmr_role`: gold `'executive'`, predicted `'ceo'`
- `fr-amf-049` `normalized.pdmr_role`: gold `'executive'`, predicted `'ceo'`

The four errors are judgment calls, all in the normalization layer, not in reading the document:
- `fr-amf-009`: the "venue" is a bank and registrar; the rules only recognise explicit off-venue wording.
- `fr-amf-020`: a legal entity whose name carries no legal form; the name heuristic cannot tell.
- `fr-amf-039`, `fr-amf-049`: "Directeur Général des Fonctions Centrales" and the CEO of a subsidiary are
  executives of the issuer, not its CEO.

### LLM fallback, local model (`llm:qwen3.5:9b@fr-amf-1`)

60 filings, 13 unread, micro precision **0.9232**, micro recall **0.7675**.

| Field | Precision | Recall | TP | FP | FN | vs baseline |
|---|---:|---:|---:|---:|---:|---|
| `amendment_details` | 0.000 | 0.000 | 0 | 1 | 2 |  |
| `declarant_is_legal_entity` | 0.404 | 0.864 | 19 | 28 | 3 |  |
| `declarant_name` | 0.936 | 0.733 | 44 | 3 | 16 |  |
| `declarant_position` | 0.851 | 0.667 | 40 | 7 | 20 |  |
| `is_amendment` | 1.000 | 0.783 | 47 | 0 | 13 |  |
| `issuer_lei` | 1.000 | 0.804 | 41 | 0 | 10 |  |
| `issuer_name` | 1.000 | 0.783 | 47 | 0 | 13 |  |
| `linked_pdmr_name` | 0.960 | 0.857 | 24 | 1 | 4 |  |
| `normalized.closely_associated` | 1.000 | 0.783 | 47 | 0 | 13 |  |
| `normalized.notifier_is_legal_entity` | 0.979 | 0.767 | 46 | 1 | 14 |  |
| `normalized.pdmr_role` | 0.957 | 0.750 | 45 | 2 | 15 |  |
| `normalized.transactions.direction` | 1.000 | 0.825 | 66 | 0 | 14 |  |
| `normalized.transactions.instrument_type` | 1.000 | 0.825 | 66 | 0 | 14 |  |
| `normalized.transactions.isin` | 1.000 | 0.792 | 61 | 0 | 16 |  |
| `normalized.transactions.nature` | 1.000 | 0.825 | 66 | 0 | 14 |  |
| `normalized.transactions.on_venue` | 0.985 | 0.812 | 65 | 1 | 15 |  |
| `reference_isin` | 1.000 | 0.446 | 25 | 0 | 31 |  |
| `transactions.aggregated_price` | 1.000 | 0.812 | 65 | 0 | 15 |  |
| `transactions.aggregated_volume` | 0.600 | 0.487 | 39 | 26 | 41 |  |
| `transactions.currency` | 1.000 | 0.825 | 66 | 0 | 14 |  |
| `transactions.instrument` | 1.000 | 0.825 | 66 | 0 | 14 |  |
| `transactions.isin` | 0.887 | 0.809 | 55 | 7 | 13 |  |
| `transactions.lines.price` | 1.000 | 0.897 | 130 | 0 | 15 |  |
| `transactions.lines.volume` | 0.623 | 0.559 | 81 | 49 | 64 |  |
| `transactions.linked_to_share_option_programme` | 1.000 | 0.825 | 66 | 0 | 14 |  |
| `transactions.nature` | 1.000 | 0.825 | 66 | 0 | 14 |  |
| `transactions.transaction_date` | 1.000 | 0.825 | 66 | 0 | 14 |  |
| `transactions.venue` | 1.000 | 0.825 | 66 | 0 | 14 |  |

`qwen3.5:9b` (Ollama, on a laptop, about 30 s per filing, temperature 0, thinking off) is a deliberately
small local model, the privacy-preserving option. Lessons:
- 13 of 60 answers failed Pydantic validation and were rejected after a retry: nothing malformed reaches silver.
- Its main error is reading French four-decimal quantities (`163.0000`) as thousands (163,000): line and
  aggregated volumes, the fields that matter most, have a precision of about 0.6. A plausible wrong number
  passes schema validation, which is why an LLM path must be evaluated before it is trusted.
- It over-reports `declarant_is_legal_entity` (true where the template is silent) and often omits the header
  reference ISIN.

**Claude API (`claude-opus-5-5`) was not evaluated**: no `ANTHROPIC_API_KEY` was available when this report was
produced. Run the command above with the key set to add its scores. Note that it sends the (here synthetic)
document text to Anthropic.

Conclusion: on this source the deterministic parser read all 1,003 filings of the period, so the fallback is a
safety net for template changes and malformed PDFs. It stays opt-in, and a model should be enabled only after
its scores here are acceptable.

## Live run
`disclosure-atlas ingest fr_amf_pdmr --start 2026-07-22` (20 October 2026, no LLM configured): 18 minutes,
1,019 documents (16 listing pages, 1,003 PDFs), **2,109 records**, no warnings, all from the deterministic parser.

| Measure | Value |
|---|---|
| Filings, issuers | 1,003 filings, 148 issuers |
| Records without transaction date | 0 |
| Records without ISIN (after the reference-ISIN fallback for shares) | 39 (1.8%) |
| Records without a valid issuer LEI | 365 (17%) |
| Closely associated declarants | 1,122 records; 1,217 records name a legal-entity notifier |
| Open-market share trades (`is_open_market`) | 1,812 |
| Non-EUR records | 20 (USD, GBP) |
| Filing lag (transaction to publication) | median 4 days, 32% over 5 days, 90th percentile 113 days |
| Positions with redacted text | 108 records (mostly a family company named like its founder: conservative) |
