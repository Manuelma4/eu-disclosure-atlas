from datetime import UTC, datetime, timedelta

import dagster as dg
from conftest import fi_doc, fi_row

from disclosure_atlas.connectors.se_fi_pdmr import SwedenFIPDMR
from disclosure_atlas.lake import Lake
from disclosure_atlas.models import Dataset
from disclosure_atlas.orchestration import quality
from disclosure_atlas.orchestration.definitions import (
    LakeResource,
    SnapshotPublisherResource,
    build_definitions,
    source_definitions,
)
from disclosure_atlas.orchestration.regulators import regulator_for


class OfflineSweden(SwedenFIPDMR):
    """The Swedish connector with a synthetic register instead of the network."""

    def __init__(self, http=None):
        super().__init__(http=object())

    def fetch(self, start, end):
        published = f"{start:%d/%m/%Y} 10:00:00"
        yield fi_doc([fi_row(**{"Publication date": published}),
                      fi_row(**{"Publication date": published, "Volume": "1.0", "Status": "Cancelled"})],
                     fetched_at=datetime.now(UTC))


def test_definitions_load_with_one_chain_per_connector():
    defs = build_definitions("/tmp/unused-lake")
    repo = defs.get_repository_def()
    keys = {key.to_user_string() for key in repo.asset_graph.get_all_asset_keys()}
    assert {"bronze/se_fi_pdmr", "silver/se_fi_pdmr", "gold/insider_transactions"} <= keys
    assert {"se_fi_pdmr_business_hours", "se_fi_pdmr_nightly_settle"} <= {s.name for s in repo.schedule_defs}
    checks = {k.name for k in repo.asset_graph.asset_check_keys}
    assert checks == {"freshness", "volume", "identifier_validity", "superseded_share"}


def test_sweden_polls_every_five_minutes_in_stockholm_business_hours():
    sweden = regulator_for("se")
    assert sweden.timezone == "Europe/Stockholm"
    assert sweden.business_hours_crons() == ["*/5 7-19 * * 1-5", "0 20 * * 1-5"]


def test_partition_run_commits_snapshots_runs_checks_and_publishes(tmp_path):
    root = str(tmp_path / "lake")
    defs = dg.Definitions.merge(
        source_definitions(OfflineSweden),
        dg.Definitions(resources={"lake": LakeResource(root=root),
                                  "snapshot_publisher": SnapshotPublisherResource(lake_root=root)}),
    )
    job = defs.resolve_job_def("se_fi_pdmr_job")
    day = (datetime.now(UTC) - timedelta(days=1)).date().isoformat()
    result = job.execute_in_process(partition_key=day, instance=dg.DagsterInstance.ephemeral())
    assert result.success
    checks = {e.check_name: e.passed
              for e in result.get_asset_check_evaluations()}
    assert checks["freshness"] and checks["identifier_validity"]
    assert not checks["superseded_share"]  # one of the two synthetic records is cancelled

    lake = Lake(root)
    assert lake.connect().execute("SELECT count(*) FROM insider_transactions").fetchone()[0] == 1
    events = (tmp_path / "lake" / "events" / "snapshots.jsonl").read_text().splitlines()
    assert len(events) == 1 and f'"snapshot_id": {lake.current_snapshot()}' in events[0]

    # Re-running the partition with unchanged bronze content commits nothing new.
    again = job.execute_in_process(partition_key=day, instance=dg.DagsterInstance.ephemeral())
    assert again.success
    assert lake.connect().execute("SELECT count(*) FROM silver_insider_transactions").fetchone()[0] == 2


def test_volume_flags_a_collapse_against_the_weekday_median(tmp_path):
    con = Lake(tmp_path).connect()
    con.execute("CREATE TEMP TABLE insider_transactions (source VARCHAR, published_at TIMESTAMPTZ)")
    today = datetime(2026, 10, 21, tzinfo=UTC)  # a Wednesday: the last complete weekday is Tuesday the 20th
    for back in range(2, 30):
        day = today - timedelta(days=back)
        if day.weekday() < 5:
            con.executemany("INSERT INTO insider_transactions VALUES ('x', ?)", [[day]] * 50)
    collapse = quality.volume(con, Dataset.INSIDER_TRANSACTIONS, "x", today=today.date())
    assert not collapse.passed and collapse.metadata["median_28d"] == 50
    con.executemany("INSERT INTO insider_transactions VALUES ('x', ?)", [[today - timedelta(days=1)]] * 40)
    assert quality.volume(con, Dataset.INSIDER_TRANSACTIONS, "x", today=today.date()).passed
