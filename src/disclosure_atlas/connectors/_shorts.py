"""Shared machinery for public net short position connectors (SSR Article 6).

Every authority publishes the same five fields (holder, issuer, ISIN, position %, position date), usually
as a "current" file (positions at or above 0.5%) and a "historic" file (every earlier publication, including
the final one when a position falls below 0.5%). A connector downloads those snapshots and turns them into
``Row`` values; ``ShortsConnector.parse`` validates them, keeps the requested window and drops the
duplicates that appear when a position is in both the current and the historic file.

Holders are almost always legal entities (funds, banks, asset managers) and keep their names. SSR Article 6
also covers natural persons, though; a holder that does not look like a legal entity is replaced by a keyed
pseudonym, as everywhere else in the dataset. Not a registered connector: the leading underscore keeps this
module out of discovery.
"""

from __future__ import annotations

import io
import re
import zipfile
from abc import abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from xml.etree import ElementTree

from ..models import Dataset, NetShortPosition
from ..normalize import clean, parse_number, record_id, valid_isin, valid_lei
from ..privacy import is_legal_entity
from .base import Connector, ParseContext, RawDocument

THRESHOLD = 0.5
# Institutional words that the generic legal-form heuristic (privacy.is_legal_entity) does not know.
_INSTITUTION = re.compile(
    r"(?<![\w-])("
    r"l\.\s?p\.?|l\.\s?l\.\s?c\.?|n\.?\s?a|ltda|fze|mbh|aktiengesellschaft|kapitalanlagegesellschaft|"
    r"bank\w*|banque|banca|banco|kantonalbank|securities|investors?|advisors?|associates|asset|assets|"
    r"gestion|gestao|gestão|group|international|global|equities|opportunities|trading|board|"
    r"retirement|pension|caisse|commercial|industriel|insurance|assurance\w*"
    r")(?![\w-])",
    re.IGNORECASE,
)
_BELOW = re.compile(r"^\s*<\s*0[.,]5\s*%?\s*$")


@dataclass(frozen=True)
class Row:
    holder: str | None
    issuer: str | None
    isin: str | None
    pct: float | None
    position_date: date | None
    below_threshold: bool = False
    holder_lei: str | None = None
    issuer_lei: str | None = None
    published_at: datetime | None = None


def parse_pct(value: Any, *, fraction: bool = False, zero_means_below: bool = False) -> tuple[float | None, bool]:
    """Position in % and whether it is the below-threshold disclosure.

    Accepts numbers, '0,50', '1.21' and '<0,5'. ``fraction`` handles sources that store 1.05% as 0.0105;
    ``zero_means_below`` handles sources that encode "fell below 0.5%" as 0.
    """
    if value is None:
        return None, False
    if isinstance(value, str) and _BELOW.match(value):
        return None, True
    pct = float(value) if isinstance(value, int | float) else parse_number(str(value).replace("%", ""))
    if pct is None:
        return None, False
    if fraction:
        pct *= 100
    pct = round(pct, 4)
    if pct == 0 and zero_means_below:
        return None, True
    return pct, pct < THRESHOLD


def to_date(value: Any, *formats: str) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = clean(str(value)) if value is not None else None
    if not text:
        return None
    for fmt in formats or ("%Y-%m-%d",):
        for candidate in (text, text[:10]):
            try:
                return datetime.strptime(candidate, fmt).date()
            except ValueError:
                continue
    return None


def holder_is_legal_entity(name: str, lei: str | None = None) -> bool:
    """A holder with an LEI, a legal form or an institutional word is a legal entity; otherwise a person."""
    return bool(lei) or is_legal_entity(name) or bool(_INSTITUTION.search(name))


def _key(text: str | None) -> str:
    return " ".join((text or "").casefold().split())


class ShortsConnector(Connector):
    dataset = Dataset.NET_SHORT_POSITIONS

    @abstractmethod
    def rows(self, doc: RawDocument) -> Iterator[Row]:
        """Yield the raw position rows of one downloaded file. Pure: no network access."""

    def document(self, url: str, response: Any, params: dict[str, Any], start: date, end: date, suffix: str,
                 **meta: Any) -> RawDocument:
        return RawDocument(
            source=self.name,
            url=url,
            params=params,
            content=response.content,
            content_type=response.headers.get("content-type"),
            fetched_at=datetime.now(UTC),
            suffix=suffix,
            meta={"window": [start.isoformat(), end.isoformat()], **meta},
        )

    def parse(self, doc: RawDocument, ctx: ParseContext) -> Iterator[NetShortPosition]:
        window = doc.meta.get("window")
        start, end = (date.fromisoformat(window[0]), date.fromisoformat(window[1])) if window else (None, None)
        seen: set[str] = set()
        for row in self.rows(doc):
            holder, issuer = clean(row.holder), clean(row.issuer)
            if not holder or not row.position_date or (row.pct is None and not row.below_threshold):
                continue
            if start and not start <= row.position_date <= end:
                continue
            isin = valid_isin(row.isin)
            instrument = isin or _key(row.isin) or _key(issuer)
            holder_lei = valid_lei(row.holder_lei)
            natural_person = not holder_is_legal_entity(holder, holder_lei)
            if natural_person:  # the id is built from the pseudonym, so it cannot confirm a guessed name
                holder = ctx.person_id(holder, scope=instrument)
            pct_key = "<0.5" if row.pct is None else f"{row.pct:.4f}"
            rid = record_id(self.name, _key(holder), instrument, row.position_date.isoformat(), pct_key)
            if rid in seen:
                continue
            seen.add(rid)
            yield NetShortPosition(
                record_id=rid,
                source=self.name,
                jurisdiction=self.jurisdiction,
                source_url=self.homepage,
                published_at=row.published_at,
                position_date=row.position_date,
                holder_name=holder,
                holder_is_natural_person=natural_person,
                holder_lei=holder_lei,
                issuer_name=issuer or "unknown issuer",
                issuer_lei=valid_lei(row.issuer_lei),
                isin=isin,
                position_pct=row.pct,
                below_threshold=row.below_threshold,
                fetched_at=doc.fetched_at,
                parser=self.parser_id,
            )


