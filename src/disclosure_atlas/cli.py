"""Command line: disclosure-atlas {sources,ingest,watch,publish,contracts,query,stats,export,reference,serve,
openapi,mcp,migrate-lake,snapshots}."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

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


def _cmd_watch(args: argparse.Namespace) -> int:
    """Re-ingest a trailing window forever: new filings appear, revisions are picked up.

    With ATLAS_KAFKA_BOOTSTRAP set, each run is followed by the change-feed publisher (new gold rows to
    atlas.filings.v1, the run to atlas.snapshots.v1), starting after the newest row already in the lake.
    """
    from .events.publisher import from_env

    connector = connectors.get(args.source)()
    lake = Lake(args.lake)
    publisher = from_env(lake, dict(os.environ))
    if publisher:
        publisher.start_at_end()
    while True:
        end = date.today()
        report = ingest(connector, lake, end - timedelta(days=args.lookback), end)
        print(f"[{time.strftime('%H:%M:%S')}] {report.summary()}", flush=True)
        if publisher:
            print(f"[{time.strftime('%H:%M:%S')}] {publisher.publish().summary()}", flush=True)
        if args.once:
            return 0
        time.sleep(args.every)


def _cmd_publish(args: argparse.Namespace) -> int:
    if args.out:
        return _publish_release(args)
    from .events.publisher import from_env

    publisher = from_env(Lake(args.lake), dict(os.environ))
    if publisher is None:
        print("Set ATLAS_KAFKA_BOOTSTRAP (e.g. localhost:19092) to publish events", file=sys.stderr)
        return 2
    print(publisher.publish(backfill=args.backfill).summary())
    return 0


def _publish_release(args: argparse.Namespace) -> int:
    from .publish import PrivacyCheckFailed, publish

    try:
        release = publish(Lake(args.lake), args.out, as_of=args.as_of)
    except PrivacyCheckFailed as error:
        print(error, file=sys.stderr)
        return 1
    for dataset, rows in release.rows.items():
        print(f"{dataset}: {rows} rows")
    print(f"Release {release.version} (snapshot {release.snapshot_id}) in {release.out}: "
          f"{len(release.files) + 1} files, checksums in SHA256SUMS")
    return 0


def _cmd_contracts(args: argparse.Namespace) -> int:
    from .events.contracts import stale_contracts, write_contracts

    if args.check:
        if stale := stale_contracts(args.out):
            print(f"Event contracts out of date in {args.out}: {', '.join(stale)}. "
                  "Run: disclosure-atlas contracts", file=sys.stderr)
            return 1
        print(f"Event contracts in {args.out} are up to date")
        return 0
    for path in write_contracts(args.out):
        print(path)
    return 0


def _cmd_reference(args: argparse.Namespace) -> int:
    lake = Lake(args.lake)
    ids = [i.strip().upper() for i in args.ids.split(",") if i.strip()] if args.ids else None
    if args.name == "fx":
        from .reference.fx import refresh_ecb_rates

        print(f"ECB reference rates: {refresh_ecb_rates(lake)} rows")
    elif args.name == "lei":
        from .reference.gleif import refresh_lei

        fetched, total = refresh_lei(lake, ids, refresh=args.refresh)
        print(f"GLEIF: {fetched} LEI records fetched, {total} cached in {lake.reference_path('gleif_lei')}")
    elif args.name == "firds":
        from .reference.firds import refresh_firds

        found, total = refresh_firds(lake, ids, keep_all=args.all)
        print(f"FIRDS: {found} instruments found, {total} cached in {lake.reference_path('firds_instruments')}")
    else:
        from .reference.openfigi import refresh_figi

        mapped, total = refresh_figi(lake, ids, refresh=args.refresh)
        print(f"OpenFIGI: {mapped} ISINs mapped, {total} listings cached in "
              f"{lake.reference_path('openfigi_listings')}")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    os.environ["ATLAS_LAKE"] = str(Lake(args.lake).root.resolve())
    uvicorn.run("disclosure_atlas.api.app:app_from_env", factory=True, host=args.host, port=args.port,
                reload=args.reload)
    return 0


def _cmd_openapi(args: argparse.Namespace) -> int:
    from .api import create_app

    print(json.dumps(create_app(args.lake).openapi(), indent=2))
    return 0


def _cmd_mcp(args: argparse.Namespace) -> int:
    """MCP server over stdio (for Claude Desktop and other local hosts); logs go to stderr."""
    from .ai.guard import SandboxedLake
    from .ai.mcp_server import build_server
    from .api.readmodel import ReadModel

    build_server(ReadModel(SandboxedLake(Lake(Path(args.lake).resolve())))).run("stdio")
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


def _cmd_migrate_lake(args: argparse.Namespace) -> int:
    lake = Lake(args.lake)
    loaded = lake.migrate()
    for dataset, rows in loaded.items():
        print(f"{dataset}: {rows} rows loaded from {lake.silver / dataset}")
    catalog = re.sub(r"password=\S+", "password=***", lake.catalog)
    print(f"DuckLake catalog {catalog}, data in {lake.data_path}, snapshot {lake.version()}")
    return 0


def _cmd_snapshots(args: argparse.Namespace) -> int:
    for snap in Lake(args.lake).snapshots()[-args.last:]:
        time = snap["snapshot_time"].isoformat(timespec="seconds") if snap["snapshot_time"] else "-"
        print(f"{snap['snapshot_id']:>8}  {time}  {snap['commit_message'] or json.dumps(snap['changes'], default=str)}")
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

    p = sub.add_parser("watch", help="ingest a trailing window repeatedly (near real time)")
    p.add_argument("source")
    p.add_argument("--every", type=int, default=300, help="seconds between runs (default: 300)")
    p.add_argument("--lookback", type=int, default=2, help="days re-fetched each run (default: 2)")
    p.add_argument("--once", action="store_true", help="run once and exit")
    p.set_defaults(func=_cmd_watch)

    p = sub.add_parser(
        "publish", help="with --out: write an open-data release (docs/open-data.md); without: gold rows to Kafka"
    )
    p.add_argument("--out", help="release folder: Parquet, DuckDB, datapackage.json, quality report, SHA256SUMS")
    p.add_argument("--as-of", type=int, help="DuckLake snapshot to release (default: the current one)")
    p.add_argument("--backfill", action="store_true",
                   help="Kafka: without a checkpoint, publish all history instead of starting at the newest row")
    p.set_defaults(func=_cmd_publish)

    p = sub.add_parser("reference", help="refresh reference data (ECB FX, GLEIF LEI, ESMA FIRDS, OpenFIGI)")
    p.add_argument("name", choices=["fx", "lei", "firds", "figi"])
    p.add_argument("--ids", help="comma-separated LEIs or ISINs instead of those in the lake (lei, firds, figi)")
    p.add_argument("--refresh", action="store_true", help="re-fetch identifiers already cached (lei, figi)")
    p.add_argument("--all", action="store_true", help="firds: keep every equity instrument, not only the lake's")
    p.set_defaults(func=_cmd_reference)

    p = sub.add_parser("serve", help="run the REST API (FastAPI + uvicorn)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--reload", action="store_true")
    p.set_defaults(func=_cmd_serve)

    sub.add_parser("openapi", help="print the OpenAPI document").set_defaults(func=_cmd_openapi)

    p = sub.add_parser("contracts", help="write the event payload JSON Schemas (CloudEvents data)")
    p.add_argument("--out", default="contracts/events")
    p.add_argument("--check", action="store_true", help="fail if the committed schemas differ from the code")
    p.set_defaults(func=_cmd_contracts)

    sub.add_parser("mcp", help="run the MCP server over stdio (needs the ai extra)").set_defaults(func=_cmd_mcp)

    p = sub.add_parser("query", help="run SQL against the gold views")
    p.add_argument("sql")
    p.add_argument("--max-rows", type=int, default=40)
    p.set_defaults(func=_cmd_query)

    sub.add_parser("stats", help="record counts per source and year").set_defaults(func=_cmd_stats)

    p = sub.add_parser("export", help="write gold tables to Parquet")
    p.add_argument("--out", default="exports")
    p.set_defaults(func=_cmd_export)

    sub.add_parser(
        "migrate-lake", help="load Parquet silver files (pre-DuckLake layout) into the DuckLake catalog; idempotent"
    ).set_defaults(func=_cmd_migrate_lake)

    p = sub.add_parser("snapshots", help="list DuckLake snapshots (ids usable as ?as_of= in the API)")
    p.add_argument("--last", type=int, default=20)
    p.set_defaults(func=_cmd_snapshots)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
