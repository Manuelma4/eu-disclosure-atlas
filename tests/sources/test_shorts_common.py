from datetime import date, datetime

import pytest
from shorts_support import FETCHED_AT, HOLDER_LEI, doc, isin

from disclosure_atlas.connectors._shorts import Row, ShortsConnector, parse_pct, table_rows, to_date
from disclosure_atlas.lake import Lake


class FakeShorts(ShortsConnector):
    name = "xx_fake_shorts"
    jurisdiction = "XX"
    authority = "Fake authority"
    homepage = "https://example.invalid/"

    def __init__(self, rows):
        super().__init__(http=object())
        self._rows = rows

    def fetch(self, start, end):  # pragma: no cover - never called
        raise NotImplementedError

    def rows(self, document):
        yield from self._rows


def row(**overrides):
    values = dict(holder="Example Capital LLP", issuer="Example Robotics SA", isin=isin("FR"), pct=0.62,
                  position_date=date(2026, 10, 14))
    values.update(overrides)
    return Row(**values)


def parse(rows, ctx, window=None):
    return list(FakeShorts(rows).parse(doc("xx_fake_shorts", b"", ".csv", window), ctx))


@pytest.mark.parametrize(("value", "kwargs", "expected"), [
    ("0,50", {}, (0.5, False)),
    ("1.21", {}, (1.21, False)),
    (0.48, {}, (0.48, True)),
    ("<0,5", {}, (None, True)),
    ("< 0.5 %", {}, (None, True)),
    (0.0105, {"fraction": True}, (1.05, False)),
    (0, {"fraction": True, "zero_means_below": True}, (None, True)),
    (0.0, {}, (0.0, True)),
    ("", {}, (None, False)),
    (None, {}, (None, False)),
])
def test_parse_pct(value, kwargs, expected):
    assert parse_pct(value, **kwargs) == expected


def test_to_date_accepts_strings_datetimes_and_padding():
    assert to_date(" 2026-08-04 ") == date(2026, 8, 4)
    assert to_date("2016-06-28T00:00:00") == date(2016, 6, 28)
    assert to_date("2026-10-16 00:00:00", "%Y-%m-%d %H:%M:%S") == date(2026, 10, 16)
    assert to_date(datetime(2026, 5, 29)) == date(2026, 5, 29)
    assert to_date("29/05/2026", "%Y-%m-%d", "%d/%m/%Y") == date(2026, 5, 29)
    assert to_date("n/a") is None and to_date(None) is None


def test_table_rows_skips_titles_and_matches_bilingual_headers():
    rows = [["Title"], [None], ["Holder\n(Innehavare)", "ISIN", "Note"], ["A LLP", "X", None], [None, None],
            ["B LP", "Y"]]
    assert list(table_rows(rows, {"holder": "holder", "isin": "isin"})) == [
        {"holder": "A LLP", "isin": "X"}, {"holder": "B LP", "isin": "Y"}]


def test_record_fields(ctx):
    (p,) = parse([row(holder_lei="not-a-lei", issuer_lei=None)], ctx)
    assert (p.source, p.jurisdiction, p.holder_name) == ("xx_fake_shorts", "XX", "Example Capital LLP")
    assert p.isin == isin("FR")
    assert p.position_pct == 0.62 and p.below_threshold is False and p.holder_lei is None
    assert p.fetched_at == FETCHED_AT and p.parser == "xx_fake_shorts@1"


def test_current_and_historic_copies_of_a_position_collapse(ctx):
    same = [row(), row(holder=" EXAMPLE  capital llp ", issuer="EXAMPLE ROBOTICS SA")]
    assert len(parse(same, ctx)) == 1


def test_distinct_dates_or_percentages_are_distinct_positions(ctx):
    rows = [row(), row(pct=0.72), row(position_date=date(2026, 10, 15)), row(pct=None, below_threshold=True)]
    assert len({p.record_id for p in parse(rows, ctx)}) == 4


def test_invalid_isin_is_dropped_but_still_identifies_the_position(ctx):
    a, b = parse([row(isin="FR0000000001"), row(isin="FR0000000003")], ctx)
    assert a.isin is None and b.isin is None and a.record_id != b.record_id


def test_window_and_incomplete_rows(ctx):
    rows = [row(), row(position_date=date(2012, 1, 2)), row(position_date=None), row(holder=""), row(pct=None)]
    assert [p.position_date for p in parse(rows, ctx, (date(2012, 11, 1), date(2026, 10, 20)))] == [date(2026, 10, 14)]


def test_gold_view_keeps_one_row_per_position_across_files(tmp_path, ctx):
    lake = Lake(tmp_path)
    for _ in range(2):  # the same position in the current and in the historic file
        lake.write_silver(FakeShorts.dataset, "xx_fake_shorts", parse([row()], ctx))
    con = lake.connect()
    assert con.execute("SELECT count(*) FROM net_short_positions").fetchone()[0] == 1


@pytest.mark.parametrize("name", ["Example Capital LLP", "Example Advisors, L.L.C.", "EXAMPLE BANK AG",
                                  "Example Kapitalanlagegesellschaft mbH", "Example Retirement System of Texas"])
def test_institutions_keep_their_name(ctx, name):
    (p,) = parse([row(holder=name)], ctx)
    assert p.holder_name == name and not p.holder_is_natural_person


def test_natural_person_holders_are_pseudonymized(ctx):
    a, b = parse([row(holder="Erik Example"), row(holder="ERIK  EXAMPLE", pct=0.72)], ctx)
    assert a.holder_is_natural_person and a.holder_name.startswith("p_")
    assert a.holder_name == b.holder_name  # stable, so the position series of one holder stays linked
    assert "erik" not in a.model_dump_json().casefold()
    (lei_holder,) = parse([row(holder="Erik Example", holder_lei=HOLDER_LEI)], ctx)
    assert lei_holder.holder_name == "Erik Example" and not lei_holder.holder_is_natural_person
