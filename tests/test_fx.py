from datetime import UTC, date, datetime

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from conftest import fi_doc, fi_row

from disclosure_atlas.connectors.base import ParseContext
from disclosure_atlas.connectors.se_fi_pdmr import SwedenFIPDMR
from disclosure_atlas.lake import Lake
from disclosure_atlas.models import Dataset
from disclosure_atlas.reference.fx import SCHEMA, parse_ecb_csv

ECB_SAMPLE = """Date,USD,JPY,SEK,CYP,
2026-10-02,1.1712,172.5,11.0020,N/A,
2026-09-30,1.1650,171.0,10.9000,N/A,
"""


def test_parse_ecb_csv_skips_missing_values():
    records = parse_ecb_csv(ECB_SAMPLE)
    assert {"date": date(2026, 10, 2), "currency": "SEK", "rate": 11.002} in records
    assert not [r for r in records if r["currency"] == "CYP"]
    assert len(records) == 6


def test_gold_amount_eur_uses_latest_rate_on_or_before_trade(tmp_path):
    lake = Lake(tmp_path)
    lake.fx_path.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(parse_ecb_csv(ECB_SAMPLE), schema=SCHEMA), lake.fx_path)
    rows = [
        fi_row(**{"Transaction date": "01/10/2026 00:00:00"}),  # weekend-style gap: uses 2026-09-30 rate
        fi_row(**{"Transaction date": "02/10/2026 00:00:00", "Volume": "100.0", "Price": "11.002"}),
        fi_row(**{"Transaction date": "02/10/2026 00:00:00", "Volume": "100.0", "Price": "1.0", "Currency": "EUR"}),
    ]
    doc = fi_doc(rows, fetched_at=datetime(2026, 10, 3, tzinfo=UTC))
    records = list(SwedenFIPDMR(http=object()).parse(doc, ParseContext(pseudonym_key=b"k")))
    lake.write_silver(Dataset.INSIDER_TRANSACTIONS, "se_fi_pdmr", records)
    amounts = dict(lake.connect().execute(
        "SELECT volume, amount_eur FROM insider_transactions WHERE currency = 'SEK'").fetchall())
    assert amounts[2750.0] == pytest.approx(2750 * 61.5 / 10.9)
    assert amounts[100.0] == pytest.approx(100.0)
    eur = lake.connect().execute("SELECT amount_eur FROM insider_transactions WHERE currency = 'EUR'").fetchone()[0]
    assert eur == pytest.approx(100.0)
