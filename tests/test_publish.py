"""Open-data releases on a synthetic lake (no real person or filing)."""

import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb
import pyarrow.parquet as pq
import pytest
from conftest import EXAMPLE_ISIN, EXAMPLE_LEI, fi_doc, fi_row

from disclosure_atlas.cli import main
from disclosure_atlas.connectors.se_fi_pdmr import SwedenFIPDMR
from disclosure_atlas.lake import Lake
from disclosure_atlas.models import Dataset, NetShortPosition
from disclosure_atlas.publish import PrivacyCheckFailed, field_descriptions, publish
from disclosure_atlas.publish.sources import SOURCE_TERMS, terms_note

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
FETCHED = datetime(2026, 10, 3, 6, 0, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[1]


def short(record_id: str, **overrides) -> NetShortPosition:
    fields = dict(
        record_id=record_id, source="se_fi_shorts", jurisdiction="SE", published_at=FETCHED,
        position_date=date(2026, 10, 2), holder_name="Example Capital LLP", holder_lei=EXAMPLE_LEI,
        issuer_name="Example Robotics AB", isin=EXAMPLE_ISIN, position_pct=0.61, fetched_at=FETCHED,
        parser="se_fi_shorts@1",
    )
    return NetShortPosition(**(fields | overrides))


@pytest.fixture
def lake(tmp_path, ctx):
    lake = Lake(tmp_path / "lake")
    connector = SwedenFIPDMR(http=object())
    doc = fi_doc([fi_row(), fi_row(Volume="10.0", **{"Nature of transaction": "Disposal"}),
                  fi_row(ISIN="SE0000000000")], fetched_at=FETCHED)
    lake.write_bronze(doc)
    lake.write_silver(Dataset.INSIDER_TRANSACTIONS, connector.name, list(connector.parse(doc, ctx)))
    lake.write_silver(Dataset.NET_SHORT_POSITIONS, "se_fi_shorts", [
        short("s1"),
        short("s2", holder_name="p_0123456789abcdef0123", holder_is_natural_person=True, holder_lei=None),
        short("s3", position_date=date(2026, 9, 30), holder_lei="NOT-A-LEI"),
    ])
    return lake


def test_release_contents(lake, tmp_path):
    out = tmp_path / "release"
    release = publish(lake, out, now=NOW)
    assert release.rows == {"insider_transactions": 3, "net_short_positions": 3}
    assert release.version == f"2026.10.20-s{lake.current_snapshot()}"
    names = {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()}
    assert names == {
        "insider_transactions.parquet", "net_short_positions.parquet", "eu-disclosure-atlas.duckdb",
        "datapackage.json", "quality.json", "quality.md", "badge.json", "SHA256SUMS",
    }
    assert not any("bronze" in name for name in names)

    # Parquet, zstd, with the gold-only columns.
    meta = pq.ParquetFile(out / "insider_transactions.parquet").metadata
    assert meta.row_group(0).column(0).compression == "ZSTD"
    assert {"is_open_market", "amount_eur", "first_seen_at"} <= set(meta.schema.names)

    # One-line query on the DuckDB file, with reuse terms and release metadata inside.
    con = duckdb.connect(str(out / "eu-disclosure-atlas.duckdb"), read_only=True)
    assert con.execute("SELECT count(*) FROM net_short_positions").fetchone()[0] == 3
    assert con.execute("SELECT commercial_use FROM sources WHERE source = 'se_fi_pdmr'").fetchone()[0] is True
    assert con.execute("SELECT snapshot_id FROM release").fetchone()[0] == release.snapshot_id
    con.close()

    # Checksums cover every other file and match.
    sums = dict(reversed(line.split("  ")) for line in (out / "SHA256SUMS").read_text().splitlines())
    assert set(sums) == names - {"SHA256SUMS"}
    for name, digest in sums.items():
        assert hashlib.sha256((out / name).read_bytes()).hexdigest() == digest


def test_datapackage(lake, tmp_path):
    out = tmp_path / "release"
    release = publish(lake, out, now=NOW)
    package = json.loads((out / "datapackage.json").read_text())
    assert package["$schema"].startswith("https://datapackage.org/profiles/2.0/")
    assert package["atlas:snapshotId"] == release.snapshot_id
    insider = next(r for r in package["resources"] if r["name"] == "insider_transactions")
    assert insider["hash"] == f"sha256:{release.files['insider_transactions.parquet']}"
    fields = {f["name"]: f for f in insider["schema"]["fields"]}
    assert fields["nature"]["constraints"]["enum"][:2] == ["buy", "sell"]
    assert fields["transaction_date"]["type"] == "date"
    assert fields["published_at"]["type"] == "datetime"
    assert fields["is_open_market"]["type"] == "boolean"
    assert "Pseudonym" in fields["pdmr_id"]["description"]
    shorts = next(r for r in package["resources"] if r["name"] == "net_short_positions")
    assert {f["name"]: f for f in shorts["schema"]["fields"]}["published_at"]["description"]  # "As above"
    sources = {s.get("atlas:source"): s for s in package["sources"]}
    assert set(sources) >= {"se_fi_pdmr", "se_fi_shorts"}
    assert sources["se_fi_pdmr"]["atlas:attribution"].startswith("Source: Finansinspektionen")
    assert "FI open data" in sources["se_fi_pdmr"]["atlas:terms"]
    assert any(s["title"].startswith("European Central Bank") for s in package["sources"])


def test_quality_report(lake, tmp_path):
    out = tmp_path / "release"
    publish(lake, out, now=NOW)
    report = json.loads((out / "quality.json").read_text())
    insider = report["datasets"]["insider_transactions"]
    (fresh,) = insider["freshness"]
    assert fresh["source"] == "se_fi_pdmr" and fresh["age_days"] == 17 and fresh["stale"] is True
    isin = next(r for r in insider["identifiers"] if r["column"] == "isin")
    assert isin["rows"] == 3 and isin["present"] == 2  # the connector drops the invalid ISIN
    assert isin["valid_rate"] == 1.0
    holder = next(r for r in report["datasets"]["net_short_positions"]["identifiers"] if r["column"] == "holder_lei")
    assert (holder["present"], holder["valid"]) == (2, 1)
    assert insider["duplicates"]["duplicate_record_ids"] == 0
    assert report["datasets"]["net_short_positions"]["privacy"] == {"natural_person_holder_not_pseudonymized": 0}
    markdown = (out / "quality.md").read_text()
    assert "| `se_fi_pdmr` |" in markdown and "stale" in markdown
    assert json.loads((out / "badge.json").read_text())["color"] == "orange"


def test_duplicates_by_business_key(lake, tmp_path):
    lake.write_silver(Dataset.NET_SHORT_POSITIONS, "se_fi_shorts", [short("s1-again")])
    publish(lake, tmp_path / "release", now=NOW)
    report = json.loads((tmp_path / "release" / "quality.json").read_text())
    assert report["datasets"]["net_short_positions"]["duplicates"] == {
        "duplicate_record_ids": 0, "business_key_groups": 1, "business_key_rows": 2,
    }


def test_release_refused_when_a_natural_person_is_named(lake, tmp_path):
    lake.write_silver(Dataset.NET_SHORT_POSITIONS, "se_fi_shorts", [
        short("leak", holder_name="Jane Example", holder_is_natural_person=True, holder_lei=None),
    ])
    with pytest.raises(PrivacyCheckFailed, match="natural_person_holder_not_pseudonymized"):
        publish(lake, tmp_path / "release", now=NOW)
    assert not (tmp_path / "release").exists()


def test_release_pins_a_snapshot(lake, tmp_path):
    pinned = lake.current_snapshot()
    lake.write_silver(Dataset.NET_SHORT_POSITIONS, "se_fi_shorts", [short("s4", position_date=date(2026, 10, 5))])
    release = publish(lake, tmp_path / "release", as_of=pinned, now=NOW)
    assert release.snapshot_id == pinned and release.rows["net_short_positions"] == 3


def test_empty_lake_release(tmp_path):
    release = publish(Lake(tmp_path / "lake"), tmp_path / "release", now=NOW)
    assert release.rows == {"insider_transactions": 0, "net_short_positions": 0}
    assert json.loads((tmp_path / "release" / "badge.json").read_text())["color"] == "brightgreen"


def test_cli_publish_out(lake, tmp_path, capsys):
    lake.close()
    assert main(["--lake", str(lake.root), "publish", "--out", str(tmp_path / "release")]) == 0
    assert "checksums in SHA256SUMS" in capsys.readouterr().out
    assert (tmp_path / "release" / "datapackage.json").exists()


def test_release_script_dry_run(lake, tmp_path):
    lake.close()
    out = tmp_path / "release"
    env = {**os.environ, "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}"}
    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / "release-data.sh"), "--lake", str(lake.root), "--out", str(out), "--dry-run"],
        capture_output=True, text=True, env=env, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "gh release create data-" in result.stdout and "--latest" in result.stdout
    notes = (out / "RELEASE_NOTES.md").read_text()
    assert "## Data-quality report" in notes and "/insider_transactions.parquet'" in notes


def test_every_documented_source_has_terms():
    for source in SOURCE_TERMS:
        assert terms_note(source), f"docs/sources/{source}.md has no Terms row"


def test_field_descriptions_cover_both_datasets():
    descriptions = field_descriptions()
    assert descriptions["insider_transactions"]["nature_raw"]
    assert descriptions["net_short_positions"]["source"] == descriptions["insider_transactions"]["source"]
