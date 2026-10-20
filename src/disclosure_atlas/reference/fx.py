"""ECB euro foreign exchange reference rates (1 EUR = rate × currency), daily since 1999.

Gold uses them with an ASOF join, so a transaction gets the latest rate published on or before its date.
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import date

import pyarrow as pa
import pyarrow.parquet as pq

from ..http import PoliteClient
from ..lake import Lake

ECB_HISTORY_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip"
SCHEMA = pa.schema([("date", pa.date32()), ("currency", pa.string()), ("rate", pa.float64())])


def parse_ecb_csv(text: str) -> list[dict[str, object]]:
    rows = list(csv.reader(io.StringIO(text)))
    currencies = [c.strip() for c in rows[0][1:]]
    records: list[dict[str, object]] = []
    for row in rows[1:]:
        if not row or not row[0].strip():
            continue
        day = date.fromisoformat(row[0].strip())
        for currency, value in zip(currencies, row[1:], strict=False):
            value = value.strip()
            if currency and value and value != "N/A":
                records.append({"date": day, "currency": currency, "rate": float(value)})
    return records


def refresh_ecb_rates(lake: Lake, http: PoliteClient | None = None) -> int:
    """Download the full ECB history and store it as lake/reference/ecb_fx.parquet; returns row count."""
    response = (http or PoliteClient()).get(ECB_HISTORY_URL)
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        text = archive.read(archive.namelist()[0]).decode("utf-8")
    records = parse_ecb_csv(text)
    lake.fx_path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(records, schema=SCHEMA), lake.fx_path, compression="zstd")
    return len(records)
