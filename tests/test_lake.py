from datetime import UTC, datetime

import pyarrow.parquet as pq
from conftest import fi_doc, fi_row

from disclosure_atlas.connectors.se_fi_pdmr import SwedenFIPDMR
from disclosure_atlas.lake import Lake
from disclosure_atlas.models import Dataset, InsiderTransaction, NetShortPosition, arrow_schema

T1 = datetime(2026, 10, 3, 6, 0, tzinfo=UTC)
T2 = datetime(2026, 10, 4, 6, 0, tzinfo=UTC)


def load(lake, doc, ctx):
    connector = SwedenFIPDMR(http=object())
    lake.write_bronze(doc)
    lake.write_silver(Dataset.INSIDER_TRANSACTIONS, connector.name, list(connector.parse(doc, ctx)))


def test_arrow_schema_covers_every_model_field():
    for model in (InsiderTransaction, NetShortPosition):
        assert arrow_schema(model).names == list(model.model_fields)


def test_empty_lake_exposes_empty_views(tmp_path):
    con = Lake(tmp_path).connect()
    assert con.execute("SELECT count(*) FROM insider_transactions").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM net_short_positions").fetchone()[0] == 0


def test_gold_keeps_latest_version_and_drops_superseded(tmp_path, ctx):
    lake = Lake(tmp_path)
    first = fi_row(Volume="2700.0")
    other = fi_row(Volume="5.0")
    load(lake, fi_doc([first, other], fetched_at=T1), ctx)
    # A later fetch shows the first row was revised and adds its corrected version.
    load(lake, fi_doc([fi_row(Volume="2700.0", Status="Revised"), fi_row(Volume="2750.0", Amendment="Yes")],
                      fetched_at=T2), ctx)
    con = lake.connect()
    assert con.execute("SELECT count(*) FROM silver_insider_transactions").fetchone()[0] == 4
    volumes = sorted(v for (v,) in con.execute("SELECT volume FROM insider_transactions").fetchall())
    assert volumes == [5.0, 2750.0]
    assert con.execute("SELECT bool_and(is_open_market) FROM insider_transactions").fetchone()[0] is True


def test_bronze_manifest_records_provenance(tmp_path, ctx):
    lake = Lake(tmp_path)
    load(lake, fi_doc([fi_row()]), ctx)
    manifest = (tmp_path / "bronze" / "se_fi_pdmr" / "_manifest.jsonl").read_text().splitlines()
    assert len(manifest) == 1 and '"sha256"' in manifest[0]


def test_export_writes_gold_parquet(tmp_path, ctx):
    lake = Lake(tmp_path / "lake")
    load(lake, fi_doc([fi_row(), fi_row(Volume="1.0", Status="Cancelled")]), ctx)
    counts = lake.export(tmp_path / "out")
    assert counts == {"insider_transactions": 1, "net_short_positions": 0}
    table = pq.read_table(tmp_path / "out" / "insider_transactions.parquet")
    assert "is_open_market" in table.column_names and "pdmr_id" in table.column_names
