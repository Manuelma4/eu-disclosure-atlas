"""Command line: disclosure-atlas {sources,ingest,query,stats,export}."""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

from . import connectors
from .lake import Lake
from .pipeline import ingest

STATS_SQL = """
SELECT 'insider_transactions' AS dataset, source, year(published_at) AS year, count(*) AS records,
       count(*) FILTER (WHERE is_open_market) AS open_market
FROM insider_transactions GROUP BY ALL
UNION ALL
SELECT 'net_short_positions', source, year(position_date), count(*), NULL
FROM net_short_positions GROUP BY ALL
ORDER BY 1, 2, 3
"""


def _cmd_sources(_: argparse.Namespace) -> int:
    for name, cls in connectors.all_connectors().items():
        print(f"{name:<22} {cls.jurisdiction}  {cls.dataset.value:<22} {cls.authority}  {cls.homepage}")
    return 0


def _cmd_ingest(args: argparse.Namespace) -> int:
    end = args.end or date.today()
    start = args.start or end - timedelta(days=args.days - 1)
    connector = connectors.get(args.source)()
    report = ingest(connector, Lake(args.lake), start, end)
    print(report.summary())
    return 0


def _cmd_query(args: argparse.Namespace) -> int:
    Lake(args.lake).connect().sql(args.sql).show(max_rows=args.max_rows, max_width=10_000)
    return 0


def _cmd_stats(args: argparse.Namespace) -> int:
    Lake(args.lake).connect().sql(STATS_SQL).show(max_rows=1_000)
    return 0


def _cmd_export(args: argparse.Namespace) -> int:
    for dataset, rows in Lake(args.lake).export(args.out).items():
        print(f"{dataset}: {rows} rows -> {args.out}/{dataset}.parquet")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="disclosure-atlas", description=__doc__)
    parser.add_argument("--lake", default="lake", help="lake root folder (default: ./lake)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("sources", help="list registered connectors").set_defaults(func=_cmd_sources)

    p = sub.add_parser("ingest", help="fetch and parse one source into the lake")
    p.add_argument("source")
    p.add_argument("--start", type=date.fromisoformat, help="first publication date (YYYY-MM-DD)")
    p.add_argument("--end", type=date.fromisoformat, help="last publication date (default: today)")
    p.add_argument("--days", type=int, default=7, help="window when --start is omitted (default: 7)")
    p.set_defaults(func=_cmd_ingest)

    p = sub.add_parser("query", help="run SQL against the gold views")
    p.add_argument("sql")
    p.add_argument("--max-rows", type=int, default=40)
    p.set_defaults(func=_cmd_query)

    sub.add_parser("stats", help="record counts per source and year").set_defaults(func=_cmd_stats)

    p = sub.add_parser("export", help="write gold tables to Parquet")
    p.add_argument("--out", default="exports")
    p.set_defaults(func=_cmd_export)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
