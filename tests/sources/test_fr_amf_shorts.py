from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from shorts_support import HOLDER_LEI, doc, isin, mock_client

from disclosure_atlas.connectors.fr_amf_shorts import DATASET_API, FranceAMFShorts
from disclosure_atlas.lake import Lake
from disclosure_atlas.pipeline import ingest

HEADER = ('"Detenteur de la position courte nette";"Legal Entity Identifier detenteur";"Emetteur / issuer";"Ratio";'
          '"code ISIN";"Date de debut position";"Date de debut de publication position";'
          '"Date de fin de publication position"')


def csv_bytes(*rows: str) -> bytes:
    return ("\n".join([HEADER, *rows]) + "\n").encode()


ROWS = (
    f'"EXAMPLE CAPITAL LLP";"{HOLDER_LEI}";"EXAMPLE ROBOTICS";"0.62";"{isin("FR")}";"2026-10-14";"2026-10-15";""',
    f'"EXAMPLE CAPITAL LLP";"{HOLDER_LEI}";"EXAMPLE ROBOTICS";"0.35";"{isin("FR")}";"2015-10-06";"2015-10-07";'
    '"2015-10-08"',
)


def test_parse(ctx):
    current, closed = FranceAMFShorts(http=object()).parse(doc("fr_amf_shorts", csv_bytes(*ROWS), ".csv"), ctx)
    assert (current.holder_name, current.holder_lei, current.issuer_name) == (
        "EXAMPLE CAPITAL LLP", HOLDER_LEI, "EXAMPLE ROBOTICS")
    assert current.isin == isin("FR") and current.position_pct == 0.62 and not current.below_threshold
    assert current.position_date == date(2026, 10, 14)
    assert current.published_at == datetime(2026, 10, 14, 22, 0, tzinfo=UTC)  # midnight in Paris
    assert closed.position_pct == 0.35 and closed.below_threshold


def test_fetch_reads_the_current_file_name_from_the_dataset_api():
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == DATASET_API:
            return httpx.Response(200, json={"resources": [
                {"id": "r1", "format": "csv", "url": "https://files.example.invalid/export_od_vad_1.csv"}]})
        assert request.url.host == "files.example.invalid"
        return httpx.Response(200, content=csv_bytes(*ROWS), headers={"content-type": "text/csv"})

    (raw,) = FranceAMFShorts(http=mock_client(handler)).fetch(date(2012, 11, 1), date(2026, 10, 20))
    assert raw.suffix == ".csv" and raw.params == {"resource": "r1"}
    assert raw.meta["window"] == ["2012-11-01", "2026-10-20"]


@pytest.mark.network
def test_live_history(tmp_path):
    end = date.today()
    report = ingest(FranceAMFShorts(), Lake(tmp_path), end - timedelta(days=60), end)
    assert report.records > 0
