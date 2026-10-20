from datetime import UTC, date, datetime

import httpx
import pytest
from conftest import EXAMPLE_ISIN, EXAMPLE_LEI, NBSP, fi_csv, fi_doc, fi_row

from disclosure_atlas.connectors.se_fi_pdmr import ROW_CAP, SwedenFIPDMR
from disclosure_atlas.lake import Lake
from disclosure_atlas.pipeline import ingest


def parse(rows, ctx):
    return list(SwedenFIPDMR(http=object()).parse(fi_doc(rows), ctx))


def test_open_market_purchase(ctx):
    (tx,) = parse([fi_row()], ctx)
    assert tx.source == "se_fi_pdmr" and tx.jurisdiction == "SE"
    assert tx.issuer_name == "Example Robotics AB (publ)" and tx.issuer_lei == EXAMPLE_LEI
    assert tx.published_at == datetime(2026, 10, 2, 21, 24, 55, tzinfo=UTC)
    assert tx.transaction_date == date(2026, 10, 1)
    assert (tx.nature, tx.direction, tx.instrument_type) == ("buy", 1, "share")
    assert tx.pdmr_role == "ceo"
    assert tx.isin == EXAMPLE_ISIN
    assert tx.volume == 2750.0 and tx.price == 61.5 and tx.amount == pytest.approx(169125.0)
    assert tx.on_venue is True and tx.is_superseded is False
    assert tx.parser == "se_fi_pdmr@1"


def test_names_of_natural_persons_never_reach_the_record(ctx):
    (tx,) = parse([fi_row()], ctx)
    assert "anna" not in tx.model_dump_json().casefold()
    assert tx.pdmr_id == tx.notifier_id and tx.notifier_name is None


def test_closely_associated_legal_entity_keeps_its_name(ctx):
    (tx,) = parse([fi_row(**{"Notifier": "Example Family Holding AB", "Closely associated": "Yes"})], ctx)
    assert tx.closely_associated is True
    assert tx.notifier_is_legal_entity is True and tx.notifier_name == "Example Family Holding AB"
    assert tx.notifier_id != tx.pdmr_id


def test_option_allotment_outside_venue(ctx):
    row = fi_row(**{
        "Nature of transaction": "Allotment", "Intrument type": "Option", "ISIN": "", "Price": "0.0",
        "Volume": "100000.0", "Trading venue": "Outside a trading venue",
        "Linked to share option programme": "Yes",
        "Position": NBSP.join(["Other", "senior", "executive"]),
    })
    (tx,) = parse([row], ctx)
    assert (tx.nature, tx.instrument_type, tx.pdmr_role) == ("grant", "option", "executive")
    assert tx.on_venue is False and tx.amount == 0.0 and tx.isin is None
    assert tx.linked_to_share_option_programme is True


def test_revised_and_cancelled_rows_are_superseded(ctx):
    old = fi_row(**{"Volume": "2700.0", "Status": "Revised"})
    new = fi_row(**{"Amendment": "Yes", "Initial notification": "", "Status": "Current",
                    "Details of amendment": "Volym från 2700,0 till 2750,0."})
    cancelled = fi_row(**{"Volume": "10.0", "Status": "Cancelled"})
    txs = parse([new, old, cancelled], ctx)
    assert [t.is_superseded for t in txs] == [False, True, True]
    assert txs[0].is_amendment and txs[0].amendment_details.startswith("Volym")


def test_identical_rows_get_distinct_stable_ids(ctx):
    txs = parse([fi_row(), fi_row()], ctx)
    assert len({t.record_id for t in txs}) == 2
    assert [t.record_id for t in parse([fi_row(), fi_row()], ctx)] == [t.record_id for t in txs]


def test_multi_role_and_mar_body_category(ctx):
    multi = NBSP.join(["Deputy", "CEO/Deputy", "Managing", "Director"]) + ", " + NBSP.join(
        ["Chief", "Financial", "Officer", "(CFO)"])
    body = NBSP.join(["Other", "member", "of", "the", "company's", "administrative,", "management", "or",
                      "supervisory", "body"])
    txs = parse([fi_row(Position=multi), fi_row(Position=body, Volume="1.0")], ctx)
    assert [t.pdmr_role for t in txs] == ["cfo", "other"]


class FakeHTTP:
    """Returns ROW_CAP rows for windows of 3+ days and 3 rows otherwise."""

    def __init__(self):
        self.calls = []

    def get(self, url, params=None):
        start = date.fromisoformat(params["Publiceringsdatum.From"])
        end = date.fromisoformat(params["Publiceringsdatum.To"])
        self.calls.append((start, end))
        n = ROW_CAP if (end - start).days >= 2 else 3
        rows = [fi_row(Volume=f"{i}.0") for i in range(n)]
        return httpx.Response(200, content=fi_csv(rows), headers={"content-type": "text/csv"},
                              request=httpx.Request("GET", url))


def test_fetch_splits_windows_that_hit_the_row_cap():
    http = FakeHTTP()
    docs = list(SwedenFIPDMR(http=http).fetch(date(2026, 10, 1), date(2026, 10, 7)))
    windows = [tuple(d.meta["window"]) for d in docs]
    assert windows == [("2026-10-01", "2026-10-02"), ("2026-10-03", "2026-10-04"),
                       ("2026-10-05", "2026-10-06"), ("2026-10-07", "2026-10-07")]
    assert all(d.meta["rows"] == 3 and not d.meta["truncated"] for d in docs)


def test_single_day_at_cap_is_flagged_truncated(tmp_path):
    class AlwaysCap(FakeHTTP):
        def get(self, url, params=None):
            rows = [fi_row(Volume=f"{i}.0") for i in range(ROW_CAP)]
            return httpx.Response(200, content=fi_csv(rows), request=httpx.Request("GET", url))

    report = ingest(SwedenFIPDMR(http=AlwaysCap()), Lake(tmp_path), date(2026, 10, 1), date(2026, 10, 1))
    assert report.records == ROW_CAP
    assert report.warnings and "row cap" in report.warnings[0]


@pytest.mark.network
def test_live_export_parses(tmp_path):
    end = date.today()
    report = ingest(SwedenFIPDMR(), Lake(tmp_path), date.fromordinal(end.toordinal() - 3), end)
    assert report.documents >= 1
    con = Lake(tmp_path).connect()
    assert con.execute("SELECT count(*) FROM insider_transactions WHERE issuer_lei IS NULL").fetchone()[0] == 0
