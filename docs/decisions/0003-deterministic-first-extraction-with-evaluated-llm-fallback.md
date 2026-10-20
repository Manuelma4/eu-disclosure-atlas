# 0003: Deterministic-first extraction with an evaluated LLM fallback

Status: accepted (2026-10-20)

## Context
Some authorities publish notifications only as documents, France's AMF among them. The EU notification
template is standardised, but layouts, languages and scans vary.

## Decision
1. Parse with deterministic rules first: they are cheap, reproducible and explainable.
2. Fall back to an LLM only for documents the parser cannot read, behind a provider-agnostic interface
   (Claude API or local Ollama models), with JSON-schema output validated by the same Pydantic models.
3. Record provenance (`parser = "llm:<model>@<prompt-version>"`) and per-field confidence.
4. Gate every parser or prompt change on a hand-verified evaluation set (field-level precision and recall),
   with a regression report against the previous run.
5. The pipeline must run end to end with no LLM configured.

## Consequences
- Most records stay reproducible without model access. LLM output is measurable and auditable rather than
  trusted by default.
- The evaluation set needs curation effort (synthetic names in the repo, real documents only in bronze).