# --- Spreadsheet readers -------------------------------------------------------------------------------------

_TABLE = "{urn:oasis:names:tc:opendocument:xmlns:table:1.0}"
_TEXT = "{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
_OFFICE = "{urn:oasis:names:tc:opendocument:xmlns:office:1.0}"
_MAX_REPEAT = 64  # trailing empty cells are "repeated" 16,000 times; real data never repeats that much


def read_ods(content: bytes) -> dict[str, list[list[Any]]]:
    """Minimal OpenDocument spreadsheet reader (standard library only): sheet name -> rows of cell values."""
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        root = ElementTree.fromstring(archive.read("content.xml"))
    t, o = _TABLE, _OFFICE
    sheets: dict[str, list[list[Any]]] = {}
    for table in root.iter(f"{t}table"):
        rows: list[list[Any]] = []
        for row in table.iter(f"{t}table-row"):
            cells: list[Any] = []
            for cell in row:
                if cell.tag not in (f"{t}table-cell", f"{t}covered-table-cell"):
                    continue
                kind = cell.get(f"{o}value-type")
                if kind in ("float", "percentage"):
                    value: Any = float(cell.get(f"{o}value"))
                elif kind == "date":
                    value = cell.get(f"{o}date-value")
                else:
                    value = "\n".join("".join(p.itertext()) for p in cell.iter(f"{_TEXT}p")) or None
                cells.extend([value] * min(int(cell.get(f"{t}number-columns-repeated", 1)), _MAX_REPEAT))
            while cells and cells[-1] in (None, ""):
                cells.pop()
            if cells:
                rows.append(cells)
        sheets[table.get(f"{t}name")] = rows
    return sheets


def _excel_extra(module: str) -> Any:
    import importlib

    try:
        return importlib.import_module(module)
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise ImportError(f"{module} is needed for this source: pip install -e '.[shorts]'") from exc


def read_xlsx(content: bytes) -> dict[str, list[list[Any]]]:
    openpyxl = _excel_extra("openpyxl")
    book = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    return {sheet.title: [list(r) for r in sheet.iter_rows(values_only=True)] for sheet in book.worksheets}


def read_xls(content: bytes) -> dict[str, list[list[Any]]]:
    xlrd = _excel_extra("xlrd")
    book = xlrd.open_workbook(file_contents=content)
    sheets: dict[str, list[list[Any]]] = {}
    for sheet in book.sheets():
        rows = []
        for i in range(sheet.nrows):
            row = []
            for cell in sheet.row(i):
                if cell.ctype == xlrd.XL_CELL_DATE:
                    row.append(xlrd.xldate.xldate_as_datetime(cell.value, book.datemode))
                elif cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                    row.append(None)
                else:
                    row.append(cell.value)
            rows.append(row)
        sheets[sheet.name] = rows
    return sheets


def table_rows(rows: list[list[Any]], columns: dict[str, str]) -> Iterator[dict[str, Any]]:
    """Rows below the header row as {field: value}.

    ``columns`` maps each field to the start of its header text (case-insensitive), so bilingual or
    multi-line headers such as "Position holder\n(Innehavare)" still match. The header row is the first
    row in which every field finds a column; rows before it (titles, notes) are skipped.
    """
    index: dict[str, int] | None = None
    for row in rows:
        if index is None:
            cells = [(clean(str(c)) or "").casefold() if c is not None else "" for c in row]
            found = {
                field: next((i for i, c in enumerate(cells) if c.startswith(prefix.casefold())), None)
                for field, prefix in columns.items()
            }
            if all(i is not None for i in found.values()):
                index = found  # type: ignore[assignment]
            continue
        if any(c not in (None, "") for c in row):
            yield {field: (row[i] if i < len(row) else None) for field, i in index.items()}
