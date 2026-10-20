"""GLEIF LEI records: official legal name, legal address country and status of each issuer LEI.

API: https://api.gleif.org/api/v1/lei-records?filter[lei]=<LEI>,<LEI>,...  (JSON:API, no key, CC0 data).
Cached in lake/reference/gleif_lei.parquet, one row per LEI; LEIs GLEIF does not know are simply absent.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from ..http import PoliteClient
from ..lake import Lake
from .enrich import GLEIF_SCHEMA
from .store import LEI_SQL, cached_keys, lake_identifiers, merge_write

GLEIF_URL = "https://api.gleif.org/api/v1/lei-records"
BATCH = 100
"""LEIs per request (the API pages at most 200 records; 100 keeps the URL short)."""
LEI_PATTERN = re.compile(r"^[A-Z0-9]{18}[0-9]{2}$")


def parse_gleif(payload: dict[str, Any], fetched_at: datetime) -> list[dict[str, Any]]:
    records = []
    for item in payload.get("data") or []:
        attributes = item.get("attributes") or {}
        entity = attributes.get("entity") or {}
        registration = attributes.get("registration") or {}
        lei = (attributes.get("lei") or item.get("id") or "").upper()
        if not lei:
            continue
        records.append({
            "lei": lei,
            "legal_name": (entity.get("legalName") or {}).get("name"),
            "legal_country": (entity.get("legalAddress") or {}).get("country"),
            "headquarters_country": (entity.get("headquartersAddress") or {}).get("country"),
            "legal_jurisdiction": entity.get("jurisdiction"),
            "entity_status": entity.get("status"),
            "registration_status": registration.get("status"),
            "fetched_at": fetched_at,
        })
    return records


def fetch_lei_records(leis: Iterable[str], http: PoliteClient) -> list[dict[str, Any]]:
    wanted = sorted({lei.upper() for lei in leis if LEI_PATTERN.match(lei.upper())})
    records: list[dict[str, Any]] = []
    for start in range(0, len(wanted), BATCH):
        batch = wanted[start : start + BATCH]
        response = http.get(GLEIF_URL, params={"filter[lei]": ",".join(batch), "page[size]": len(batch)})
        records += parse_gleif(response.json(), datetime.now(UTC))
    return records


def refresh_lei(
    lake: Lake, leis: Iterable[str] | None = None, *, refresh: bool = False, http: PoliteClient | None = None
) -> tuple[int, int]:
    """Look up the lake's issuer LEIs (or ``leis``); only uncached ones unless ``refresh``. Returns (fetched, total)."""
    path = lake.reference_path("gleif_lei")
    wanted = set(leis) if leis is not None else lake_identifiers(lake, LEI_SQL)
    if not refresh:
        wanted -= cached_keys(path, GLEIF_SCHEMA, "lei")
    records = fetch_lei_records(wanted, http or PoliteClient()) if wanted else []
    return len(records), merge_write(path, GLEIF_SCHEMA, "lei", records)
