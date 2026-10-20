"""OpenFIGI mapping: ISIN -> exchange listings (ticker and Bloomberg exchange code), with one primary listing.

API: POST https://api.openfigi.com/v3/mapping with ``[{"idType": "ID_ISIN", "idValue": ...}, ...]``. Free; an
API key (``OPENFIGI_API_KEY``, sent as ``X-OPENFIGI-APIKEY``) raises the limits. Published limits for the mapping
endpoint (https://www.openfigi.com/api/documentation): 25 requests per minute and 10 jobs per request without a
key, 250 requests per minute and 100 jobs per request with one. The client stays under them.

The primary listing is the one on the issuer's home market, read from the ISIN country prefix (``FP`` for
France, ``SS`` for Sweden...); otherwise a local venue listing that is not on another home market, a German
venue or ``XS`` (seen for Nordic growth-market shares that have no ``SS`` listing); otherwise the first
composite equity listing; otherwise the first listing.
Cached in lake/reference/openfigi_listings.parquet; an ISIN without a match is cached as one row with no FIGI.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from ..http import PoliteClient
from ..lake import Lake
from .enrich import FIGI_SCHEMA
from .store import ISIN_SQL, cached_keys, lake_identifiers, merge_write

MAPPING_URL = "https://api.openfigi.com/v3/mapping"
API_KEY_ENV = "OPENFIGI_API_KEY"

# (jobs per request, seconds between requests), with a margin under the published per-minute limits.
LIMITS_WITHOUT_KEY = (10, 2.5)  # 25 requests per minute
LIMITS_WITH_KEY = (100, 0.25)  # 250 requests per minute

# Bloomberg composite exchange code of each country's home market, keyed by the ISIN country prefix. Countries
# not listed fall back to the first composite equity listing.
HOME_EXCHANGE = {
    "AT": "AV", "BE": "BB", "CH": "SW", "CZ": "CP", "DE": "GR", "DK": "DC", "ES": "SM", "FI": "FH", "FR": "FP",
    "GB": "LN", "GR": "GA", "HU": "HB", "IE": "ID", "IT": "IM", "LU": "LX", "NL": "NA", "NO": "NO", "PL": "PW",
    "PT": "PL", "SE": "SS", "US": "US",
}
GERMAN_VENUES = {"GR", "GY", "GF", "GD", "GS", "GM", "GB", "GH", "GI"}
"""Bloomberg codes of German venues, where many European shares have secondary listings."""


def primary_index(isin: str, listings: list[dict[str, Any]]) -> int | None:
    """Index of the primary listing among one ISIN's OpenFIGI results."""
    candidates = [i for i, row in enumerate(listings) if row.get("ticker")]
    if not candidates:
        return None
    home = HOME_EXCHANGE.get(isin[:2].upper())
    for i in candidates:
        if home and listings[i].get("exchCode") == home:
            return i
    foreign = (set(HOME_EXCHANGE.values()) | (GERMAN_VENUES if isin[:2].upper() != "DE" else set())) - {home}
    for i in candidates:  # a local venue listing (not a composite) outside the usual foreign secondary markets
        row = listings[i]
        if row.get("figi") != row.get("compositeFIGI") and row.get("exchCode") not in foreign | {"XS"}:
            return i
    for i in candidates:
        row = listings[i]
        if row.get("figi") and row.get("figi") == row.get("compositeFIGI") and row.get("marketSector") == "Equity":
            return i
    return candidates[0]


def parse_mapping(isins: list[str], payload: list[dict[str, Any]], fetched_at: datetime) -> list[dict[str, Any]]:
    """Rows for one mapping response (aligned with the request's jobs). Errored jobs are left out (retried later)."""
    records: list[dict[str, Any]] = []
    for isin, result in zip(isins, payload, strict=False):
        listings = result.get("data")
        if listings is None:
            if "warning" in result:  # "No identifier found.": cache the miss
                records.append({"isin": isin, "is_primary": False, "fetched_at": fetched_at})
            continue
        primary = primary_index(isin, listings)
        for i, row in enumerate(listings):
            records.append({
                "isin": isin,
                "figi": row.get("figi"),
                "composite_figi": row.get("compositeFIGI"),
                "share_class_figi": row.get("shareClassFIGI"),
                "ticker": row.get("ticker"),
                "exch_code": row.get("exchCode"),
                "name": row.get("name"),
                "security_type": row.get("securityType"),
                "market_sector": row.get("marketSector"),
                "is_primary": i == primary,
                "fetched_at": fetched_at,
            })
    return records


def fetch_listings(
    isins: Iterable[str], http: PoliteClient | None = None, api_key: str | None = None
) -> list[dict[str, Any]]:
    batch, interval = LIMITS_WITH_KEY if api_key else LIMITS_WITHOUT_KEY
    if http is None:
        http = PoliteClient(min_interval=interval)
    else:
        http.min_interval = max(http.min_interval, interval)
    headers = {"X-OPENFIGI-APIKEY": api_key} if api_key else None
    wanted = sorted({isin.strip().upper() for isin in isins if len(isin.strip()) == 12})
    records: list[dict[str, Any]] = []
    for start in range(0, len(wanted), batch):
        chunk = wanted[start : start + batch]
        jobs = [{"idType": "ID_ISIN", "idValue": isin} for isin in chunk]
        response = http.post_json(MAPPING_URL, jobs, headers=headers)
        records += parse_mapping(chunk, response.json(), datetime.now(UTC))
    return records


def refresh_figi(
    lake: Lake, isins: Iterable[str] | None = None, *, refresh: bool = False, http: PoliteClient | None = None
) -> tuple[int, int]:
    """Map the lake's ISINs (or ``isins``); only uncached ones unless ``refresh``. Returns (ISINs mapped, rows)."""
    path = lake.reference_path("openfigi_listings")
    wanted = set(isins) if isins is not None else lake_identifiers(lake, ISIN_SQL)
    if not refresh:
        wanted -= cached_keys(path, FIGI_SCHEMA, "isin")
    records = fetch_listings(wanted, http, os.environ.get(API_KEY_ENV) or None) if wanted else []
    return len({r["isin"] for r in records}), merge_write(path, FIGI_SCHEMA, "isin", records)
