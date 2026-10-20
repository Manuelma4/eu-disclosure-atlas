"""Field-level evaluation of an extractor against a hand-verified gold set (docs/decisions/0003).

Every leaf of an ``ExtractedFiling`` is one field, named by its path without indices
(``transactions.lines.price``). Transactions and price lines are aligned by position, so a missing or extra
transaction counts against every one of its fields. For each field:

- true positive: the extractor returned a value equal to the gold value;
- false positive: it returned a value that is wrong (or where the gold set has none);
- false negative: the gold set has a value that the extractor did not return correctly.

A wrong value is therefore both a false positive and a false negative. Confidence fields are not scored.
A document the extractor could not read counts every gold field as a false negative.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from ..normalize import clean
from .schema import ExtractedFiling

Prediction = ExtractedFiling | BaseException | None
_INDEX = re.compile(r"\[\d+\]")


@dataclass(frozen=True)
class GoldCase:
    id: str
    text: str
    labels: ExtractedFiling
    normalized: dict[str, Any]
    sha256: str | None = None
    notes: str = ""


def load_gold(folder: Path | str) -> list[GoldCase]:
    cases = []
    for path in sorted(Path(folder).glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        cases.append(GoldCase(id=raw["id"], text=raw["text"], labels=ExtractedFiling.model_validate(raw["labels"]),
                              normalized=raw.get("normalized", {}), sha256=raw.get("sha256"),
                              notes=raw.get("notes", "")))
    return cases


def flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    """{'transactions[0].lines[1].price': 1.5, ...}, without confidence fields."""
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if key != "confidence":
                out.update(flatten(item, f"{prefix}.{key}" if prefix else key))
        return out
    if isinstance(value, list):
        out = {}
        for i, item in enumerate(value):
            out.update(flatten(item, f"{prefix}[{i}]"))
        return out
    return {prefix: value}


def same(gold: Any, predicted: Any) -> bool:
    if isinstance(gold, float) or isinstance(predicted, float):
        try:
            return math.isclose(float(gold), float(predicted), rel_tol=1e-9, abs_tol=1e-9)
        except (TypeError, ValueError):
            return False
    if isinstance(gold, str) and isinstance(predicted, str):
        return clean(gold) == clean(predicted)
    if isinstance(gold, date) and isinstance(predicted, str):
        return gold.isoformat() == predicted
    return gold == predicted


@dataclass
class FieldScore:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 1.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 1.0


@dataclass
class Report:
    fields: dict[str, FieldScore] = field(default_factory=dict)
    documents: int = 0
    unread: list[str] = field(default_factory=list)
    errors: list[tuple[str, str, Any, Any]] = field(default_factory=list)

    def _total(self) -> FieldScore:
        total = FieldScore()
        for s in self.fields.values():
            total.tp, total.fp, total.fn = total.tp + s.tp, total.fp + s.fp, total.fn + s.fn
        return total

    @property
    def micro_precision(self) -> float:
        return self._total().precision

    @property
    def micro_recall(self) -> float:
        return self._total().recall

    def summary(self) -> str:
        lines = [f"{self.documents} documents, {len(self.unread)} unread, micro precision "
                 f"{self.micro_precision:.4f}, micro recall {self.micro_recall:.4f}"]
        lines += [f"  {doc} {name}: gold={gold!r} predicted={pred!r}" for doc, name, gold, pred in self.errors[:20]]
        return "\n".join(lines)

    def to_json(self) -> dict[str, Any]:
        return {
            "documents": self.documents,
            "unread": self.unread,
            "micro": {"precision": self.micro_precision, "recall": self.micro_recall},
            "fields": {name: {"tp": s.tp, "fp": s.fp, "fn": s.fn, "precision": s.precision, "recall": s.recall}
                       for name, s in sorted(self.fields.items())},
            "errors": [list(e) for e in self.errors],
        }


def score(pairs: Iterable[tuple[GoldCase, Prediction]],
          normalize: Callable[[ExtractedFiling], dict[str, Any]] | None = None) -> Report:
    """Score predictions; with ``normalize``, also score the normalized values against ``case.normalized``."""
    report = Report()
    for case, predicted in pairs:
        report.documents += 1
        gold = {**flatten(case.labels), **({f"normalized.{k}": v for k, v in case.normalized.items()}
                                          if normalize else {})}
        if not isinstance(predicted, ExtractedFiling):
            report.unread.append(case.id)
            got: dict[str, Any] = {}
        else:
            got = flatten(predicted)
            if normalize:
                got.update({f"normalized.{k}": v for k, v in normalize(predicted).items()})
        for key in sorted(set(gold) | set(got)):
            name = _INDEX.sub("", key)
            s = report.fields.setdefault(name, FieldScore())
            g, p = gold.get(key), got.get(key)
            if p is not None and g is not None and same(g, p):
                s.tp += 1
                continue
            if p is not None:
                s.fp += 1
            if g is not None:
                s.fn += 1
            if p is not None or g is not None:
                report.errors.append((case.id, key, g, p))
    return report
