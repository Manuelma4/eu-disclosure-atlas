"""Intermediate schema for one MAR Article 19 notification, as printed on the EU template.

Both extraction paths produce it: the deterministic template parser of a connector and the LLM fallback. The
connector then maps it to ``InsiderTransaction`` rows, so normalization, pseudonymization and record ids are
the same whichever path read the document.

The declarant's name is held here only in memory, long enough to be turned into a pseudonym. It is never
written to silver or gold (see docs/decisions/0002).
"""

from __future__ import annotations

import copy
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PriceVolume(_Strict):
    """One price and volume line of template field 4c."""

    price: float | None = Field(description="Unit price as printed, without currency")
    volume: float | None = Field(description="Number of units")


class TransactionConfidence(_Strict):
    """Model-reported confidence in [0, 1] for each field group of one transaction (LLM path only)."""

    transaction_date: float = Field(ge=0, le=1)
    venue: float = Field(ge=0, le=1)
    nature: float = Field(ge=0, le=1)
    instrument: float = Field(ge=0, le=1)
    isin: float = Field(ge=0, le=1)
    price_volume: float = Field(ge=0, le=1)


class FilingConfidence(_Strict):
    """Model-reported confidence in [0, 1] for each filing-level field (LLM path only)."""

    declarant: float = Field(ge=0, le=1)
    linked_pdmr: float = Field(ge=0, le=1)
    position: float = Field(ge=0, le=1)
    amendment: float = Field(ge=0, le=1)
    issuer_name: float = Field(ge=0, le=1)
    issuer_lei: float = Field(ge=0, le=1)


class ExtractedTransaction(_Strict):
    """One transaction block (template section 4)."""

    transaction_date: date | None = Field(description="4e, date of the transaction, ISO 8601")
    venue: str | None = Field(description="4f, place of the transaction as printed")
    nature: str | None = Field(description="4b, nature of the transaction as printed")
    instrument: str | None = Field(description="4a, description of the financial instrument as printed")
    isin: str | None = Field(description="4a, identification code (usually an ISIN)")
    currency: str | None = Field(description="ISO 4217 code of the prices, e.g. EUR, GBP, USD")
    lines: list[PriceVolume] = Field(description="4c, every price and volume line, in order")
    aggregated_price: float | None = Field(description="4d, aggregated (weighted average) price")
    aggregated_volume: float | None = Field(description="4d, aggregated volume")
    linked_to_share_option_programme: bool | None = Field(
        description="Whether the transaction is linked to a share option programme or a free share allocation"
    )
    confidence: TransactionConfidence | None = None


class ExtractedFiling(_Strict):
    """One notification: the declarant, the issuer and one or more transactions."""

    declarant_name: str | None = Field(
        description="Section 1a, name of the notifying person: the person discharging managerial "
        "responsibilities, or the closely associated person (natural person or legal entity), exactly as printed"
    )
    declarant_is_legal_entity: bool | None = Field(
        description="True if the notifying person is a legal entity, false if a natural person, null if unknown"
    )
    linked_pdmr_name: str | None = Field(
        description="When the notifying person is closely associated, the name of the person discharging "
        "managerial responsibilities they are linked to, exactly as printed; otherwise null"
    )
    declarant_position: str | None = Field(description="Section 2a, position or status, exactly as printed")
    is_amendment: bool = Field(description="Section 2b, true if the notification is an amendment")
    amendment_details: str | None = Field(
        description="Section 2b, the explanation printed for an amendment, if any (not the bare word)"
    )
    issuer_name: str | None = Field(description="Section 3a, issuer name")
    issuer_lei: str | None = Field(description="Section 3b, issuer LEI (20 characters)")
    reference_isin: str | None = Field(
        description="ISIN printed in the document header next to the authority's reference, if any"
    )
    transactions: list[ExtractedTransaction]
    confidence: FilingConfidence | None = None


# Numeric bounds are checked by Pydantic after decoding; constrained decoders do not all support them.
_UNSUPPORTED_KEYWORDS = {"title", "default", "minimum", "maximum"}


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """JSON schema with every $ref inlined, every property required and no additional properties.

    This is the subset accepted by constrained decoding (Claude structured outputs, Ollama ``format``).
    Optional values stay optional through ``null`` in their type, not by being left out.
    """
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(copy.deepcopy(defs[node["$ref"].split("/")[-1]]))
            node = {k: resolve(v) for k, v in node.items() if k not in _UNSUPPORTED_KEYWORDS}
            if node.get("type") == "object" and "properties" in node:
                node["required"] = list(node["properties"])
                node["additionalProperties"] = False
            return node
        if isinstance(node, list):
            return [resolve(item) for item in node]
        return node

    return resolve(schema)
