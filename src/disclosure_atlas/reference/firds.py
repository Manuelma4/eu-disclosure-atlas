"""ESMA FIRDS full reference files: ISIN -> instrument full name, CFI, notional currency and issuer LEI.

ESMA publishes a full snapshot of the instruments admitted to trading on EU venues every week (FULINS files,
split by the first letter of the CFI code, zipped ISO 20022 auth.017 XML). The file index is a Solr endpoint:
https://registers.esma.europa.eu/solr/esma_registers_firds_files/select

Only the equity files (``FULINS_E_*``: shares, depositary receipts, units...) are read. By default only the ISINs
used in the lake are kept; ``keep_all`` stores every equity instrument. One row per ISIN: an instrument listed on
several venues appears once per venue in FIRDS, and the first record wins (the same name, CFI and issuer).
Cached in lake/reference/firds_instruments.parquet.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Iterable, Iterator
from datetime import UTC, date, datetime, timedelta
from typing import IO, Any
from xml.etree.ElementTree import iterparse

from ..http import PoliteClient
from ..lake import Lake
from .enrich import FIRDS_SCHEMA
from .store import ISIN_SQL, lake_identifiers, merge_write

FILES_URL = "https://registers.esma.europa.eu/solr/esma_registers_firds_files/select"
FILE_PREFIX = "FULINS_E_"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(element: Any, *path: str) -> Any:
    for name in path:
        if element is None:
            return None
        element = next((c for c in element if _local(c.tag) == name), None)
    return element


def _text(element: Any, *path: str) -> str | None:
    node = _child(element, *path)
    return node.text.strip() if node is not None and node.text and node.text.strip() else None


def parse_firds_xml(
    stream: IO[bytes], file_date: date | None, fetched_at: datetime, wanted: set[str] | None = None
) -> Iterator[dict[str, Any]]:
    """Stream one FULINS XML file, yielding the first record of each (wanted) ISIN."""
    seen: set[str] = set()
    for _, element in iterparse(stream, events=("end",)):
        if _local(element.tag) != "RefData":
            continue
        isin = _text(element, "FinInstrmGnlAttrbts", "Id")
        if isin and isin not in seen and (wanted is None or isin in wanted):
            seen.add(isin)
            yield {
                "isin": isin,
                "full_name": _text(element, "FinInstrmGnlAttrbts", "FullNm"),
                "short_name": _text(element, "FinInstrmGnlAttrbts", "ShrtNm"),
                "cfi": _text(element, "FinInstrmGnlAttrbts", "ClssfctnTp"),
                "currency": _text(element, "FinInstrmGnlAttrbts", "NtnlCcy"),
                "issuer_lei": _text(element, "Issr"),
                "trading_venue": _text(element, "TechAttrbts", "RlvntTradgVn"),
                "competent_authority": _text(element, "TechAttrbts", "RlvntCmptntAuthrty"),
                "file_date": file_date,
                "fetched_at": fetched_at,
            }
        element.clear()


def parse_firds_zip(
    content: bytes, file_date: date | None, fetched_at: datetime, wanted: set[str] | None = None
) -> Iterator[dict[str, Any]]:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        for name in archive.namelist():
            if name.lower().endswith(".xml"):
                with archive.open(name) as stream:
                    yield from parse_firds_xml(stream, file_date, fetched_at, wanted)


def latest_full_files(http: PoliteClient, today: date | None = None) -> tuple[date, list[str]]:
    """Download links of the most recent complete set of equity FULINS files (published weekly)."""
    end = today or date.today()
    start = end - timedelta(days=14)
    response = http.get(FILES_URL, params={
        "q": "*",
        "fq": f"publication_date:[{start.isoformat()}T00:00:00Z TO {end.isoformat()}T23:59:59Z]",
        "wt": "json",
        "rows": 1000,
    })
    files = [
        doc for doc in response.json()["response"]["docs"]
        if doc.get("file_type") == "FULINS" and str(doc.get("file_name", "")).startswith(FILE_PREFIX)
    ]
    if not files:
        raise LookupError(f"No {FILE_PREFIX}* file published between {start} and {end}")
    latest = max(doc["publication_date"][:10] for doc in files)
    links = sorted(doc["download_link"] for doc in files if doc["publication_date"][:10] == latest)
    return date.fromisoformat(latest), links


def _dedupe(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[str, dict[str, Any]] = {}
    for record in records:
        unique.setdefault(record["isin"], record)
    return list(unique.values())


def refresh_firds(
    lake: Lake, isins: Iterable[str] | None = None, *, keep_all: bool = False, http: PoliteClient | None = None
) -> tuple[int, int]:
    """Read the latest weekly equity files and cache the lake's ISINs (or ``isins``). Returns (found, total)."""
    http = http or PoliteClient()
    wanted = None if keep_all else (set(isins) if isins is not None else lake_identifiers(lake, ISIN_SQL))
    if wanted is not None and not wanted:
        return 0, merge_write(lake.reference_path("firds_instruments"), FIRDS_SCHEMA, "isin", [])
    file_date, links = latest_full_files(http)
    records: list[dict[str, Any]] = []
    for link in links:
        content = http.get(link).content
        records += parse_firds_zip(content, file_date, datetime.now(UTC), wanted)
    records = _dedupe(records)
    return len(records), merge_write(lake.reference_path("firds_instruments"), FIRDS_SCHEMA, "isin", records)
