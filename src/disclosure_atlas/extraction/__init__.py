"""Document extraction shared by document-only sources: schema, LLM providers and the LLM fallback.

Deterministic parsers live with their connector; this package holds what is source independent.
See docs/decisions/0003 (deterministic first, evaluated LLM fallback).
"""

from .llm import PROMPT_VERSION, ExtractionError, LLMExtraction, LLMExtractor
from .providers import AnthropicProvider, LLMProvider, OllamaProvider, ProviderError, provider_from_env
from .schema import (
    ExtractedFiling,
    ExtractedTransaction,
    FilingConfidence,
    PriceVolume,
    TransactionConfidence,
    strict_json_schema,
)

__all__ = [
    "PROMPT_VERSION",
    "AnthropicProvider",
    "ExtractedFiling",
    "ExtractedTransaction",
    "ExtractionError",
    "FilingConfidence",
    "LLMExtraction",
    "LLMExtractor",
    "LLMProvider",
    "OllamaProvider",
    "PriceVolume",
    "ProviderError",
    "TransactionConfidence",
    "provider_from_env",
    "strict_json_schema",
]
