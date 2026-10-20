from datetime import date, timedelta
from pathlib import Path

import pytest
from shorts_support import doc

from disclosure_atlas.connectors.es_cnmv_shorts import SpainCNMVShorts
from disclosure_atlas.lake import Lake
from disclosure_atlas.pipeline import ingest

FIXTURE = Path(__file__).with_name("fixtures") / "es_cnmv_shorts.xls"  # see fixtures/make_es_cnmv_shorts_xls.py
ISIN = "ES0EXAMPLE17"
ISSUER_LEI = "959800EXAMPLE0000180"


def parse(ctx, window=None):
    pytest.importorskip("xlrd")
    return list(SpainCNMVShorts(http=object()).parse(doc("es_cnmv_shorts", FIXTURE.read_bytes(), ".xls", window), ctx))


def test_parse_all_sheets_without_double_counting(ctx):
    positions = parse(ctx)
    # Current is a subset of Series; Previous holds closed positions, back to the pre-SSR regime (2010).
    assert [(p.holder_name, p.position_date.isoformat(), p.position_pct) for p in positions] == [
        ("Example Capital LLP", "2026-10-14", 0.62),
        ("Example Capital LLP", "2026-09-30", 0.55),
        ("Sample Partners LP", "2021-02-19", None),
        ("Sample Partners LP", "2021-02-18", 0.55),
        ("Sample Partners LP", "2010-06-10", 0.52),
    ]
    first = positions[0]
    assert (first.issuer_name, first.issuer_lei, first.isin, first.holder_lei) == (
        "EXAMPLE ENERGIA, S.A.", ISSUER_LEI, ISIN, None)
    assert positions[2].below_threshold  # published as 0


def test_window_drops_pre_ssr_rows(ctx):
    assert len(parse(ctx, (date(2012, 11, 1), date(2026, 10, 20)))) == 4


@pytest.mark.network
def test_live_workbook(tmp_path):
    end = date.today()
    report = ingest(SpainCNMVShorts(), Lake(tmp_path), end - timedelta(days=60), end)
    assert report.records > 0
