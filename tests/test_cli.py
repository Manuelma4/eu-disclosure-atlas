import pytest
from conftest import fi_doc, fi_row

from disclosure_atlas import connectors
from disclosure_atlas.cli import main
from disclosure_atlas.connectors.se_fi_pdmr import SwedenFIPDMR


def test_registry_discovers_connectors():
    assert "se_fi_pdmr" in connectors.all_connectors()
    with pytest.raises(KeyError):
        connectors.get("xx_missing")


def test_sources_lists_sweden(capsys):
    assert main(["sources"]) == 0
    assert "se_fi_pdmr" in capsys.readouterr().out


def test_ingest_stats_query_export_offline(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(SwedenFIPDMR, "fetch", lambda self, start, end: iter([fi_doc([fi_row(), fi_row(
        **{"Nature of transaction": "Disposal", "Volume": "10.0"})])]))
    lake = str(tmp_path / "lake")
    assert main(["--lake", lake, "ingest", "se_fi_pdmr", "--start", "2026-10-01", "--end", "2026-10-02"]) == 0
    assert "2 records" in capsys.readouterr().out
    assert main(["--lake", lake, "stats"]) == 0
    assert "se_fi_pdmr" in capsys.readouterr().out
    assert main(["--lake", lake, "query", "SELECT nature, count(*) AS n FROM insider_transactions GROUP BY 1"]) == 0
    assert "sell" in capsys.readouterr().out
    assert main(["--lake", lake, "export", "--out", str(tmp_path / "out")]) == 0
    assert (tmp_path / "out" / "insider_transactions.parquet").exists()
