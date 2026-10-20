"""LLM fallback: read a notification the deterministic parser could not, into an ``ExtractedFiling``.

The output is constrained by a JSON schema derived from the Pydantic model, then validated by that model,
so a malformed answer never reaches silver. Provenance is ``llm:<model>@<prompt-version>``; any change to
the prompt must bump ``PROMPT_VERSION`` and be re-run against the evaluation set (docs/decisions/0003).
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import ValidationError

from .providers import LLMProvider, ProviderError
from .schema import ExtractedFiling, strict_json_schema

PROMPT_VERSION = "fr-amf-1"
MAX_DOCUMENT_CHARS = 200_000

SYSTEM_PROMPT = """\
You extract data from one notification of a managers' transaction under Article 19 of the EU Market Abuse
Regulation, laid out on the template of Commission Implementing Regulation (EU) 2016/523. The document text
may be in French, English or both (the same notification twice); read one language version only and never
report a transaction twice.

Rules:
- Copy text fields (names, position, venue, nature, instrument) exactly as printed, in the language of the
  version you read. Do not translate or normalize them.
- The line naming the person is usually "NAME, position": put the name in declarant_name and the rest in
  declarant_position. When it reads "ENTITY personne morale liée à PERSON, position" (or "legal person
  closely associated with", or "personne liée à"), declarant_name is ENTITY, linked_pdmr_name is PERSON and
  declarant_is_legal_entity is true for "personne morale" / "legal person".
- Section 2b reads "Notification initiale" / "Initial notification" for an initial notification. Anything
  else is an amendment; copy any explanation printed there to amendment_details.
- Each "DETAIL DE LA TRANSACTION" / "DETAIL OF THE TRANSACTION" block is one transaction. Its price and
  volume lines go to `lines`, in order; the aggregated price and volume go to the aggregated fields.
- Numbers use a space as thousands separator and a point as decimal separator ("2 738.0000" is 2738).
- Dates are ISO 8601 (YYYY-MM-DD). Currencies are ISO 4217 codes ("Euro" is EUR, "Livre Sterling" is GBP).
- Use null for anything not printed. Never invent a value.
- Confidence is your probability, from 0 to 1, that the field group is exactly right.
"""


class ExtractionError(RuntimeError):
    """The model's answer could not be turned into a valid ``ExtractedFiling``."""


@dataclass(frozen=True)
class LLMExtraction:
    filing: ExtractedFiling
    parser: str


class LLMExtractor:
    def __init__(self, provider: LLMProvider, attempts: int = 2) -> None:
        self.provider = provider
        self.attempts = attempts
        self.schema = strict_json_schema(ExtractedFiling)

    @property
    def parser_id(self) -> str:
        return f"llm:{self.provider.model}@{PROMPT_VERSION}"

    def extract(self, text: str) -> LLMExtraction:
        """Extract one filing from the document text; retries once on an invalid answer."""
        if len(text) > MAX_DOCUMENT_CHARS:
            raise ExtractionError(f"document has {len(text)} characters, more than {MAX_DOCUMENT_CHARS}")
        errors: list[str] = []
        for _ in range(self.attempts):
            try:
                answer = self.provider.complete_json(SYSTEM_PROMPT, text, self.schema)
                filing = ExtractedFiling.model_validate(answer)
            except (ProviderError, ValidationError) as exc:
                errors.append(str(exc).splitlines()[0])
                continue
            if not filing.transactions:
                errors.append("no transaction extracted")
                continue
            return LLMExtraction(filing=filing, parser=self.parser_id)
        raise ExtractionError(f"{self.parser_id} failed: {'; '.join(errors)}")
