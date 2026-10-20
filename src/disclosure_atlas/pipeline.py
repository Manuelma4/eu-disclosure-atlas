"""Ingestion: connector.fetch -> bronze -> connector.parse -> silver."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .connectors.base import Connector, ParseContext
from .lake import Lake


@dataclass
class IngestReport:
    source: str
    start: date
    end: date
    documents: int = 0
    records: int = 0
    superseded: int = 0
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        text = (
            f"{self.source} {self.start}..{self.end}: {self.documents} documents, "
            f"{self.records} records ({self.superseded} superseded)"
        )
        return "\n".join([text, *(f"  warning: {w}" for w in self.warnings)])


def ingest(connector: Connector, lake: Lake, start: date, end: date) -> IngestReport:
    report = IngestReport(source=connector.name, start=start, end=end)
    ctx = ParseContext(pseudonym_key=lake.pseudonym_key())
    for doc in connector.fetch(start, end):
        lake.write_bronze(doc)
        records = list(connector.parse(doc, ctx))
        lake.write_silver(connector.dataset, connector.name, records)
        report.documents += 1
        report.records += len(records)
        report.superseded += sum(1 for r in records if r.is_superseded)
        if warning := doc.meta.get("warning"):
            report.warnings.append(str(warning))
        if doc.meta.get("truncated"):
            report.warnings.append(f"{doc.meta.get('window')} hit the source row cap; data may be incomplete")
    return report
